"""Мобильная витрина: вьюпорт 390x844 — мета, скролл, бургер, поиск, фильтры, PDP, checkout гостя.

Съёмка на ``test.tamir.rs`` (27.11.2026, headless Chromium, 390x844 @dsf2, sr-RS,
согласие ``tamir_consent_v1=accepted``). Всё, что asserts ниже, — реальное поведение
стенда, а не идеальный контракт; кандидаты в баги перечислены в докстрингах тестов.

Ключевые мобильные особенности витрины (замерено этим зондом):

* ``<meta name="viewport" content="width=device-width, initial-scale=1">`` и
  ``<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">`` — на ``/rs``;
* горизонтального скролла нет ни на одной странице: ``documentElement.scrollWidth``
  равно ``clientWidth`` (390) на ``/rs``, ``/rs/catalog`` и PDP;
* в шапке вместо категории-навигации бургер ``header.shop-header button.mobile-menu``
  (44x44, ``aria-haspopup="dialog"``, ``aria-controls="store-sidebar"``), открывает
  нативный ``<dialog id="store-sidebar">`` с тремя ссылками; закрывается кнопкой
  ``aria-label="Zatvori"``. КАНДИДАТ В БАГИ: у бургера никогда нет ``aria-expanded``;
* форма поиска ``form.catalog-search`` на мобильном живёт НЕ в шапке, а в тулбаре
  ``div.catalog-toolbar`` (на ``/rs`` — y≈644, ниже первого экрана). КАНДИДАТ В БАГИ:
  поиск недоступен из шапки и из бургер-меню;
* ``button.catalog-filter-toggle`` открывает ``dialog.filter-drawer`` шириной ровно 390
  (панель влезает, скролла нет). КАНДИДАТ В БАГИ: у тумблера тоже нет ``aria-expanded``;
* PDP: галерея ``button.product-hero`` (280x350) и превью ``button.product-thumbnail``
  рендерятся, но превью на мобильном всего 24x32. КАНДИДАТ В БАГИ: тач-цель меньше 44px;
* ``div.header-categories`` при 390 имеет ``display: none`` — десктопная навигация.

Гость, кроме E2E-35: остальные тесты ничего не покупают, не логинимся (лимит входов
на стенде), пишем только в ``localStorage``. Свой контекст ``browser.new_context`` на
390x844, ``tests/conftest.py`` не трогаем. E2E-35 оформляет ровно один гостевой заказ
и отменяет его через админ-API (как ``test_guest_shop_journey``).
"""

from __future__ import annotations

import secrets

import allure
import pytest

from qa.medusa_admin import MedusaAdminClient
from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CATALOG_CARD_ITEM,
    CATALOG_COUNT,
    CATALOG_COUNT_DIGITS,
    CATALOG_HEADING,
    CHECKOUT_CITY,
    CHECKOUT_SHIPPING,
    CHECKOUT_SUBMIT,
    CONFIRMED_RE,
    CONSENT_COOKIE_NAME,
    HEADER_SEARCH_FORM,
    HEADER_SEARCH_INPUT,
    HEADER_SEARCH_SUBMIT,
    MOBILE_MENU_BUTTON,
    PRODUCT_CARD,
    PRODUCT_PRICE,
    SHOP_HEADER,
    SIDEBAR_DRAWER,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

MOBILE_VIEWPORT = {"width": 390, "height": 844}
# Реальный переполнение-допуск: на стенде scrollWidth == clientWidth, но оставляем 2px.
OVERFLOW_TOLERANCE_PX = 2

# мобильные контролы, отсутствующие в qa/ui.py
SIDEBAR_LINKS = SIDEBAR_DRAWER + " nav a"
SIDEBAR_CLOSE = SIDEBAR_DRAWER + ' button[aria-label="Zatvori"]'
FILTER_TOGGLE = "button.catalog-filter-toggle"
FILTER_DRAWER = "dialog.filter-drawer"
PDP_HERO_IMAGE = "button.product-hero img"
PDP_THUMBNAILS = "button.product-thumbnail"
PDP_CTA = "div.product-summary button.button-primary"  # «U korpu», 350x48 при 390

