"""UI модерации отзывов в админке Tamir (E2E-28…E2E-31).

Страница модерации живёт на хосте API: ``{API_URL}/app/reviews`` (замерено
зондом 28.11.2026, headless Chromium 1440x900). Контракт UI — по живому HTML
``/app/reviews``, а не по догадкам:

* ``h1`` «Reviews / Recenzije», рядом кнопка «Refresh / Osveži»;
* отзывы — не таблица, а карточки ``<article>``: в шапке карточки ``<strong>``
  (товар), ``<span>`` рейтинга (``5.0/5``), ``<span>`` статуса (слово API:
  ``published`` / ``deleted``), ``<span>`` счётчика полезности; ниже ``<p>``
  «имя — email — дата»; дальше форма ``<form>`` с ``input[name=name]`` и
  ``textarea[name=body]`` и кнопками: у ``published`` — «Save text / Sačuvaj
  tekst» (submit), «Hide / Sakrij», «Delete / Obriši»; у ``deleted`` —
  «Restore / Vrati»;
* пагинация «Previous / Prethodno» / «Next / Sledeće»; на странице нет ни
  ``<select>``-фильтров, ни ссылок на детальную страницу отзыва — поэтому
  проверка статусов идёт через админ-API (``GET /admin/reviews``), а UI
  отвечает за сами действия;
* список отсортирован свежими сверху, фильтров по id нет — свой отзыв ищем
  текстом email автора в карточке.

Данные: один отзыв на модуль создаётся API-рецептом из
``tests/scenario/test_review_write_api.py`` (регистрация с капча-обходом,
``POST /store/reviews``) и в финале мягко удаляется через
``POST /admin/reviews/<id>`` — на стенде не остаётся опубликованного мусора
(удалённые строки видны в списке, это нормально).

TB-28 («модерация отзывов»): если ``GET /admin/reviews`` снова ляжет, фикстура
скипается с явной причиной — заморозка стенда не должна выглядеть как провал
теста; когда маршрут жив (замер 28.11.2026 — ``200``), тесты идут по UI.
Порядок тестов в файле: контент-проверки до раундтрипа статусов, чтобы
неудачная кнопка не ломала соседний кейс.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any, Optional

import allure
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from qa.config import QaConfig
from qa.http import CAPTCHA_BYPASS_ENV, captcha_bypass_token, with_captcha_bypass

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

LOGIN_PATH = "/app/login"
REVIEWS_PATH = "/app/reviews"
#: тот же товар каталога стэнда, что в tests/scenario/test_review_write_api.py
PRODUCT_ID = "prod_01M4R26AQMQTKE7PHGQV0F3TC5"
REVIEW_BODY = "QA reviews-admin review: moderation roundtrip from the UI."
RATING = 5


def _admin_url(config: QaConfig, path: str) -> str:
    """Ссылка админки: она на хосте API, а не витрины (замерено зондом)."""
    return f"{config.api_url.rstrip('/')}{path}"


def _login_form(page, email: str, password: str) -> None:
    """Заполняет и отправляет единственную форму входа (``action=/app/login``)."""
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')


def _wait_logged_in(page) -> None:
    """Ждём, пока роутер унесёт нас со страницы входа."""
    page.wait_for_function(
        "() => !window.location.pathname.startsWith('/app/login')", timeout=30_000
    )


# ------------------------------------------------------------------ admin API


def _admin_headers(admin_client) -> dict[str, str]:
    """Заголовок модератора для прямых вызовов админ-API."""
    return {"Authorization": f"Bearer {admin_client.login()}"}


def _moderation_state(admin_client) -> tuple[bool, str]:
    """Жива ли модерация отзывов: ``(да, "")`` или ``(нет, текст для skip)``."""
    client = admin_client.client
    try:
        response = client.get(
            "/admin/reviews?limit=1&offset=0&fields=id,status,version",
            headers=_admin_headers(admin_client),
            publishable_key=False,
        )
    except Exception as exc:
        return False, f"GET /admin/reviews не ответил: {exc}"
    if response.status_code in (200, 201):
        return True, ""
    return False, (
        f"GET /admin/reviews вернул {response.status_code}: модерация отзывов "
        "недоступна (TB-26/TB-28)"
    )


def _find_admin_review(
    client, headers: dict[str, str], review_id: str
) -> tuple[Optional[dict[str, Any]], int, Any]:
    """Строка отзыва в ``GET /admin/reviews``; ``(None, код, тело)``, если строки нет.

    Фильтров по id на витрине модерации нет, поэтому проходим до трёх страниц
    по 100 — свежий отзыв всегда в первых строках (список свежими сверху).
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


