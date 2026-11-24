"""E2E-3. Зарегистрированный покупатель делает заказ: вход → товар → корзина → оформление → заказ в кабинете.

Сценарий идёт на **живом тестовом стэнде** ``https://test.tamir.rs`` (без моков) и проверяет
главное отличие от гостевого пути: заказ **привязан к аккаунту** — его номер виден в списке
заказов ``div#account-orders`` и совпадает с ``display_id`` из админ-API. Оплата —
«наложенный платёж» (``cod``), внешних платёжных учётных данных не требуется.

Учётные данные — **постоянный тестовый аккаунт стэнда**, который читаем из переменных
окружения ``QA_WEB_EMAIL`` / ``QA_WEB_PASSWORD`` (значения нигде не логируются). Если
переменные не заданы — тест скипается. Вход делается **ровно один раз за тест**: стенд
разрешает не больше 5 входов в час, повторных попыток быть не должно. Сам аккаунт тест
НЕ удаляет (его снимают руками), а за собой отменяет тестовый заказ через админ-API и
снимает резерв товара из незавершённых корзин.
"""

from __future__ import annotations

import os
import re

import pytest

from qa.config import load_config
from qa.medusa_admin import MedusaAdminClient
from qa.ui import ACCOUNT_ORDERS, CONFIRMED_RE, Storefront

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Данные, созданные этим модулем в браузере (корзина живёт в localStorage страницы,
# заказ — на стэнде). Список нужны, чтобы уборка догнала их даже при падении теста.
_LEFTOVER_CARTS: list[str] = []
_ORDERS_TO_CANCEL: list[str] = []

BUYER = {
    "firstName": "QA",
    "lastName": "Automation",
    "phone": "+381643210987",
    "address1": "Knez Mihailova 5",
    "postalCode": "11000",
    "city": "Beograd",
}


@pytest.fixture(scope="module")
def test_account() -> tuple[str, str]:
    """Постоянный тестовый аккаунт стэнда из ``QA_WEB_EMAIL`` / ``QA_WEB_PASSWORD``."""
    load_config()  # подтягивает .env в os.environ (в CI переменные задаёт раннер)
    email = (os.environ.get("QA_WEB_EMAIL") or "").strip()
    password = os.environ.get("QA_WEB_PASSWORD") or ""
    if not email or not password:
        pytest.skip("нет тестового аккаунта: QA_WEB_EMAIL / QA_WEB_PASSWORD не заданы")
    return email, password


@pytest.fixture(scope="module", autouse=True)
def _cleanup_test_data(api_client, admin_client: MedusaAdminClient):
    """Убирает тестовые данные модуля: отменяет незакрытые заказы и снимает резерв корзин.

    Тестовый аккаунт не трогает — он постоянный на стэнде и чистится руками.
    """
    yield
    for order_id in list(_ORDERS_TO_CANCEL):
        try:
            admin_client.cancel_order(order_id)
        except Exception:  # noqa: BLE001 — уборка не должна ронять тест
            pass
    _ORDERS_TO_CANCEL.clear()
    for cart_id in _LEFTOVER_CARTS:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — корзина могла уйти в заказ
            pass
    _LEFTOVER_CARTS.clear()


def test_registered_customer_places_order(
    page, steps, admin_client: MedusaAdminClient, test_account: tuple[str, str]
):
    """Зарегистрированный покупатель: вход → карточка → корзина → заказ → он в личном кабинете."""
    email, password = test_account
    shop = Storefront(page)

    with steps("Войти в личный кабинет", "открыт аккаунт"):
        # Один-единственный вход за тест: у стэнда лимит 5 входов в час.
        shop.login(email, password)
        assert shop.wait_until(shop.is_logged_in, 10.0), (
            "вход в личный кабинет не выполнен: проверьте QA_WEB_EMAIL / QA_WEB_PASSWORD "
            "(повторный вход не делаем — стенд разрешает не больше 5 входов в час)"
        )

    with steps("Открыть каталог и выбрать первый товар", "карточка товара открывается"):
        # Маршруты англоязычные, URL после rewrite может выглядеть «старым» — верим содержимому.
        shop.goto_catalog()
        card = shop.first_product_card()
        assert card.count() > 0, "в каталоге нет ни одной карточки товара"
        card.click()
        page.locator("h1").first.wait_for(state="visible", timeout=30_000)

    with steps("Проверить карточку товара", "название, цена и наличие на месте"):
        pdp_title = shop.product_title()
        assert pdp_title, "у карточки товара пустой заголовок"
        price = shop.product_price()
        assert re.search(r"\d", price), f"в карточке нет цены: {price!r}"
        assert shop.product_stock_text(), f"в карточке нет сведений о наличии ({pdp_title!r})"

    with steps("Добавить товар в корзину", "в корзине одна позиция"):
        shop.add_to_cart()
        shop.goto_cart()
        assert shop.cart_line_count() == 1, f"в корзине {shop.cart_line_count()} позиции, ожидалась 1"

    with steps("Перейти к оформлению заказа", "открыта форма оформления"):
        cart_id = shop.cart_id()
        if cart_id:
            _LEFTOVER_CARTS.append(cart_id)
        shop.go_to_checkout()
        assert page.locator("#checkout-lastName").count() > 0, "формы получателя на оформлении нет"

    with steps("Заполнить данные и выбрать способ доставки", "доставка предложена"):
        # Почта — почта аккаунта (значение в сообщения об ошибках не печатаем).
        shop.set_checkout_details({**BUYER, "email": email})
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
        order_id = shop.order_id_from_url()
        if order_id:
            _ORDERS_TO_CANCEL.append(order_id)

    with steps("Открыть заказы в личном кабинете", "созданный заказ есть в списке"):
        shop.open_account_orders()
        orders_block = page.locator(ACCOUNT_ORDERS)
        assert orders_block.count() > 0, "в личном кабинете нет блока заказов (div#account-orders)"
        account_orders = orders_block.inner_text()
        assert number in account_orders, (
            f"заказ №{number} не найден в списке заказов аккаунта: {account_orders[:300]!r}"
        )

    with steps("Сверить заказ с админкой и убрать тестовые данные", "заказ найден и отменён"):
        # complete в витринном API не отдаёт metadata — данные заказа перечитываем из админки.
        order = admin_client.get_order(order_id)
        assert order, f"заказ {order_id} не найден в админ-API: {admin_client.last_error}"
        assert str(order.get("display_id")) == number, (
            f"номер заказа в кабинете {number}, в админке {order.get('display_id')}"
        )
        assert admin_client.cancel_order(order_id), f"не удалось отменить тестовый заказ {order_id}"
        if order_id in _ORDERS_TO_CANCEL:
            _ORDERS_TO_CANCEL.remove(order_id)
        if cart_id:
            _LEFTOVER_CARTS.remove(cart_id)
