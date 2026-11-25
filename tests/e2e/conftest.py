"""Общие браузерные фикстуры e2e: тестовый аккаунт стэнда и **один вход на прогон**.

Стенд разрешает не больше 5 входов в час на связку email+IP, поэтому все
залогиненные тесты делят один авторизованный контекст браузера (``logged_in_page``).
Повторных попыток входа фикстура не делает — при неудаче тесты падают с внятной
причиной, а не сжирают лимит.
"""

from __future__ import annotations

import os

import pytest

from qa.config import load_config
from qa.ui import Storefront


@pytest.fixture(scope="session")
def e2e_account() -> tuple[str, str]:
    """Постоянный тестовый аккаунт стэнда из ``QA_WEB_EMAIL`` / ``QA_WEB_PASSWORD``."""
    load_config()  # подтягивает .env в os.environ (в CI переменные задаёт раннер)
    email = (os.environ.get("QA_WEB_EMAIL") or "").strip()
    password = os.environ.get("QA_WEB_PASSWORD") or ""
    if not email or not password:
        pytest.skip("нет тестового аккаунта: QA_WEB_EMAIL / QA_WEB_PASSWORD не заданы")
    return email, password


@pytest.fixture(scope="session")
def logged_in_page(browser, browser_context_args, base_url, e2e_account):
    """Страница браузера с уже выполненным входом тестового аккаунта.

    Вход — ровно один на прогон; контекст живёт до конца сессии pytest. Относительные
    ``page.goto`` работают: ``browser_context_args`` несёт ``base_url`` стэнда и
    принятое cookie согласие ``tamir_consent_v1``.
    """
    email, password = e2e_account
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    shop = Storefront(page)
    shop.login(email, password)
    if not shop.wait_until(shop.is_logged_in, 10.0):
        context.close()
        pytest.fail(
            "вход тестового аккаунта не выполнен: проверьте QA_WEB_EMAIL / QA_WEB_PASSWORD "
            "(повторный вход не делаем — стенд разрешает не больше 5 входов в час)"
        )
    yield page
    context.close()
