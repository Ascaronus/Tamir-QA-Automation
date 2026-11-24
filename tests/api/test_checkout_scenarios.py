"""Сценарии бэкенда: заказ покупателя проходит весь путь и доходит до лавки.

Это не проверка «эндпоинт отвечает 200».  Здесь тест делает то же, что делает
человек: берёт товар из каталога, кладёт его в корзину, меняет количество,
выбирает доставку, оформляет заказ, а затем проверяет три вещи — что заказ
виден покупателю (витринный API, которым пользуется страница заказа), что он
виден продавцу (админ-API) и что тестовые данные за собой убраны.

Данных много не нужно: за прогон оформляется ровно один заказ, он же и
отменяется в конце.  Незавершённые корзины освобождают товар в финальном
``teardown`` — иначе они держат резерв на складе стэнда.

Шаги описаны фикстурой ``steps``: они попадают и в Allure, и в текст тест-кейса
в Kiwi TCMS (см. ``qa/steps.py``).
"""

from __future__ import annotations

import allure
import pytest

from qa.medusa_store import (
    StoreApiError,
    first_line,
    pick_buyable_variant,
    product_price,
    variant_stock,
)

pytestmark = [
    pytest.mark.api,
    pytest.mark.scenario,
    allure.feature("Заказ покупателя"),
]

TEST_EMAIL = "qa-checkout@example.com"
ADDRESS = {
    "country_code": "rs",
    "first_name": "QA",
    "last_name": "Automation",
    "address1": "Test 1",
    "postal_code": "11070",
    "city": "Beograd",
}


@allure.story("Гость оформляет заказ")
@allure.severity(allure.severity_level.BLOCKER)
def test_guest_order_reaches_the_shop(
    api_client, admin_client, run_tag, steps, _cleanup_carts
):
    """Покупатель без регистрации получает заказ, а лавка видит его у себя."""
    store = api_client
    if admin_client is None:
        pytest.skip("нет доступа к админ-API (MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD)")

    with steps("Открываем витрину и берём регион доставки",
               "есть регион с валютой и страной доставки"):
        region = store.first_region()
        assert region.get("currency_code"), "у региона должна быть валюта"
        assert region.get("countries"), "у региона должны быть страны доставки"

    with steps("Выбираем в каталоге товар, который можно купить",
               "у товара есть цена и остаток больше одного"):
        product, variant = pick_buyable_variant(store.products(limit=30), min_stock=2)
        price = product_price(variant)
        assert price and price > 0, f"у товара {product.get('handle')} нет цены"
        allure.attach(
            f"{product.get('title')} / {variant.get('title')}: "
            f"{price} {region['currency_code']}",
            "что покупаем",
            allure.attachment_type.TEXT,
        )

    with steps("Создаём корзину покупателя",
               "корзина создана и помечена меткой тестового прогона"):
        cart = store.create_cart(str(region["id"]), run_tag=run_tag, email=TEST_EMAIL)
        cart_id = str(cart["id"])
        _cleanup_carts.append(cart_id)
        assert (cart.get("metadata") or {}).get("qa_run") == run_tag, (
            "корзина не помечена прогоном — её не уберёт уборка"
        )

    with steps("Кладём один товар в корзину", "в корзине одна позиция, сумма равна цене"):
        cart = store.add_item(cart_id, str(variant["id"]), 1)
        line = first_line(cart)
        assert cart.get("item_total") == price, (
            f"корзина {cart.get('item_total')} != цена товара {price}"
        )

    with steps("Меняем количество позиции на два", "сумма позиций выросла вдвое"):
        updated = store.set_quantity(cart_id, str(line["id"]), 2)
        assert first_line(updated)["quantity"] == 2, "количество не изменилось"
        assert updated.get("item_total") == price * 2, (
            f"сумма {updated.get('item_total')} != {price} x 2"
        )

    with steps("Выбираем способ доставки", "доставка закрепилась в корзине"):
        options = store.shipping_options(cart_id)
        assert options, "для корзины нет ни одного способа доставки"
        cart = store.select_shipping(cart_id, str(options[0]["id"]))
        assert cart.get("shipping_methods"), "способ доставки не закрепился в корзине"

    with steps("Указываем адрес получателя", "итог = позиции + доставка"):
        cart = store.set_shipping_address(cart_id, ADDRESS)
        expected_total = cart.get("item_total") + (cart.get("shipping_total") or 0)
        assert cart.get("total") == expected_total, (
            f"итог {cart.get('total')} != позиции + доставка ({expected_total})"
        )

    with steps("Оформляем заказ", "магазин вернул заказ с человеческим номером"):
        order, failure = store.complete_checkout(cart_id)
        assert failure is None, f"оформление не удалось: {failure}"
        assert order and order.get("id"), "в ответе нет заказа"

    order_id = str(order["id"])
    assert order.get("display_id"), "у заказа нет человеческого номера"
    assert order.get("total") == expected_total, "итог заказа разошёлся с корзиной"
    # Заказ оформлен намеренно и отменён ниже; корзина больше не нужна.
    _cleanup_carts.remove(cart_id)

    with steps("Покупатель открывает свой заказ",
               "заказ читается по номеру: тот же номер, товар и количество"):
        seen = store.get_order(order_id)
        assert seen.get("id") == order_id, "заказ не читается по своему номеру"
        assert seen.get("display_id") == order.get("display_id")
        assert seen.get("email") == TEST_EMAIL, "в заказе потерялась почта покупателя"
        assert len(seen.get("items") or []) == 1, "в заказе не та набор позиций"
        # ``quantity`` из ``fields=`` на этой сборке не отдаётся, поэтому
        # количество смотрим в полной выборке заказа.
        full = store.get_order(order_id, fields="")
        assert (full.get("items") or [{}])[0].get("quantity") == 2
        # Ответ ``complete`` метки не носит (проверено на стэнде), поэтому
        # метку прогона читаем заказом: по ней работает уборка после набегов.
        assert (seen.get("metadata") or {}).get("qa_run") == run_tag, (
            "метка прогона не перешла из корзины в заказ — её не уберёт уборка"
        )

    with steps("Продавец находит заказ в админке и отменяет его",
               "заказ виден лавке и после отмены имеет статус «отменён»"):
        found = admin_client.get_order(order_id)
        assert found.get("display_id") == order.get("display_id"), (
            "лавка не видит заказ покупателя"
        )
        assert admin_client.cancel_order(order_id), (
            f"не удалось отменить заказ: {admin_client.last_error}"
        )
        assert admin_client.get_order(order_id).get("status") == "canceled", (
            "заказ остался активным после отмены"
        )

    with steps("Покупателю уходит письмо о заказе",
               "в почтовом ящике стэнда появилось письмо на адрес покупателя"):
        # На стэнде почта собирается в Mailpit; это не проверка текста письма,
        # а проверка, что уведомление о заказе вообще появляется.
        from qa.mailbox import Mailbox

        box = Mailbox()
        if not box.configured:
            pytest.skip("MAILPIT_URL не задан — проверять письмо негде")
        message = box.wait_for_order_email(TEST_EMAIL, timeout=90)
        assert message is not None, "письмо о заказе не пришло"


