"""E2E-CAT: страница «Svi proizvodi» — счётчик и сетка карточек совпадают с выдачей API,
ссылки категорий и карточек ведут на маршруты витрины, а подгрузка остатка работает или не нужна."""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CATALOG_CARD_ITEM,
    CATALOG_COUNT_LABEL,
    CATALOG_COUNT_NUMBER,
    CATALOG_LOAD_MORE,
    CATALOG_TITLE_HEADING,
    CATEGORIES_LINKS,
    PRODUCT_CARD,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

_PRODUCT_FIELDS = "id,handle,title"


def _api_products(api_client: MedusaStoreClient, steps) -> list[dict]:
    """Товары витрины из API с непустыми ``handle`` и ``title``."""
    with steps("спросить список товаров витрины через API",
               "GET /store/products отвечает непустым списком с handle и title"):
        body = api_client._get(
            f"/store/products?limit=50&country_code=rs&fields={_PRODUCT_FIELDS}")
    products = [p for p in (body.get("products") or []) if p.get("handle")]
    assert products, "API /store/products не вернул ни одного товара со handle"
    return products


def _api_total(api_client: MedusaStoreClient, steps) -> int:
    """Число товаров витрины из поля ``count`` списочного ответа API."""
    with steps("спросить число товаров витрины через API",
               "GET /store/products отвечает полем count > 0"):
        body = api_client._get("/store/products?limit=1&country_code=rs&fields=id")
    total = int(body.get("count") or 0)
    assert total > 0, f"API /store/products вернул count={body.get('count')!r}, ожидалось число > 0"
    return total


def _open_catalog(shop: Storefront, page, steps) -> None:
    """Открывает каталог и ждёт карточки на сетке."""
    with steps("открыть страницу всех товаров каталога",
               "URL содержит /rs/catalog и на сетке появились карточки"):
        shop.goto_catalog()
    assert shop.wait_until(lambda: page.locator(CATALOG_CARD_ITEM).count() > 0, 20), \
        f"после {page.url} на сетке так и не появилось ни одной карточки ({CATALOG_CARD_ITEM})"


def test_catalog_title_and_count_match_api(page, steps, api_client: MedusaStoreClient) -> None:
    """Заголовок страницы и счётчик «N proizvoda» совпадают с числом товаров из API."""
    total = _api_total(api_client, steps)
    shop = Storefront(page)
    _open_catalog(shop, page, steps)

    with steps("прочитать заголовок страницы всех товаров",
               f"блок {CATALOG_TITLE_HEADING} один и содержит «Svi proizvodi»"):
        headings = page.locator(CATALOG_TITLE_HEADING)
        assert headings.count() == 1, \
            f"селектор {CATALOG_TITLE_HEADING}: найдено {headings.count()}, ожидался один заголовок"
        title = headings.first.inner_text().strip()
        assert title == "Svi proizvodi", \
            f"заголовок каталога {title!r} не равен ожидаемому «Svi proizvodi»"

    with steps("сверить счётчик товаров с выдачей API",
               f"тексты {CATALOG_COUNT_LABEL} и {CATALOG_COUNT_NUMBER} содержат число из API"):
        label = page.locator(CATALOG_COUNT_LABEL).first.inner_text().strip()
        number = page.locator(CATALOG_COUNT_NUMBER).first.inner_text().strip()
        assert number == str(total), \
            f"{CATALOG_COUNT_NUMBER} = {number!r}, а API насчитал {total}"
        assert label.startswith(str(total)), \
            f"{CATALOG_COUNT_LABEL} = {label!r} не начинается с числа из API ({total})"
        assert shop.catalog_count() == total, \
            f"shop.catalog_count() = {shop.catalog_count()} не равно числу товаров из API ({total})"


