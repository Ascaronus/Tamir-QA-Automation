"""S3-1. Регистрация покупателя: код на почту, активация, вход и кулдаун повторной отправки.

Живой сценарий через API витрины: ``POST /store/registration/start`` присылает на
адрес шестизначный код, ``POST /store/registration/verify`` активирует покупателя,
``POST /auth/customer/emailpass`` входит активированным аккаунтом. Повторный
``start`` для того же адреса обязан упереться в кулдаун ``429`` с кодом
``CODE_COOLDOWN``, а ``verify`` с неверным кодом — в ``CODE_INVALID``. Капчу
Turnstile сценарий обходит полем ``_qa_captcha_bypass`` (токен берётся из
``QA_CAPTCHA_BYPASS_TOKEN``); без токена тесты пропускаются, а не падают.
Письма читаются из Mailpit (``MAILPIT_URL``). Адрес уникален на прогон, поэтому
покупатель может остаться на стенде: повторных регистраций того же адреса нет.
"""

from __future__ import annotations

import re
import time
import uuid

import allure
import pytest

from qa.http import CAPTCHA_BYPASS_ENV, captcha_bypass_token, with_captcha_bypass

pytestmark = [pytest.mark.scenario, pytest.mark.api]

#: challenge регистрации — 64 hex-символа (сброс пароля сейчас тоже в таком формате)
CHALLENGE_ID_RE = re.compile(r"^[a-f0-9]{64}$")
CODE_RE = re.compile(r"^\d{6}$")
WELCOME_SUBJECT_RE = re.compile(r"dobro|welcome", re.IGNORECASE)


def _skip_without_captcha_bypass() -> None:
    """Стенд включает Turnstile на start; без токена обхода сценарий не собрать."""
    if not captcha_bypass_token():
        pytest.skip(f"{CAPTCHA_BYPASS_ENV} не задана: обойти Turnstile нечем")


def _http(api_client):
    """``MedusaStoreClient`` — обёртка; POST/GET с заголовками живут в ``.client``."""
    return api_client.client


def _new_email(run_tag: str) -> str:
    """Уникальный на прогон адрес: лимиты отправки привязаны к адресу (5/час)."""
    return f"qa-reg-{run_tag}-{uuid.uuid4().hex[:8]}@example.com"


def _registration_payload(email: str, challenge_id: str, code: str) -> dict:
    """Тело ``verify`` из контракта ``registrationProfile``: профиль + согласие.

    ``country_code`` бэкенд сверяет строго со значением ``rs`` — другая страна
    означает ``REGISTRATION_INVALID``, поэтому поле зафиксировано здесь.
    """
    return {
        "email": email,
        "challenge_id": challenge_id,
        "code": code,
        "password": f"Qa!{uuid.uuid4().hex}x",
        "first_name": "QA",
        "last_name": "Registration",
        "phone": "+381 64 1234567",
        "postal_code": "11070",
        "city": "Beograd",
        "country_code": "rs",
        "terms_accepted": True,
    }


