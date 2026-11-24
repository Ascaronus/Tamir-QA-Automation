"""E2E-1. Гость проходит весь путь покупки: каталог → карточка → корзина → оформление.

Сценарий идёт на **живом тестовом стэнде** ``https://test.tamir.rs`` (без моков) и проверяет
то, что видит пользователь: цену и наличие в карточке, состав и пересчёт корзины, поля
оформления, номер заказа на подтверждении. Оплата — «наложенный платёж» (``cod``, провайдер
``pp_system_default``), поэтому внешних платёжных учётных данных не требуется.

За собой тест убирает данные стэнда: тестовый заказ отменяется через админ-API, а позиции
незавершённых корзин снимаются через store-API, чтобы корзина не держала резерв товара.
"""

from __future__ import annotations

import os
import re

import pytest

from qa.medusa_admin import MedusaAdminClient
from qa.medusa_store import MedusaStoreClient
from qa.ui import CONFIRMED_RE, Storefront

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Корзины, созданные в браузере (guest-корзина живёт в localStorage страницы).
_LEFTOVER_CARTS: list[str] = []

BUYER = {
    "firstName": "QA",
    "lastName": "Automation",
    "phone": "+381643210987",
    "address1": "Knez Mihailova 5",
    "postalCode": "11000",
    "city": "Beograd",
}


def _buyer_email() -> str:
    """Почта гостевого заказа по формату, который знает уборка ``scripts/qa_cleanup.py``."""
    tag = os.environ.get("GITHUB_RUN_NUMBER") or f"local{os.getpid()}"
    return f"qa-e2e-{tag}@example.com"


@pytest.fixture(scope="module", autouse=True)
def _cleanup_guest_carts(api_client):
    """Снимает резерв товара, оставшийся от незавершённых корзин этого модуля."""
    yield
    store = MedusaStoreClient(api_client)
    for cart_id in _LEFTOVER_CARTS:
        try:
            store.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — корзина могла уйти в заказ; уборка не роняет тест
            pass


def test_guest_buy_from_catalog_to_order(page, steps, admin_client: MedusaAdminClient):
    """Гость покупает: каталог → карточка → корзина → оформление → номер заказа."""
    shop = Storefront(page)

    with steps("Открыть каталог и выбрать первый товар", "список товаров, карточка открывается"):
        shop.goto_catalog()
        card = shop.first_product_card()
        assert card.count() > 0, "в каталоге нет ни одной карточки товара"
        catalog_title = card.inner_text().strip()
        card.click()
        page.wait_for_url("**/products/**", timeout=30_000)

    with steps("Проверить карточку товара", "название, цена и наличие на месте"):
        pdp_title = shop.product_title()
        assert pdp_title, "у карточки товара пустой заголовок"
        assert pdp_title.split()[0].lower() in catalog_title.lower(), (
            f"карточка не соответствует товару из каталога: {catalog_title!r} ≠ {pdp_title!r}"
        )
        price = shop.product_price()
        assert re.search(r"\d", price), f"в карточке нет цены: {price!r}"
        assert shop.product_stock_text(), f"в карточке нет сведений о наличии ({pdp_title!r})"

    with steps("Добавить товар в корзину", "витрина ответила созданием корзины"):
        shop.add_to_cart()

    with steps("Проверить корзину", "1 позиция с ценой из карточки"):
        shop.goto_cart()
        assert shop.cart_line_count() == 1, f"в корзине {shop.cart_line_count()} позиции, ожидалась 1"
        assert shop.cart_quantity() == 1, f"количество {shop.cart_quantity()}, ожидалось 1"
        assert shop.cart_line_price() == price, (
            f"цена в корзине {shop.cart_line_price()!r} не равна цене из карточки {price!r}"
        )

    with steps("Увеличить количество до 2", "корзина пересчитана"):
        shop.increase_cart_quantity()
        assert shop.cart_quantity() == 2, f"количество в корзине {shop.cart_quantity()}, ожидалось 2"

    with steps("Перейти к оформлению заказа", "форма оформления с данными получателя"):
        cart_id = shop.cart_id()
        if cart_id:
            _LEFTOVER_CARTS.append(cart_id)
        shop.go_to_checkout()
        assert page.locator("#checkout-lastName").count() > 0, "формы получателя на оформлении нет"

    with steps("Заполнить данные и выбрать способ доставки", "доставка предложена"):
        shop.set_checkout_details({**BUYER, "email": _buyer_email()})
        shop.prepare_delivery()
        assert page.locator("input[name=shipping]").count() > 0, "способы доставки не появились"
        page.locator("input[name=shipping]").first.check()
        page.wait_for_timeout(800)

    with steps("Оформить заказ", "подтверждение с номером заказа"):
        shop.place_order()
        text = shop.confirmation_text()
        assert CONFIRMED_RE.search(text), f"вместо подтверждения показано: {text!r}"
        number = shop.order_number()
        assert number, (
            f"в подтверждении нет номера заказа: {page.locator('main').inner_text()[:300]!r}"
        )

    with steps("Сверить заказ с админкой и убрать тестовые данные", "заказ найден и отменён"):
        order_id = shop.order_id_from_url()
        body = admin_client.get(f"/admin/orders/{order_id}?fields=id,display_id,status")
        order = body.get("order") if isinstance(body, dict) else None
        assert order, f"заказ {order_id} не найден в админ-API: {admin_client.last_error}"
        assert str(order.get("display_id")) == number, (
            f"номер заказа в витрине {number}, в админке {order.get('display_id')}"
        )
        assert admin_client.cancel_order(order_id), f"не удалось отменить тестовый заказ {order_id}"
        if cart_id:
            _LEFTOVER_CARTS.remove(cart_id)
