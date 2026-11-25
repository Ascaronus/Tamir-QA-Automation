"""E2E-20. Голоса в отзывах: гостю вместо кнопок показывают ссылки входа, а покупатель голосует и видит изменённый счёт."""

# Файл использует сессионную фикстуру logged_in_page (ровно один вход на прогон —
# стенд разрешает не больше 5 входов покупателя в час), поэтому его запускают одним
# прогоном за час и отдельно от гостевых файлов.

from __future__ import annotations

import allure
import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    REVIEW_ACTIONS,
    REVIEW_BODIES,
    REVIEW_CARD,
    REVIEW_EXPAND_TOGGLES,
    REVIEW_SCORE,
    REVIEW_VOTE_BUTTONS,
    REVIEW_VOTE_LOGIN_LINKS,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

_PRODUCT_FIELDS = "id,handle,title"


def _pick_product_with_reviews(store: MedusaStoreClient) -> tuple[dict, dict]:
    """Первый товар каталога, у которого ``count > 0``; если таких нет — честный skip."""
    body = store._get(f"/store/products?limit=24&country_code=rs&fields={_PRODUCT_FIELDS}")
    products = body.get("products") or []
    if not products:
        pytest.skip("каталог не отдаёт товары — проверять голоса в отзывах не на чём")

    scanned: list[str] = []
    for product in products:
        product_id = str(product.get("id") or "")
        if not product_id:
            continue
        payload = store._get(f"/store/reviews?product_id={product_id}&offset=0")
        scanned.append(f"{product.get('handle')}={payload.get('count')}")
        if int(payload.get("count") or 0) > 0 and (payload.get("reviews") or []):
            allure.attach(", ".join(scanned), "поиск товара с отзывами", allure.attachment_type.TEXT)
            return product, payload

    allure.attach(", ".join(scanned), "отзывов нет ни у одного товара", allure.attachment_type.TEXT)
    pytest.skip("на стенде нет опубликованных отзывов — голосовать не по чему")


def _open_reviews_of(shop: Storefront, product: dict) -> None:
    """Карточка товара, список отзывов развёрнут, карточек отзывов дождались."""
    page = shop.page
    handle = str(product["handle"])
    shop.open_product(f"/rs/products/{handle}")
    assert page.locator(REVIEW_CARD).count() > 0 or page.locator(REVIEW_EXPAND_TOGGLES).count() > 0, (
        f"в карточке {handle!r} нет ни {REVIEW_CARD}, ни переключателей {REVIEW_EXPAND_TOGGLES}"
    )
    bodies = page.locator(REVIEW_BODIES)
    if bodies.count() > 0 and bodies.first.get_attribute("hidden") is not None:
        page.locator(REVIEW_EXPAND_TOGGLES).first.click()
        assert shop.wait_until(lambda: bodies.first.get_attribute("hidden") is None, 10.0), (
            f"после клика по {REVIEW_EXPAND_TOGGLES} список отзывов остался скрытым"
        )
    assert shop.wait_until(lambda: page.locator(REVIEW_CARD).count() > 0, 15.0), (
        f"на странице {handle!r} так и не появилось ни одной {REVIEW_CARD}"
    )


def _score_of_first_review(page) -> int:
    """Число из span счётчика первой карточки: ``+2`` -> 2, ``-1`` -> -1."""
    return int(page.locator(REVIEW_SCORE).first.inner_text().strip())


