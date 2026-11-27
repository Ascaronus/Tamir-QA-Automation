"""E2E админки Medusa v2 на стэнде: вход, guards, список товаров, список заказов.

Зонды probe_admin1/2/3 (27.11.2026, headless Chromium 1440x900). Админка
``medusa-admin`` (Next.js) живёт **на хосте API**, а не витрины: ``/app/*`` на
``test-api.tamir.rs`` отдают её, а на витрине ``test.tamir.rs/app`` — Next.js 404.
Всё, что asserts ниже, — реальное поведение стенда; кандидаты в баги помечены
«КАНДИДАТ В БАГИ» в докстрингах, дефекты не чиним и не закрепляем падающими
тестами. Модерация отзывов (``/app/reviews``, заморожена TB-28), возвраты и
выбор способа оплаты здесь не трогаются; сохранений/удалений нет — только
навигация и чтение.

Зафиксированные зондом факты:

* анонимный вход: ``/app``, ``/app/products``, ``/app/orders`` → редирект на
  ``{API_URL}/app/login`` с ``h1`` «Welcome to Medusa»; форма —
  ``input[name=email]`` + ``input[name=password]`` (``type=password``) и кнопка
  ``Continue with Email``;
* неверный пароль: остаёмся на ``/app/login``, на странице текст
  «Invalid email or password», форма цела. КАНДИДАТ В БАГИ: сообщения нет ни в
  ``[role=alert]``, ни в ``[role=status]``, и оно к тому же всплывает как
  необработанное JS-исключение (единственный ``pageerror`` = это сообщение);
  поэтому тест ждёт текст, а не роль;
* успешный вход прилетает на ``/app/orders`` (``h1`` «Orders»), отдельного
  дашборда нет: ``/app`` тоже ведёт на ``/app/orders``;
* ``/app/products``: ``thead`` — Product/Collection/Sales Channels/Variants/
  Status, подпись ``16 of 16 results`` — ровно столько же, сколько
  ``/store/products`` отдаёт витрине; ``/app/products?q=kapa`` — 10 строк и
  ``10 of 10 results`` (поле ``input[type=search][name=q]``);
* ``/app/orders``: ``thead`` — Order/Date/Customer/Sales Channel/Payment/
  Fulfillment/Order Total, пагинация ``20 of 164 results``. КАНДИДАТ В БАГИ:
  в списке остаются заказы прошлых прогонов QA E2E — мусорные данные стэнда
  не чистятся (тест от этого не зависит, он проверяет непустоту);
* ``Ctrl+K`` — командная палитра: ``dialog`` c ``input[role=combobox]``
  (плейсхолдер «Jump to or find anything...»), закрывается по ``Escape``;
* на всех посещённых страницах админки ``pageerror`` не было ни одного.

Логинимся один раз на весь прогон (session-scoped контекст) + один неверный
логин в отдельном анонимном контексте: всего два POST на ``/auth/user/emailpass``.
Реквизиты админки — только из окружения (``MEDUSA_ADMIN_EMAIL`` /
``MEDUSA_ADMIN_PASSWORD``), в отчёты не попадают.
"""

from __future__ import annotations

import os
import re

import allure
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from qa.config import QaConfig
from qa.medusa_store import MedusaStoreClient

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

LOGIN_PATH = "/app/login"


def _admin_url(config: QaConfig, path: str) -> str:
    """Ссылка админки: она на хосте API, а не витрины (замерено зондом)."""
    return f"{config.api_url}{path}"


def _login_form(page, email: str, password: str) -> None:
    """Заполняет и отправляет единственную форму входа (``action=/app/login``)."""
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')


def _wait_logged_in(page, timeout: int = 30_000) -> None:
    """Ждём, пока роутер унесёт нас со страницы входа."""
    page.wait_for_function(
        "() => !window.location.pathname.startsWith('/app/login')", timeout=timeout
    )


def _watch(page) -> list[str]:
    """JS-ошибки страницы: админка не должна падать ни на одном экране."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


def _table_heads(page) -> list[str]:
    page.wait_for_selector("table thead th", timeout=30_000)
    return [
        t.strip()
        for t in page.locator("table thead th").all_inner_texts()
    ]


def _rows(page):
    return page.locator("table tbody tr")


def _results_counts(page) -> tuple[int, int]:
    """(видимых строк, всего) из подписи пагинации «N of M results»."""
    page.wait_for_function(
        r"() => /\d+\s+of\s+\d+\s+results/.test(document.body.innerText)",
        timeout=30_000,
    )
    m = re.search(r"(\d+)\s+of\s+(\d+)\s+results", page.inner_text("body"))
    assert m is not None
    return int(m.group(1)), int(m.group(2))


@pytest.fixture(scope="session")
def admin_credentials() -> tuple[str, str]:
    """Логин/пароль админки стэнда только из ``.env`` (значения не печатаем)."""
    email = os.getenv("MEDUSA_ADMIN_EMAIL", "")
    password = os.getenv("MEDUSA_ADMIN_PASSWORD", "")
    if not (email and password):
        pytest.skip(
            "нужны MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD в .env (admin API)"
        )
    return email, password


@pytest.fixture(scope="session")
def admin_context(browser, config: QaConfig, admin_credentials: tuple[str, str]):
    """Один авторизованный контекст на прогон: вход выполняется ровно один раз."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(_admin_url(config, LOGIN_PATH), wait_until="domcontentloaded")
        _login_form(page, *admin_credentials)
        _wait_logged_in(page)
    except Exception as exc:  # не печатаем реквизиты — только суть сбоя
        context.close()
        pytest.fail(f"вход в админку стэнда не удался: {type(exc).__name__}")
    page.close()
    yield context
    context.close()


