"""E2E-2. Саморегистрация витрины останавливается на проверке «вы не бот»."""

from __future__ import annotations

import uuid

import pytest

from qa.ui import (
    CAPTCHA_TITLE_RE,
    CODE_SENT_RE,
    REGISTER_EMAIL,
    REGISTER_TITLE_RE,
    Storefront,
)

pytestmark = [pytest.mark.e2e]

# Сценарий доходит только до проверки: код никуда не уходит, ящика не существует,
# поэтому на стенде не остаётся ни аккаунта, ни писем.
REGISTRANT = {
    "firstName": "QA",
    "lastName": "Gate",
    "postalCode": "11000",
    "city": "Beograd",
    "phone": "+381641234567",
    "password": "Qa!Gate2026",
}

START_PATH = "registration/start"


def _registrant_email() -> str:
    """Почта в формате уборки ``scripts/qa_cleanup.py``: даже если код уйдёт, ящика нет."""
    return f"qa-e2e-reg-{uuid.uuid4().hex[:10]}@example.com"


def test_registration_stops_at_captcha_gate(page, mailbox, steps):
    """E2E-2. Саморегистрация упирается в проверку Turnstile и не создаёт аккаунт.

    Так зафиксировано ограничение витрины (см. ``docs/FINDINGS-frontend-backend.md``):
    ``POST /store/registration/start`` отправляется только с токеном Turnstile, а
    headless-браузер его не получает. Путь «клиент с паролем» покрыт сценарием
    ``test_registered_customer_journey.py`` на заведённом аккаунте.
    """
    email = _registrant_email()
    shop = Storefront(page)

    # ``POST .../store/registration/start`` витрина шлёт только получив токен Turnstile,
    # поэтому слушатели вешаются до отправки: любой запрос останется в поле зрения.
    posts: list[str] = []
    posts_ok: dict[str, int] = {}

    def _on_request(request) -> None:
        if START_PATH in request.url and request.method == "POST":
            posts.append(request.url)

    def _on_response(response) -> None:
        if START_PATH in response.url and response.request.method == "POST":
            posts_ok[response.url] = response.status

    page.on("request", _on_request)
    page.on("response", _on_response)

    with steps("Открыть страницу регистрации", "открывается форма «Napravi nalog»"):
        # ``/rs/account/register`` — rewrite на ``/rs/nalog/registracija``, поэтому проверяем
        # содержимое страницы, а не адрес.
        shop.goto_register()
        title = shop.registration_title()
        assert REGISTER_TITLE_RE.search(title), f"форма регистрации не открылась: {title!r}"

    with steps("Заполнить обязательные поля", "значения приняты, город выбран"):
        shop.set_registration_details({**REGISTRANT, "email": email})
        assert page.input_value(REGISTER_EMAIL), "email не лёг в поле формы"
        chosen = shop.selected_city()
        assert chosen == "Beograd", f"в <select> города {chosen!r}, ожидался «Beograd»"

    with steps("Отправить форму", "появляется проверка безопасности, код не уходит"):
        shop.submit_registration()
        gate = shop.wait_for_captcha_gate(30_000)
        assert gate, (
            "проверка «вы не бот» не появилась — витрина либо уходит на регистрацию без "
            f"токена, либо не может открыть капчу; ошибки формы: {shop.registration_errors()!r}"
        )
        assert CAPTCHA_TITLE_RE.search(gate), f"открылось что-то не про безопасность: {gate[:200]!r}"

    with steps("Дать проверке доиграть", "успешных POST registration/start нет, аккаунт не создан"):
        # Turnstile может «доигрывать» медленно: если токен всё же выпустят, запрос появится следом
        page.wait_for_timeout(8_000)
        assert not posts_ok, (
            f"Turnstile выпустил токен и регистрация ушла на стенд: {posts_ok} — "
            "проверка обошлась и аккаунт мог быть создан"
        )
        assert not posts, (
            f"форма всё равно ушла на {START_PATH} без успешного токена: {posts} — "
            "проверка не удержала отправку"
        )
        assert not shop.registration_otp_requested(), (
            "появилось поле кода из письма — витрина ушла дальше проверки"
        )
        body = page.locator("main").inner_text()
        assert not CODE_SENT_RE.search(body), (
            f"открылся экран «Potvrdite email adresu» — код ушёл на стенд: {body[:200]!r}"
        )

    with steps("Убедиться, что аккаунт не создан", "письмо на этот адрес не приходило"):
        if mailbox.configured:
            letters = mailbox.messages_for(email)
            assert not letters, (
                f"на {email} пришло письмо о регистрации: "
                f"{[m.get('Subject') for m in letters]} — значит аккаунт всё-таки создан"
            )