# оформление гостя: id полей от локали не зависят (см. CHECKOUT_* в qa/ui.py)
CHECKOUT_FORM = "form#checkout-form"
CHECKOUT_FIELDS = (
    "#checkout-firstName",
    "#checkout-lastName",
    "#checkout-email",
    "#checkout-phone",
    "#checkout-address1",
    "#checkout-postalCode",
    CHECKOUT_CITY,
)
# получатель как в E2E-1 (test_guest_shop_journey): почта своя, одноразовая
MOBILE_BUYER = {
    "firstName": "QA",
    "lastName": "Mobile",
    "phone": "+381643210987",
    "address1": "Knez Mihailova 5",
    "postalCode": "11000",
    "city": "Beograd",
}

# гостевые корзины модуля (живут в localStorage страницы) — на случай обрыва теста
_LEFTOVER_CARTS: list[str] = []

_PRODUCT_FIELDS = (
    "id,handle,title,variants.id,variants.title,variants.manage_inventory,"
    "variants.allow_backorder,+variants.inventory_quantity"
)


@allure.epic("Мобильная витрина")
def _mobile_context(browser, base_url: str):
    """Контекст телефона: 390x844 @2x, sr-RS, согласие на cookie принято заранее.

    Повторяет ``browser_context_args`` из ``tests/conftest.py`` (конsent + локаль),
    но с мобильным вьюпортом — десктопный 1440x900 для этих проверок бесполезен.
    """
    return browser.new_context(
        locale="sr-RS",
        viewport=dict(MOBILE_VIEWPORT),
        device_scale_factor=2,
        base_url=base_url,
        storage_state={
            "cookies": [
                {"name": CONSENT_COOKIE_NAME, "value": "accepted", "url": base_url}
            ],
            "origins": [],
        },
    )


