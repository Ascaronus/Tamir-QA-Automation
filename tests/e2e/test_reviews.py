"""E2E-13. Гость читает отзывы в карточке товара: счётчик, список и вход вместо формы.

Проверка идёт по живому стенду ``https://test.tamir.rs`` (без моков). Тест берёт из
store-API товар, у которого есть опубликованные отзывы, и сверяет то, что видит
покупатель, с тем, что отдаёт ``GET /store/reviews``: блок ``REVIEWS_SECTION`` на месте,
в заголовке то же число, что вернул API, переключатель ``REVIEWS_EXPAND`` снимает
атрибут ``hidden`` со списка отзывов, а в списке виден текст отзыва из API.

Отдельно проверяется право гостя: писать отзыв может только вошедший покупатель,
поэтому у гостя формы ``REVIEW_WRITE_FORM`` быть не должно, а вместо неё — подсказка
войти. Модалка Cloudflare Turnstile тоже не появляется: создание отзыва намеренно
не проверяется ни в браузере, ни через API, потому что обход Turnstile на стенде
выключен и токен получить нечем.

Если на стенде нет ни одного опубликованного отзыва, тест честно скипается: сверять
счётчик и список не с чем, а придумывать отзывы нельзя — чужие данные не трогаем.
"""

from __future__ import annotations

import re

import allure
import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CAPTCHA_DIALOG,
    CAPTCHA_TITLE_RE,
    REVIEW_CONTENT,
    REVIEW_WRITE_ANCHOR,
    REVIEWS_EXPAND,
    REVIEWS_HEADING,
    REVIEWS_SECTION,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

_PRODUCT_FIELDS = "id,handle,title"

_TOTAL_RE = re.compile(r"\((\d+)\)")
# гостю показывают подсказку войти: «...potrebno je da se prijavite» / «Prijavite se»
_GATE_RE = re.compile(r"javite|login|loguj", re.I)


def _store_reviews(store: MedusaStoreClient, product_id: str) -> dict:
    """``GET /store/reviews?product_id=..&offset=0`` — тот же ответ, что читает страница."""
    return store._get(f"/store/reviews?product_id={product_id}&offset=0")


def _pick_product_with_reviews(store: MedusaStoreClient) -> tuple[dict, dict]:
    """Первый товар каталога, у которого ``count > 0``; если таких нет — честный skip."""
    body = store._get(f"/store/products?limit=24&country_code=rs&fields={_PRODUCT_FIELDS}")
    products = body.get("products") or []
    if not products:
        pytest.skip("каталог не отдаёт товары — проверять блок отзывов не на чём")

    scanned: list[str] = []
    for product in products:
        product_id = str(product.get("id") or "")
        if not product_id:
            continue
        payload = _store_reviews(store, product_id)
        scanned.append(f"{product.get('handle')}={payload.get('count')}")
        if int(payload.get("count") or 0) > 0 and (payload.get("reviews") or []):
            allure.attach(", ".join(scanned), "поиск товара с отзывами", allure.attachment_type.TEXT)
            return product, payload

    allure.attach(", ".join(scanned), "отзывов нет ни у одного товара", allure.attachment_type.TEXT)
    pytest.skip("на стенде нет отзывов для теста")


def _gate_text(shop: Storefront) -> str:
    """Что витрина показывает вместо формы отзыва.

    ``div#review-write`` в SSR пустой: подсказку «Prijavite se …» рисует клиент,
    когда ``AuthProvider`` узнал состояние входа, поэтому ждём гидратации через
    ``Storefront.wait_for_review_gate()``.
    """
    return shop.wait_for_review_gate()


def _captcha_dialogs(page) -> str:
    """Текст модалок капчи на странице ('', если их нет)."""
    found: list[str] = []
    boxes = page.locator(CAPTCHA_DIALOG)
    for index in range(boxes.count()):
        text = boxes.nth(index).inner_text().strip()
        if CAPTCHA_TITLE_RE.search(text):
            found.append(text)
    return " | ".join(found)


