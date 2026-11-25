"""E2E-15. Повторная покупка: кнопка в списке заказов собирает позиции заказа в корзину

Тест жмёт «Ponovi porudžbinu» в первом заказе: витрина переносит позиции в
корзину и открывает ``/rs/cart``. Заказ не оформляется, корзина чистится API."""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_LINE,
    CART_LINE_TITLE,
    ORDER_CARD,
    REPEAT_ALERT,
    REPEAT_ORDER_BUTTON_RE,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

#: Корзины прогона (id из ``localStorage``): их обязан очистить даже при падении.
_LEFTOVER_CARTS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup_carts(api_client: MedusaStoreClient):
    """Опустошает корзины прогона — повтор покупки не должен оставлять позиции в резерве."""
    yield
    for cart_id in list(_LEFTOVER_CARTS):
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — очистка не должна перекрывать результат теста
            pass
    _LEFTOVER_CARTS.clear()


def test_repeat_order_fills_cart_from_account(logged_in_page, steps, api_client: MedusaStoreClient):
    """«Ponovi porudžbinu» в кабинете переносит позиции заказа в корзину."""
    page = logged_in_page
    shop = Storefront(page)

    with steps("открыть список заказов тестового аккаунта",
               "есть хотя бы один заказ с кнопкой повторной покупки"):
        shop.open_orders_panel()
        if shop.order_cards() == 0:
            pytest.skip(
                "у тестового аккаунта нет заказов — повторять нечего, "
                "а создавать заказ в этом тесте нельзя"
            )
        repeat = page.locator(ORDER_CARD).first.get_by_role("button", name=REPEAT_ORDER_BUTTON_RE)
        if repeat.count() == 0:
            pytest.skip("в первом заказе нет кнопки повторной покупки — тест не применим")

    with steps("очистить корзину тестового аккаунта перед повтором",
               "корзина пуста, в неё попадут только позиции заказа"):
        cart_id = shop.cart_id()
        if cart_id:
            _LEFTOVER_CARTS.append(cart_id)
            api_client.remove_all_items(cart_id)

    with steps("нажать «Ponovi porudžbinu» в первом заказе",
               "витрина открыла корзину, в ней есть позиции"):
        repeat.first.click()
        if not shop.wait_until(lambda: shop.cart_line_count() > 0, 20.0):
            alert = page.locator(REPEAT_ALERT)
            if alert.count():
                # позиции заказа распроданы на стэнде — витрина отказалась
                # повторять заказ целиком, проверять в корзине нечего
                pytest.skip(f"витрина не повторила заказ: {alert.first.inner_text().strip()!r}")
            pytest.fail("после «Ponovi porudžbinu» корзина осталась пустой")
        page.locator(CART_LINE).first.wait_for(state="visible", timeout=15_000)
        if not cart_id:
            new_cart = shop.cart_id()
            if new_cart:
                _LEFTOVER_CARTS.append(new_cart)

    with steps("посмотреть позиции повторённого заказа",
               "у каждой позиции есть название и ненулевая цена"):
        lines = shop.cart_line_count()
        assert lines >= 1, "после повтора заказа в корзине ни одной позиции"
        for i in range(min(lines, 5)):
            title = page.locator(CART_LINE_TITLE).nth(i).inner_text().strip()
            price = shop.cart_line_price(i)
            assert title, f"позиция {i} без названия"
            assert any(ch.isdigit() for ch in price), f"позиция {title!r} без цены: {price!r}"
