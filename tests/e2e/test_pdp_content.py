"""E2E-21: карточка товара на живом стенде — заголовок и крошки сходятся с API, цена,
наличие, выбор варианта и характеристики отрисованы, блок доставки/возврата раскрывается,
похожие товары доезжают после прокрутки."""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    PDP_ADDED_NOTICE,
    PDP_ATTRIBUTES,
    PDP_ATTRIBUTE_ROWS,
    PDP_BREADCRUMBS,
    PDP_DELIVERY,
    PDP_DELIVERY_SUMMARY,
    PDP_DELIVERY_TERMS_LINK,
    PDP_RELATED,
    PDP_RELATED_CARDS,
    PDP_RELATED_TITLE,
    PDP_TITLE_HEADING,
    PDP_VARIANT_BUTTONS,
    PDP_VARIANT_FIELDSETS,
    PRODUCT_PRICE,
    PRODUCT_STOCK,
    Storefront,
    money_to_number,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

_PRODUCT_FIELDS = "id,handle,title"


def _pick_product(api_client: MedusaStoreClient, steps) -> dict:
    """Первый товар с непустым ``handle`` и ``title`` из витринного каталога."""
    with steps("спросить каталог витрины через API",
               "GET /store/products отвечает товарами с handle и title"):
        body = api_client._get(
            f"/store/products?limit=24&country_code=rs&fields={_PRODUCT_FIELDS}")
    products = [p for p in (body.get("products") or []) if p.get("handle") and p.get("title")]
    assert products, "API /store/products не вернул ни одного товара с handle и title"
    return products[0]


def _open_pdp(shop: Storefront, page, product: dict, steps) -> None:
    """Открывает карточку по ``/rs/products/<handle>`` и ждёт заголовка."""
    with steps(f"открыть карточку товара {product['handle']!r}",
               f"URL /rs/products/{product['handle']}, страница отрисовала заголовок"):
        shop.open_product(f"/rs/products/{product['handle']}")
    assert shop.wait_until(lambda: page.locator(PDP_TITLE_HEADING).count() > 0, 20), \
        f"после {shop.page.url} так и не появился заголовок карточки ({PDP_TITLE_HEADING})"


def test_pdp_title_matches_api(page, steps, api_client: MedusaStoreClient) -> None:
    """Заголовок ``h1`` карточки ровно один и совпадает с ``title`` из API."""
    product = _pick_product(api_client, steps)
    shop = Storefront(page)
    _open_pdp(shop, page, product, steps)

    with steps("сверить заголовок карточки с выдачей API",
               "на странице ровно один h1 и его текст равен title товара из API"):
        headings = page.locator(PDP_TITLE_HEADING)
        assert headings.count() == 1, \
            f"селектор {PDP_TITLE_HEADING}: найдено h1 {headings.count()}, ожидался один"
        shown = headings.first.inner_text().strip()
        assert shown == product["title"].strip(), \
            f"заголовок карточки {shown!r} не совпал с title из API {product['title']!r}"


def test_pdp_breadcrumbs_lead_to_catalog(page, steps, api_client: MedusaStoreClient) -> None:
    """Хлебные крошки ведут на главную и в категорию, а последняя — текущий товар."""
    product = _pick_product(api_client, steps)
    shop = Storefront(page)
    _open_pdp(shop, page, product, steps)

    with steps("пробежать по хлебным крошкам карточки",
               "крошки ведут в каталог/категорию, последняя — название товара"):
        crumbs = page.locator(PDP_BREADCRUMBS)
        assert crumbs.count() == 1, \
            f"селектор {PDP_BREADCRUMBS}: найдено {crumbs.count()}, ожидалась одна панель крошек"
        links = crumbs.first.locator("a")
        assert links.count() >= 1, \
            f"в {PDP_BREADCRUMBS} нет ни одной ссылки-крошки ({links.count()})"
        hrefs = [str(h or "") for h in links.evaluate_all("els => els.map(e => e.getAttribute('href'))")]
        assert all(h.startswith("/rs") for h in hrefs), \
            f"ссылки-крошки ведут за пределы локали rs: {hrefs}"
        category_hrefs = [h for h in hrefs if "/catalog/" in h]
        assert category_hrefs, \
            f"в крошках нет ссылки на категорию каталога, получено {hrefs}"

    with steps("проверить, что последняя крошка — это текущий товар",
               "элемент с aria-current содержит название товара из API"):
        current = page.locator(PDP_BREADCRUMBS + " span[aria-current]")
        assert current.count() == 1, \
            f"селектор {PDP_BREADCRUMBS} span[aria-current]: найдено {current.count()}, ожидался один"
        crumb_text = (current.first.text_content() or "").strip()
        assert crumb_text == product["title"].strip(), \
            f"текущая крошка {crumb_text!r} не равна title товара {product['title']!r}"


