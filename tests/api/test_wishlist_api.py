"""API-контракт вишлиста: ``/store/wishlist`` (GET/POST/DELETE).

Страница ``/profile/wishlist`` и сердечки в каталоге (UI-проверка —
``tests/e2e/test_wishlist.py``) держатся на трёх ручках витринного API.
Файл называется ``test_wishlist_api.py``, а не ``test_wishlist.py``: каталоги
тестов идут без ``__init__.py``, и два одноимённых файла (в ``tests/api`` и
``tests/e2e``) ломали бы коллекцию pytest (``import file mismatch``) —
падают оба прогона CI, и api, и e2e.
Контракт сверен по запросам к ``test.tamir.rs`` (27.11.2026):

* ``GET /store/wishlist`` → 200 ``{"product_ids": [<prod_...>, ...]}``;
  ответ приватный и не кэшируемый: ``Cache-Control: private, no-store``.
  Порядок — новые сверху (см. ``test_list_returns_newest_first``).
* ``POST /store/wishlist`` телом ``{"product_id": "prod_..."}`` → 200
  ``{"product_id": ..., "saved": true}``; повтор того же id — тот же 200,
  в списке по-прежнему одна запись (идемпотентно).
* ``DELETE /store/wishlist/:productId`` → 200
  ``{"product_id": ..., "saved": false}``; повтор удаления — снова 200,
  удаление уже отсутствующего id тоже 200 (идемпотентно).
* Ошибки: неизвестный, но корректный id → 404 ``PRODUCT_NOT_FOUND``;
  id плохого формата → 400 ``INVALID_PRODUCT``; тело без ``product_id`` →
  400 ``INVALID_PRODUCT``; лишние поля в теле (``customer_id``) → 400
  ``INVALID_BODY``; ``product_id`` в пути не читается из query.
* **Отклонение от контракта (BUG-кандидат).** Без токена ручки отдают 401 с
  телом middleware ``{"message": "Unauthorized"}`` вместо ожидаемого
  ``{"code": "LOGIN_REQUIRED"}`` — поля ``code`` в ответе нет, поэтому
  клиент отличает «нужен вход» только по HTTP-коду.
* **Отклонение от контракта (BUG-кандидат).** Тело запроса больше ~2 КБ не
  отсекается как 4xx: ``product_id`` в 3000 символов (тело ~3 КБ) даёт
  HTTP 500 ``unknown_error`` вместо ожидаемых 413/400. Границу замеряли на
  теле с лишним полем: до ~1.9 КБ включительно — честный 400
  ``INVALID_BODY``, от ~2.0 КБ — уже 500. Длина в *пути* лимита тела не
  касается: ``DELETE`` с 3 КБ в пути остаётся 400 ``INVALID_PRODUCT``.
  Проверка — ``test_oversized_body_returns_500_on_staging``.

Запросы идут через ``QaHttpClient`` (он режет прод-хосты и подставляет
publishable-ключ витрины); авторизация — покупатель из ``QA_WEB_EMAIL``:
``POST /auth/customer/emailpass`` (тот же вход, что у e2e и сценария
отзывов). Аккаунт на стэнде общий и уже что-то держит в вишлисте, поэтому
``wishlist`` снимает снимок списка до теста и убирает только то, что добавил
тест, а сам вход делаем один раз за прогон: на стэнде вход ограничен пятью
попытками в час.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from qa.medusa_store import MedusaStoreClient

pytestmark = [pytest.mark.api]

#: Ответы ``/store/wishlist`` без ``Authorization`` отдаёт middleware, а не ручка:
#: кода ошибки в теле нет (см. модульный докстринг), поэтому здесь только текст.
UNAUTHENTICATED_BODY_KEYS = {"message"}


# --------------------------------------------------------------------------- вход

@pytest.fixture(scope="session")
def buyer_token(api_client: MedusaStoreClient) -> str:
    """Токен покупателя стенда; ``api_client`` — чтобы ход был через общий транспорт.

    Один вход на прогон обязателен: ``/auth/customer/emailpass`` на стэнде
    ограничен пятью попытками в час, а тестов, которым нужен покупатель, несколько.
    """
    email = (os.getenv("QA_WEB_EMAIL") or "").strip()
    password = os.getenv("QA_WEB_PASSWORD") or ""
    if not email or not password:
        pytest.skip("нужны QA_WEB_EMAIL и QA_WEB_PASSWORD — профиль покупателя стенда")

    response = api_client.client.post(
        "/auth/customer/emailpass",
        json={"email": email, "password": password},
    )
    if response.status_code == 429:
        pytest.skip("превышен лимит входов на стэнде (5 в час) — вишлист не проверяем")
    if response.status_code != 200:
        pytest.fail(f"вход покупателем не получился: HTTP {response.status_code} {response.text[:120]}")
    token = (response.json() or {}).get("token") or ""
    if not token:
        pytest.fail("emailpass не вернул token")
    return token


# --------------------------------------------------------------------------- API

class WishlistApi:
    """Три ручки ``/store/wishlist`` от имени покупателя."""

    def __init__(self, api: MedusaStoreClient, token: str) -> None:
        self.http = api.client
        self.headers = {"Authorization": f"Bearer {token}"}

    def list(self) -> dict[str, Any]:
        return self.http.json_of(self.http.get("/store/wishlist", headers=self.headers))

    def get_raw(self, headers: dict[str, str] | None = None):
        """GET без подстановки заголовков — для проверки «нет токена»."""
        return self.http.get("/store/wishlist", headers=headers or {})

    def add(self, product_id: str, **extra: Any) -> dict[str, Any]:
        return self.http.json_of(
            self.http.post("/store/wishlist", json={"product_id": product_id, **extra},
                           headers=self.headers)
        )

    def add_raw(self, payload: Any):
        """POST «как есть» — в теле могут быть лишние поля или большой мусор."""
        return self.http.post("/store/wishlist", json=payload, headers=self.headers)

    def remove(self, product_id: str) -> dict[str, Any]:
        return self.http.json_of(
            self.http.delete(f"/store/wishlist/{product_id}", headers=self.headers)
        )


@pytest.fixture()
def wl(api_client: MedusaStoreClient, buyer_token: str) -> WishlistApi:
    return WishlistApi(api_client, buyer_token)


#: Снимок вишлиста общего аккаунта до тестов: им проверяем, что прогон ничего в
#: аккаунте не оставил (см. ``test_wishlist_is_restored_after_writes``).
_SNAPSHOT: list[str] = []
_SNAPSHOT_TAKEN = False


@pytest.fixture()
def wishlist(wl: WishlistApi) -> list[str]:
    """Снимок до теста и уборка после: аккаунт на стэнде общий.

    Убираем только то, что тест добавил, — чужие записи аккаунта не трогаем,
    иначе соседние прогоны (e2e, сценарии) останутся без своего вишлиста.
    """
    global _SNAPSHOT_TAKEN

    before = wl.list().get("product_ids") or []
    if not _SNAPSHOT_TAKEN:
        _SNAPSHOT[:] = before
        _SNAPSHOT_TAKEN = True
    yield before
    now = wl.list().get("product_ids") or []
    for product_id in now:
        if product_id not in before:
            wl.remove(product_id)
    restored = wl.list().get("product_ids") or []
    extra = [product_id for product_id in restored if product_id not in before]
    if extra:
        print(f"⚠️ вишлист аккаунта не убран: {extra}")


@pytest.fixture(scope="module")
def products(api_client: MedusaStoreClient) -> list[dict[str, Any]]:
    return api_client.products(limit=50, region_id=api_client.first_region()["id"])


@pytest.fixture()
def product_id(products: list[dict[str, Any]]) -> str:
    for product in products:
        if str(product.get("id", "")).startswith("prod_"):
            return product["id"]
    pytest.fail("в каталоге стенда нет ни одного товара с prod_ id")


# --------------------------------------------------------------------------- GET

def test_list_contract(wl: WishlistApi, wishlist: list[str], steps) -> None:
    """GET отдаёт список строк ``product_ids`` и запрет кэширования приватного ответа."""
    with steps("GET /store/wishlist"):
        response = wl.get_raw(wl.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert "product_ids" in body, "в ответе нет product_ids: UI нечему рисовать сердечки"
    ids = body["product_ids"]
    assert isinstance(ids, list), f"product_ids должен быть списком, а не {type(ids)}"
    assert all(isinstance(item, str) for item in ids), "product_ids обязан быть списком строк"
    assert all(item.startswith("prod_") for item in ids), f"в списке не id товара: {ids}"
    assert len(ids) == len(set(ids)), f"product_ids содержит повторы: {ids}"

    with steps("ответ приватный и не кэшируемый"):
        cache_control = (response.headers.get("Cache-Control") or "").lower()
    assert "no-store" in cache_control, f"ожидаем Cache-Control: ... no-store, получено {cache_control!r}"
    assert "private" in cache_control, f"ожидаем Cache-Control: private, получено {cache_control!r}"


def test_list_returns_newest_first(wl: WishlistApi, wishlist: list[str],
                                   products: list[dict[str, Any]], steps) -> None:
    """Порядок списка — новые сверху: так страница рисует «недавно добавленное»."""
    pair = [
        p["id"] for p in products
        if str(p.get("id", "")).startswith("prod_") and p["id"] not in wishlist
    ][:2]
    if len(pair) < 2:
        pytest.skip("в каталоге стенда нет двух товаров, которых ещё нет в вишлисте")
    first, second = pair

    wl.add(first)
    wl.add(second)
    with steps("второй добавленный товар стоит выше первого"):
        ids = wl.list()["product_ids"]
    assert second in ids and first in ids, f"в списке нет добавленного: {ids}"
    assert ids.index(second) < ids.index(first), f"ожидаем новые сверху, получили {ids}"


# --------------------------------------------------------------------------- POST

def test_add_catalog_product(wl: WishlistApi, wishlist: list[str], product_id: str,
                             steps) -> None:
    """POST товара из каталога: 200 ``saved: true`` и товар в списке."""
    with steps("POST /store/wishlist"):
        body = wl.add(product_id)

    assert body == {"product_id": product_id, "saved": True}, f"получили {body}"
    with steps("товар появился в списке"):
        ids = wl.list()["product_ids"]
    assert product_id in ids, f"товара нет в списке: {ids}"
    assert ids.count(product_id) == 1, f"после одного добавления дубликат: {ids}"


def test_add_is_idempotent(wl: WishlistApi, wishlist: list[str], product_id: str,
                           steps) -> None:
    """Повторный POST того же товара — тот же 200, но без второй записи.

    Иначе двойной клик по сердечку удваивал бы вишлист.
    """
    wl.add(product_id)
    with steps("повторный POST"):
        body = wl.add(product_id)

    assert body == {"product_id": product_id, "saved": True}, f"получили {body}"
    ids = wl.list()["product_ids"]
    assert ids.count(product_id) == 1, f"повтор добавил дубликат: {ids}"


def test_concurrent_adds_store_single_entry(wl: WishlistApi, wishlist: list[str],
                                            product_id: str, steps) -> None:
    """Пять параллельных POST одного товара оставляют одну запись.

    Так проверяем гонку двойного клика: идемпотентность POST'а по одной и той же
    паре (customer, product).
    """
    with steps("пять параллельных POST одного товара"):
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(wl.add_raw, {"product_id": product_id}) for _ in range(5)]
            responses = [future.result() for future in futures]
        results = [(response.status_code, response.json()) for response in responses]

    assert all(code == 200 for code, _ in results), f"параллельный POST отдал {results}"
    assert all(body.get("saved") is True for _, body in results), f"в ответах нет saved: {results}"
    ids = wl.list()["product_ids"]
    assert ids.count(product_id) == 1, f"пять параллельных добавлений дали {ids.count(product_id)} записей: {ids}"


def test_add_unknown_product_returns_404(wl: WishlistApi, wishlist: list[str],
                                         steps) -> None:
    """Корректный, но несуществующий ``prod_`` id → 404 ``PRODUCT_NOT_FOUND``."""
    with steps("POST несуществующего товара"):
        response = wl.add_raw({"product_id": "prod_qa0000000000000000000ff"})

    assert response.status_code == 404, response.text
    assert response.json().get("code") == "PRODUCT_NOT_FOUND", response.text
    assert wl.list()["product_ids"] == wishlist, "список изменился из-за ошибочного POST"


def test_add_malformed_product_returns_400(wl: WishlistApi, wishlist: list[str],
                                           steps) -> None:
    """``product_id`` не-формата → 400 ``INVALID_PRODUCT`` (в БД не стучимся)."""
    with steps("POST product_id=not-a-product"):
        response = wl.add_raw({"product_id": "not-a-product"})

    assert response.status_code == 400, response.text
    assert response.json().get("code") == "INVALID_PRODUCT", response.text
    assert wl.list()["product_ids"] == wishlist, "список изменился из-за ошибочного POST"


def test_add_without_product_id_returns_400(wl: WishlistApi, wishlist: list[str],
                                            steps) -> None:
    """Пустое тело → 400; на стэнде код ошибки — ``INVALID_PRODUCT``."""
    with steps("POST без product_id"):
        response = wl.add_raw({})

    assert response.status_code == 400, response.text
    assert response.json().get("code") == "INVALID_PRODUCT", (
        "ождаем INVALID_PRODUCT (так на стэнде), получили " + response.text[:120]
    )
    assert wl.list()["product_ids"] == wishlist, "список изменился из-за ошибочного POST"


def test_add_foreign_customer_id_returns_400(wl: WishlistApi, wishlist: list[str],
                                             product_id: str, steps) -> None:
    """Чужой ``customer_id`` в теле → 400 ``INVALID_BODY``, а не запись в чужой вишлист.

    Иначе любой покупатель записывал бы товары в чужие списки: профиль строится
    по токену, а не по полям запроса.
    """
    with steps("POST с customer_id другого покупателя"):
        response = wl.add_raw({"product_id": product_id, "customer_id": "cus_qa_someone_else"})

    assert response.status_code == 400, response.text
    assert response.json().get("code") == "INVALID_BODY", response.text
    assert wl.list()["product_ids"] == wishlist, "чужой customer_id что-то добавил в наш список"


def test_oversized_body_returns_500_on_staging(wl: WishlistApi, wishlist: list[str],
                                               product_id: str, steps) -> None:
    """Большое тело (несколько КБ) стенд валит в 500 вместо 4xx — отклонение.

    Замерено на стэнде: ``product_id`` в 2000 символов (тело ~2 КБ) ещё
    обрабатывается как обычно → 400 ``INVALID_PRODUCT``; ``product_id`` в 3000
    символов (тело ~3 КБ) и лишнее поле от ~2 КБ → HTTP 500
    ``unknown_error``. По контракту здесь должен быть 4xx (413 Payload Too
    Large или 400 ``INVALID_BODY``) — это BUG-кандидат: невалидный запрос не
    должен ронять ручку, а такой 500 маскируется клиенту под сбой сети.

    Тест зафиксирован на факте стенда: когда бэкенд начнёт отвечать 4xx, тест
    упадёт и попросит обновить ожидание.
    """
    with steps("product_id 3000 символов (тело ~3 КБ)"):
        response = wl.add_raw({"product_id": "prod_" + "a" * 3000})

    assert response.status_code == 500, (
        f"факт стенда — HTTP 500; если стало {response.status_code}, бэкенд починили: "
        f"обнови ожидание на 4xx. Ответ: {response.text[:120]}"
    )
    assert response.json().get("code") == "unknown_error", response.text[:120]

    with steps("product_id 2000 символов (тело под лимитом) остаётся 400"):
        under_limit = wl.add_raw({"product_id": "prod_" + "a" * 2000})
    assert under_limit.status_code == 400, under_limit.text[:120]
    assert under_limit.json().get("code") == "INVALID_PRODUCT", under_limit.text[:120]

    assert wl.list()["product_ids"] == wishlist, "большое тело что-то добавило в список"


# --------------------------------------------------------------------------- DELETE

def test_remove_product(wl: WishlistApi, wishlist: list[str], product_id: str,
                        steps) -> None:
    """DELETE добавленного товара: 200 ``saved: false`` и товара нет в списке."""
    wl.add(product_id)
    assert product_id in wl.list()["product_ids"]

    with steps("DELETE /store/wishlist/:productId"):
        body = wl.remove(product_id)

    assert body == {"product_id": product_id, "saved": False}, f"получили {body}"
    ids = wl.list()["product_ids"]
    assert product_id not in ids, f"товар остался в списке: {ids}"


def test_remove_is_idempotent(wl: WishlistApi, wishlist: list[str], product_id: str,
                              steps) -> None:
    """Повторный DELETE того же товара — снова 200 ``saved: false``.

    Иначе вторая вкладка с открытой страницей показывала бы ошибку после того,
    как товар уже убрали.
    """
    wl.add(product_id)

    first = wl.remove(product_id)
    with steps("повторный DELETE"):
        second = wl.remove(product_id)

    assert first == {"product_id": product_id, "saved": False}, f"первый DELETE: {first}"
    assert second == {"product_id": product_id, "saved": False}, f"второй DELETE: {second}"
    assert product_id not in wl.list()["product_ids"], "товар вернулся в список"


def test_remove_never_added_product_returns_200(wl: WishlistApi, wishlist: list[str],
                                                steps) -> None:
    """DELETE товара, которого в списке не было, — тоже 200 (операция идемпотентна)."""
    with steps("DELETE отсутствующего товара"):
        body = wl.remove("prod_qa0000000000000000000ff")

    assert body == {"product_id": "prod_qa0000000000000000000ff", "saved": False}, f"получили {body}"
    assert wl.list()["product_ids"] == wishlist, "список изменился из-за ошибочного DELETE"


def test_remove_malformed_product_returns_400(wl: WishlistApi, wishlist: list[str],
                                              steps) -> None:
    """``product_id`` не-формата в пути → 400 ``INVALID_PRODUCT``."""
    with steps("DELETE not-a-product"):
        response = wl.http.delete("/store/wishlist/not-a-product", headers=wl.headers)

    assert response.status_code == 400, response.text
    assert response.json().get("code") == "INVALID_PRODUCT", response.text
    assert wl.list()["product_ids"] == wishlist, "список изменился из-за ошибочного DELETE"


# --------------------------------------------------------------------------- авторизация

def test_requires_authentication(api_client: MedusaStoreClient, product_id: str,
                                 steps) -> None:
    """Без ``Authorization`` все три ручки отдают 401 — чужой вишлист не читается.

    Тело 401 отдаёт middleware (``{"message": "Unauthorized"}``): поля ``code``
    в нём нет, хотя по контракту ждём ``LOGIN_REQUIRED`` — отклонение отмечено
    в докстринге модуля как BUG-кандидат.
    """
    http = api_client.client
    with steps("GET /store/wishlist без токена"):
        get_response = http.get("/store/wishlist")
    assert get_response.status_code == 401, get_response.text

    with steps("POST /store/wishlist без токена"):
        post_response = http.post("/store/wishlist", json={"product_id": product_id})
    assert post_response.status_code == 401, post_response.text

    with steps("DELETE /store/wishlist/:productId без токена"):
        delete_response = http.delete(f"/store/wishlist/{product_id}")
    assert delete_response.status_code == 401, delete_response.text

    body = get_response.json()
    assert set(body) == UNAUTHENTICATED_BODY_KEYS, (
        "тело 401 изменилось: сверяем с контрактом " + str(body)
    )
    assert body.get("code") != "LOGIN_REQUIRED", (
        "401 больше неmiddleware: сверяем тело с контрактом (ожидали code=LOGIN_REQUIRED)"
    )


# --------------------------------------------------------------------------- уборка

def test_wishlist_is_restored_after_writes(wl: WishlistApi, steps) -> None:
    """Последний тест: прогон ничего не оставил в общем аккаунте стенда.

    Читает снимок, который сняла фикстура ``wishlist`` на первом тесте, поэтому
    объявлен в конце файла — pytest запускает тесты в порядке объявления.
    """
    if not _SNAPSHOT_TAKEN:
        pytest.skip("фикстура wishlist не снимала снимок — сравнивать не с чем")
    with steps("список вернул состояние до прогона"):
        ids = wl.list()["product_ids"]
    assert ids == _SNAPSHOT, (
        "тесты оставили след в общем аккаунте: было "
        f"{_SNAPSHOT}, стало {ids}"
    )