def _admin_review_row(admin_client, review_id: str) -> Optional[dict[str, Any]]:
    row, *_ = _find_admin_review(admin_client.client, _admin_headers(admin_client), review_id)
    return row


def _wait_admin_status(admin_client, review_id: str, expected: str, timeout: float = 20.0) -> str:
    """Ждёт ``status == expected`` в ``GET /admin/reviews``; возвращает последний виденный."""
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        row = _admin_review_row(admin_client, review_id)
        last = str((row or {}).get("status") or "")
        if last == expected or time.monotonic() >= deadline:
            return last
        time.sleep(1.5)


# ---------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def admin_credentials() -> tuple[str, str]:
    """Логин/пароль админки стэнда только из ``.env`` (значения не печатаем)."""
    email = os.getenv("MEDUSA_ADMIN_EMAIL", "")
    password = os.getenv("MEDUSA_ADMIN_PASSWORD", "")
    if not (email and password):
        pytest.skip("нужны MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD в .env (admin API)")
    return email, password


@pytest.fixture(scope="module")
def reviews_admin_context(browser, config: QaConfig, admin_credentials: tuple[str, str]):
    """Один авторизованный контекст на модуль: вход выполняется ровно один раз."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(_admin_url(config, LOGIN_PATH), wait_until="domcontentloaded")
        _login_form(page, *admin_credentials)
        _wait_logged_in(page)
    except Exception as exc:  # не печатаем реквизиты — только суть сбоя
        context.close()
        pytest.fail(f"вход в админку стэнда не удался: {type(exc).__name__}")
    page.close()
    yield context
    context.close()


@pytest.fixture
def reviews_page(reviews_admin_context, config: QaConfig):
    """Свежая страница ``/app/reviews`` под одной админ-сессией модуля."""
    page = reviews_admin_context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(_admin_url(config, REVIEWS_PATH), wait_until="domcontentloaded")
    page.locator("article").first.wait_for(state="visible", timeout=30_000)
    try:
        yield page, errors
    finally:
        page.close()


@pytest.fixture(scope="module")
def qa_review(api_client, mailbox, admin_client, run_tag) -> dict[str, str]:
    """Свежий отзыв стэнда через API: ``{"id", "email"}``; в финале — мягкое удаление."""
    if not captcha_bypass_token():
        pytest.skip(f"{CAPTCHA_BYPASS_ENV} не задана: обойти Turnstile нечем")
    moderation_ok, moderation_note = _moderation_state(admin_client)
    if not moderation_ok:
        pytest.skip(moderation_note)

    http = api_client.client
    email = f"qa-reviews-{run_tag}-{uuid.uuid4().hex[:8]}@example.com"
    password = f"Qa!{uuid.uuid4().hex}x"

    response = http.post("/store/registration/start", json=with_captcha_bypass({"email": email}))
    assert response.status_code == 200, (
        f"start регистрации вернул {response.status_code}: {http.json_of(response)!r}"
    )
    challenge_id = str(http.json_of(response).get("challenge_id") or "")
    assert challenge_id, "регистрация без challenge_id"

    code = mailbox.wait_for_code(email)
    response = http.post(
        "/store/registration/verify",
        json={
            "email": email,
            "challenge_id": challenge_id,
            "code": code,
            "password": password,
            "first_name": "QA",
            "last_name": "Reviews",
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

    response = http.post("/auth/customer/emailpass", json={"email": email, "password": password})
    assert response.status_code == 200, (
        f"вход свежего покупателя не прошёл: {response.status_code} {response.text!r}"
    )
    logged_in = http.json_of(response)
    token = str(logged_in.get("token") or logged_in.get("access_token") or "")
    assert token, f"в ответе входа нет token: {sorted(logged_in)!r}"

    response = http.post(
        "/store/reviews",
        json=with_captcha_bypass({"product_id": PRODUCT_ID, "rating": RATING, "body": REVIEW_BODY}),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, (
        f"POST /store/reviews вернул {response.status_code}: {http.json_of(response)!r}"
    )
    review = http.json_of(response).get("review") or {}
    review_id = str(review.get("id") or "")
    assert review_id.startswith("rev_"), f"id отзыва {review_id!r} не начинается с rev_"
    allure.attach(review_id, "id созданного отзыва", allure.attachment_type.TEXT)

    yield {"id": review_id, "email": email}

    try:  # уборка: мягко гасим свой отзыв, опубликованным он на стенде не остаётся
        row = _admin_review_row(admin_client, review_id)
        if row is not None and str(row.get("status")) != "deleted":
            response = admin_client.client.post(
                f"/admin/reviews/{review_id}",
                json={"status": "deleted", "version": row.get("version")},
                headers=_admin_headers(admin_client),
                publishable_key=False,
            )
            if response.status_code not in (200, 201):
                allure.attach(
                    f"POST /admin/reviews/{review_id} вернул {response.status_code}",
                    "уборка отзыва",
                    allure.attachment_type.TEXT,
                )
    except Exception as exc:  # сбой уборки не перекрывает результат модуля
        allure.attach(f"уборка не выполнена: {exc}", "уборка отзыва", allure.attachment_type.TEXT)


# ---------------------------------------------------------------- helpers UI


def _card(page, needle: str):
    """Карточка ``<article>``, в тексте которой есть ``needle`` (email автора)."""
    return page.locator("article").filter(has_text=needle)


def _open_reviews(page, config: QaConfig) -> None:
    page.goto(_admin_url(config, REVIEWS_PATH), wait_until="domcontentloaded")
    page.locator("article").first.wait_for(state="visible", timeout=30_000)


def _wait_card(page, needle: str, attempts: int = 3):
    """Ищем карточку отзыва, перезагружая страницу: SSR-список мог отстать."""
    for _ in range(attempts):
        card = _card(page, needle)
        try:
            card.first.wait_for(state="visible", timeout=10_000)
            return card.first
        except PlaywrightTimeoutError:
            page.wait_for_timeout(2_000)
            page.reload(wait_until="domcontentloaded")
            page.locator("article").first.wait_for(state="visible", timeout=30_000)
    pytest.fail(f"в списке /app/reviews не найдена карточка с текстом {needle!r}")


# ---------------------------------------------------------------- тесты


@allure.story("Модерация отзывов: экран /app/reviews")
@allure.severity(allure.severity_level.NORMAL)
def test_reviews_page_shows_moderation_ui(reviews_page, steps):
    """E2E-28: ``h1`` «Reviews / Recenzije», карточки-``article``, форма и кнопки.

    TB-28: пока экран доступен, тест проверяет его контракт; если модерация
    ляжет, ``GET /admin/reviews`` покажет это ещё на фикстуре (skip), а UI-
    тест честно упадёт здесь — оба варианта в отчёте различимы.
    """
    page, errors = reviews_page

    steps("Админ открывает /app/reviews", "h1 «Reviews / Recenzije»")
    heading = page.locator("h1").first
    heading.wait_for(timeout=15_000)
    assert "Reviews / Recenzije" in heading.inner_text()

    steps("Кнопка обновления на месте", "«Refresh / Osveži»")
    assert page.get_by_role("button", name=re.compile("Refresh", re.I)).count() >= 1

    steps("Отзывы — карточки, а не таблица", "article c формой и «Save text»")
    assert page.locator("article").count() >= 1
    card = page.locator("article").first
    assert card.locator("form").count() == 1
    assert card.get_by_role("button", name=re.compile("Save text", re.I)).count() == 1

    steps("Фильтров-селектов на странице нет", "select count = 0")
    assert page.locator("select").count() == 0
    assert not errors, f"JS-ошибки на /app/reviews: {errors[:3]!r}"


@allure.story("Модерация отзывов: новый отзыв в списке")
@allure.severity(allure.severity_level.NORMAL)
def test_created_review_visible_in_list(qa_review, reviews_page, admin_client, steps):
    """E2E-29: отзыв из API появляется в списке с email автора и бейджем published."""
    page, _errors = reviews_page

    steps("Список отсортирован свежими сверху", "свой отзыв на первой странице")
    card = _wait_card(page, qa_review["email"])

    steps("Бейдж статуса карточки", "published")
    assert card.locator("span", has_text="published").count() >= 1

    steps("Админ-API подтверждает тот же статус", "GET /admin/reviews: published")
    row = _admin_review_row(admin_client, qa_review["id"])
    assert row is not None, "отзыва нет в GET /admin/reviews"
    assert str(row.get("status")) == "published", f"статус в админ-API: {row.get('status')!r}"


@allure.story("Модерация отзывов: содержимое карточки")
@allure.severity(allure.severity_level.NORMAL)
def test_review_card_content(qa_review, reviews_page, steps):
    """E2E-31: карточка показывает товар, рейтинг, автора и редактируемую форму."""
    page, _errors = reviews_page
    card = _wait_card(page, qa_review["email"])

    steps("Шапка карточки: товар и рейтинг", "непустой <strong> и «N/5»")
    product = card.locator("strong").first.inner_text().strip()
    assert product, "название товара в карточке пустое"
    assert re.search(r"\d+(?:[.,]\d+)?\s*/\s*5", card.inner_text()), (
        f"в карточке нет рейтинга вида «5.0/5»: {card.inner_text()[:200]!r}"
    )

    steps("Автор карточки", "email покупателя в тексте")
    assert qa_review["email"] in card.inner_text()

    steps("Форма редактирования заполнена текущими данными", "input[name=name] + textarea[name=body]")
    assert card.locator('input[name="name"]').count() == 1
    body_field = card.locator('textarea[name="body"]')
    assert body_field.count() == 1
    assert REVIEW_BODY in (body_field.first.input_value() or ""), (
        "текст отзыва в форме не совпадает с созданным"
    )


@allure.story("Модерация отзывов: Hide/Restore через UI")
@allure.severity(allure.severity_level.CRITICAL)
def test_hide_and_restore_roundtrip_via_ui(qa_review, reviews_page, admin_client, config, steps):
    """E2E-30: «Hide / Sakrij» и «Restore / Vrati» меняют статус (контроль по админ-API)."""
    page, _errors = reviews_page
    card = _wait_card(page, qa_review["email"])

    steps("Клик «Hide / Sakrij»", "GET /admin/reviews: status = hidden")
    card.get_by_role("button", name=re.compile("Hide", re.I)).first.click()
    status = _wait_admin_status(admin_client, qa_review["id"], "hidden")
    assert status == "hidden", f"после «Hide / Sakrij» статус {status!r}, ожидался hidden"

    steps("Клик «Restore / Vrati»", "GET /admin/reviews: status = published")
    _open_reviews(page, config)
    card = _wait_card(page, qa_review["email"])
    card.get_by_role("button", name=re.compile("Restore", re.I)).first.click()
    status = _wait_admin_status(admin_client, qa_review["id"], "published")
    assert status == "published", (
        f"после «Restore / Vrati» статус {status!r}, ожидался published"
    )

