"""S1-7. Модерация отзыва: hide → проверит витрину → restore.

Сценарий про то, что делает лавка, когда отзыв ей не нравится: модератор прячет его
через админ-API, витрина перестаёт его отдавать, а потом отзыв возвращают на место.
Проверяются три вещи сразу:

* ``GET /admin/reviews`` отдаёт отзывы с ``status`` и ``version``;
* ``POST /admin/reviews/<id>`` телом ``{"status": ..., "version": ...}`` меняет только
  статус, а витрина (``GET /store/reviews``) показывает исключительно ``published``;
* оптимистичная блокировка: запрос со старым ``version`` отбит кодом ``409`` и
  ``REVIEW_CHANGED``, то есть два модератора не перетирают правки друг друга.

Новых данных тест не создаёт: отзыв берётся существующий (создание отзыва требует
токен Cloudflare Turnstile, обход которого на стенде выключен). Чужие данные обязаны
остаться как были: если отзыв успели спрятать, восстановление выполняется в ``finally``
и возвращает ``status = published`` даже когда средний шаг упал.
"""

from __future__ import annotations

from typing import Any, Optional

import allure
import pytest

pytestmark = [pytest.mark.scenario]

REVIEW_FIELDS = "id,product_id,rating,status,version,created_at"


def _admin_reviews(admin) -> Optional[list[dict[str, Any]]]:
    """``GET /admin/reviews`` или ``None``, если список отзывов в админке недоступен."""
    body = admin.get(f"/admin/reviews?limit=50&offset=0&fields={REVIEW_FIELDS}")
    if not isinstance(body, dict):
        return None
    reviews = body.get("reviews")
    return reviews if isinstance(reviews, list) else []


def _review_version(admin, review_id: str) -> Optional[int]:
    """Актуальный ``version`` отзыва: после каждой правки модератора он растёт."""
    for review in _admin_reviews(admin) or []:
        if str(review.get("id")) == review_id:
            version = review.get("version")
            return version if isinstance(version, int) else None
    return None


def _store_reviews(store, product_id: str) -> dict[str, Any]:
    """Список отзывов товара глазами покупателя — именно его отдаёт карточка товара."""
    return store._get(f"/store/reviews?product_id={product_id}&offset=0")


def _review_on_store(store, review_id: str, product_id: str) -> bool:
    """Видит ли витрина отзыв (в ``GET /store/reviews`` выходят только ``published``)."""
    return any(
        str(item.get("id")) == review_id
        for item in _store_reviews(store, product_id).get("reviews") or []
    )


def _store_review_hint(store, limit: int = 8) -> str:
    """Одной строкой: есть ли вообще опубликованные отзывы на витрине (для текста skip).

    Нужна, когда админка список не отдаёт: по витринному API видно, что прятать нечего,
    а не то что доступ к отзывам просто закрыт.
    """
    try:
        body = store._get(f"/store/products?limit={limit}&country_code=rs&fields=id,handle")
        counts = []
        for product in (body.get("products") or [])[:limit]:
            product_id = str(product.get("id") or "")
            if not product_id:
                continue
            payload = store._get(f"/store/reviews?product_id={product_id}&offset=0")
            counts.append(f"{product.get('handle')}={int(payload.get('count') or 0)}")
    except Exception:  # noqa: BLE001 — подсказка не должна перекрывать настоящий отказ
        return "витринный список отзывов прочитать не удалось"
    if not all(count.endswith("=0") for count in counts):
        return "на витрине есть опубликованные отзывы, но админка их не отдаёт"
    return "на витрине опубликованных отзывов тоже нет: " + ", ".join(counts)


def _post_status_raw(admin, review_id: str, payload: dict[str, Any]) -> tuple[int, Any]:
    """``POST /admin/reviews/<id>`` сырьём: нужны и код ответа, и тело с ``error_code``.

    ``MedusaAdminClient.post`` при ошибке возвращает ``None`` и не даёт прочитать код
    ошибки, поэтому шаг про оптимистичную блокировку идёт тем же HTTP-транспортом.
    Секреты не печатаем.
    """
    response = admin.client.post(
        f"/admin/reviews/{review_id}",
        json=payload,
        headers={"Authorization": f"Bearer {admin.login()}"},
        publishable_key=False,
    )
    return response.status_code, admin.client.json_of(response)


def _restore(admin, store, review_id: str, product_id: str, known_version: int) -> str:
    """Возвращает отзыв в ``published``; '' если всё хорошо, текст проблемы если нет."""
    versions: list[int] = []
    fresh = _review_version(admin, review_id)
    if isinstance(fresh, int):
        versions.append(fresh)
    # админка могла не отдать список — пробуем версии сразу за известной
    versions.extend(known_version + step for step in (1, 2, 3))
    for version in dict.fromkeys(versions):
        status, body = _post_status_raw(admin, review_id, {"status": "published", "version": version})
        if status in (200, 201) and isinstance(body, dict) and body.get("success") is True:
            if _review_on_store(store, review_id, product_id):
                return ""
    return f"отзыв {review_id} так и не вернулся на витрину (status = published)"


