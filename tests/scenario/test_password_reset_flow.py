"""S3-2. Сброс пароля: OTP-код на почту, смена пароля, повторный код и капча.

Живой сценарий на уникальном покупателе (регистрация с обходом капчи, никаких
чужих аккаунтов): ``POST /store/password-reset/start`` присылает шестизначный
OTP-код, ``POST /store/password-reset/verify`` принимает новый пароль и отвечает
``{"success": true}``. В одном сценарии проверяются все обещания продукта:
слабый пароль — ``400 PASSWORD_INVALID`` (challenge жив), успешный verify,
повторная выдача тем же кодом — ``400 CODE_USED``, вход новым паролем работает,
старый — ``401``. Ссылки в письме нет — только код, код читается
``mailbox.wait_for_code``. Писем на адрес ровно два (регистрация + сброс),
лимит 5 писем/час на адрес не превышается.
"""

from __future__ import annotations

import re
import uuid

import allure
import pytest

from qa.http import CAPTCHA_BYPASS_ENV, captcha_bypass_token, with_captcha_bypass

pytestmark = [pytest.mark.scenario, pytest.mark.api]

#: challenge сброса пароля на стенде — 64 hex-символа (как у регистрации)
RESET_CHALLENGE_ID_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
CODE_RE = re.compile(r"^\d{6}$")


def _skip_without_captcha_bypass() -> None:
    """И start сброса, и регистрация требует капчу: без токена сценария нет."""
    if not captcha_bypass_token():
        pytest.skip(f"{CAPTCHA_BYPASS_ENV} не задана: обойти Turnstile нечем")


def _http(api_client):
    """``MedusaStoreClient`` — обёртка; POST/GET с заголовками живут в ``.client``."""
    return api_client.client


def _new_email(run_tag: str) -> str:
    """Уникальный на прогон адрес: лимиты отправки привязаны к адресу (5/час)."""
    return f"qa-pwd-{run_tag}-{uuid.uuid4().hex[:8]}@example.com"


def _new_password() -> str:
    return f"Qa!{uuid.uuid4().hex}x"


def _register_customer(api_client, mailbox, email: str, password: str, steps) -> None:
    """Активирует покупателя по OTP-коду из письма — стартовая точка сценария.

    Профиль заполняется по контракту ``registrationProfile``; ``country_code``
    бэкенд сверяет строго со значением ``rs``.
    """
    http = _http(api_client)
    with steps("завести покупателя для сценария сброса", "start вернул 200"):
        response = http.post(
            "/store/registration/start", json=with_captcha_bypass({"email": email})
        )
        assert response.status_code == 200, (
            f"start регистрации вернул {response.status_code}: {http.json_of(response)!r}"
        )
        challenge_id = str(http.json_of(response).get("challenge_id") or "")
        assert challenge_id, "регистрация без challenge_id"

    with steps("прочитать OTP-код регистрации из письма", "код из 6 цифр"):
        code = mailbox.wait_for_code(email)
        assert CODE_RE.match(code), f"код {code!r} не состоит из шести цифр"

    with steps("активировать покупателя", "200 и verified = true"):
        response = http.post(
            "/store/registration/verify",
            json={
                "email": email,
                "challenge_id": challenge_id,
                "code": code,
                "password": password,
                "first_name": "QA",
                "last_name": "Reset",
                "phone": "+381 64 1234567",
                "postal_code": "11070",
                "city": "Beograd",
                "country_code": "rs",
                "terms_accepted": True,
            },
        )
        assert response.status_code == 200, (
            f"verify регистрации вернул {response.status_code}: {http.json_of(response)!r}"
        )


def _dispose_customer(admin_client, email: str) -> str:
    """Пытается удалить временного покупателя админом; "" если вышло.

    ``DELETE /admin/customers/{id}`` доступен не всякой учётке, поэтому уборка
    best-effort: провал только фиксируется, но не валит тест.
    """
    try:
        client = admin_client.client
        headers = {"Authorization": f"Bearer {admin_client.login()}"}
        listed = client.get(
            f"/admin/customers?q={email}", headers=headers, publishable_key=False
        )
        rows = (client.json_of(listed) or {}).get("customers") or []
        for row in rows:
            if str(row.get("email") or "").lower() == email.lower():
                response = client.delete(
                    f"/admin/customers/{row.get('id')}",
                    headers=headers,
                    publishable_key=False,
                )
                if response.status_code in (200, 204):
                    return ""
                return (
                    f"DELETE /admin/customers/{row.get('id')} вернул "
                    f"{response.status_code} — покупатель остался на стенде"
                )
        return f"покупатель {email} не найден в админ-списке"
    except Exception as exc:  # noqa: BLE001 - уборка не должна валить сценарий
        return f"уборка покупателя не выполнена: {exc}"


