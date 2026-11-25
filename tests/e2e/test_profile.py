"""E2E-16. Профиль кабинета: телефон и город сохраняются, перечитываются после перезагрузки
и дают flash «Podaci su sačuvani»; заведомо неверный телефон даёт ошибку у поля и не
сохраняется.
"""

from __future__ import annotations

import re

import pytest

from qa.ui import (
    PROFILE_ADDRESS_FIELDS,
    PROFILE_ADDRESS_PLACEHOLDER,
    PROFILE_FIELDS,
    PROFILE_FORM,
    Storefront,
    profile_field_error,
)

pytestmark = [pytest.mark.e2e]

#: Оригиналы полей прогона: их возвращает фикстура уборки.
_PROFILE_ORIGINALS: dict[str, str] = {}

PHONE_FIELD = PROFILE_FIELDS["phone"]


def _digits(value: str) -> str:
    """Телефон без пробелов: ``formatPhone`` рисует ``+381 64 000 0123``."""
    return re.sub(r"\s+", "", value or "")


@pytest.fixture(autouse=True)
def _restore_profile(logged_in_page):
    """Возвращает профиль к значениям прогона и проверяет, что уборка сохранена."""
    yield
    if not _PROFILE_ORIGINALS:
        return
    shop = Storefront(logged_in_page)
    page = logged_in_page
    try:
        shop.open_profile_panel()
        page.wait_for_selector(PROFILE_FORM, timeout=20_000)
        for name in ("phone", *PROFILE_ADDRESS_FIELDS):
            original = _PROFILE_ORIGINALS.get(name)
            if original is None:
                continue
            if name == "city":
                shop.set_profile_city(original)
            elif name == "address1" and not original:
                shop.set_profile_value(name, PROFILE_ADDRESS_PLACEHOLDER)
            else:
                shop.set_profile_value(name, original)
        shop.save_profile()
        assert shop.wait_until(shop.profile_saved_flash, timeout_s=20.0), (
            f"уборка профиля не сохранилась: {shop.profile_error_text() or 'без текста ошибки'}"
        )
        # контроль уборки: значения правда вернулись на сервер, а не только в форму
        page.reload(wait_until="domcontentloaded")
        shop.open_profile_panel()
        page.wait_for_selector(PROFILE_FORM, timeout=20_000)
        assert _digits(shop.profile_value("phone")) == _digits(_PROFILE_ORIGINALS["phone"])
        assert shop.profile_value("city") == _PROFILE_ORIGINALS["city"]
    finally:
        _PROFILE_ORIGINALS.clear()


def _qa_phone(run_tag: str) -> str:
    """Корректный E.164 телефон, уникальный для прогона."""
    digits = "".join(ch for ch in run_tag if ch.isdigit())
    return f"+38164{(digits + '00000000')[:8]}"


def _save_expect_flash(shop: Storefront) -> bool:
    """Клик «Sačuvaj izmene» и ожидание сообщения о сохранении."""
    shop.save_profile()
    return shop.wait_until(shop.profile_saved_flash, timeout_s=20.0)


def test_profile_save_and_validation(logged_in_page, run_tag, steps):
    """Сохранение профиля подтверждается flash и остаётся в силе, неверный телефон — ошибка поля."""
    page = logged_in_page
    shop = Storefront(page)

    with steps("открыть профиль кабинета", "форма `form.profile-form` заполнена"):
        shop.open_profile_panel()
        page.wait_for_selector(PROFILE_FORM, timeout=20_000)
        for name in PROFILE_FIELDS:
            _PROFILE_ORIGINALS[name] = shop.profile_value(name) or ""

    new_phone = _qa_phone(run_tag)
    new_city = f"Grad-{run_tag}"[:40]

    with steps("позитив: новый телефон и город сохранены",
               "flash «Podaci su sačuvani» появляется"):
        shop.set_profile_value("phone", new_phone)
        shop.set_profile_city(new_city)
        # город + индекс без улицы форма считает неполным адресом, поэтому пишем и улицу
        shop.set_profile_value("address1", "QA test address 1")
        assert _save_expect_flash(shop), (
            f"сохранение не подтверждено flash: {shop.profile_error_text() or 'без текста ошибки'}"
        )

    with steps("значения перечитываются после перезагрузки страницы",
               "phone и city = новые, форма снова доступна"):
        page.reload(wait_until="domcontentloaded")
        shop.open_profile_panel()
        page.wait_for_selector(PROFILE_FORM, timeout=20_000)
        assert _digits(shop.profile_value("phone")) == new_phone, (
            f"телефон не сохранился: {shop.profile_value('phone')!r} вместо {new_phone!r}"
        )
        assert shop.profile_value("city") == new_city, (
            f"город не сохранился: {shop.profile_value('city')!r} вместо {new_city!r}"
        )

    with steps("негатив: неверный телефон (код страны без номера)",
               "у поля телефона есть `span.field-error`, сохранения нет"):
        shop.set_profile_value("phone", "+381")
        shop.save_profile()
        field_error = page.locator(profile_field_error(PHONE_FIELD))
        page.wait_for_selector(profile_field_error(PHONE_FIELD), timeout=10_000)
        assert field_error.first.inner_text().strip(), "подпись ошибки телефона пустая"

    with steps("неверная правка не сохранилась",
               "после перезагрузки формы телефон остался прежним"):
        page.reload(wait_until="domcontentloaded")
        shop.open_profile_panel()
        page.wait_for_selector(PROFILE_FORM, timeout=20_000)
        assert _digits(shop.profile_value("phone")) == new_phone, (
            f"неверный телефон ушёл в профиль: {shop.profile_value('phone')!r}"
        )