@allure.story("Голосование в отзывах")
@allure.severity(allure.severity_level.NORMAL)
def test_guest_sees_login_links_instead_of_vote_buttons(page, steps, api_client: MedusaStoreClient):
    """Гостю вместо кнопок «за/против» показывают ссылки на вход."""
    shop = Storefront(page)

    with steps("найти в API товар с опубликованными отзывами", "есть товар, у которого count > 0"):
        product, payload = _pick_product_with_reviews(api_client)
        allure.attach(
            f"{product['handle']}: count={payload.get('count')}",
            "товар с отзывами",
            allure.attachment_type.TEXT,
        )

    with steps("открыть карточку товара и развернуть отзывы", f"{REVIEW_CARD} видна"):
        _open_reviews_of(shop, product)

    with steps("сверить блок действий первой карточки для гостя",
               f"ссылок {REVIEW_VOTE_LOGIN_LINKS} >= 1, кнопок {REVIEW_VOTE_BUTTONS} нет"):
        actions = page.locator(REVIEW_ACTIONS)
        assert shop.wait_until(lambda: actions.count() > 0, 15.0), (
            f"в карточке отзыва нет блока действий ({REVIEW_ACTIONS})"
        )
        vote_buttons = page.locator(REVIEW_VOTE_BUTTONS)
        assert vote_buttons.count() == 0, (
            f"гость видит кнопки голосования {REVIEW_VOTE_BUTTONS} "
            f"(count={vote_buttons.count()}) — голосовать может только вошедший покупатель"
        )
        login_links = page.locator(REVIEW_VOTE_LOGIN_LINKS)
        assert login_links.count() > 0, (
            f"вместо кнопок голосования нет ссылок на вход ({REVIEW_VOTE_LOGIN_LINKS}); "
            f"html блока: {actions.first.inner_text()!r}"
        )
        href = login_links.first.get_attribute("href") or ""
        assert "prijava" in href or "login" in href, (
            f"ссылка вместо кнопки голосования ведёт не на вход: href={href!r}"
        )
        allure.attach(
            f"links={login_links.count()}, href={href}",
            "гостевые ссылки входа в отзывах",
            allure.attachment_type.TEXT,
        )


@allure.story("Голосование в отзывах")
@allure.severity(allure.severity_level.NORMAL)
def test_logged_in_buyer_vote_changes_score_and_locks_buttons(logged_in_page, steps,
                                                              api_client: MedusaStoreClient):
    """Покупатель голосует по первой кнопке первой карточки: счёт меняется на ±1, кнопки гаснут."""
    page = logged_in_page
    shop = Storefront(page)

    with steps("найти в API товар с опубликованными отзывами", "есть товар, у которого count > 0"):
        product, payload = _pick_product_with_reviews(api_client)

    with steps("открыть карточку товара залогиненным и развернуть отзывы",
               f"{REVIEW_CARD} и кнопки {REVIEW_VOTE_BUTTONS} видны"):
        _open_reviews_of(shop, product)
        vote_buttons = page.locator(REVIEW_VOTE_BUTTONS)
        assert shop.wait_until(lambda: vote_buttons.count() > 0, 15.0), (
            f"у залогиненного покупателя так и не появились кнопки {REVIEW_VOTE_BUTTONS} "
            f"(count={vote_buttons.count()})"
        )
        assert vote_buttons.count() >= 2, (
            f"у первой карточки ожидалось 2 кнопки (за/против), нашли {vote_buttons.count()} "
            f"({REVIEW_VOTE_BUTTONS})"
        )
        if vote_buttons.first.is_disabled():
            pytest.skip(
                f"кнопка {REVIEW_VOTE_BUTTONS} уже disabled до клика — этот аккаунт, "
                "по-видимому, уже голосовал в первом отзыве; повторный вход не делаем"
            )

    with steps("прочитать счёт до голоса", f"{REVIEW_SCORE} отдаёт число"):
        before = _score_of_first_review(page)

    with steps("кликнуть первую кнопку голосования",
               f"счёт {REVIEW_SCORE} изменился ровно на 1 (±), направление не важно"):
        vote_buttons.first.click()
        score_now: list[int] = []

        def _changed() -> bool:
            try:
                score_now.clear()
                score_now.append(_score_of_first_review(page))
            except (ValueError, AssertionError):
                return False
            return score_now[0] != before

        assert shop.wait_until(_changed, 10.0), (
            f"счёт {REVIEW_SCORE} не изменился за 10 с после клика по кнопке: "
            f"до={before}, после={score_now[0] if score_now else '?'}"
        )
        delta = score_now[0] - before
        assert delta in (-1, 1), (
            f"голос изменил счёт на {delta:+d} (до={before}, после={score_now[0]}) — "
            "ожидалось ±1"
        )
        allure.attach(
            f"до={before}, после={score_now[0]}",
            "счёт отзыва после голоса",
            allure.attachment_type.TEXT,
        )

    with steps("сверить состояние кнопок после голоса",
               f"обе кнопки {REVIEW_VOTE_BUTTONS} первой карточки disabled (один голос на отзыв)"):
        first = page.locator(REVIEW_VOTE_BUTTONS).nth(0)
        second = page.locator(REVIEW_VOTE_BUTTONS).nth(1)
        assert first.is_disabled() and second.is_disabled(), (
            f"после голоса кнопки не погасли: nth(0).disabled={first.is_disabled()}, "
            f"nth(1).disabled={second.is_disabled()} ({REVIEW_VOTE_BUTTONS})"
        )
