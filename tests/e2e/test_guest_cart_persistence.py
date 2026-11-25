"""E2E-12. Гостевая корзина сохраняется после перезагрузки страницы и перехода по каталогу

Тест идёт по **живому стэнду** ``https://test.tamir.rs`` без авторизации: гость
кладёт товар в корзину, перезагружает страницу и уходит по маршруту
каталог → карточка товара → корзина. Идентификатор гостевой корзины живёт в
``localStorage`` (ключ ``tb_cart_id``) и позиции подтягиваются по нему — ни на
шаге перехода, ни после перезагрузки корзина не обнуляется и не подменяется новой.

Нюанс стэнда (проверено прогоном): после перезагрузки корзина сначала рендерится
пустой (SSR не знает id из localStorage) и позиции дорисовывает клиент — поэтому
шаги ждут позиции через ``wait_for_cart_lines``, а не сверяют сразу.
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_LINE,
    CART_PREVIEW_ROW,
    CART_PREVIEW_WRAP,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Корзины, оставшиеся у теста (guest-корзина живёт в localStorage браузера, тест её не оформляет).
_LEFTOVER_CARTS: list[str] = []

_PRODUCT_FIELDS = "id,handle,title,variants.id,variants.manage_inventory,variants.allow_backorder"


def _pick_buyable_handle(api_client: MedusaStoreClient) -> tuple[str, str] | None:
    """Хэндл и заголовок первого покупаемого товара стэнда (через API)."""
    body = api_client._get(f"/store/products?limit=50&country_code=rs&fields={_PRODUCT_FIELDS}")
    for product in body.get("products") or []:
        handle = product.get("handle") or ""
        if handle and any(variant.get("id") for variant in product.get("variants") or []):
            return handle, product.get("title") or handle
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


def test_guest_cart_survives_reload(page, steps, api_client: MedusaStoreClient):
    """Гостевая корзина переживает перезагрузку страницы и переходы каталог → товар → корзина."""
    shop = Storefront(page)

    with steps("добавить товар в корзину без авторизации",
               "витрина получила POST /line-items, id корзины появился в localStorage"):
        picked = _pick_buyable_handle(api_client)
        if not picked:
            pytest.skip("на стэнде нет покупаемых товаров — корзину нечем наполнить")
        handle, title = picked
        shop.goto_catalog()
        shop.open_product(f"/rs/products/{handle}")
        shop.add_to_cart()
        cart_id = shop.cart_id()
        assert cart_id, "после добавления позиции id корзины не появился в localStorage"
        _LEFTOVER_CARTS.append(cart_id)

    with steps("перезагрузить страницу корзины",
               "позиция на месте и id корзины в localStorage тот же, что до перезагрузки"):
        shop.goto_cart()
        assert shop.wait_for_cart_lines(1), "после открытия корзины позиция в ней не появилась"
        page.reload()
        assert shop.wait_for_cart_lines(1), \
            "после перезагрузки страницы позиция пропала из корзины"
        assert shop.cart_id() == cart_id, \
            "витрина подменила гостевую корзину новой вместо сохранённой"
        assert title.lower() in page.locator(CART_LINE).first.inner_text().lower(), \
            "после перезагрузки в корзине стоит не тот товар"

    with steps("пройти по каталогу: список товаров, карточка другого товара, снова корзина",
               "после каждого перехода позиция и id корзины остаются прежними"):
        shop.goto_catalog()
        assert page.locator(CART_PREVIEW_WRAP).count() > 0, "в шапке каталога нет корзины"
        shop.open_product(f"/rs/products/{handle}")
        shop.goto_cart()
        assert shop.wait_for_cart_lines(1), "после перехода по каталогу позиция пропала из корзины"
        assert shop.cart_id() == cart_id, \
            "после перехода по каталогу id корзины сменился — гостевая корзина потеряна"
        assert title.lower() in page.locator(CART_LINE).first.inner_text().lower(), \
            "после перехода по каталогу в корзине не тот товар"

    with steps("навести курсор на корзину в шапке",
               "предпросмотр показывает ту же позицию — состояние корзины общее для страниц"):
        page.locator(CART_PREVIEW_WRAP).first.hover()
        assert shop.wait_until(lambda: page.locator(CART_PREVIEW_ROW).count() == 1, 10.0), \
            "в предпросмотре корзины нет сохранённой позиции"
        assert title.lower() in page.locator(CART_PREVIEW_ROW).first.inner_text().lower(), \
            "предпросмотр показывает не тот товар, что лежит в корзине"