@allure.story("Блок отзывов в карточке товара")
@allure.severity(allure.severity_level.NORMAL)
def test_guest_reads_reviews_block(page, steps, api_client: MedusaStoreClient):
    """Гость видит отзывы и счётчик, но не форму написания отзыва."""
    shop = Storefront(page)

    with steps("найти в API товар с опубликованными отзывами", "есть товар, у которого count > 0"):
        product, payload = _pick_product_with_reviews(api_client)
        handle = str(product["handle"])
        reviews = payload.get("reviews") or []
        expected_count = int(payload.get("count") or 0)
        allure.attach(
            f"{handle}: count={expected_count}, rating={reviews[0].get('rating')}, "
            f"status={reviews[0].get('status')}",
            "что сверяем на витрине",
            allure.attachment_type.TEXT,
        )

    with steps("открыть карточку товара", f"блок {REVIEWS_SECTION} на месте, заголовок непустой"):
        shop.open_product(f"/rs/products/{handle}")
        assert page.locator(REVIEWS_SECTION).count() > 0, (
            f"в карточке {handle!r} нет блока отзывов ({REVIEWS_SECTION})"
        )
        heading = shop.reviews_heading_text()
        assert heading, f"заголовок блока отзывов пустой: {REVIEWS_HEADING!r} ничего не нашло"

    with steps("сверить число отзывов на витрине с API", "в заголовке то же N, что вернул API"):
        shown = _TOTAL_RE.search(heading)
        assert shown, f"в заголовке {heading!r} нет числа отзывов в скобках"
        assert int(shown.group(1)) == expected_count, (
            f"витрина показывает {shown.group(1)!r} по заголовку {heading!r}, "
            f"a GET /store/reviews отдаёт count={expected_count}"
        )

    with steps("раскрыть список отзывов кнопкой REVIEWS_EXPAND",
               "у div#product-review-content снят hidden, отзывы отрисованы"):
        toggle = page.locator(REVIEWS_EXPAND)
        assert toggle.count() > 0, f"на карточке нет переключателя списка ({REVIEWS_EXPAND})"
        content = page.locator(REVIEW_CONTENT)
        content.first.wait_for(state="attached", timeout=20_000)
        if content.first.get_attribute("hidden") is not None:
            toggle.first.click()
            assert shop.wait_until(
                lambda: content.first.get_attribute("hidden") is None, 10.0
            ), "после клика по REVIEWS_EXPAND список отзывов остался скрытым"
        assert content.first.get_attribute("hidden") is None, (
            "список отзывов скрыт: атрибут hidden не снят"
        )
        block_text = " ".join(content.first.inner_text().split())
        snippet = " ".join(str(reviews[0].get("body") or "").split())[:40].strip()
        assert block_text, "раскрытый список отзывов пуст"
        assert snippet and snippet in block_text, (
            f"текст отзыва из API не найден в списке витрины: {snippet!r} ≠ {block_text[:200]!r}"
        )

    with steps("проверить, что гость не может писать отзыв",
               "формы нет, вместо неё подсказка войти, модалки Turnstile нет"):
        assert shop.review_form_is_visible() is False, (
            "гость получил форму написания отзыва — писать может только вошедший покупатель"
        )
        gate = _gate_text(shop)
        assert _GATE_RE.search(gate), f"вместо подсказки войти показано: {gate[:200]!r}"
        assert not _captcha_dialogs(page), (
            "на карточке появилась модалка Cloudflare Turnstile — создание отзыва не проверяется"
        )

    with steps("проверить звёздный рейтинг первой карточки отзыва",
               "rating из API в диапазоне 1..5, текст блока отзывов непустой"):
        for review in reviews:
            rating = review.get("rating")
            assert isinstance(rating, int) and 1 <= rating <= 5, (
                f"у отзыва {review.get('id')} рейтинг {rating!r} вне диапазона 1..5"
            )
        assert block_text, "текст блока отзывов на витрине пустой"
        # Голосовать не пробуем: POST /store/reviews/<id>/vote требует сессию покупателя.
