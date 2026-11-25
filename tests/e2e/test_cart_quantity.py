"""E2E-10. Изменение количества позиции в корзине: кнопка «+» увеличивает, «−» уменьшает

Тест идёт по **живому стэнду** ``https://test.tamir.rs``. Берёт из API товар с
остатком не меньше 3 (на стэнде почти у всех вариантов остаток 1), добавляет его
в корзину и меняет количество степпером на странице корзины: «−» неактивна при
одной позиции, «+» доводит до 3, «−» возвращает к 2. Каждая смена количества —
это ``POST .../line-items`` (проверено прогоном), и шаг ждёт именно ответ витрины.
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_HEADING,
    CART_LINE,
    CART_LINE_TITLE,
    CART_QTY_VALUE,
    QTY_BUTTON,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Корзины, оставшиеся у теста (guest-корзина живёт в localStorage браузера, тест её не оформляет).
_LEFTOVER_CARTS: list[str] = []

_PRODUCT_FIELDS = (
    "id,handle,title,variants.id,variants.title,variants.manage_inventory,"
    "variants.allow_backorder,+variants.inventory_quantity"
)


def _pick_in_stock(api_client: MedusaStoreClient, need: int) -> tuple[str, str] | None:
    """Хэндл и заголовок товара стэнда с остатком не меньше ``need``.

    На стэнде почти у всех вариантов остаток 1 — берём товар с запасом, иначе
    конкурентный прогон или чужой тест обнулит остаток посреди проверки «+».
    """
    body = api_client._get(f"/store/products?limit=50&country_code=rs&fields={_PRODUCT_FIELDS}")
    for product in body.get("products") or []:
        handle = product.get("handle") or ""
        if not handle:
            continue
        for variant in product.get("variants") or []:
            stock = variant.get("inventory_quantity")
            if stock is not None and stock >= need:
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


def test_cart_quantity_increase_decrease(page, steps, api_client: MedusaStoreClient):
    """«+» в корзине увеличивает количество позиции, «−» уменьшает; при одной позиции «−» заблокирована."""
    shop = Storefront(page)

    with steps("открыть каталог и выбрать товар с остатком не меньше 3",
               "через API найден товар с достаточным остатком"):
        picked = _pick_in_stock(api_client, 3)
        if not picked:
            pytest.skip("на стэнде нет товара с остатком >= 3 — количество менять не на чём")
        handle, title = picked
        shop.goto_catalog()

    with steps("открыть карточку товара и добавить одну позицию в корзину",
               "витрина получила POST /line-items, в localStorage есть id корзины"):
        shop.open_product(f"/rs/products/{handle}")
        shop.add_to_cart()
        cart_id = shop.cart_id()
        assert cart_id, "после добавления позиции id корзины не появился в localStorage"
        _LEFTOVER_CARTS.append(cart_id)

    with steps("открыть корзину", "на странице корзины ровно одна позиция с количеством 1"):
        shop.goto_cart()
        assert shop.wait_for_cart_lines(1), "в корзине не появилась добавленная позиция"
        line = page.locator(CART_LINE).first
        assert line.locator(QTY_BUTTON).count() == 2, "у позиции нет степпера количества из двух кнопок"
        assert line.locator(CART_QTY_VALUE).first.inner_text().strip() == "1", \
            "после добавления количество позиции не равно 1"

    with steps("посмотреть на кнопку уменьшения при одной позиции",
               "кнопка «−» заблокирована — в корзине не бывает нуля позиции"):
        minus = line.locator(QTY_BUTTON).first
        assert minus.is_disabled(), "кнопка «−» активна при количестве 1 — можно удалить позицию молча"

    with steps("увеличить количество кнопки «+» до трёх",
               "витрина приняла POST /line-items, в корзине позиции и на степпере 2 и затем 3"):
        shop.increase_cart_quantity(0, 2)
        shop.increase_cart_quantity(0, 3)
        assert line.locator(CART_QTY_VALUE).first.inner_text().strip() == "3", \
            "степпер показывает не 3 после двух нажатий «+»"

    with steps("уменьшить количество кнопкой «−»",
               "витрина приняла POST /line-items с количеством 2, степпер тоже показывает 2"):
        with page.expect_response(
            lambda r: "/line-items" in r.url and r.request.method == "POST", timeout=30_000
        ):
            line.locator(QTY_BUTTON).first.click()
        assert shop.wait_until(
            lambda: line.locator(CART_QTY_VALUE).first.inner_text().strip() == "2", 10.0
        ), "степпер не показал 2 после нажатия «−»"
        assert not minus.is_disabled(), "кнопка «−» заблокирована при количестве 2"

    with steps("сверить позицию после изменения количества",
               "позиция та же (товар из карточки), корзина по-прежнему с одной позицией"):
        assert page.locator(CART_LINE).count() == 1, "изменение количества завело в корзину вторую позицию"
        assert title.lower() in line.locator(CART_LINE_TITLE).first.inner_text().lower(), \
            "у позиции с изменённым количеством пропало название товара из карточки"
        assert page.locator(CART_HEADING).count() > 0, "после степпера ушли со страницы корзины"
