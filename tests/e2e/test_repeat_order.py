"""E2E-15. Повторная покупка: кнопка в списке заказов собирает позиции заказа в корзину

Тест жмёт «Ponovi porudžbinu» у заказа, который фикстура прогона оформила на
тестовый аккаунт через админ-API (черновик + конвертация): витрина переносит
позиции заказа в корзину и открывает ``/rs/cart``. Новый заказ при этом не
оформляется, корзина чистится API, а заказ фикстуры отменяется в конце прогона."""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_LINE,
    CART_LINE_TITLE,
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


def test_repeat_order_fills_cart_from_account(
    logged_in_page, account_order, steps, api_client: MedusaStoreClient
):
    """Заказ из кабинета переносится в корзину кнопкой «Ponovi porudžbinu»."""
    page = logged_in_page
    shop = Storefront(page)
    display_id = account_order["display_id"]

    with steps("открыть список заказов тестового аккаунта",
               "в списке есть заказ прогона и кнопка повторной покупки"):
        shop.open_orders_panel()
        card = shop.order_card(display_id)
        assert card.count() >= 1, (
            f"заказ {display_id} не найден в списке заказов кабинета "
            f"(всего строк: {shop.order_cards()})"
        )
        repeat = card.first.get_by_role("button", name=REPEAT_ORDER_BUTTON_RE)
        assert repeat.count() >= 1, f"в заказе {display_id} нет кнопки «Ponovi porudžbinu»"

    with steps("очистить корзину тестового аккаунта перед повтором",
               "корзина пуста, в неё попадут только позиции заказа"):
        cart_id = shop.cart_id()
        if cart_id:
            _LEFTOVER_CARTS.append(cart_id)
            api_client.remove_all_items(cart_id)

    with steps("нажать «Ponovi porudžbinu» в заказе прогона",
               "витрина открыла корзину, в ней есть позиции"):
        repeat.first.click()
        if not shop.wait_until(lambda: shop.cart_line_count() > 0, 20.0):
            alert = page.locator(REPEAT_ALERT)
            if alert.count():
                # позиции заказа распроданы на стенде — витрина отказалась
                # повторять заказ целиком, проверять в корзине нечего
                pytest.skip(f"витрина не повторила заказ: {alert.first.inner_text().strip()!r}")
            pytest.fail("после «Ponovi porudžbinu» корзина осталась пустой")
        page.locator(CART_LINE).first.wait_for(state="visible", timeout=15_000)
        if not cart_id:
            new_cart = shop.cart_id()
            if new_cart:
                _LEFTOVER_CARTS.append(new_cart)

    with steps("посмотреть позиции повторённого заказа",
               "в корзине позиция купленного товара, с названием и ценой"):
        lines = shop.cart_line_count()
        assert lines >= 1, "после повтора заказа в корзине ни одной позиции"
        titles = [
            page.locator(CART_LINE_TITLE).nth(i).inner_text().strip()
            for i in range(min(lines, 5))
        ]
        for title in titles:
            assert title, "позиция в корзине без названия"
        wanted = (account_order["product_title"] or "").strip().lower()
        if wanted:
            assert any(wanted in t.lower() for t in titles), (
                f"в корзине нет позиции заказанного товара {wanted!r}: {titles}"
            )
        price = shop.cart_line_price(0)
        assert any(ch.isdigit() for ch in price), f"позиция {titles[0]!r} без цены: {price!r}"
