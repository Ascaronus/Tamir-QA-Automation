"""E2E-18. Виджет чата TAMIR: лаунчер открывает панель podrška, кнопка закрытия её снимает."""

from __future__ import annotations

import allure
import pytest

from qa.ui import (
    CHAT_CLOSE_BUTTONS,
    CHAT_LAUNCHER,
    CHAT_LOG,
    CHAT_PANEL,
    Storefront,
)

pytestmark = [pytest.mark.e2e]

# ЗАМЕТКИ ПО КОНТРАКТУ (отправлены в отчёт, тест их не проверяет):
# 1) CHAT_CLOSE_BUTTONS = CHAT_PANEL + " button:not([name=chat-message]):not([type=submit])"
#    сверхмеряет: в DOM панели под ним и настоящий «×»/«Zatvori», и disabled-сабмит
#    «Započni razgovor» (он вне формы с name=chat-message и без type=submit). Реальная
#    кнопка закрытия — `<button type="button" class="icon-button" aria-label="Zatvori">`
#    стоит первой, поэтому везде берём .first.
# 2) Гость до начала разговора видит в панели форму Ime/Email/Telefon: привета нет,
#    `input[name=chat-message]` (CHAT_INPUT) отсутствует (count=0), поэтому ни текст
#    приветствия, ни CHAT_INPUT не проверяем. `div.tamir-chat-messages[role=log]` при этом
#    присутствует, но пустой.
# 3) Сообщения в чат не отправляем и «Započni razgovor» не нажимаем: на стенде это создаёт
#    реальную поддержку-переписку (POST /api/chat/session).


def _wait_widget_hydrated(shop: Storefront) -> None:
    """Виджет собран через next/dynamic со ssr:false — в SSR-HTML его нет вовсе."""
    page = shop.page
    assert shop.wait_until(lambda: page.locator(CHAT_LAUNCHER).count() > 0, 15.0), (
        f"за 15 с так и не появился {CHAT_LAUNCHER} — чат не гидратировался"
    )


def _launcher_expanded(page) -> str | None:
    return page.locator(CHAT_LAUNCHER).first.get_attribute("aria-expanded")


@allure.story("Виджет чата TAMIR")
@allure.severity(allure.severity_level.NORMAL)
def test_chat_launcher_opens_panel_and_closes(page, steps):
    """Гость открывает чат лаунчером, сверяет панель и закрывает её кнопкой ×."""
    shop = Storefront(page)

    with steps("открыть каталог и дождаться гидратации виджета",
               f"{CHAT_LAUNCHER} появился, aria-expanded=false"):
        shop.goto_catalog()
        _wait_widget_hydrated(shop)
        launcher = page.locator(CHAT_LAUNCHER).first
        assert _launcher_expanded(page) == "false", (
            f"до открытия у {CHAT_LAUNCHER} aria-expanded={_launcher_expanded(page)!r}, "
            "ожидался false"
        )
        allure.attach(
            f"label={launcher.inner_text()!r}",
            "лаунчер чата",
            allure.attachment_type.TEXT,
        )

    with steps("кликнуть по лаунчеру чата",
               f"{CHAT_PANEL} виден с role=dialog, заголовок про podrška"):
        launcher.click()
        panel = page.locator(CHAT_PANEL)
        panel.first.wait_for(state="visible", timeout=10_000)
        assert panel.first.get_attribute("role") == "dialog", (
            f"у {CHAT_PANEL} role={panel.first.get_attribute('role')!r}, ожидался dialog"
        )
        heading = page.locator(CHAT_PANEL + " h2, " + CHAT_PANEL + " h3").first
        heading_text = " ".join(heading.inner_text().split())
        assert "podr" in heading_text.lower(), (
            f"в заголовке панели нет слова podrška: {heading_text!r}"
        )

    with steps("сверить журнал сообщений и состояние лаунчера",
               f"{CHAT_LOG} присутствует с role=log, aria-expanded=true"):
        log = page.locator(CHAT_LOG).first
        assert page.locator(CHAT_LOG).count() >= 1, f"в панели нет {CHAT_LOG}"
        assert log.get_attribute("role") == "log", (
            f"у {CHAT_LOG} role={log.get_attribute('role')!r}, ожидался log"
        )
        assert _launcher_expanded(page) == "true", (
            f"после открытия у {CHAT_LAUNCHER} aria-expanded={_launcher_expanded(page)!r}, "
            "ожидался true"
        )

    with steps("закрыть панель кнопкой CLOSE (первая кнопка панели)",
               f"{CHAT_PANEL} убран из DOM, aria-expanded снова false"):
        close = page.locator(CHAT_CLOSE_BUTTONS).first
        close.click()
        assert shop.wait_until(lambda: page.locator(CHAT_PANEL).count() == 0, 10.0), (
            f"после закрытия {CHAT_PANEL} остался в DOM "
            f"(count={page.locator(CHAT_PANEL).count()})"
        )
        assert _launcher_expanded(page) == "false", (
            f"после закрытия у {CHAT_LAUNCHER} aria-expanded={_launcher_expanded(page)!r}, "
            "ожидался false"
        )


@allure.story("Виджет чата TAMIR")
@allure.severity(allure.severity_level.NORMAL)
def test_chat_panel_reopens_after_close(page, steps):
    """Панель переоткрывается лаунчером после закрытия — без отправки сообщений."""
    shop = Storefront(page)

    with steps("открыть и закрыть чат",
               f"{CHAT_PANEL} появился и исчез, лаунчер на месте"):
        shop.goto_catalog()
        _wait_widget_hydrated(shop)
        launcher = page.locator(CHAT_LAUNCHER).first
        launcher.click()
        page.locator(CHAT_PANEL).first.wait_for(state="visible", timeout=10_000)
        page.locator(CHAT_CLOSE_BUTTONS).first.click()
        assert shop.wait_until(lambda: page.locator(CHAT_PANEL).count() == 0, 10.0), (
            f"после закрытия {CHAT_PANEL} остался в DOM "
            f"(count={page.locator(CHAT_PANEL).count()})"
        )

    with steps("открыть чат повторно",
               f"{CHAT_PANEL} снова виден, {CHAT_LOG} с role=log на месте"):
        launcher.click()
        panel = page.locator(CHAT_PANEL)
        panel.first.wait_for(state="visible", timeout=10_000)
        log = page.locator(CHAT_LOG).first
        assert page.locator(CHAT_LOG).count() >= 1, (
            f"при повторном открытии в панели нет {CHAT_LOG}"
        )
        assert log.get_attribute("role") == "log", (
            f"у {CHAT_LOG} role={log.get_attribute('role')!r}, ожидался log"
        )
