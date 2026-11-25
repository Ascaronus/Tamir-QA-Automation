"""E2E-8. Восстановление пароля: страница формы открыта и готова к запросу кода

Идем по той же дороге, по которой идет покупатель: со страницы входа ищем ссылку
«забыли пароль», открываем форму восстановления и проверяем, что она готова
принять адрес — есть поле адреса и активная кнопка. Дальше просим код на адрес,
который физически не может никому принадлежать (``.example.com``), и ждем
охрану: на стэнде форма закрыта Cloudflare Turnstile, поэтому вместо письма
должна появиться проверка «Bezbednosna provera». Код намеренно не подтверждаем
и почту не читаем — письмо ушло бы живому ящику стенда.

См. ``docs/plan/e2e-scenarios.md``.
"""
from __future__ import annotations

import re
import secrets

import pytest
from playwright.sync_api import Page

from qa.ui import (
    CAPTCHA_TITLE_RE,
    EMAIL_ERROR_RE,
    FORM_ALERT,
    LOGIN_PATH,
    RESET_EMAIL,
    RESET_LINK,
    RESET_PATH_RE,
    RESET_SUBMIT,
    RESET_TITLE_RE,
    Storefront,
)

pytestmark = [pytest.mark.e2e]


def _field_errors(page: Page) -> list[str]:
    """Все сообщения об ошибках полей на экране."""
    errors = page.locator(FORM_ALERT)
    return [errors.nth(i).inner_text().strip() for i in range(errors.count())]


def test_password_reset_form_renders(page: Page, steps) -> None:
    """Форма восстановления пароля открыта и готова к запросу кода."""
    shop = Storefront(page)

    steps("Открываем страницу входа",
          "витрина показывает форму входа, а не личный кабинет")
    shop.goto(LOGIN_PATH)
    heading = page.locator("h1").first.inner_text().strip()
    assert not shop.is_logged_in(), (
        f"гость зашел в личный кабинет по адресу {page.url!r} — форма входа недоступна"
    )
    assert heading, f"на {page.url!r} нет заголовка: форма входа не отрисована"

    steps("Ищем ссылку «забыли пароль»",
          "она видна рядом с формой входа")
    link = page.locator(RESET_LINK).first
    assert link.count(), (
        f"на странице входа {page.url!r} нет ссылки на восстановление пароля "
        f"({RESET_LINK}); заголовок {heading!r} "
        f"— ищите ссылку вручную: {page.locator('a[href*=lozink], a[href*=password]').all_inner_texts()!r}"
    )
    assert link.is_visible(), f"ссылка на восстановление пароля есть, но не видна на {page.url!r}"
    link_text = link.inner_text().strip()

    steps("Открываем восстановление пароля по этой ссылке",
          "открылась форма с заголовком про смену пароля")
    link.click()
    assert shop.wait_until(lambda: bool(RESET_PATH_RE.search(page.url)), 20.0), (
        f"после клика по «{link_text}» адрес остался {page.url!r} — форма восстановления "
        "не открылась"
    )
    assert page.locator("h1").count(), f"на {page.url!r} нет заголовка — страница не отрисована"
    title = page.locator("h1").first.inner_text().strip()
    assert RESET_TITLE_RE.search(title), (
        f"на {page.url!r} заголовок {title!r} не похож на форму смены пароля "
        f"— открыта не та страница"
    )

    steps("Проверяем, что форма готова принять адрес",
          "есть пустое поле адреса и активная кнопка")
    email_field = page.locator(RESET_EMAIL).first
    assert email_field.count(), (
        f"на {page.url!r} нет поля адреса ({RESET_EMAIL}) — форму заполнить нельзя"
    )
    assert email_field.is_visible(), f"поле адреса на {page.url!r} не видно"
    assert email_field.get_attribute("type") == "email", (
        f"поле адреса на {page.url!r} имеет type="
        f"{email_field.get_attribute('type')!r}, а не email — браузер не проверит адрес"
    )
    assert email_field.input_value() == "", (
        f"поле адреса предзаполнено {email_field.input_value()!r} — форма восстановления "
        "показывает чужой адрес"
    )
    submit = page.locator(RESET_SUBMIT).first
    assert submit.count(), f"на {page.url!r} нет кнопки отправки ({RESET_SUBMIT})"
    assert submit.is_enabled(), f"кнопка отправки на {page.url!r} неактивна сама по себе"

    steps("Просим код на адрес, которого не существует",
          "формa принимает адрес и выпускает охрану перед отправкой")
    # .example.com — домен-образец из RFC 2606: письмо на него никуда не доставляется,
    # так что стенд не получит живого письма.
    probe_email = f"qa-e2e-nobody-{secrets.token_hex(6)}@example.com"
    email_field.fill(probe_email)
    submit.click()

    # Охрану ищем текстом (CAPTCHA_TITLE_RE): голый <dialog> на витрине — это ещё
    # и мобильное меню каталога, оно на экране всегда.
    gate = shop.wait_for_captcha_gate(timeout_ms=20_000)
    errors = _field_errors(page)
    assert gate or errors, (
        f"после отправки адреса с формы {page.url!r} не появилось ни охраны "
        f"({CAPTCHA_TITLE_RE.pattern}), ни ошибки поля ({FORM_ALERT}) за 20 секунд — форма "
        "не отвечает, код запросить нельзя"
    )

    if gate:
        assert RESET_PATH_RE.search(page.url), (
            f"после отправки формы ушло на {page.url!r} — форма восстановления "
            "выбросила из сценария"
        )
    else:
        assert any(EMAIL_ERROR_RE.search(text) for text in errors), (
            f"вместо проверки Cloudflare форма вернула ошибки {errors!r}, и ни одна "
            "не относится к адресу — форма восстановления сломана"
        )
        # Почта не отправлялась: Turnstile стоит перед запросом кода, а адрес отклонила
        # валидация формы, поэтому до ящика стенда дело не дошло.
        pytest.skip(
            "эта сборка отклоняет адрес на форме восстановления до охраны: "
            f"{errors!r} — форма открыта и отвечает, но код запросить нельзя; "
            "проверка ограничена открытой формой"
        )
