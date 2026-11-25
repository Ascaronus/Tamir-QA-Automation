"""E2E-14. Избранное: сердечко на PDP добавляет товар, страница /rs/wishlist и счётчик
шапки показывают обе позиции; снятое исчезает после перезагрузки. Избранное аккаунта
возвращается к исходному состоянию — аккаунт на стэнде общий.
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CATALOG_CARD_ITEM,
    FAVORITE_BUTTON,
    PRODUCT_CARD,
    WISHLIST_HEADING,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

#: href товаров, добавленных этим тестом: уборка снимает их с избранного в любом случае
_FAVORITES_ADDED: list[str] = []


@pytest.fixture(autouse=True)
def _restore_favorites(logged_in_page, api_client: MedusaStoreClient):
    """Возвращает избранное аккаунта к исходному состоянию (шаг 6 задания)."""
    del api_client  # избранное убирается витриной: админ-эндпоинтов /admin/wishlists нет
    yield
    shop = Storefront(logged_in_page)
    for href in list(_FAVORITES_ADDED):
        try:
            shop.open_product(href)
            shop.wait_favorite_ready()
            if shop.favorite_state() is True:
                shop.toggle_favorite()
                shop.wait_favorite_state(False, timeout_s=20.0)
        except Exception:  # noqa: BLE001 — уборка не перекрывает результат теста
            pass
    _FAVORITES_ADDED.clear()


def _catalog_picks(shop: Storefront, want: int = 2) -> list[tuple[str, str]]:
    """Берёт `want` товаров каталога: (href, заголовок) карточек с кнопкой избранного."""
    shop.goto_catalog()
    shop.wait_until(lambda: shop.page.locator(CATALOG_CARD_ITEM).count() > 0, timeout_s=30.0)
    picks: list[tuple[str, str]] = []
    cards = shop.page.locator(CATALOG_CARD_ITEM)
    for i in range(min(cards.count(), 12)):
        card = cards.nth(i)
        if card.locator(FAVORITE_BUTTON).count() == 0:
            continue
        href = card.locator(PRODUCT_CARD).first.get_attribute("href") or ""
        title = (card.locator(PRODUCT_CARD).first.inner_text() or "").strip()
        if href and title and all(href != h for h, _ in picks):
            picks.append((href, title))
        if len(picks) >= want:
            break
    return picks


def _wishlist_snapshot(shop: Storefront) -> str:
    """Что витрина показывает в избранном: позиции, «нет в продаже», пустой список, счётчик."""
    return (
        f"позиций {shop.wishlist_item_count()}, "
        f"недоступных {shop.wishlist_unavailable_count()}, "
        f"пусто={shop.wishlist_is_empty()}, счётчик={shop.wishlist_header_count()}"
    )


def test_wishlist_add_remove_and_pdp_state(logged_in_page, api_client: MedusaStoreClient, steps):
    """Сердечко на PDP добавляет и снимает товар; страница и счётчик согласованы с PDP."""
    del api_client  # избранное читаем витриной (счётчик и карточки), а не счётом по API
    page = logged_in_page
    shop = Storefront(page)

    with steps("запомнить исходное состояние избранного аккаунта",
               "список пуст или в нём уже есть позиции"):
        shop.open_wishlist()
        shop.page.wait_for_selector(WISHLIST_HEADING, timeout=15_000)
        shop.wait_until(lambda: shop.wishlist_is_empty() or shop.wishlist_item_count() > 0,
                        timeout_s=20.0)
        initial = max(shop.wishlist_item_count(), 0)

    picks = _catalog_picks(shop, want=2)
    assert len(picks) == 2, f"в каталоге не нашлось двух товаров с кнопкой избранного: {picks}"
    first_href, first_title = picks[0]
    second_href, second_title = picks[1]

    with steps(f"добавить «{first_title}» в избранное прямо на PDP",
               "кнопка отвечает aria-pressed=true"):
        shop.open_product(first_href)
        shop.wait_favorite_ready()
        shop.toggle_favorite()
        _FAVORITES_ADDED.append(first_href)
        assert shop.wait_favorite_state(True, timeout_s=20.0), (
            f"сердечко не нажалось на PDP: {_wishlist_snapshot(shop)}"
        )

    with steps("второй товар добавляется так же",
               "на повторном заходе на PDP сердечко уже нажато"):
        shop.open_product(second_href)
        shop.wait_favorite_ready()
        shop.toggle_favorite()
        _FAVORITES_ADDED.append(second_href)
        assert shop.wait_favorite_state(True, timeout_s=20.0), (
            f"сердечко не нажалось на PDP: {_wishlist_snapshot(shop)}"
        )
        shop.open_product(second_href)
        shop.wait_favorite_ready()
        assert shop.favorite_state() is True, "избранное не вернулось на повторном заходе на PDP"

    with steps("страница /rs/wishlist показывает добавленные позиции",
               f"число позиций = {initial} + 2, оба заголовка на месте"):
        shop.open_wishlist()
        shop.page.wait_for_selector(WISHLIST_HEADING, timeout=15_000)
        assert shop.wait_wishlist_size(initial + 2), (
            f"избранное выросло не на 2: было {initial}, {_wishlist_snapshot(shop)}"
        )
        body = page.locator("main").inner_text()
        for title in (first_title, second_title):
            assert title in body, f"в избранном нет позиции «{title}»"

    with steps("счётчик в шапке равен числу позиций в списке",
               f"ожидание = {initial + 2}"):
        assert shop.wishlist_header_count() == initial + 2, (
            f"счётчик в шапке {shop.wishlist_header_count()} != позиций {initial + 2}"
        )

    with steps("снятие товара с PDP исчезает из избранного после перезагрузки страницы",
               f"число позиций = {initial + 1}"):
        shop.open_product(first_href)
        shop.wait_favorite_ready()
        shop.toggle_favorite()
        _FAVORITES_ADDED.remove(first_href)
        assert shop.wait_favorite_state(False, timeout_s=20.0), (
            f"сердечко не снялось на PDP: {_wishlist_snapshot(shop)}"
        )
        shop.open_wishlist()
        page.reload(wait_until="domcontentloaded")
        shop.page.wait_for_selector(WISHLIST_HEADING, timeout=15_000)
        assert shop.wait_wishlist_size(initial + 1), (
            f"снятие не пропало из избранного: было {initial}, {_wishlist_snapshot(shop)}"
        )
