"""E2E-17. Cookie-баннер нового посетителя: без согласия он виден, а кнопки ставят tamir_consent_v1."""

from __future__ import annotations

import allure
import pytest

from qa.ui import (
    CONSENT_COOKIE_NAME,
    COOKIE_ACCEPT_BUTTON,
    COOKIE_BANNER,
    COOKIE_BANNER_LINKS,
    COOKIE_REJECT_BUTTON,
    Storefront,
)

pytestmark = [pytest.mark.e2e]

# ЗАМЕТКА ПО КОНТРАКТУ (в тест не входит, отправлено в отчёт): настройка cookie из подвала
# недоступна тесту — COOKIE_SETTINGS_TRIGGER = 'button[aria-controls="cookie-banner"]' на
# живом стенде даёт count=0, хотя кнопка «Podešavanja kolačića» в nav.footer-bottom есть:
#     <button type="button" class="underline underline-offset-4">Podešavanja kolačića</button>
# то есть атрибута aria-controls у неё нет.


def _fresh_visitor(browser, base_url: str):
    """Контекст нового посетителя — без согласия на аналитику.

    ``browser_context_args`` из ``tests/conftest.py`` заранее ставит
    ``tamir_consent_v1=accepted``, поэтому контекст берём прямо из ``browser`` и только
    свои параметры задаём.
    """
    return browser.new_context(
        locale="sr-RS",
        viewport={"width": 1440, "height": 900},
        base_url=base_url,
    )


def _consent_value(context) -> str | None:
    """Значение cookie согласия (``None`` — посетитель ещё не выбирал)."""
    for cookie in context.cookies():
        if cookie["name"] == CONSENT_COOKIE_NAME:
            return str(cookie["value"])
    return None


@allure.story("Согласие на аналитические cookie")
@allure.severity(allure.severity_level.NORMAL)
def test_new_visitor_rejects_analytics_cookie(browser, base_url, steps):
    """Первый визит: баннер виден, «Odbij analitiku» ставит rejected и закрывает баннер."""
    context = _fresh_visitor(browser, base_url)
    page = context.new_page()
    shop = Storefront(page)
    try:
        with steps("открыть каталог новым посетителем",
                   f"{COOKIE_BANNER} виден, cookie {CONSENT_COOKIE_NAME} ещё не выставлен"):
            shop.goto("/rs/catalog")
            banner = page.locator(COOKIE_BANNER)
            banner.first.wait_for(state="visible", timeout=15_000)
            assert banner.count() == 1, (
                f"баннеров cookie {banner.count()!r}, ожидался один ({COOKIE_BANNER})"
            )
            assert _consent_value(context) is None, (
                f"новый посетитель пришёл уже с согласием: {CONSENT_COOKIE_NAME}="
                f"{_consent_value(context)!r}"
            )

        with steps("сверить текст и ссылки баннера",
                   "текст про kolačiće непустой, есть ссылка на Politika kolačića"):
            text = " ".join(banner.first.inner_text().split())
            assert "kola" in text.lower(), f"в тексте баннера нет слова про cookie: {text[:200]!r}"
            links = page.locator(COOKIE_BANNER_LINKS)
            assert links.count() > 0, f"в баннере нет ни одной ссылки ({COOKIE_BANNER_LINKS})"
            allure.attach(text, "текст cookie-баннера", allure.attachment_type.TEXT)

        with steps("отказаться от аналитики кнопкой COOKIE_REJECT_BUTTON",
                   f"cookie {CONSENT_COOKIE_NAME}=rejected, баннер убран из DOM"):
            page.locator(COOKIE_REJECT_BUTTON).first.click()
            assert shop.wait_until(lambda: page.locator(COOKIE_BANNER).count() == 0, 10.0), (
                f"после «Odbij analitiku» баннер {COOKIE_BANNER} остался в DOM "
                f"(count={page.locator(COOKIE_BANNER).count()})"
            )
            assert _consent_value(context) == "rejected", (
                f"после отказа {CONSENT_COOKIE_NAME}={_consent_value(context)!r}, "
                "ожидался rejected"
            )

        with steps("перезагрузить страницу", "выбор сохранён: баннер не появляется заново"):
            page.reload(wait_until="domcontentloaded")
            assert page.locator(COOKIE_BANNER).count() == 0, (
                f"после перезагрузки баннер {COOKIE_BANNER} появился снова "
                f"(count={page.locator(COOKIE_BANNER).count()})"
            )
            assert _consent_value(context) == "rejected", (
                f"после перезагрузки {CONSENT_COOKIE_NAME}={_consent_value(context)!r}, "
                "ожидался rejected"
            )
    finally:
        context.close()


@allure.story("Согласие на аналитические cookie")
@allure.severity(allure.severity_level.NORMAL)
def test_new_visitor_accepts_analytics_cookie(browser, base_url, steps):
    """Первый визит: «Prihvati analitiku» ставит accepted и тоже снимает баннер."""
    context = _fresh_visitor(browser, base_url)
    page = context.new_page()
    shop = Storefront(page)
    try:
        with steps("открыть каталог новым посетителем",
                   f"{COOKIE_BANNER} виден, cookie {CONSENT_COOKIE_NAME} ещё не выставлен"):
            shop.goto("/rs/catalog")
            banner = page.locator(COOKIE_BANNER)
            banner.first.wait_for(state="visible", timeout=15_000)
            assert _consent_value(context) is None, (
                f"новый посетитель пришёл уже с согласием: {CONSENT_COOKIE_NAME}="
                f"{_consent_value(context)!r}"
            )

        with steps("принять аналитику кнопкой COOKIE_ACCEPT_BUTTON",
                   f"cookie {CONSENT_COOKIE_NAME}=accepted, баннер убран из DOM"):
            page.locator(COOKIE_ACCEPT_BUTTON).first.click()
            assert shop.wait_until(lambda: page.locator(COOKIE_BANNER).count() == 0, 10.0), (
                f"после «Prihvati analitiku» баннер {COOKIE_BANNER} остался в DOM "
                f"(count={page.locator(COOKIE_BANNER).count()})"
            )
            assert _consent_value(context) == "accepted", (
                f"после принятия {CONSENT_COOKIE_NAME}={_consent_value(context)!r}, "
                "ожидался accepted"
            )

        with steps("перезагрузить страницу", "согласие сохранено: баннер больше не показывается"):
            page.reload(wait_until="domcontentloaded")
            assert page.locator(COOKIE_BANNER).count() == 0, (
                f"после перезагрузки баннер {COOKIE_BANNER} появился снова "
                f"(count={page.locator(COOKIE_BANNER).count()})"
            )
            assert _consent_value(context) == "accepted", (
                f"после перезагрузки {CONSENT_COOKIE_NAME}={_consent_value(context)!r}, "
                "ожидался accepted"
            )
    finally:
        context.close()