@allure.story("Сброс пароля по OTP-коду из письма")
@allure.severity(allure.severity_level.CRITICAL)
def test_password_reset_end_to_end(api_client, mailbox, admin_client, run_tag, steps):
    """OTP приходит, пароль меняется, повторный код мертв, вход новым работает."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    email = _new_email(run_tag)
    old_password = _new_password()
    new_password = _new_password()
    _register_customer(api_client, mailbox, email, old_password, steps)
    cleanup_note = ""

    try:
        with steps("запустить сброс пароля", "200, challenge_id из 64 hex-символов"):
            response = http.post(
                "/store/password-reset/start", json=with_captcha_bypass({"email": email})
            )
            assert response.status_code == 200, (
                f"reset start вернул {response.status_code}: {http.json_of(response)!r}"
            )
            started = http.json_of(response)
            challenge_id = str(started.get("challenge_id") or "")
            assert RESET_CHALLENGE_ID_RE.match(challenge_id), (
                f"challenge_id сброса не 64 hex: {challenge_id!r}"
            )
            assert started.get("expires_at"), f"в ответе start нет expires_at: {started!r}"

        with steps("прочитать OTP-код сброса из письма", "новое письмо, код из 6 цифр"):
            code = mailbox.wait_for_code(email)
            assert CODE_RE.match(code), f"код {code!r} не состоит из шести цифр"
            allure.attach(f"почта: {email}", "как искали код", allure.attachment_type.TEXT)

        with steps("сменить пароль на слишком короткий", "400 и код PASSWORD_INVALID"):
            response = http.post(
                "/store/password-reset/verify",
                json={"email": email, "challenge_id": challenge_id, "code": code, "password": "short"},
            )
            assert response.status_code == 400, (
                f"слабый пароль принят: {response.status_code} {http.json_of(response)!r}"
            )
            rejected = http.json_of(response)
            assert rejected.get("code") == "PASSWORD_INVALID", (
                f"код ошибки {rejected!r}, ожидался PASSWORD_INVALID"
            )

        with steps("сменить пароль корректно", "200 и success = true"):
            response = http.post(
                "/store/password-reset/verify",
                json={"email": email, "challenge_id": challenge_id, "code": code, "password": new_password},
            )
            assert response.status_code == 200, (
                f"reset verify вернул {response.status_code}: {http.json_of(response)!r}"
            )
            assert http.json_of(response).get("success") is True, "в ответе нет success = true"

        with steps("повторить verify тем же кодом", "400 и код CODE_USED"):
            response = http.post(
                "/store/password-reset/verify",
                json={"email": email, "challenge_id": challenge_id, "code": code, "password": _new_password()},
            )
            assert response.status_code == 400, (
                f"повторный verify вернулся {response.status_code}: {http.json_of(response)!r}"
            )
            rejected = http.json_of(response)
            assert rejected.get("code") == "CODE_USED", f"код ошибки {rejected!r}, ожидался CODE_USED"

        with steps("войти новым паролем", "emailpass отдаёт token"):
            response = http.post("/auth/customer/emailpass", json={"email": email, "password": new_password})
            assert response.status_code == 200, (
                f"вход новым паролем не прошёл: {response.status_code} {http.json_of(response)!r}"
            )
            logged_in = http.json_of(response)
            token = str(logged_in.get("token") or logged_in.get("access_token") or "")
            assert token, f"в ответе входа нет token: {sorted(logged_in)!r}"

        with steps("старый пароль больше не работает", "emailpass отвечает 401"):
            response = http.post("/auth/customer/emailpass", json={"email": email, "password": old_password})
            assert response.status_code == 401, (
                f"вход старым паролем вернулся {response.status_code} — пароль не заменился"
            )
    finally:
        cleanup_note = _dispose_customer(admin_client, email)
        if cleanup_note:
            allure.attach(cleanup_note, "уборка покупателя", allure.attachment_type.TEXT)


@allure.story("Сброс пароля по OTP-коду из письма")
def test_password_reset_start_requires_captcha(api_client, steps):
    """``start`` без капчи не создаёт challenge — обход обязателен на обеих ветках."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    with steps("start сброса без поля капчи", "400 и код CAPTCHA_REQUIRED"):
        response = http.post(
            "/store/password-reset/start", json={"email": f"qa-pwd-nope-{uuid.uuid4().hex[:8]}@example.com"}
        )
        assert response.status_code == 400, (
            f"start без капчи вернулся {response.status_code}: {http.json_of(response)!r}"
        )
        rejected = http.json_of(response)
        assert rejected.get("code") == "CAPTCHA_REQUIRED", (
            f"код ошибки {rejected!r}, ожидался CAPTCHA_REQUIRED"
        )


@allure.story("Сброс пароля по OTP-коду из письма")
def test_password_reset_unknown_email_sends_nothing(api_client, mailbox, run_tag, steps):
    """Несуществующая почта получает тот же 200, но письмо не уходит — антиперечисление."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    ghost = f"qa-pwd-ghost-{run_tag}-{uuid.uuid4().hex[:8]}@example.com"

    with steps("start сброса для несуществующей почты", "200 на том же контракте"):
        response = http.post(
            "/store/password-reset/start", json=with_captcha_bypass({"email": ghost})
        )
        assert response.status_code == 200, (
            f"start для ghost-адреса вернулся {response.status_code}: {http.json_of(response)!r}"
        )
        assert http.json_of(response).get("challenge_id"), "старт без challenge_id"

    with steps("убедиться, что письмо не отправлено", "в Mailpit для адреса ничего нет"):
        assert not mailbox.messages_for(ghost), (
            f"для несуществующей {ghost} нашлось письмо — старт выдаёт существование аккаунта"
        )