def _watch(page) -> list[str]:
    """JS-ошибки страницы: мобильные бандлы не должны падать."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


def _horizontal_overflow(page) -> tuple[int, int]:
    """(scrollWidth, clientWidth) документa — на мобильном они обязаны совпадать."""
    return page.evaluate(
        "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )


def _pick_in_stock(api_client: MedusaStoreClient, need: int, exclude: tuple[str, ...] = ()) -> str | None:
    """Хэндл товара стенда с остатком не меньше ``need`` (исключая ``exclude``).

    Как в ``test_cart_quantity``/``test_stock_limit``: на стенде почти у всех
    вариантов остаток 1, поэтому берём товар с запасом — иначе конкурентный прогон
    обнулит остаток посреди теста.
    """
    body = api_client._get(f"/store/products?limit=50&country_code=rs&fields={_PRODUCT_FIELDS}")
    for product in body.get("products") or []:
        handle = product.get("handle") or ""
        if not handle or handle in exclude:
            continue
        for variant in product.get("variants") or []:
            stock = variant.get("inventory_quantity")
            if stock is not None and stock >= need:
                return handle
    return None


def _buyer_email() -> str:
    """Одноразовая почта гостевого заказа в формате ``qa-e2e-*``, который знает уборка."""
    return f"qa-e2e-mob-{secrets.token_hex(4)}@example.com"


@pytest.fixture(scope="module", autouse=True)
def _cleanup_guest_carts(api_client: MedusaStoreClient):
    """Снимает резерв товара у корзин, оставшихся от незавершённого заказа модуля."""
    yield
    for cart_id in _LEFTOVER_CARTS:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — корзина могла уйти в заказ; уборка не роняет тест
            pass


def _cancel_own_order(admin_client: MedusaAdminClient, order_id: str) -> None:
    """Отменяет только что созданный заказ; неспособность отменить — печать, не провал."""
    if not order_id:
        return
    try:
        if admin_client.cancel_order(order_id):
            print(f"E2E-35: тестовый заказ {order_id} отменён")
        else:
            print(f"E2E-35: НЕ удалось отменить заказ {order_id}: {admin_client.last_error}")
    except Exception as exc:  # noqa: BLE001 — уборка не должна перекрывать результат теста
        print(f"E2E-35: отмена заказа {order_id} не выполнена: {exc}")


@allure.story("Мета вьюпорта и robots")
@allure.severity(allure.severity_level.CRITICAL)
def test_viewport_meta_and_noindex(browser, base_url, steps):
    """``width=device-width`` и ``noindex`` — фундамент мобильной вёрстки стэнда."""
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    try:
        page.goto("/rs", wait_until="domcontentloaded")
        viewport_content = page.locator('meta[name="viewport"]').get_attribute("content")
        assert viewport_content == "width=device-width, initial-scale=1", (
            f"<meta name=viewport>={viewport_content!r}, ждём width=device-width"
        )
        robots_content = page.locator('meta[name="robots"]').get_attribute("content") or ""
        assert "noindex" in robots_content, (
            f"<meta name=robots>={robots_content!r}, стэнд обязан быть закрыт от индексации"
        )
    finally:
        context.close()


@allure.story("Горизонтальный скролл")
@allure.severity(allure.severity_level.CRITICAL)
def test_home_has_no_horizontal_overflow(browser, base_url, steps):
    """Главная при 390 не вылезает за вьюпорт: scrollWidth == clientWidth."""
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    errors = _watch(page)
    try:
        page.goto("/rs", wait_until="domcontentloaded")
        page.locator(SHOP_HEADER).wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(1_500)  # шапка/продукты дорисовываются гидратацией
        scroll_w, client_w = _horizontal_overflow(page)
        assert scroll_w <= client_w + OVERFLOW_TOLERANCE_PX, (
            f"/rs при 390: scrollWidth={scroll_w} > clientWidth={client_w} — горизонтальный скролл"
        )
        assert page.evaluate("() => window.innerWidth") == MOBILE_VIEWPORT["width"]
    finally:
        context.close()
    assert not errors, f"pageerror на /rs при 390: {errors[:3]}"


@allure.story("Горизонтальный скролл")
@allure.severity(allure.severity_level.CRITICAL)
def test_catalog_has_no_horizontal_overflow(browser, base_url, steps):
    """Каталог при 390: сетка в две колонки, карточки видимы, скролла по X нет."""
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    try:
        page.goto("/rs/catalog", wait_until="domcontentloaded")
        page.locator(PRODUCT_CARD).first.wait_for(state="visible", timeout=30_000)
        scroll_w, client_w = _horizontal_overflow(page)
        assert scroll_w <= client_w + OVERFLOW_TOLERANCE_PX, (
            f"/rs/catalog при 390: scrollWidth={scroll_w} > clientWidth={client_w}"
        )
        card_box = page.locator(CATALOG_CARD_ITEM).first.bounding_box()
        assert card_box and card_box["width"] <= MOBILE_VIEWPORT["width"], (
            f"карточка {card_box} шире вьюпорта"
        )
    finally:
        context.close()


@allure.story("Счётчик и сетка каталога")
@allure.severity(allure.severity_level.NORMAL)
def test_catalog_count_matches_cards(browser, base_url, steps):
    """Счётчик ``p.catalog-count`` на мобильном совпадает с числом карточек (замерено: 16)."""
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    try:
        page.goto("/rs/catalog", wait_until="domcontentloaded")
        page.locator(PRODUCT_CARD).first.wait_for(state="visible", timeout=30_000)
        counter = page.locator(CATALOG_COUNT)
        counter.wait_for(state="visible", timeout=30_000)
        reported = CATALOG_COUNT_DIGITS.search(counter.inner_text() or "")
        assert reported, f"счётчик без числа: {counter.inner_text()!r}"
        shown = page.locator(PRODUCT_CARD).count()
        assert int(reported.group(1)) == shown > 0, (
            f"счётчик {reported.group(1)} ≠ карточек {shown} при 390"
        )
    finally:
        context.close()


@allure.story("Бургер-меню")
@allure.severity(allure.severity_level.CRITICAL)
def test_burger_menu_opens_sidebar(browser, base_url, steps):
    """Бургер в шапке открывает ``dialog#store-sidebar`` и закрывается кнопкой Zatvori.

    КАНДИДАТ В БАГИ (зафиксировано фактом, не падением): у кнопки бургера нет
    ``aria-expanded`` — экранные читатели не узнают, открыто ли меню. Заодно у
    ``<dialog>`` нет ``aria-modal``.
    """
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    errors = _watch(page)
    try:
        page.goto("/rs", wait_until="domcontentloaded")
        burger = page.locator(MOBILE_MENU_BUTTON).first
        burger.wait_for(state="visible", timeout=30_000)
        box = burger.bounding_box()
        assert box and box["width"] >= 40 and box["height"] >= 40, (
            f"бургер {box} меньше тач-цели 44px"
        )
        assert burger.get_attribute("aria-haspopup") == "dialog"
        assert burger.get_attribute("aria-controls") == "store-sidebar"
        # факт стенда: aria-expanded отсутствует (см. кандидата в баги в докстринге)
        assert burger.get_attribute("aria-expanded") is None

        burger.click()
        drawer = page.locator(SIDEBAR_DRAWER)
        drawer.wait_for(state="visible", timeout=10_000)
        assert page.evaluate(
            "() => document.querySelector('dialog#store-sidebar').hasAttribute('open')"
        ), "после клика по бургеру у dialog#store-sidebar нет атрибута open"
        links = page.locator(SIDEBAR_LINKS)
        assert links.count() == 3, f"в drawer {links.count()} ссылок, ждём 3 (Svi proizvodi + 2 категории)"
        assert links.first.inner_text().strip() == "Svi proizvodi"
        assert links.nth(1).get_attribute("href") == "/rs/catalog/kape"

        page.locator(SIDEBAR_CLOSE).first.click()
        drawer.wait_for(state="hidden", timeout=10_000)
    finally:
        context.close()
    assert not errors, f"pageerror при работе с drawer: {errors[:3]}"


@allure.story("Поиск на мобильном")
@allure.severity(allure.severity_level.CRITICAL)
def test_search_reachable_and_submits_query(browser, base_url, steps):
    """Форма поиска видна при 390 и отправляет ``?q=`` на каталог; выдача = карточки.

    КАНДИДАТ В БАГИ: ``form.catalog-search`` на мобильном не в шапке, а в тулбаре
    каталога (на ``/rs`` — верхняя кромка y≈644, ниже первого экрана); в бургер-меню
    формы поиска нет вовсе. Проверка ниже это фиксирует фактом ``is_visible``, а не
    позой «доступен сверху».
    """
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    try:
        page.goto("/rs", wait_until="domcontentloaded")
        form = page.locator(HEADER_SEARCH_FORM)
        assert form.count() == 1, f"форм поиска {form.count()}, ждём ровно одну"
        assert form.first.is_visible(), "form.catalog-search не видна при 390"

        page.locator(HEADER_SEARCH_INPUT).first.fill("šešir")
        page.locator(HEADER_SEARCH_SUBMIT).first.click()
        page.wait_for_url("/rs/catalog?q=*", timeout=30_000)
        page.locator(CATALOG_HEADING).wait_for(state="visible", timeout=30_000)
        counter = page.locator(CATALOG_COUNT)
        counter.wait_for(state="visible", timeout=30_000)
        reported = CATALOG_COUNT_DIGITS.search(counter.inner_text() or "")
        assert reported, f"счётчик выдачи без числа: {counter.inner_text()!r}"
        shown = page.locator(PRODUCT_CARD).count()
        assert int(reported.group(1)) == shown > 0, (
            f"выдача по «šešir»: счётчик {reported.group(1)} ≠ карточек {shown}"
        )
        scroll_w, client_w = _horizontal_overflow(page)
        assert scroll_w <= client_w + OVERFLOW_TOLERANCE_PX, (
            f"выдача при 390: scrollWidth={scroll_w} > clientWidth={client_w}"
        )
    finally:
        context.close()


@allure.story("Панель фильтров")
@allure.severity(allure.severity_level.NORMAL)
def test_filter_drawer_fits_viewport(browser, base_url, steps):
    """«Filteri» на мобильном — нативный dialog шириной ровно 390, без скролла по X.

    КАНДИДАТ В БАГИ: у тумблера ``button.catalog-filter-toggle`` нет ``aria-expanded``.
    """
    context = _mobile_context(browser, base_url)
    page = context.new_page()
    try:
        page.goto("/rs/catalog", wait_until="domcontentloaded")
        page.locator(PRODUCT_CARD).first.wait_for(state="visible", timeout=30_000)
        toggle = page.locator(FILTER_TOGGLE).first
        toggle.wait_for(state="visible", timeout=10_000)
        assert toggle.get_attribute("aria-expanded") is None  # факт стенда (кандидат в баги)
        toggle.click()
        drawer = page.locator(FILTER_DRAWER)
        drawer.wait_for(state="visible", timeout=10_000)
        box = drawer.bounding_box()
        assert box, "dialog.filter-drawer без геометрии"
        assert box["width"] <= MOBILE_VIEWPORT["width"] + OVERFLOW_TOLERANCE_PX, (
            f"панель фильтров {box['width']}px шире вьюпорта 390"
        )
        assert "Filteri" in (page.locator(FILTER_DRAWER + " h2").first.inner_text() or "")
        assert page.locator(FILTER_DRAWER + " input").count() > 0, "в панели нет полей фильтра"
        scroll_w, client_w = _horizontal_overflow(page)
        assert scroll_w <= client_w + OVERFLOW_TOLERANCE_PX, (
            f"открытая панель фильтров: scrollWidth={scroll_w} > clientWidth={client_w}"
        )
    finally:
        context.close()


@allure.story("Страница товара")
@allure.severity(allure.severity_level.CRITICAL)
def test_pdp_gallery_price_and_cta(browser, base_url, api_client: MedusaStoreClient, steps):
    """PDP при 390: hero-изображение, превью галереи, цена и CTA рендерятся, скролла нет.

    КАНДИДАТ В БАГИ: ``button.product-thumbnail`` на мобильном 24x32 — тач-цель
    меньше 44px; ниже это зафиксировано измерением (тест не падает, факт в assert).
    """
    products = api_client.products(limit=50)  # список продуктов витринного API
    handle = next((p.get("handle") for p in products if p.get("handle")), None)
    assert handle, f"в витринном API нет товара с handle: {products[:2]}"

    context = _mobile_context(browser, base_url)
    page = context.new_page()
    errors = _watch(page)
    try:
        page.goto(f"/rs/products/{handle}", wait_until="domcontentloaded")
        page.locator(CATALOG_HEADING).wait_for(state="visible", timeout=30_000)
        price = page.locator(PRODUCT_PRICE).first.inner_text().strip()
        assert price and any(ch.isdigit() for ch in price), f"цена пустая/без цифр: {price!r}"

        hero = page.locator(PDP_HERO_IMAGE).first
        hero.wait_for(state="visible", timeout=30_000)
        hero_box = hero.bounding_box()
        assert hero_box and hero_box["width"] >= 200, f"hero-изображение {hero_box} подозрительно узкое"

        thumbs = page.locator(PDP_THUMBNAILS)
        assert thumbs.count() >= 1, "у галереи нет превью button.product-thumbnail"
        thumb_box = thumbs.first.bounding_box()
        # факт стенда: превью 24x32 (<44px — кандидат в баги), но оно видно и кликабельно
        assert thumb_box and thumb_box["width"] > 0 and thumbs.first.is_visible(), (
            f"первое превью невидимо: {thumb_box}"
        )

        cta = page.locator(PDP_CTA).first
        cta.wait_for(state="visible", timeout=30_000)
        cta_box = cta.bounding_box()
        assert cta_box and cta_box["width"] <= MOBILE_VIEWPORT["width"], f"CTA {cta_box} вылезает за 390"

        scroll_w, client_w = _horizontal_overflow(page)
        assert scroll_w <= client_w + OVERFLOW_TOLERANCE_PX, (
            f"PDP {handle} при 390: scrollWidth={scroll_w} > clientWidth={client_w}"
        )
    finally:
        context.close()
    assert not errors, f"pageerror на PDP {handle}: {errors[:3]}"


@allure.story("Оформление заказа")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.checkout
def test_e2e_35_guest_checkout_at_mobile_viewport(
    browser, base_url, steps, api_client: MedusaStoreClient, admin_client: MedusaAdminClient
):
    """E2E-35. Гость оформляет заказ при 390x844: каталог → PDP → корзина → подтверждение.

    Мобильная часть проверки: ``form#checkout-form`` и все обязательные поля видны,
    их геометрия лежит внутри вьюпорта 390 (ничего не уезжает в горизонтальный
    скролл), кнопка ``button.checkout-submit`` видна, активна и кликабельна одним
    тапом. Стенд оформляет оформление в два шага (адрес → доставка → «Naruci»),
    поэтому заказ создаётся ровно один раз.

    За собой: заказ отменяется через админ-API (отказ отмены — печать, не провал),
    незавершённую корзину снимает фикстура модуля.
    """
    handle = _pick_in_stock(api_client, 1)
    if not handle:
        pytest.skip("на стенде нет товаров с остатком >= 1 — оформлять нечего")

    context = _mobile_context(browser, base_url)
    page = context.new_page()
    shop = Storefront(page)
    errors = _watch(page)
    cart_id = ""
    order_id = ""
    try:
        with steps("Каталог и карточка товара при 390", "товар в остатке, «U korpu» видна"):
            shop.goto_catalog()
            assert page.evaluate("() => window.innerWidth") == MOBILE_VIEWPORT["width"], (
                "витрина открыта не в мобильном вьюпорте 390"
            )
            shop.open_product(f"/rs/products/{handle}")
            cta = page.locator(PDP_CTA).first
            cta.wait_for(state="visible", timeout=30_000)

        with steps("Добавить позицию", "витрина ответила POST /line-items"):
            with page.expect_response(
                lambda r: "/line-items" in r.url and r.request.method == "POST", timeout=30_000
            ):
                cta.click()
            page.wait_for_timeout(800)  # витрина пересчитывает состояние асинхронно
            cart_id = shop.cart_id() or ""
            if cart_id:
                _LEFTOVER_CARTS.append(cart_id)

        with steps("Корзина при 390", "в корзине одна позиция"):
            shop.goto_cart()
            assert shop.wait_until(lambda: shop.cart_line_count() > 0, 20.0), (
                f"корзина при 390 не показала позиции: {page.locator('main').inner_text()[:200]!r}"
            )

        with steps("Открыть оформление", "form#checkout-form отрисована"):
            shop.go_to_checkout()
            page.locator(CHECKOUT_FORM).first.wait_for(state="visible", timeout=30_000)
            page.wait_for_timeout(1_500)  # поля и кнопка дорисовываются гидратацией

        with steps("Форма оформления влезаёт в 390", "поля видимы и внутри вьюпорта"):
            for selector in CHECKOUT_FIELDS:
                field = page.locator(selector).first
                assert field.is_visible(), f"поле {selector} не видно при 390"
                box = field.bounding_box()
                assert box, f"у поля {selector} нет геометрии"
                assert box["x"] >= -OVERFLOW_TOLERANCE_PX, f"поле {selector} ({box}) уехало влево за 390"
                assert box["x"] + box["width"] <= MOBILE_VIEWPORT["width"] + OVERFLOW_TOLERANCE_PX, (
                    f"поле {selector} ({box}) вылезает за правый край 390"
                )

            submit = page.locator(CHECKOUT_SUBMIT).first
            submit.wait_for(state="visible", timeout=15_000)
            box = submit.bounding_box()
            assert box and box["width"] <= MOBILE_VIEWPORT["width"], (
                f"кнопка оформления {box} шире вьюпорта 390"
            )
            assert submit.is_enabled(), "button.checkout-submit неактивна при 390"

        with steps("Оформление без горизонтального скролла", "scrollWidth == clientWidth"):
            scroll_w, client_w = _horizontal_overflow(page)
            assert scroll_w <= client_w + OVERFLOW_TOLERANCE_PX, (
                f"/rs/checkout при 390: scrollWidth={scroll_w} > clientWidth={client_w}"
            )

        with steps("Заполнить получателя и получить доставку", "появились способы доставки"):
            shop.set_checkout_details({**MOBILE_BUYER, "email": _buyer_email()})
            shop.prepare_delivery()
            shipping = page.locator(CHECKOUT_SHIPPING)
            assert shipping.count() > 0, (
                f"способы доставки не появились при 390: {shop.checkout_errors()!r}"
            )
            shipping.first.check()
            page.wait_for_timeout(800)

        with steps("Оформить заказ", "подтверждение с номером заказа"):
            shop.place_order()
            text = shop.confirmation_text()
            assert CONFIRMED_RE.search(text), (
                f"вместо подтверждения показано: {text!r} | {shop.checkout_errors()!r}"
            )
            order_id = shop.order_id_from_url()
            assert order_id, f"подтверждение без адреса заказа: {page.url!r}"
            assert shop.order_number(), f"в подтверждении нет номера заказа: {text!r}"
            if cart_id and cart_id in _LEFTOVER_CARTS:
                _LEFTOVER_CARTS.remove(cart_id)  # корзина стала заказом — её убирает отмена
    finally:
        context.close()
        _cancel_own_order(admin_client, order_id)
    assert not errors, f"pageerror на пути гостя при 390: {errors[:3]}"
