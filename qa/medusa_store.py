"""Витринный API Medusa и шаги, из которых состоит заказ покупателя.

Модуль отвечает на вопрос «как выглядит заказ со стороны бэкенда»: клиент,
который проходит тот же путь, что и человек в браузере, — регион -> товар ->
корзина -> позиция -> доставка -> адрес -> оплата -> ``complete``.  Ответы
разбираются честно, ошибки возвращаются как данные (а не исключениями), а каждый
шаг уходит в Allure, чтобы в отчёте было видно, что именно делал тест.

Проверенный контракт стенда (Medusa 2.13.6):

1. ``POST /store/carts`` (``region_id`` + ``metadata.qa_run``)
2. ``POST /store/carts/{id}/line-items`` (такой же ``variant_id``
   складывается в одну позицию, а не создаёт вторую)
3. ``POST /store/carts/{id}/line-items/{line_id}`` — смена количества
4. ``GET  /store/shipping-options?cart_id=`` -> ``POST /store/carts/{id}/shipping-methods``
   (тело ``{"option_id": ...}``; ``/fulfillment`` на этой сборке отдает 404)
5. ``POST /store/carts/{id}`` (email / адрес доставки)
6. ``POST /store/payment-collections`` (``cart_id``)
7. ``POST /store/payment-collections/{pay_col}/payment-sessions`` (``provider_id``)
8. ``POST /store/carts/{id}/complete`` -> ``{"type": "order", "order": {...}}``
9. ``GET  /store/orders/{order_id}`` — заказ доступен по своему id
   (в Medusa id заказа и есть «секрет» гостя)

Чего на этой сборке нет: ``GET /store/carts``, ``DELETE /store/carts/{id}``,
``PUT /store/carts/{id}/line-items/{id}``, ``POST /store/orders/{id}/confirmation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote as _quote

import allure

from qa.http import QaHttpClient

# Поля варианта со склада. ``inventory_quantity`` витрина отдаёт только когда её
# явно просят в ``fields=``; на части товаров она null — его читают как «нет
# данных», а не как «нулевой остаток».
VARIANT_FIELDS = (
    "id,handle,title,description,status,thumbnail,"
    "variants.id,variants.title,variants.handle,variants.sku,"
    "variants.manage_inventory,variants.allow_backorder,variants.inventory_quantity,"
    "variants.calculated_price.calculated_amount,variants.calculated_price.currency_code"
)

#: ``payment_collections.*`` в ``fields=`` запроса корзины на этой сборке отдает
#: HTTP 500 (проверено), поэтому про них молчим — id платежных сессий читаем из
#: ответов mutation-эндпоинтов.
CART_FIELDS = (
    "id,region_id,currency_code,email,metadata,item_subtotal,item_total,subtotal,"
    "shipping_total,tax_total,total,items.id,items.title,items.quantity,"
    "items.unit_price,items.variant_id,items.metadata,"
    "shipping_methods.id,shipping_methods.amount"
)

#: ``items.quantity`` из ``fields=`` заказа на этой сборке не отдаётся молча
#: (проверено: ``quantity``, ``raw_quantity``, ``items.subtotal`` выпадают,
#: остальные поля приходят), поэтому количество позиций читают заказом без
#: ``fields=`` — там Medusa отдаёт выборку по умолчанию.
ORDER_FIELDS = (
    "id,display_id,email,currency_code,metadata,"
    "items.id,items.title,items.unit_price,items.variant_id,items.product_handle,"
    "shipping_methods.id,shipping_methods.amount,"
    "total,subtotal,tax_total,shipping_total,item_total"
)

PAYMENT_FIELDS = "id,provider_id,region_id"

#: «Ручная» оплата (Cash on Delivery): проходит без внешних платёжных ключей,
#: именно её выбирает витрина на staging (radio ``input[name=payment][value=cod]``).
PREFERRED_PAYMENT_PROVIDER = "pp_system_default"


class StoreApiError(AssertionError):
    """Неожиданный ответ витринного API (падает как обычная проверка)."""


def _body(response: Any) -> dict[str, Any]:
    """Medusa v2 отдаёт ошибочные ответы голым текстом, поэтому без исключений."""
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def unwrap(body: dict[str, Any], key: str) -> dict[str, Any]:
    """Medusa v2 оборачивает объекты (``{"cart": ...}``); на всякий случай и v1."""
    inner = body.get(key)
    return inner if isinstance(inner, dict) else body


def _merge_fields(*parts: str) -> str:
    """Собираем один корректный ``fields=``: без дублей и без родительских полей.

    Medusa не отдаёт ничего, если в списке полей смешаны ``variants.calculated_price``
    и ``variants.calculated_price.calculated_amount``, поэтому дочерние поля у нас
    имеют приоритет.
    """
    seen: list[str] = []
    for part in parts:
        for name in (field.strip() for field in part.split(",")):
            if not name:
                continue
            if any(existing == name or existing.startswith(name + ".") for existing in seen):
                continue
            seen.append(name)
    return ",".join(seen)


@dataclass
class ApiFailure:
    """Честный ответ API, когда вызов ожидаемо отклонён (например, остаток)."""

    status: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"HTTP {self.status} {self.code}: {self.message}"


@dataclass
class CheckoutResult:
    """Итог оформления заказа: именно это и проверяют сценарии."""

    cart_id: str = ""
    order_id: str = ""
    display_id: Any = None
    email: str = ""
    currency: str = ""
    total: Any = None
    item_titles: list[str] = field(default_factory=list)
    item_quantity: int = 0
    payment_provider: str = ""
    shipping_amount: Any = None
    steps: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class MedusaStoreClient:
    """Пошаговый проход по оформлению заказа (гость или покупатель)."""

    def __init__(self, client: Any) -> None:
        """Принимает либо адрес стэнда, либо готовый :class:`QaHttpClient`.

        Ключ витрины (``x-publishable-api-key``) и защита от прод-хостов живут в
        ``QaHttpClient``, поэтому при строке-адресе клиент строим сами.
        """
        if isinstance(client, str):
            from qa.http import QaHttpClient as _Http

            client = _Http(client)
        self.client = client

    # ---------- транспорт ----------

    def _get(self, path: str) -> dict[str, Any]:
        return _body(self.client.get(path))

    def _post(self, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return _body(self.client.post(path, json=payload or {}))

    def _delete(self, path: str) -> dict[str, Any]:
        return _body(self.client.delete(path))

    # ---------- справочные данные ----------

    @allure.step("Регион и валюта витрины")
    def first_region(self) -> dict[str, Any]:
        body = self._get("/store/regions")
        regions = body.get("regions") or []
        if not regions:
            raise StoreApiError("У стенда нет ни одного региона — торговать нечем")
        return regions[0]

    @allure.step("Каталог витрины")
    def products(
        self, limit: int = 20, extra_fields: str = "", region_id: str = ""
    ) -> list[dict[str, Any]]:
        """Товары витрины.

        ``calculated_price`` появляется только когда запрос привязан к региону
        (``region_id=``), поэтому регион передаём явно — иначе цен не видно.
        """
        fields = _merge_fields(VARIANT_FIELDS, extra_fields)
        region = region_id or str(self.first_region().get("id") or "")
        body = self._get(f"/store/products?limit={int(limit)}&fields={fields}&region_id={region}")
        return body.get("products") or []

    def product_by_handle(self, handle: str) -> dict[str, Any]:
        body = self._get(
            f"/store/products/{_quote(handle, safe='')}"
            f"?fields={VARIANT_FIELDS}"
        )
        return unwrap(body, "product")

    @allure.step("Публикуем платёжные провайдеры региона")
    def payment_providers(self, region_id: str) -> list[dict[str, Any]]:
        """``/store/payment-providers`` не в списке полей по умолчанию, просим явно."""
        body = self._get(
            f"/store/payment-providers?region_id={region_id}&fields={PAYMENT_FIELDS}"
        )
        providers = body.get("payment_providers") or []
        if not providers:
            raise StoreApiError(
                f"В регионе {region_id} нет ни одного платёжного провайдера"
            )
        return providers

    @allure.step("Способы доставки для корзины")
    def shipping_options(self, cart_id: str) -> list[dict[str, Any]]:
        body = self._get(
            f"/store/shipping-options?cart_id={cart_id}"
            "&fields=id,name,amount,price_type,data"
        )
        return body.get("shipping_options") or []

    # ---------- корзина ----------

    @allure.step("Создаём корзину с меткой тестового прогона")
    def create_cart(self, region_id: str, run_tag: str, email: str = "") -> dict[str, Any]:
        metadata: dict[str, Any] = {"qa_run": run_tag}
        payload: dict[str, Any] = {"region_id": region_id, "metadata": metadata}
        if email:
            payload["email"] = email
        cart = unwrap(self._post("/store/carts", payload), "cart")
        cart_id = cart.get("id")
        if not cart_id:
            raise StoreApiError(f"Корзина не создалась: {cart}")
        return self.get_cart(str(cart_id))

    @allure.step("Добавляем товар в корзину")
    def add_item(
        self, cart_id: str, variant_id: str, quantity: int = 1
    ) -> dict[str, Any]:
        body = self._post(
            f"/store/carts/{cart_id}/line-items",
            {"variant_id": variant_id, "quantity": int(quantity)},
        )
        return self._after_mutation(cart_id, body)

    @allure.step("Меняем количество позиции")
    def set_quantity(
        self, cart_id: str, line_id: str, quantity: int
    ) -> dict[str, Any]:
        body = self._post(
            f"/store/carts/{cart_id}/line-items/{line_id}",
            {"quantity": int(quantity)},
        )
        return self._after_mutation(cart_id, body)

    @allure.step("Убираем позицию из корзины")
    def remove_item(self, cart_id: str, line_id: str) -> dict[str, Any]:
        body = self._delete(f"/store/carts/{cart_id}/line-items/{line_id}")
        return self._after_mutation(cart_id, body)

    def _after_mutation(self, cart_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Состояние корзины после изменения.

        Medusa v2 на mutation-эндпоинтах отвечает по-разному: ``line-items``
        возвращают одну позицию, ``shipping-methods`` и ``shipping-address`` —
        корзину без сумм, а отказ — вовсе без корзины.  Поэтому успешный ответ
        всегда перечитываем корзиной: так проверки видят то же, что видит
        покупатель на странице корзины, а отказ остаётся данными, которые
        вернул API.
        """
        if body.get("type") in ("insufficient_inventory", "error") or "code" in body:
            return body
        return self.get_cart(cart_id)

    def remove_all_items(self, cart_id: str) -> None:
        """Освобождаем резерв товара: незавершённая корзина его держит."""
        cart = self.get_cart(cart_id)
        for item in cart.get("items") or []:
            line_id = item.get("id")
            if line_id:
                self.remove_item(cart_id, line_id)

    def get_cart(self, cart_id: str) -> dict[str, Any]:
        return unwrap(self._get(f"/store/carts/{cart_id}?fields={CART_FIELDS}"), "cart")

    @allure.step("Выбираем способ доставки")
    def select_shipping(self, cart_id: str, option_id: str) -> dict[str, Any]:
        body = self._post(
            f"/store/carts/{cart_id}/shipping-methods", {"option_id": option_id}
        )
        return self._after_mutation(cart_id, body)

    @allure.step("Указываем email покупателя")
    def set_email(self, cart_id: str, email: str) -> dict[str, Any]:
        return self._after_mutation(cart_id, self._post(f"/store/carts/{cart_id}", {"email": email}))

    @allure.step("Указываем адрес доставки")
    def set_shipping_address(
        self, cart_id: str, address: dict[str, Any]
    ) -> dict[str, Any]:
        return self._after_mutation(
            cart_id,
            self._post(f"/store/carts/{cart_id}/shipping-address", {"shipping_address": address}),
        )

    @allure.step("Получаем идентификатор заказа для гостя")
    def order_token(self, email: str) -> str:
        body = self._post("/store/order/confirmed", {"email": email})
        return str(body.get("order_id") or "")

    # ---------- оформление ----------

    @allure.step("Оформляем заказ")
    def complete_checkout(
        self, cart_id: str, provider_id: str = "", region_id: str = ""
    ) -> tuple[Optional[dict[str, Any]], Optional[ApiFailure]]:
        """Платёж сессия -> ``complete``; ошибочный ответ возвращаем данными.

        Порядок такой же, как у витрины: коллекция платежей, сессия выбранного
        провайдера, и только потом ``complete``.  На этой сборке сессий в новой
        коллекции нет — их создаёт сам тест.
        """
        collection = unwrap(
            self._post("/store/payment-collections", {"cart_id": cart_id}),
            "payment_collection",
        )
        collection_id = collection.get("id")
        if not collection_id:
            return None, ApiFailure(0, "no_payment_collection", str(collection))

        provider = provider_id or self.pick_provider(
            region_id or str(collection.get("region_id") or "")
        )
        session_payload = self._post(
            f"/store/payment-collections/{collection_id}/payment-sessions",
            {"provider_id": provider},
        )
        session = session_payload.get("payment_session")
        if not isinstance(session, dict):
            # На этой сборке Medusa отвечает обновлённой коллекцией, а не сессией.
            updated = session_payload.get("payment_collection") or {}
            created = updated.get("payment_sessions") or []
            session = created[-1] if created else {}
        if not session.get("id"):
            return None, ApiFailure(0, "no_payment_session", str(session_payload))

        response = self.client.post(f"/store/carts/{cart_id}/complete")
        body = _body(response)
        order = body.get("order")
        if not isinstance(order, dict):
            order = unwrap(body, "order") if body.get("type") == "order" else None
        if not order or not order.get("id"):
            data = body.get("error") or body
            return None, ApiFailure(
                int(getattr(response, "status_code", 0) or 0),
                str(data.get("code") or body.get("type") or "checkout_failed"),
                str(data.get("message") or data)[:400],
            )
        order.setdefault("payment_provider", provider)
        return order, None

    def pick_provider(self, region_id: str) -> str:
        """Провайдер для оформления: «ручная» оплата не требует внешних ключей."""
        if not region_id:
            region_id = str(self.first_region().get("id") or "")
        for provider in self.payment_providers(region_id):
            if provider.get("id") == PREFERRED_PAYMENT_PROVIDER:
                return PREFERRED_PAYMENT_PROVIDER
        return str(self.payment_providers(region_id)[0].get("id") or "")

    # ---------- заказ ----------

    @allure.step("Читаем заказ витринным API (как это делает страница заказа)")
    def get_order(self, order_id: str, fields: str = ORDER_FIELDS) -> dict[str, Any]:
        """Заказ по id; ``fields=""`` — полная выборка Medusa (в ней есть ``quantity``)."""
        path = f"/store/orders/{order_id}"
        if fields:
            path += f"?fields={fields}"
        return unwrap(self._get(path), "order")

    # ---------- готовый путь «полный заказ» ----------

    def run_checkout(
        self,
        *,
        variant: dict[str, Any],
        quantity: int = 1,
        run_tag: str,
        email: str,
        address: Optional[dict[str, Any]] = None,
        shipping_option_id: Optional[str] = None,
    ) -> CheckoutResult:
        """Проходит весь путь и возвращает то, что видно покупателю."""
        result = CheckoutResult(email=email)
        region = self.first_region()
        result.steps.append("region")

        cart = self.create_cart(str(region["id"]), run_tag=run_tag, email=email)
        result.cart_id = str(cart.get("id") or "")
        result.steps.append("cart")

        cart = self.add_item(result.cart_id, str(variant["id"]), quantity)
        result.steps.append("add_item")
        items = cart.get("items") or []
        if not items:
            raise StoreApiError(f"Позиция не появилась в корзине: {cart}")
        result.item_titles = [str(i.get("title") or "") for i in items]
        result.item_quantity = sum(int(i.get("quantity") or 0) for i in items)

        if not shipping_option_id:
            options = self.shipping_options(result.cart_id)
            if options:
                shipping_option_id = str(options[0]["id"])
        if shipping_option_id:
            cart = self.select_shipping(result.cart_id, shipping_option_id)
            result.steps.append("shipping")

        if address:
            cart = self.set_shipping_address(result.cart_id, address)
            result.steps.append("address")

        result.currency = str(cart.get("currency_code") or region.get("currency_code") or "")
        result.total = cart.get("total")

        order, failure = self.complete_checkout(
            result.cart_id, region_id=str(region.get("id") or "")
        )
        if failure is not None:
            result.raw = {"failure": str(failure)}
            raise StoreApiError(f"Заказ не оформился: {failure}")

        result.order_id = str(order.get("id") or "")
        result.display_id = order.get("display_id")
        result.raw = order
        result.steps.append("complete")
        return result


