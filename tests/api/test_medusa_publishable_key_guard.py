"""Контракт publishable-ключа Medusa (M1, проверено на стенде).

Факт: `GET /store/products` без заголовка `x-publishable-api-key` отвечает
400 с телом `{"type": "not_allowed", ...}`. Позитивная ветка с реальным ключом
выполняется только если ключ передан в CI как секрет `MEDUSA_PUBLISHABLE_API_KEY`.
"""

from __future__ import annotations

import os

import allure
import pytest

from qa.config import QaConfig
from qa.http import PUBLISHABLE_KEY_ENV, QaHttpClient

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def http_client(config: QaConfig) -> "QaHttpClient":
    """Голый HTTP-клиент API.

    Фикстура `api_client` отдаёт `MedusaStoreClient` (методы про корзину и заказ),
    а этим проверкам нужен именно сырой ответ: заголовок publishable-ключа
    приходится включать и выключать вручную.
    """
    client = QaHttpClient(config.api_url)
    try:
        yield client
    finally:
        client.close()


@allure.step("GET /store/products без ключа отвечает 400 not_allowed")
def test_products_without_publishable_key_is_rejected(http_client: QaHttpClient) -> None:
    """`/store/products` без `x-publishable-api-key` — 400 и `type: not_allowed`."""
    # Заголовок намеренно подаётся как отсутствующий: в CI ключ задан как секрет
    # и QaHttpClient подставил бы его сам, поэтому просим явный отказ от ключа.
    response = http_client.get("/store/products?limit=1", publishable_key=False)

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
def test_products_with_publishable_key_returns_list(http_client: QaHttpClient) -> None:
    """`/store/products` с ключом — 200 и `products` списком."""
    response = http_client.get("/store/products?limit=1")

    assert response.status_code == 200, (
        f"/store/products с ключом вернул {response.status_code}, ожидался 200"
    )
    body = response.json()
    assert isinstance(body.get("products"), list), (
        f"поле products должно быть списком, получено: {type(body.get('products'))!r}"
    )


@allure.step("GET /api/health отвечает 404 — health-проба не должна на него опираться")
def test_api_health_route_is_absent(http_client: QaHttpClient) -> None:
    """Зафиксированный факт: на этой версии Medusa роута `/api/health` нет.

    Поэтому health-пробы стека должны использовать другие точки (например,
    доступность витрины или `/store/products` с ключом), а не `/api/health`.
    """
    response = http_client.get("/api/health")
    assert response.status_code == 404, (
        f"/api/health вернул {response.status_code}, а на этой версии Medusa "
        "роут должен отсутствовать (404)"
    )


@allure.step("GET /health отвечает 200 — это рабочая health-точка Medusa")
def test_api_health_route_works(http_client: QaHttpClient) -> None:
    """Рабочая точка — `/health` (без префикса `/api`), ключа она не требует."""
    response = http_client.get("/health")
    assert response.status_code == 200, (
        f"/health вернул {response.status_code}, ожидался 200"
    )
