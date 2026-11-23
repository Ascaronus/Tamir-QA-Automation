"""Контракт publishable-ключа Medusa (M1, проверено на стенде).

Факт: `GET /store/products` без заголовка `x-publishable-api-key` отвечает
400 с телом `{"type": "not_allowed", ...}`. Позитивная ветка с реальным ключом
выполняется только если ключ передан в CI как секрет `MEDUSA_PUBLISHABLE_API_KEY`.
"""

from __future__ import annotations

import os

import allure
import pytest

from qa.http import PUBLISHABLE_KEY_ENV, QaHttpClient

pytestmark = pytest.mark.api


@allure.step("GET /store/products без ключа отвечает 400 not_allowed")
def test_products_without_publishable_key_is_rejected(api_client: QaHttpClient) -> None:
    # Ключа в запросе намеренно нет: QaHttpClient не подставит заголовок,
    # если переменная окружения не задана.
    response = api_client.get("/store/products?limit=1")

    assert response.status_code == 400, (
        f"/store/products без ключа вернул {response.status_code}, ожидался 400"
    )

    body = response.json()
    assert body.get("type") == "not_allowed", (
        f"ожидается type == 'not_allowed', получено: {body!r}"
    )

    message = str(body.get("message", ""))
    assert "x-publishable-api-key" in message.lower(), (
        f"в сообщении не упомянут заголовок x-publishable-api-key: {message!r}"
    )


@pytest.mark.skipif(
    not os.getenv(PUBLISHABLE_KEY_ENV),
    reason=f"{PUBLISHABLE_KEY_ENV} не задан — позитивная ветка с ключом недоступна",
)
@allure.step("GET /store/products с ключом отвечает 200 и списком товаров")
def test_products_with_publishable_key_returns_list(api_client: QaHttpClient) -> None:
    response = api_client.get("/store/products?limit=1")

    assert response.status_code == 200, (
        f"/store/products с ключом вернул {response.status_code}, ожидался 200"
    )
    body = response.json()
    assert isinstance(body.get("products"), list), (
        f"поле products должно быть списком, получено: {type(body.get('products'))!r}"
    )


@allure.step("GET /api/health отвечает 404 — health-проба не должна на него опираться")
def test_api_health_route_is_absent(api_client: QaHttpClient) -> None:
    """Зафиксированный факт: на этой версии Medusa роута `/api/health` нет.

    Поэтому health-пробы стека должны использовать другие точки (например,
    доступность витрины или `/store/products` с ключом), а не `/api/health`.
    """
    response = api_client.get("/api/health")
    assert response.status_code == 404, (
        f"/api/health вернул {response.status_code}, а на этой версии Medusa "
        "роут должен отсутствовать (404)"
    )