def test_pdp_price_and_stock_rendered(page, steps, api_client: MedusaStoreClient) -> None:
    """Цена читается числом, наличие и область ``aria-live`` отрисованы."""
    product = _pick_product(api_client, steps)
    shop = Storefront(page)
    _open_pdp(shop, page, product, steps)

    with steps("прочитать цену товара на карточке",
               f"блок {PRODUCT_PRICE} один и содержит положительную сумму в динарах"):
        prices = page.locator(PRODUCT_PRICE)
        assert prices.count() >= 1, f"селектор {PRODUCT_PRICE} не найден на карточке товара"
        price_text = prices.first.inner_text().strip()
        amount = money_to_number(price_text)
        assert amount is not None and amount > 0, \
            f"{PRODUCT_PRICE} = {price_text!r} не читается как положительная цена"

    with steps("прочитать текст о наличии",
               f"блок {PRODUCT_STOCK} непустой и рядом есть живая область {PDP_ADDED_NOTICE}"):
        stock = shop.product_stock_text()
        assert stock, f"селектор {PRODUCT_STOCK} вернул пустой текст о наличии"
        live = page.locator(PDP_ADDED_NOTICE)
        assert live.count() >= 1, \
            f"на карточке нет области {PDP_ADDED_NOTICE} ({live.count()}) — изменения не announceются"


def test_pdp_variant_options_and_attributes(page, steps, api_client: MedusaStoreClient) -> None:
    """Варианты товара показаны кнопками с выбранным, а характеристики — парами dt/dd."""
    product = _pick_product(api_client, steps)
    shop = Storefront(page)
    _open_pdp(shop, page, product, steps)

    with steps("посмотреть выбор варианта на карточке",
               f"есть {PDP_VARIANT_FIELDSETS} с кнопками {PDP_VARIANT_BUTTONS} и выбранным вариантом"):
        fieldsets = page.locator(PDP_VARIANT_FIELDSETS)
        assert fieldsets.count() >= 1, \
            f"селектор {PDP_VARIANT_FIELDSETS}: найдено {fieldsets.count()}, ожидался минимум один"
        options = page.locator(PDP_VARIANT_BUTTONS)
        assert options.count() >= 1, \
            f"селектор {PDP_VARIANT_BUTTONS}: ни одной кнопки варианта ({options.count()})"
        selected = page.locator(PDP_VARIANT_BUTTONS + "[aria-pressed=true]")
        assert selected.count() >= 1, \
            f"ни одна кнопка варианта не помечена выбранной (aria-pressed=true: {selected.count()})"

    with steps("раскрыть блок характеристик товара",
               "в details.product-attributes есть непустые пары «характеристика — значение»"):
        blocks = page.locator(PDP_ATTRIBUTES)
        assert blocks.count() >= 1, \
            f"селектор {PDP_ATTRIBUTES} не найден на карточке товара ({blocks.count()})"
        rows = page.locator(PDP_ATTRIBUTE_ROWS)
        assert rows.count() >= 1, \
            f"в {PDP_ATTRIBUTES} нет ни одной строки характеристик ({PDP_ATTRIBUTE_ROWS}: {rows.count()})"
        # inner_text у свёрнутого <details> пустой — читаем text_content.
        pairs = [( (r.locator("dt").first.text_content() or "").strip(),
                  (r.locator("dd").first.text_content() or "").strip())
                 for r in rows.all() if r.locator("dt").count() and r.locator("dd").count()]
        filled = [(k, v) for k, v in pairs if k and v]
        assert filled, f"в {PDP_ATTRIBUTES} все пары характеристик пустые: {pairs}"


def test_pdp_delivery_and_related_blocks(page, steps, api_client: MedusaStoreClient) -> None:
    """Блок доставки/возврата раскрывается, похожие товары появляются после прокрутки."""
    product = _pick_product(api_client, steps)
    shop = Storefront(page)
    _open_pdp(shop, page, product, steps)

    with steps("развернуть блок доставки и возврата",
               f"в {PDP_DELIVERY} есть текст о доставке и ссылка на условия"):
        delivery = page.locator(PDP_DELIVERY)
        assert delivery.count() >= 1, (
            f"селектор {PDP_DELIVERY} не найден на карточке ({delivery.count()}) — "
            "витрина рисует блок «Dostava i plaćanje / Povraćaj i zamena» у каждого товара")
        summary = page.locator(PDP_DELIVERY_SUMMARY).first
        summary.click()
        body = (delivery.first.inner_text() or "").strip()
        assert body, f"раскрытый {PDP_DELIVERY} оказался без текста"
        terms = page.locator(PDP_DELIVERY_TERMS_LINK)
        assert terms.count() >= 1, \
            f"в {PDP_DELIVERY} нет ссылки на условия доставки ({PDP_DELIVERY_TERMS_LINK})"

    with steps("докрутить страницу до конца и дождаться похожих товаров",
               f"{PDP_RELATED} виден, заголовок {PDP_RELATED_TITLE} непустой, карточки есть"):
        shop.scroll_to_bottom()
        assert shop.wait_until(lambda: page.locator(PDP_RELATED).count() > 0, 25), \
            (f"после прокрутки так и не появился {PDP_RELATED} — витрина подгружает его "
             "со вторым запросом к каталогу")
        related = page.locator(PDP_RELATED).first
        related.scroll_into_view_if_needed()
        assert related.is_visible(), f"{PDP_RELATED} есть в DOM, но не виден"
        title = page.locator(PDP_RELATED_TITLE).first.inner_text().strip()
        assert title, f"заголовок {PDP_RELATED_TITLE} пустой"
        cards = page.locator(PDP_RELATED_CARDS).count()
        assert cards >= 1, f"в {PDP_RELATED} ни одной карточки товара ({PDP_RELATED_CARDS})"