@allure.story("Модерация отзыва")
@allure.severity(allure.severity_level.CRITICAL)
def test_hide_review_then_restore(api_client, admin_client, steps):
    """Лавка прячет отзыв, витрина его теряет, затем отзыв возвращается покупателю."""
    store = api_client

    with steps("найти в админке опубликованный отзыв", "есть отзыв со status = published"):
        reviews = _admin_reviews(admin_client)
        if reviews is None:
            pytest.skip(
                f"список отзывов в админке недоступен: {admin_client.last_error}; "
                f"{_store_review_hint(store)} — проверять модерацию нечем"
            )
        published = next((r for r in reviews if str(r.get("status")) == "published"), None)
        if published is None:
            pytest.skip("на стенде нет опубликованных отзывов для теста")
        review_id = str(published.get("id") or "")
        product_id = str(published.get("product_id") or "")
        version = published.get("version")
        if not review_id or not product_id or not isinstance(version, int):
            pytest.skip(f"у отзыва {review_id or published!r} нет id/product_id/version — сценарий не собрать")
        allure.attach(
            f"отзыв {review_id}, товар {product_id}, version={version}",
            "что модерируем",
            allure.attachment_type.TEXT,
        )

    hidden = False
    restored = False
    hazard = ""
    try:
        with steps("до модерации витрина показывает отзыв",
                   "отзыв есть в GET /store/reviews, count учитывает его"):
            before = _store_reviews(store, product_id)
            count_before = int(before.get("count") or 0)
            assert count_before >= 1, f"товар {product_id} и до модерации отдаёт count={count_before}"
            assert _review_on_store(store, review_id, product_id), (
                f"отзыв {review_id} и до модерации не виден витрине"
            )

        with steps("модератор прячет отзыв", "POST /admin/reviews/<id> вернул success = true"):
            body = admin_client.post(
                f"/admin/reviews/{review_id}", {"status": "hidden", "version": version}
            )
            hidden = True  # правка отправлена: проверим её исход в finally при любом исходе
            assert isinstance(body, dict) and body.get("success") is True, (
                f"скрыть отзыв не удалось: {body!r} | {admin_client.last_error}"
            )

        with steps("витрина больше не отдаёт скрытый отзыв",
                   "отзыва нет в списке, count уменьшился на 1"):
            after = _store_reviews(store, product_id)
            assert not _review_on_store(store, review_id, product_id), (
                f"скрытый отзыв {review_id} всё ещё виден на витрине"
            )
            count_after = int(after.get("count") or 0)
            assert count_after == count_before - 1, (
                f"count на витрине {count_after}, ожидалось {count_before - 1}"
            )

        with steps("повторный POST с устаревшим version", "409 и error_code = REVIEW_CHANGED"):
            status, body = _post_status_raw(
                admin_client, review_id, {"status": "published", "version": version}
            )
            assert status == 409, f"устаревшая версия принята: HTTP {status} {body!r}"
            code = body.get("error_code") if isinstance(body, dict) else None
            assert code == "REVIEW_CHANGED", f"код ошибки {code!r}, ожидался REVIEW_CHANGED"

        with steps("вернуть отзыв и перечитать витрину",
                   "POST с актуальным version дал success, отзыв снова виден, count как был"):
            fresh = _review_version(admin_client, review_id)
            assert isinstance(fresh, int), f"админка не отдаёт актуальную версию отзыва {review_id}"
            body = admin_client.post(
                f"/admin/reviews/{review_id}", {"status": "published", "version": fresh}
            )
            assert isinstance(body, dict) and body.get("success") is True, (
                f"вернуть отзыв не удалось: {body!r} | {admin_client.last_error}"
            )
            restored = True
            assert _review_on_store(store, review_id, product_id), (
                f"отзыв {review_id} не вернулся на витрину"
            )
            back = int(_store_reviews(store, product_id).get("count") or 0)
            assert back == count_before, f"count на витрине {back}, до модерации было {count_before}"
            allure.attach(
                f"отзыв {review_id}: status = published, count = {back} — как до теста",
                "данные стенда восстановлены",
                allure.attachment_type.TEXT,
            )
    finally:
        if hidden and not restored:
            with allure.step("аварийное восстановление: вернуть отзыв в published"):
                hazard = _restore(admin_client, store, review_id, product_id, version)
                allure.attach(
                    hazard or f"отзыв {review_id} снова published и виден витрине",
                    "итог восстановления",
                    allure.attachment_type.TEXT,
                )

    if hazard:
        pytest.fail(f"чужой отзыв остался изменённым: {hazard}")