@allure.story("Склад не даёт купить больше, чем есть")
@allure.severity(allure.severity_level.CRITICAL)
def test_too_much_of_a_product_is_rejected(api_client, run_tag, steps, _cleanup_carts):
    """Попытка взять больше остатка заканчивается понятным отказом."""
    store = api_client

    with steps("Смотрим остаток товара на складе",
               "витрина отдаёт число доступных штук"):
        product, variant = pick_buyable_variant(store.products(limit=30), min_stock=1)
        stock = variant_stock(variant)
        if stock is None:
            pytest.skip("витрина не отдаёт остаток товара — проверять не от чего")

    with steps("Пытаемся положить в корзину больше, чем есть на складе",
               "магазин отказывает и не добавляет позицию"):
        cart = store.create_cart(str(store.first_region()["id"]), run_tag=run_tag)
        cart_id = str(cart["id"])
        _cleanup_carts.append(cart_id)
        failure = None
        try:
            cart = store.add_item(cart_id, str(variant["id"]), stock + 50)
        except StoreApiError as exc:  # отказ на уровне транспорта
            failure = str(exc)

        if failure is not None:
            assert "inventory" in failure.lower() or "остат" in failure.lower(), failure
        else:
            # Medusa v2 отвечает кодом insufficient_inventory и не кладёт товар.
            assert not (cart or {}).get("items"), (
                f"сверх остатка добавили {len((cart or {}).get('items') or [])} позиций"
            )

    with steps("Перечитываем корзину",
               "после отказа корзина пуста и к оплате ноль"):
        refreshed = store.get_cart(cart_id)
        assert not refreshed.get("items"), (
            "после отказа в корзине осталась позиция — покупатель увидит то, чего нет"
        )
        assert refreshed.get("total") in (None, 0), "пустая корзина показывает оплату"


@allure.story("Товары каталога открываются на витрине")
@allure.severity(allure.severity_level.NORMAL)
def test_every_catalog_product_has_a_working_page(api_client, web_client, config, steps):
    """Ссылка витрины строится из ``handle``: битый handle = битая карточка."""
    store = api_client
    checked = 0
    broken: list[str] = []

    with steps("Берём первые товары каталога", "витрина отдаёт товары со ссылкой"):
        products = store.products(limit=8)[:8]
        assert products, "каталог пуст"

    with steps("Открываем карточку каждого товара",
               "все карточки отвечают 200"):
        for product in products:
            handle = str(product.get("handle") or "")
            if not handle:
                broken.append(f"{product.get('id')} без handle")
                continue
            response = web_client.get(f"{config.web_url}/rs/products/{handle}")
            if response.status_code != 200:
                broken.append(f"/rs/products/{handle} -> {response.status_code}")
            checked += 1

    allure.attach(
        f"проверено карточек: {checked}\nбитых: {len(broken)}",
        "итог",
        allure.attachment_type.TEXT,
    )
    assert not broken, "карточки товаров не открываются: " + "; ".join(broken)


@pytest.fixture
def _cleanup_carts(api_client):
    """Убирает резерв товара из корзин, которые тест не довёл до заказа."""
    touched: list[str] = []
    yield touched
    for cart_id in touched:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 - уборка не должна перекрывать результат теста
            pass