@pytest.fixture
def anon_admin_page(browser, config: QaConfig):
    """Страница админки без сессии: guards и неверный пароль."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


@allure.story("Гость не попадает в админку")
@allure.severity(allure.severity_level.CRITICAL)
def test_anonymous_is_redirected_to_login(anon_admin_page, config: QaConfig, steps):
    """``/app``, ``/app/products``, ``/app/orders`` без сессии → форма входа.

    Замерено: все три маршрута дают ``{API_URL}/app/login`` с ``h1``
    «Welcome to Medusa»; форма — email + password + ``Continue with Email``.
    Редирект с ``/app`` — клиентский (URL меняется после гидрации), поэтому
    ждём смену адреса, а не проверяем его сразу после ``goto``. Отдельный
    факт: админки нет на хосте витрины — ``{WEB_URL}/app`` отдаёт Next.js 404
    (проверяем это как есть, чтобы URL админки не «переехал»).
    """
    page = anon_admin_page
    errors = _watch(page)

    for path in ("/app", "/app/products", "/app/orders"):
        steps(f"Гость открывает {path}", "редирект на /app/login")
        page.goto(_admin_url(config, path), wait_until="domcontentloaded")
        try:
            page.wait_for_url("**/app/login**", timeout=15_000)
        except PlaywrightTimeoutError:
            pytest.fail(f"{path} не привёл на страницу входа (url={page.url})")
        heading = page.locator("h1")
        heading.first.wait_for(timeout=15_000)
        assert "Welcome to Medusa" in heading.first.inner_text()

    steps("Форма входа на месте", "email + password + Continue with Email")
    assert page.locator('input[name="email"]').count() == 1
    assert page.locator('input[name="password"]').get_attribute("type") == "password"
    assert "Continue with Email" in page.locator('button[type="submit"]').inner_text()

    steps("На витрине /app нет админки", "Next.js 404")
    response = page.goto(f"{config.web_url}/app", wait_until="domcontentloaded")
    assert response is not None and response.status == 404

    assert errors == []


@allure.story("Неверный пароль не пускает внутрь")
@allure.severity(allure.severity_level.CRITICAL)
def test_wrong_password_is_rejected(anon_admin_page, config: QaConfig,
                                    admin_credentials: tuple[str, str], steps):
    """Неверный пароль: остаёмся на ``/app/login`` с текстом об ошибке.

    Замерено (27.11.2026): ``Invalid email or password`` появляется в тексте
    страницы, форма сохраняется, JWT в ``localStorage`` не появляется.
    КАНДИДАТ В БАГИ: текст не обёрнут в ``[role=alert]``/``[role=status]`` и
    при этом всплывает как необработанное JS-исключение — единственный
    ``pageerror`` на странице ровно с этим сообщением. Закрепляем текст и
    сам факт pageerror, чтобы починка a11y не сломала тест.
    """
    page = anon_admin_page
    errors = _watch(page)

    steps("Открываем форму входа", "Welcome to Medusa")
    page.goto(_admin_url(config, LOGIN_PATH), wait_until="domcontentloaded")
    page.locator("h1").first.wait_for(timeout=15_000)

    steps("Отправляем неверный пароль", "остаемся на /app/login с текстом ошибки")
    email, _ = admin_credentials
    _login_form(page, email, "definitely-wrong-password")
    page.wait_for_function(
        "() => document.body.innerText.includes('Invalid email or password')",
        timeout=20_000,
    )
    assert "/app/login" in page.url
    assert page.locator('input[name="email"]').count() == 1

    steps("Сессии не появилось", "нет JWT в localStorage")
    tokens = page.evaluate(
        "() => Object.entries(localStorage).filter(([k, v]) =>"
        " k.toLowerCase().includes('token') || v.includes('eyJ'))"
    )
    assert tokens == []
    assert all("Invalid email or password" in e for e in errors), errors


@allure.story("Успешный вход открывает админку")
@allure.severity(allure.severity_level.CRITICAL)
def test_login_lands_on_orders(admin_context, config: QaConfig, steps):
    """После входа ``/app`` → ``/app/orders`` с ``h1`` «Orders».

    Замерено: отдельного дашборда нет — Medusa v2 ведёт прямиком в список
    заказов (это и есть лендинг после логина). КАНДИДАТ В БАГИ в стэнде:
    в шапке смешаны языки (``Recenzije``, ``Импорт``) — не проверяем и не
    закрепляем, просто не мешаем навигации.
    """
    page = admin_context.new_page()
    errors = _watch(page)
    try:
        steps("Авторизованный /app", "редирект на /app/orders")
        page.goto(_admin_url(config, "/app"), wait_until="domcontentloaded")
        page.wait_for_url("**/app/orders**", timeout=30_000)
        assert "/app/orders" in page.url

        steps("Экран заказов собрался", "h1 Orders + боковая навигация")
        heading = page.locator("h1")
        heading.first.wait_for(timeout=15_000)
        assert "Orders" in heading.first.inner_text()
        for label in ("Products", "Orders"):
            assert page.get_by_role("link", name=label).count() > 0
        assert errors == []
    finally:
        page.close()


@allure.story("Список товаров админки совпадает с каталогом стэнда")
@allure.severity(allure.severity_level.CRITICAL)
def test_admin_products_match_storefront(admin_context, config: QaConfig,
                                         api_client: MedusaStoreClient, steps):
    """``/app/products``: таблица с 16 строками = 16 товаров ``/store/products``.

    Замерено: ``thead`` — Product/Collection/Sales Channels/Variants/Status,
    подпись ``16 of 16 results``; на стэнде ровно 16 опубликованных товаров,
    и витрина отдаёт те же 16. Сверяем названия один-в-один — админка не
    должна терять или добавлять товары.
    """
    page = admin_context.new_page()
    errors = _watch(page)
    try:
        steps("Открываем /app/products", "таблица товаров")
        page.goto(_admin_url(config, "/app/products"), wait_until="domcontentloaded")
        heads = _table_heads(page)
        assert heads[:5] == ["Product", "Collection", "Sales Channels", "Variants",
                             "Status"], heads

        steps("Считаем строки и подпись пагинации", "16 of 16 results")
        shown, total = _results_counts(page)
        assert _rows(page).count() == shown
        assert total == shown, f"{total} всего, но на странице {shown}"

        steps("Сверяем с API витрины", "те же 16 названий")
        store_titles = {p["title"] for p in api_client.products(limit=100)}
        assert len(store_titles) == total, (len(store_titles), total)
        admin_titles = {
            _rows(page).nth(i).locator("td").first.inner_text().strip()
            for i in range(shown)
        }
        assert admin_titles == store_titles

        assert errors == []
    finally:
        page.close()


@allure.story("Список заказов админки рендерится")
@allure.severity(allure.severity_level.CRITICAL)
def test_admin_orders_list_renders(admin_context, config: QaConfig, steps):
    """``/app/orders`` рендерится и непустой (факт стэнда, а не идеал).

    Замерено: ``thead`` — Order/Date/Customer/Sales Channel/Payment/
    Fulfillment/Order Total; подпись вида ``20 of 164 results`` (20 на
    страницу, всего > 0). КАНДИДАТ В БАГИ: среди заказов — остатки прогонов
    QA E2E; тест сознательно не привязан к точному числу 164, только к
    согласованности пагинации и непустоте.
    """
    page = admin_context.new_page()
    errors = _watch(page)
    try:
        steps("Открываем /app/orders", "таблица заказов")
        page.goto(_admin_url(config, "/app/orders"), wait_until="domcontentloaded")
        heads = _table_heads(page)
        assert heads[:7] == ["Order", "Date", "Customer", "Sales Channel", "Payment",
                             "Fulfillment", "Order Total"], heads

        steps("Пагинация согласована", "видимых строк <= всего, всего > 0")
        shown, total = _results_counts(page)
        assert _rows(page).count() == shown
        assert shown > 0 and total >= shown
        assert errors == []
    finally:
        page.close()


@allure.story("Поиск по товарам и командная палитра")
@allure.severity(allure.severity_level.NORMAL)
def test_admin_product_search_and_command_palette(admin_context, config: QaConfig,
                                                  steps):
    """``/app/products?q=kapa`` фильтрует таблицу, ``Ctrl+K`` открывает палитру.

    Замерено: ``?q=kapa`` — 10 строк и ``10 of 10 results``; поле —
    ``input[type=search][name=q]``. ``Ctrl+K`` — диалог с
    ``input[role=combobox]`` («Jump to or find anything...»), ``Escape`` его
    закрывает, страница не падает.
    """
    page = admin_context.new_page()
    errors = _watch(page)
    try:
        steps("Прямой URL с q=kapa", "10 of 10 results")
        page.goto(_admin_url(config, "/app/products?q=kapa"),
                  wait_until="domcontentloaded")
        _table_heads(page)
        shown, total = _results_counts(page)
        assert shown == total == _rows(page).count()
        assert 0 < shown < 16, f"фильтр не сузил выдачу: {shown}"

        steps("Поле поиска на месте", "input[type=search][name=q]")
        search = page.locator('input[type="search"][name="q"]')
        assert search.count() == 1
        assert search.input_value() == "kapa"

        steps("Ctrl+K открывает командную палитру", "combobox с плейсхолдером")
        page.keyboard.press("Control+k")
        combobox = page.get_by_placeholder("Jump to or find anything...")
        combobox.wait_for(timeout=10_000)
        combobox.fill("orders")
        page.keyboard.press("Escape")
        assert combobox.count() == 0 or not combobox.first.is_visible()
        assert errors == []
    finally:
        page.close()
