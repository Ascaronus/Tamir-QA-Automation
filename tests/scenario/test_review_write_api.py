"""S3-3. Отзыв покупателя: публикация без модерации, витрина, капча и рейтинг.

Живой сценарий через API витрины на одном покупателе: регистрация с обходом
капчи, вход ``POST /auth/customer/emailpass`` и пост ``POST /store/reviews``.
В одном сценарии проверяются обещания продукта: пост без капчи — ``400
CAPTCHA_REQUIRED``, рейтинг вне шкалы — ``400 RATING_REQUIRED``, корректный пост
— ``201``; на стенде ``tamir_review.status`` по умолчанию ``published``, поэтому
отзыв обязан появиться в ``GET /store/reviews?product_id=...`` сразу, без шага
модерации. Анонимный пост отдельно отвергается (``401 LOGIN_REQUIRED``), он не
тратит писем. За собой тестирующий гасит отзыв админ-API мягким удалением
``POST /admin/reviews/<id>`` телом ``{"status": "deleted", "version": N}``:
жёсткого удаления в продукте нет, а ``version`` обязана совпадать с текущей
(отзыв, созданный через API, стартует с ``version = 0``). Уборка проверяемая:
после неё отзыв обязан исчезнуть и из витрины, и из ``published`` в админке,
иначе тест падает, а не оставляет на стенде мусор. Отзыв не создаётся вовсе,
если админка отзывов недоступна (``503 REVIEWS_UNAVAILABLE``, см. TB-26):
погасить его было бы нечем. Чужие отзывы (сид ``QA-FIXTURE``) не трогаются.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import allure
import pytest
import requests

from qa.http import CAPTCHA_BYPASS_ENV, captcha_bypass_token, with_captcha_bypass
from qa.medusa_admin import MedusaAdminError

pytestmark = [pytest.mark.scenario, pytest.mark.api]

#: товар каталога стэнда (после пересидинга 2026-11 каталог получил новые id;
#: старого ``prod_0101H...`` больше нет — ``404 PRODUCT_NOT_FOUND``)
PRODUCT_ID = "prod_01M4R26AQMQTKE7PHGQV0F3TC5"
REVIEW_BODY = "QA scenario review: quality feels solid, delivery was on time."


def _skip_without_captcha_bypass() -> None:
    """Регистрация и пост отзыва идут через Turnstile: без токена сценария нет."""
    if not captcha_bypass_token():
        pytest.skip(f"{CAPTCHA_BYPASS_ENV} не задана: обойти Turnstile нечем")


def _http(api_client):
    """``MedusaStoreClient`` — обёртка; POST/GET с заголовками живут в ``.client``."""
    return api_client.client


def _new_email(run_tag: str) -> str:
    """Уникальный на прогон адрес покупателя (один отзыв на пару товар/покупатель)."""
    return f"qa-rev-{run_tag}-{uuid.uuid4().hex[:8]}@example.com"


def _register_and_login(api_client, mailbox, email: str, steps) -> str:
    """Заводит покупателя по OTP-коду из письма и возвращает Bearer-токен входа."""
    http = _http(api_client)
    password = f"Qa!{uuid.uuid4().hex}x"

    with steps("зарегистрировать покупателя для отзыва", "start вернул 200"):
        response = http.post(
            "/store/registration/start", json=with_captcha_bypass({"email": email})
        )
        assert response.status_code == 200, (
            f"start регистрации вернул {response.status_code}: {http.json_of(response)!r}"
        )
        challenge_id = str(http.json_of(response).get("challenge_id") or "")
        assert challenge_id, "регистрация без challenge_id"

    with steps("активировать покупателя по коду из письма", "200 и verified = true"):
        code = mailbox.wait_for_code(email)
        response = http.post(
            "/store/registration/verify",
            json={
                "email": email,
                "challenge_id": challenge_id,
                "code": code,
                "password": password,
                "first_name": "QA",
                "last_name": "Review",
                "phone": "+381 64 1234567",
                "postal_code": "11070",
                "city": "Beograd",
                "country_code": "rs",
                "terms_accepted": True,
            },
        )
        assert response.status_code == 200, (
            f"verify регистрации вернул {response.status_code}: {http.json_of(response)!r}"
        )

    with steps("войти покупателем", "emailpass отдаёт token"):
        response = http.post("/auth/customer/emailpass", json={"email": email, "password": password})
        assert response.status_code == 200, (
            f"вход свежего покупателя не прошёл: {response.status_code} {response.text!r}"
        )
        logged_in = http.json_of(response)
        token = str(logged_in.get("token") or logged_in.get("access_token") or "")
        assert token, f"в ответе входа нет token: {sorted(logged_in)!r}"
        return token


def _bearer(api_client, token: str) -> dict[str, str]:
    """Заголовок авторизации покупателя поверх обычных заголовков клиента."""
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(admin_client) -> dict[str, str]:
    """Заголовок модератора для прямых вызовов админ-API."""
    return {"Authorization": f"Bearer {admin_client.login()}"}


def _find_admin_review(client, headers: dict[str, str], review_id: str) -> tuple[Optional[dict], int, Any]:
    """Строка отзыва в ``GET /admin/reviews``; ``(None, код, тело)``, если строки нет.

    Свежий отзыв админка отдаёт первым, но список листается, поэтому проходим
    до трёх страниц по 100. Если вместо списка пришла ошибка (``503
    REVIEWS_UNAVAILABLE``), возвращаем её код и тело — по ним видно, что
    модерация лежит (TB-26), а не то, что отзыва просто нет.
    """
    for offset in (0, 100, 200):
        response = client.get(
            f"/admin/reviews?limit=100&offset={offset}&fields=id,status,version",
            headers=headers,
            publishable_key=False,
        )
        body = client.json_of(response) or {}
        rows = body.get("reviews")
        if not isinstance(rows, list):
            return None, response.status_code, body
        for row in rows:
            if str(row.get("id")) == review_id:
                return row, response.status_code, body
        if len(rows) < 100:
            break
    return None, 200, None


def _moderation_state(admin_client) -> tuple[bool, str]:
    """Жива ли модерация отзывов: ``(да, "")`` или ``(нет, текст для skip)``.

    Проверка идёт до создания отзыва: без ``GET /admin/reviews`` погасить свой
    отзыв нечем, а неубранный отзыв остаётся опубликованным на стенде.
    """
    client = admin_client.client
    try:
        response = client.get(
            "/admin/reviews?limit=1&offset=0&fields=id,status,version",
            headers=_admin_headers(admin_client),
            publishable_key=False,
        )
    except (requests.RequestException, MedusaAdminError) as exc:
        return False, f"GET /admin/reviews не ответил: {exc}"
    if response.status_code in (200, 201):
        return True, ""
    body = client.json_of(response) or {}
    code = body.get("code") or body.get("error_code") or body.get("message") or "?"
    hint = " — см. TB-26 (модератор без карточки покупателя)" if response.status_code == 503 else ""
    return False, f"GET /admin/reviews вернул {response.status_code} {code}{hint}"


def _soft_delete_review(admin_client, review_id: str) -> str:
    """Гасит отзыв мягким удалением и проверяет, что он ушёл; "" если вышло.

    ``POST /admin/reviews/<id>`` сверяет ``version`` с текущей строкой: не
    совпала — ``409 REVIEW_CHANGED``. Отзыв, созданный через API витрины,
    стартует с ``version = 0`` (сиды стэнда — с ``1``), поэтому сначала берём
    актуальную ``version`` из админ-списка, а её номер и запасные версии.
    После правки строка обязана исчезнуть из админ-списка или перестать быть
    ``published`` — это и проверяем, поэтому протекающий отзыв тест не спрячет.
    """
    client = admin_client.client
    last_status = 0
    try:
        headers = _admin_headers(admin_client)
        row, list_status, _ = _find_admin_review(client, headers, review_id)
        if row is None and list_status not in (200, 201):
            return (
                f"GET /admin/reviews вернул {list_status}: удалить и проверить отзыв "
                "нечем — см. TB-26"
            )
        versions: list[int] = []
        if isinstance(row, dict) and isinstance(row.get("version"), int):
            versions.append(row["version"])
        versions.extend([0, 1, 2, 3])
        for version in dict.fromkeys(versions):
            response = client.post(
                f"/admin/reviews/{review_id}",
                json={"status": "deleted", "version": version},
                headers=headers,
                publishable_key=False,
            )
            last_status = response.status_code
            if response.status_code in (200, 201) and (client.json_of(response) or {}).get("success"):
                break

        row, list_status, _ = _find_admin_review(client, headers, review_id)
        if row is None:
            if list_status in (200, 201):
                return ""
            return (
                f"проверить уборку не удалось: GET /admin/reviews вернул {list_status} "
                "— см. TB-26"
            )
        status = str(row.get("status") or "")
        if status != "published":
            return ""
        return (
            f"отзыв {review_id} остался status = published "
            f"(POST /admin/reviews/{review_id} вернул {last_status})"
        )
    except (requests.RequestException, MedusaAdminError) as exc:  # сеть/таймаут — не провал сценария
        return f"уборка не выполнена: {exc}"
    return (
        f"POST /admin/reviews/{review_id} (status = deleted) вернул {last_status}: "
        "права модератора есть только у admin@tamir.local, а у учётки прогона их нет"
    )


@allure.story("Отзыв покупателя: публикация и витрина")
@allure.severity(allure.severity_level.CRITICAL)
def test_review_publishes_and_appears_on_storefront(api_client, mailbox, admin_client, run_tag, steps):
    """Капча и рейтинг отсекаются, отзыв сразу виден витрине, уборка проверена."""
    _skip_without_captcha_bypass()
    with steps("проверить, что модерация отзывов жива", "GET /admin/reviews = 200"):
        moderation_ok, moderation_note = _moderation_state(admin_client)
        if not moderation_ok:
            # Без админки отзывов нечем гасить свой отзыв, а неубранный отзыв
            # остаётся опубликованным на витрине стенда.
            pytest.skip(
                f"отзыв не создаём: погасить его нечем ({moderation_note})"
            )
    http = _http(api_client)
    email = _new_email(run_tag)
    token = _register_and_login(api_client, mailbox, email, steps)
    review_id = ""
    cleanup_note = ""

    try:
        with steps("post отзыва без поля капчи", "400 и код CAPTCHA_REQUIRED"):
            response = http.post(
                "/store/reviews",
                json={"product_id": PRODUCT_ID, "rating": 4, "body": REVIEW_BODY},
                headers=_bearer(api_client, token),
            )
            assert response.status_code == 400, (
                f"post без капчи вернулся {response.status_code}: {http.json_of(response)!r}"
            )
            rejected = http.json_of(response)
            assert rejected.get("code") == "CAPTCHA_REQUIRED", (
                f"код ошибки {rejected!r}, ожидался CAPTCHA_REQUIRED"
            )

        with steps("post отзыва с rating = 0", "400 и код RATING_REQUIRED"):
            response = http.post(
                "/store/reviews",
                json=with_captcha_bypass({"product_id": PRODUCT_ID, "rating": 0, "body": REVIEW_BODY}),
                headers=_bearer(api_client, token),
            )
            assert response.status_code == 400, (
                f"rating = 0 принят: {response.status_code} {http.json_of(response)!r}"
            )
            rejected = http.json_of(response)
            assert rejected.get("code") == "RATING_REQUIRED", (
                f"код ошибки {rejected!r}, ожидался RATING_REQUIRED"
            )

        with steps("опубликовать отзыв покупателя", "201 и объект отзыва"):
            response = http.post(
                "/store/reviews",
                json=with_captcha_bypass({"product_id": PRODUCT_ID, "rating": 5, "body": REVIEW_BODY}),
                headers=_bearer(api_client, token),
            )
            assert response.status_code == 201, (
                f"POST /store/reviews вернул {response.status_code}: {http.json_of(response)!r}"
            )
            review = http.json_of(response).get("review") or {}
            review_id = str(review.get("id") or "")
            assert review_id.startswith("rev_"), f"id отзыва {review_id!r} не начинается с rev_"
            assert review.get("rating") == 5, f"рейтинг в ответе: {review.get('rating')!r}"
            assert review.get("body") == REVIEW_BODY, "тело отзыва в ответе изменилось"
            allure.attach(review_id, "id созданного отзыва", allure.attachment_type.TEXT)

        with steps("отзыв сразу виден витрине без модерации", "200, review_id в списке"):
            response = http.get("/store/reviews", params={"product_id": PRODUCT_ID})
            assert response.status_code == 200, (
                f"GET /store/reviews вернул {response.status_code}: {http.json_of(response)!r}"
            )
            listing = http.json_of(response)
            ids = [str(r.get("id") or "") for r in listing.get("reviews") or []]
            assert review_id in ids, f"отзыва {review_id} нет в витрине: {ids!r}"

        with steps("GET витрины с токеном автора", "has_review = true для своего отзыва"):
            response = http.get(
                "/store/reviews",
                params={"product_id": PRODUCT_ID},
                headers=_bearer(api_client, token),
            )
            assert response.status_code == 200, (
                f"GET /store/reviews с токеном вернул {response.status_code}: {http.json_of(response)!r}"
            )
            mine = http.json_of(response)
            assert mine.get("has_review") is True, f"has_review для автора: {mine.get('has_review')!r}"
            assert int(mine.get("count") or 0) >= 1, f"count опубликованных отзывов: {mine.get('count')!r}"

        with steps("погасить отзыв за собой", "мягкое удаление принято админкой"):
            cleanup_note = _soft_delete_review(admin_client, review_id)

        with steps("отзыв убран с витрины", "review_id исчез из GET /store/reviews"):
            # Витрина отдаёт только ``published``: её ответ и есть проверка уборки,
            # поэтому протекающий отзыв тест уже не спрячет заметкой в отчёте.
            response = http.get("/store/reviews", params={"product_id": PRODUCT_ID})
            assert response.status_code == 200, (
                f"GET /store/reviews вернул {response.status_code}: {http.json_of(response)!r}"
            )
            ids = [str(r.get("id") or "") for r in http.json_of(response).get("reviews") or []]
            assert review_id not in ids, (
                f"отзыв {review_id} остался опубликованным на стенде — уборка не прошла: "
                f"{cleanup_note or 'админка не ответила (см. TB-26)'}"
            )
            if cleanup_note:
                allure.attach(cleanup_note, "уборка: замечание к админ-API", allure.attachment_type.TEXT)
            cleanup_note = ""
            review_id = ""
    finally:
        if review_id:
            cleanup_note = cleanup_note or _soft_delete_review(admin_client, review_id)
    if cleanup_note:
        # Сюда попадаем, только если шаг про витрину не дошёл до конца: тогда
        # протечка не проверена и её нужно показать в отчёте отдельным провалом.
        allure.attach(cleanup_note, "уборка отзыва", allure.attachment_type.TEXT)
        pytest.fail(f"уборка отзыва не подтверждена: {cleanup_note}")


@allure.story("Отзыв покупателя: публикация и витрина")
def test_review_requires_login(api_client, steps):
    """Аноним не может публиковать отзыв — ``401 LOGIN_REQUIRED`` (писем не тратит)."""
    _skip_without_captcha_bypass()
    http = _http(api_client)
    with steps("post отзыва без токена покупателя", "401, Medusa-ответ Unauthorized"):
        response = http.post(
            "/store/reviews",
            json=with_captcha_bypass({"product_id": PRODUCT_ID, "rating": 4, "body": REVIEW_BODY}),
        )
        assert response.status_code == 401, (
            f"анонимный post вернулся {response.status_code}: {response.text!r}"
        )
        rejected = http.json_of(response)
        # Стенд отсекает анонима на Middleware Medusa ({"message": "Unauthorized"}),
        # поэтому фирменный код LOGIN_REQUIRED в теле может и не дойти до нас.
        assert rejected.get("message") == "Unauthorized" or rejected.get("code") == "LOGIN_REQUIRED", (
            f"тело 401: {rejected!r}"
        )