def test_catalog_cards_match_api_listing(page, steps, api_client: MedusaStoreClient) -> None:
    """Карточки на сетке — те же товары, что в API, с ценами и непустыми заголовками."""
    products = _api_products(api_client, steps)
    shop = Storefront(page)
    _open_catalog(shop, page, steps)

    with steps("сверить сетку карточек со списком API",
               "число карточек равно числу товаров API и это те же handle"):
        cards = page.locator(CATALOG_CARD_ITEM)
        assert cards.count() == len(products), \
            f"карточек {CATALOG_CARD_ITEM}: {cards.count()}, а в API товаров {len(products)}"
        hrefs = shop.catalog_hrefs()
        assert hrefs, "shop.catalog_hrefs() вернул пустой список ссылок"
        expected = {f"/rs/products/{p['handle']}" for p in products}
        assert set(hrefs) <= expected, \
            f"карточки ведут на товары, которых нет в API: {sorted(set(hrefs) - expected)}"

    with steps("прочитать заголовки и цены карточек",
               f"у каждой карточки непустой {PRODUCT_CARD} и цена читается числом"):
        titles = page.locator(PRODUCT_CARD)
        assert titles.count() >= len(products), \
            f"ссылок {PRODUCT_CARD} меньше карточек: {titles.count()} < {cards.count()}"
        texts = [t.strip() for t in titles.evaluate_all("els => els.map(e => e.textContent.trim())")]
        empty = [t for t in texts if not t]
        assert not empty, f"есть карточки с пустым заголовком ({PRODUCT_CARD}): {texts}"
        prices = shop.catalog_prices()
        assert len(prices) == cards.count(), \
            f"цен прочитано {len(prices)}, а карточек {cards.count()}"
        bad = [p for p in prices if p <= 0]
        assert not bad, f"есть карточки с нечитаемой или нулевой ценой: {prices}"


def test_catalog_category_links(page, steps) -> None:
    """Ссылки категорий в шапке ведут на страницы каталога, и переход по ним работает."""
    shop = Storefront(page)
    _open_catalog(shop, page, steps)

    with steps("посмотреть ссылки категорий в шапке",
               f"{CATEGORIES_LINKS} непусты и ведут на маршруты локали rs"):
        links = page.locator(CATEGORIES_LINKS)
        assert links.count() >= 2, \
            f"селектор {CATEGORIES_LINKS}: найдено ссылок {links.count()}, ожидалось минимум две"
        hrefs = [str(h or "") for h in links.evaluate_all("els => els.map(e => e.getAttribute('href'))")]
        assert all(h.startswith("/rs") for h in hrefs), \
            f"ссылки категорий ведут за пределы локали rs: {hrefs}"
        category = next((h for h in hrefs if h.startswith("/rs/catalog/")), None)
        assert category, f"среди ссылок категорий {hrefs} нет ни одной на /rs/catalog/"

    with steps(f"перейти по ссылке категории {category!r}",
               "URL совпал со ссылкой и на сетке снова есть карточки"):
        page.locator(f'{CATEGORIES_LINKS}[href="{category}"]').first.click()
        assert shop.wait_until(lambda: category in page.url, timeout_s=20), \
            f"после клика по категории URL = {page.url}, ожидалось {category!r}"
        assert shop.wait_until(lambda: page.locator(CATALOG_CARD_ITEM).count() > 0, 20), \
            f"на странице категории {category} нет ни одной карточки товара"


def test_catalog_load_more_or_all_visible(page, steps, api_client: MedusaStoreClient) -> None:
    """Если есть кнопка подгрузки — она добавляет карточки; иначе вся витрина уже на странице."""
    total = _api_total(api_client, steps)
    shop = Storefront(page)
    _open_catalog(shop, page, steps)

    with steps("проверить подгрузку остатка каталога",
               f"либо {CATALOG_LOAD_MORE} добавляет карточки, либо вся витрина видна сразу"):
        button = page.locator(CATALOG_LOAD_MORE)
        shown = shop.catalog_count()
        if button.count() == 0:
            assert shown == total, \
                f"кнопки {CATALOG_LOAD_MORE} нет, но видно {shown} из {total} товаров API"
        else:
            assert shown < total, \
                f"вся витрина ({shown}) уже видна, но кнопка {CATALOG_LOAD_MORE} всё ещё на странице"
            button.first.click()
            assert shop.wait_until(lambda: shop.catalog_count() > shown, timeout_s=20), \
                f"клик по {CATALOG_LOAD_MORE} не увеличил число карточек ({shown})"
            assert shop.catalog_count() <= total, \
                f"подгрузка показала больше товаров, чем в API: {shop.catalog_count()} > {total}"