def product_price(variant: dict[str, Any]) -> Optional[int]:
    """Цена варианта в копейках (Medusa хранит деньги в минимальных единицах)."""
    calc = (variant.get("calculated_price") or {}).get("calculated_amount")
    return int(calc) if isinstance(calc, (int, float)) else None


def variant_stock(variant: dict[str, Any]) -> Optional[int]:
    """Остаток на складе; ``None`` — витрина его не отдаёт."""
    raw = variant.get("inventory_quantity")
    return int(raw) if isinstance(raw, (int, float)) else None


def pick_buyable_variant(
    products: list[dict[str, Any]], min_stock: int = 1
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Товар, который точно можно купить: есть вариант, цена и остаток."""
    for product in products:
        for variant in product.get("variants") or []:
            if not variant.get("id"):
                continue
            if product_price(variant) is None:
                continue
            stock = variant_stock(variant)
            if stock is not None and stock < min_stock:
                continue
            return product, variant
    raise StoreApiError(
        "В каталоге нет ни одного товара с ценой: "
        f"товаров={len(products)}, "
        f"вариантов={sum(len(p.get('variants') or []) for p in products)} — "
        "нужен region_id в запросе, иначе витрина не отдаёт calculated_price"
    )


def first_line(cart: dict[str, Any]) -> dict[str, Any]:
    items = cart.get("items") or []
    if not items:
        raise StoreApiError(f"В корзине нет позиций: {cart}")
    return items[0]
