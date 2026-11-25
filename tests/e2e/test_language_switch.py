"""E2E-16. Переключатель языка в шапке переводит витрину sr↔en: адрес, cookie store_locale и тексты."""

from __future__ import annotations

from urllib.parse import urlparse

import allure
import pytest

from qa.ui import (
    LANGUAGE_BUTTONS,
    LANGUAGE_SWITCHER,
    SHOP_HEADER,
    Storefront,
)

pytestmark = [pytest.mark.e2e]

# Имя cookie локали: qa/ui.py описывает его комментарием у LANGUAGE_SWITCHER, но константу
# не экспортирует, поэтому имя зафиксировано здесь (это не селектор).
_LOCALE_COOKIE = "store_locale"

# Пройденный путь на стенде: /rs/catalog -> /en/catalog (сегмент /rs из адреса en-локали
# выпадает). Комментарий в qa/ui.py обещает /en/rs/... — расхождение отправлено в отчёт.


def _buttons(page):
    """Кнопки локали ``LANGUAGE_BUTTONS``: на странице их две пары (шапка и подвал)."""
    return page.locator(LANGUAGE_BUTTONS)


def _pressed_map(page) -> dict[str, list[str]]:
    """«текст кнопки -> список aria-pressed» по всем переключателям на странице."""
    buttons = _buttons(page)
    found: dict[str, list[str]] = {}
    for index in range(buttons.count()):
        code = (buttons.nth(index).text_content() or "").strip().lower()
        found.setdefault(code, []).append(buttons.nth(index).get_attribute("aria-pressed") or "")
    return found


def _header_text(shop: Storefront) -> str:
    """Одна строка текста шапки ``SHOP_HEADER`` — по ней и сверяем локализацию."""
    return " ".join(shop.page.locator(SHOP_HEADER).first.inner_text().split())


def _locale_from_url(page) -> str:
    """Первый сегмент пути: ``rs`` или ``en`` (пустая строка на главной без префикса)."""
    parts = [segment for segment in urlparse(page.url).path.split("/") if segment]
    return parts[0] if parts else ""


def _cookie_value(page, name: str) -> str | None:
    for cookie in page.context.cookies():
        if cookie["name"] == name:
            return str(cookie["value"])
    return None


def _click_language(shop: Storefront, code: str) -> None:
    """Клик по кнопке локали с нужным текстом (первая найденная — из шапки).

    Next.js уводит на другой путь мягкой клиентской навигацией, поэтому ждём смены
    префикса адреса через ``Storefront.wait_until``, а не только load-события.
    """
    page = shop.page
    buttons = _buttons(page)
    for index in range(buttons.count()):
        if (buttons.nth(index).text_content() or "").strip().lower() == code:
            allure.attach(
                f"кнопка #{index} из {buttons.count()} ({LANGUAGE_BUTTONS})",
                f"клик «{code}»",
                allure.attachment_type.TEXT,
            )
            buttons.nth(index).click()
            page.wait_for_load_state("domcontentloaded")
            shop.wait_until(lambda: _locale_from_url(page) == code, 10.0)
            return
    texts = [(buttons.nth(i).text_content() or "").strip() for i in range(buttons.count())]
    pytest.fail(f"среди {LANGUAGE_BUTTONS} (всего {buttons.count()}) нет кнопки {code!r}: {texts}")


@allure.story("Переключатель языка sr/en")
@allure.severity(allure.severity_level.NORMAL)
def test_language_switch_round_trip_sr_en_sr(page, steps):
    """Гость меняет sr -> en -> sr: адрес, cookie и перевод шапки идут за выбором."""
    shop = Storefront(page)

    with steps("открыть каталог и найти переключатель языка",
               f"{LANGUAGE_SWITCHER} с role=group, текущий язык — sr"):
        shop.goto_catalog()
        switchers = page.locator(LANGUAGE_SWITCHER)
        assert switchers.count() >= 1, f"на витрине нет ни одного {LANGUAGE_SWITCHER}"
        assert switchers.first.get_attribute("role") == "group", (
            f"у {LANGUAGE_SWITCHER} role={switchers.first.get_attribute('role')!r}, ожидался group"
        )
        pressed = _pressed_map(page)
        assert set(pressed.get("sr", [])) == {"true"}, (
            f"в sr-локали кнопка «sr» не помечена aria-pressed=true: {pressed}"
        )
        assert set(pressed.get("en", [])) == {"false"}, (
            f"в sr-локали кнопка «en» помечена как текущая: {pressed}"
        )

    with steps("запомнить sr-тексты шапки", f"{SHOP_HEADER} читается по-сербски («Korpa», «Prijavi se»)"):
        sr_header = _header_text(shop)
        assert "Korpa" in sr_header, f"в sr-шапке нет слова «Korpa»: {sr_header!r}"
        assert _locale_from_url(page) == "rs", f"адрес не sr-локали: {page.url}"

    with steps("переключить язык на en кнопкой LANGUAGE_BUTTONS",
               f"путь стал en, cookie {_LOCALE_COOKIE}=en, «en» теперь aria-pressed=true"):
        _click_language(shop, "en")
        assert _locale_from_url(page) == "en", f"после «en» адрес не en-локали: {page.url}"
        assert _cookie_value(page, _LOCALE_COOKIE) == "en", (
            f"cookie {_LOCALE_COOKIE}={_cookie_value(page, _LOCALE_COOKIE)!r}, ожидался en"
        )
        pressed = _pressed_map(page)
        assert set(pressed.get("en", [])) == {"true"}, (
            f"после переключения «en» не помечена aria-pressed=true: {pressed}"
        )
        assert set(pressed.get("sr", [])) == {"false"}, (
            f"после переключения «sr» всё ещё помечена текущей: {pressed}"
        )
        shop.first_product_card().wait_for(state="visible", timeout=30_000)

    with steps("сверить перевод шапки в en-локали",
               f"{SHOP_HEADER} показывает «Cart»/«Sign in» и не показывает «Korpa»"):
        en_header = _header_text(shop)
        assert "Cart" in en_header, f"в en-шапке нет слова «Cart»: {en_header!r}"
        assert "Korpa" not in en_header, f"en-шапка осталась по-сербски: {en_header!r}"
        allure.attach(
            f"sr: {sr_header}\nen: {en_header}",
            "перевод шапки sr -> en",
            allure.attachment_type.TEXT,
        )

    with steps("вернуть sr кнопкой LANGUAGE_BUTTONS",
               f"путь снова rs, cookie {_LOCALE_COOKIE}=sr, «Korpa» вернулась"):
        _click_language(shop, "sr")
        assert _locale_from_url(page) == "rs", f"после «sr» адрес не вернулся в rs: {page.url}"
        assert _cookie_value(page, _LOCALE_COOKIE) == "sr", (
            f"cookie {_LOCALE_COOKIE}={_cookie_value(page, _LOCALE_COOKIE)!r}, ожидался sr"
        )
        back = _header_text(shop)
        assert "Korpa" in back, f"после возврата в sr шапка не переведена: {back!r}"
