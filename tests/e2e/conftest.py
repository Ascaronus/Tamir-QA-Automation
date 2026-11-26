"""Общие браузерные фикстуры e2e: тестовый аккаунт стэнда и **один вход на прогон**.

Стенд разрешает не больше 5 входов в час на связку email+IP, поэтому все
залогиненные тесты делят один авторизованный контекст браузера (``logged_in_page``).
Повторных попыток входа фикстура не делает — при неудаче тесты падают с внятной
причиной, а не сжирают лимит.
"""

from __future__ import annotations

import os
import uuid

import pytest

from qa.config import load_config
from qa.medusa_store import MedusaStoreClient, product_price, variant_stock
from qa.ui import Storefront


@pytest.fixture(scope="session")
def e2e_account() -> tuple[str, str]:
    """Постоянный тестовый аккаунт стэнда из ``QA_WEB_EMAIL`` / ``QA_WEB_PASSWORD``."""
    load_config()  # подтягивает .env в os.environ (в CI переменные задаёт раннер)
    email = (os.environ.get("QA_WEB_EMAIL") or "").strip()
    password = os.environ.get("QA_WEB_PASSWORD") or ""
    if not email or not password:
        pytest.skip("нет тестового аккаунта: QA_WEB_EMAIL / QA_WEB_PASSWORD не заданы")
    return email, password


@pytest.fixture(scope="session")
def logged_in_page(browser, browser_context_args, base_url, e2e_account):
    """Страница браузера с уже выполненным входом тестового аккаунта.

    Вход — ровно один на прогон; контекст живёт до конца сессии pytest. Относительные
    ``page.goto`` работают: ``browser_context_args`` несёт ``base_url`` стэнда и
    принятое cookie согласие ``tamir_consent_v1``.
    """
    email, password = e2e_account
    context = browser.new_context(**browser_context_args)
    page = context.new_page()
    shop = Storefront(page)
    shop.login(email, password)
    if not shop.wait_until(shop.is_logged_in, 10.0):
        context.close()
        pytest.fail(
            "вход тестового аккаунта не выполнен: проверьте QA_WEB_EMAIL / QA_WEB_PASSWORD "
            "(повторный вход не делаем — стенд разрешает не больше 5 входов в час)"
        )
    yield page
    context.close()


def _richest_buyable_variant(store: MedusaStoreClient) -> tuple[dict, dict]:
    """Вариант с наибольшим остатком: заказ фикстуры не должен съедать склад стенда."""
    best: tuple[int, dict, dict] | None = None
    for product in store.products(limit=60):
        for variant in product.get("variants") or []:
            if not variant.get("id") or product_price(variant) is None:
                continue
            stock = variant_stock(variant)
            if stock is None or stock < 1:
                continue
            if best is None or stock > best[0]:
                best = (stock, product, variant)
    if best is None:
        return {}, {}
    return best[1], best[2]


@pytest.fixture(scope="session")
def account_order(e2e_account, api_client: MedusaStoreClient, admin_client) -> dict:
    """Заказ тестового аккаунта: создаём через админ-API и отменяем в конце прогона.

    Кабинет показывает только заказы с ``customer_id`` вошедшего покупателя, а
    постоянный аккаунт стенда заказов не имеет — тесты кабинета (повтор покупки)
    висели бы на ``skip``. Путь один: черновик ``POST /admin/draft-orders`` +
    ``convert-to-order``. Товар берётся с наибольшим остатком, заказ помечается
    ``metadata.qa_run``; удалить его нельзя (``DELETE /admin/orders/{id}`` — 404),
    поэтому в конце — отмена.
    """
    email, _ = e2e_account
    customer_id = admin_client.customer_id_for_email(email)
    if not customer_id:
        pytest.skip(f"на стенде нет покупателя {email} — тестам кабинета не на чём работать")
    product, variant = _richest_buyable_variant(api_client)
    if not variant:
        pytest.skip("на стенде нет товара с остатком — заказ для тестов кабинета не создать")
    order = admin_client.create_order_for_customer(
        customer_id=customer_id,
        region_id=api_client.first_region()["id"],
        variant_id=str(variant["id"]),
        unit_price=product_price(variant) or 0,
        run_tag=f"qa-e2e-{uuid.uuid4().hex[:8]}",
    )
    yield {
        "id": str(order["id"]),
        "display_id": order.get("display_id"),
        "variant_id": str(variant["id"]),
        "product_title": str(product.get("title") or ""),
    }
    if not admin_client.cancel_order(str(order["id"])):
        print(
            f"заказ фикстуры {order.get('display_id')} не отменён: {admin_client.last_error}"
        )