@allure.story("Регистрация по коду из письма")
@allure.severity(allure.severity_level.CRITICAL)
def test_registration_end_to_end(api_client, mailbox, run_tag, steps):
    """Код приходит на почту, активация проходит, покупатель входит и получает приветствие."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    email = _new_email(run_tag)
    password = f"Qa!{uuid.uuid4().hex}x"

    with steps("запросить код регистрации", "200, challenge_id из 64 hex-символов"):
        response = http.post(
            "/store/registration/start", json=with_captcha_bypass({"email": email})
        )
        assert response.status_code == 200, (
            f"start вернул {response.status_code}: {http.json_of(response)!r}"
        )
        started = http.json_of(response)
        challenge_id = str(started.get("challenge_id") or "")
        assert CHALLENGE_ID_RE.match(challenge_id), f"challenge_id не формата регистрации: {challenge_id!r}"
        assert started.get("expires_at"), f"в ответе нет expires_at: {started!r}"

    with steps("повторный start для того же адреса", "429 и код CODE_COOLDOWN"):
        response = http.post(
            "/store/registration/start", json=with_captcha_bypass({"email": email})
        )
        assert response.status_code == 429, (
            f"повторный start вернулся {response.status_code}: {http.json_of(response)!r}"
        )
        cooldown = http.json_of(response)
        assert cooldown.get("code") == "CODE_COOLDOWN", f"код ошибки {cooldown!r}, ожидался CODE_COOLDOWN"

    with steps("прочитать шестизначный код из письма", "в письме на адрес есть код из 6 цифр"):
        code = mailbox.wait_for_code(email)
        assert CODE_RE.match(code), f"код {code!r} не состоит из шести цифр"
        allure.attach(f"почта: {email}", "как искали код", allure.attachment_type.TEXT)

    with steps("активировать регистрацию", "200 и verified = true"):
        response = http.post(
            "/store/registration/verify",
            json=_registration_payload(email, challenge_id, code) | {"password": password},
        )
        assert response.status_code == 200, (
            f"verify вернул {response.status_code}: {http.json_of(response)!r}"
        )
        assert http.json_of(response).get("verified") is True, "в ответе verify нет verified = true"

    with steps("войти активированным покупателем", "emailpass отдаёт token"):
        response = http.post("/auth/customer/emailpass", json={"email": email, "password": password})
        assert response.status_code == 200, (
            f"вход свежего покупателя не прошёл: {response.status_code} {http.json_of(response)!r}"
        )
        logged_in = http.json_of(response)
        token = str(logged_in.get("token") or logged_in.get("access_token") or "")
        assert token, f"в ответе входа нет token: {sorted(logged_in)!r}"

    with steps("после активации приходит приветственное письмо", "письмо с темой Dobro došli/Welcome"):
        deadline = time.monotonic() + 60
        subjects: list[str] = []
        while time.monotonic() < deadline:
            subjects = [str(m.get("Subject") or "") for m in mailbox.messages_for(email)]
            if any(WELCOME_SUBJECT_RE.search(subject) for subject in subjects):
                break
            time.sleep(3.0)
        assert any(WELCOME_SUBJECT_RE.search(subject) for subject in subjects), (
            f"за 60с нет приветственного письма на {email}; темы: {subjects!r}"
        )


@allure.story("Регистрация по коду из письма")
def test_registration_verify_rejects_wrong_code(api_client, run_tag, steps):
    """Неверный код подтверждения не активирует покупателя."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    email = _new_email(run_tag)

    with steps("запросить код регистрации", "200, challenge_id получен"):
        response = http.post(
            "/store/registration/start", json=with_captcha_bypass({"email": email})
        )
        assert response.status_code == 200, (
            f"start вернул {response.status_code}: {http.json_of(response)!r}"
        )
        challenge_id = str(http.json_of(response).get("challenge_id") or "")
        assert challenge_id, "старт без challenge_id"

    with steps("активировать с заведомо неверным кодом", "400 и код CODE_INVALID"):
        wrong = "000000"
        response = http.post(
            "/store/registration/verify", json=_registration_payload(email, challenge_id, wrong)
        )
        assert response.status_code == 400, (
            f"неверный код принят: {response.status_code} {http.json_of(response)!r}"
        )
        rejected = http.json_of(response)
        assert rejected.get("code") == "CODE_INVALID", f"код ошибки {rejected!r}, ожидался CODE_INVALID"


@allure.story("Регистрация по коду из письма")
def test_registration_start_requires_email(api_client, steps):
    """Пустой старт регистрации отбивается до создания challenge."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    with steps("start без e-mail", "400 и код REGISTRATION_INVALID"):
        response = http.post("/store/registration/start", json=with_captcha_bypass({}))
        assert response.status_code == 400, (
            f"пустой start вернулся {response.status_code}: {http.json_of(response)!r}"
        )
        rejected = http.json_of(response)
        assert rejected.get("code") == "REGISTRATION_INVALID", (
            f"код ошибки {rejected!r}, ожидался REGISTRATION_INVALID"
        )
