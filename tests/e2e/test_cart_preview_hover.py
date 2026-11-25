"""E2E-9. Предпросмотр корзины в шапке открывается при наведении и показывает её содержимое

Тест идёт по **живому стэнду** ``https://test.tamir.rs``: открывает карточку товара,
наводится на корзину в шапке до и после добавления позиции и сверяет содержимое
выпадающей панели. Нюанс стэнда (проверено прогоном): диалог «Proizvod je dodat u
korpu» модальный и перекрывает шапку — пока он открыт, предпросмотр по наведению не
открывается, поэтому перед наведением диалог закрывается кнопкой «Nastavi kupovinu».
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_ADDED_CONTINUE,
    CART_ADDED_DIALOG,
    CART_PREVIEW,
    CART_PREVIEW_EMPTY,
    CART_PREVIEW_NAME,
    CART_PREVIEW_ROW,
    CART_PREVIEW_WRAP,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Корзины, оставшиеся у теста (guest-корзина живёт в localStorage браузера, тест её не оформляет).
_LEFTOVER_CARTS: list[str] = []

_PRODUCT_FIELDS = (
    "id,handle,title,variants.id,variants.title,variants.manage_inventory,"
    "variants.allow_backorder,+variants.inventory_quantity"
)


def _pick_in_stock(api_client: MedusaStoreClient, need: int, exclude: tuple[str, ...] = ()) -> str | None:
    """Хэндл товара стэнда с остатком не меньше ``need`` (исключая ``exclude``).

    На стэнде почти у всех вариантов остаток 1 — берём товар с запасом, чтобы
    конкурентный прогон не обнулил его посреди теста.
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


@pytest.fixture(scope="module", autouse=True)
def _cleanup_guest_carts(api_client: MedusaStoreClient):
    """Удаляет содержимое корзин, оставшихся от прерванного теста, — не мусорить на стэнде."""
    yield
    for cart_id in _LEFTOVER_CARTS:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — чистка не должна перекрывать результат теста
            pass


def test_cart_preview_opens_on_hover(page, steps, api_client: MedusaStoreClient):
    """Предпросмотр корзины в шапке открывается при наведении и показывает содержимое корзины."""
    shop = Storefront(page)

    with steps("открыть каталог и карточку товара", "открылась страница товара, в шапке есть корзина"):
        handle = _pick_in_stock(api_client, 1)
        if not handle:
            pytest.skip("на стэнде нет товаров в остатке — нечего добавлять в корзину")
        shop.goto_catalog()
        shop.open_product(f"/rs/products/{handle}")
        assert page.locator(CART_PREVIEW_WRAP).count() > 0, "в шапке нет обёртки корзины, открывающей предпросмотр"
        assert page.locator(CART_PREVIEW).count() == 0, "до наведения предпросмотр корзины должен быть закрыт"

    with steps("навести курсор на корзину в шапке при пустой корзине", "панель открылась и сообщает, что корзина пуста"):
        page.locator(CART_PREVIEW_WRAP).first.hover()
        assert shop.wait_until(lambda: page.locator(CART_PREVIEW).count() > 0, 10.0), \
            "предпросмотр корзины не открылся при наведении"
        assert shop.wait_until(lambda: page.locator(CART_PREVIEW_EMPTY).count() > 0, 10.0), \
            "в предпросмотре нет сообщения о пустой корзине"
        assert page.locator(CART_PREVIEW_ROW).count() == 0, "в предпросмотре пустой корзины есть позиции"

    with steps("добавить товар в корзину", "витрина получила POST /line-items, id корзины сохранён"):
        shop.add_to_cart()
        cart_id = shop.cart_id()
        assert cart_id, "после добавления позиции id корзины не появился в localStorage"
        _LEFTOVER_CARTS.append(cart_id)

    with steps("закрыть диалог «товар добавлен в корзину»", "диалог закрыт, страница товара на месте"):
        page.locator(CART_ADDED_CONTINUE).first.click()
        assert shop.wait_until(lambda: page.locator(CART_ADDED_DIALOG).count() == 0, 10.0), \
            "диалог «товар добавлен» не закрылся по кнопке «Nastavi kupovinu»"
        assert "/products/" in page.url, "после закрытия диалога должны оставаться на странице товара"

    with steps("навести курсор на корзину в шапке снова", "панель показывает добавленный товар и сумму"):
        page.locator(CART_PREVIEW_WRAP).first.hover()
        assert shop.wait_until(lambda: page.locator(CART_PREVIEW_ROW).count() > 0, 10.0), \
            "в предпросмотре не появилась позиция корзины после наведения"
        assert page.locator(CART_PREVIEW_EMPTY).count() == 0, "предпросмотр по-прежнему считает корзину пустой"
        name = page.locator(CART_PREVIEW_NAME).first.inner_text().strip()
        expected = shop.product_title().lower()
        assert expected in name.lower(), \
            f"в предпросмотре нет названия товара со страницы: {name!r} vs {expected!r}"
        panel_text = page.locator(CART_PREVIEW).first.inner_text()
        assert "Ukupno" in panel_text, f"в предпросмотре нет строки суммы: {panel_text[:200]!r}"

    with steps("кликнуть мимо панели предпросмотра", "панель закрылась, страница не уехала"):
        page.mouse.click(10, 400)
        assert shop.wait_until(lambda: page.locator(CART_PREVIEW).count() == 0, 10.0), \
            "предпросмотр не закрылся по клику вне панели"
        assert "/products/" in page.url, "клик вне панели ушёл с страницы товара"
