"""E2E-11. Удаление позиции из корзины: позиция исчезает, корзина до пустого состояния

Тест идёт по **живому стэнду** ``https://test.tamir.rs``. Кладёт в корзину две
разные товары, удаляет сначала одну (второй на месте), потом вторую — и корзина
переходит в пустое состояние («Vaša korpa je prazna»). Удаление — это
``DELETE .../line-items/<id>`` (проверено прогоном), и шаг ждёт именно ответ
витрины, а не только клик.
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_EMPTY,
    CART_LINE,
    CART_REMOVE,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Корзины, оставшиеся у теста (guest-корзина живёт в localStorage браузера, тест её не оформляет).
_LEFTOVER_CARTS: list[str] = []

_PRODUCT_FIELDS = "id,handle,title,variants.id,variants.manage_inventory,variants.allow_backorder"


def _pick_buyable_handles(api_client: MedusaStoreClient, count: int) -> list[tuple[str, str]]:
    """Хэндлы и заголовки первых ``count`` покупаемых товаров стэнда (через API).

    Идём по API, а не по сетке каталога: на стэнде 19 товаров, часть вне остатка,
    и полагаться на порядок карточок хрупко — API отдаёт стабильный список.
    """
    body = api_client._get(f"/store/products?limit=50&country_code=rs&fields={_PRODUCT_FIELDS}")
    picked: list[tuple[str, str]] = []
    for product in body.get("products") or []:
        handle = product.get("handle") or ""
        if not handle:
            continue
        if any(variant.get("id") for variant in product.get("variants") or []):
            picked.append((handle, product.get("title") or handle))
        if len(picked) == count:
            break
    return picked


def _add_product(page, shop: Storefront, handle: str) -> None:
    """Открывает карточку товара по хэндлу и добавляет одну позицию."""
    shop.open_product(f"/rs/products/{handle}")
    shop.add_to_cart()


def _remove_line(page, shop: Storefront, title: str) -> None:
    """Убирает из корзины первую позицию с названием ``title`` кнопкой удаления."""
    line = page.locator(f"{CART_LINE}:has-text('{title}')").first
    assert line.count() > 0, f"в корзине нет позиции «{title}», которую надо удалить"
    with page.expect_response(
        lambda r: "/line-items" in r.url and r.request.method == "DELETE", timeout=30_000
    ):
        line.locator(CART_REMOVE).first.click()


@pytest.fixture(scope="module", autouse=True)
def _cleanup_guest_carts(api_client: MedusaStoreClient):
    """Удаляет содержимое корзин, оставшихся от прерванного теста, — не мусорить на стэнде."""
    yield
    for cart_id in _LEFTOVER_CARTS:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — чистка не должна перекрывать результат теста
            pass


def test_cart_remove_line_item(page, steps, api_client: MedusaStoreClient):
    """Удаление позиции кнопкой корзина теряет её, а после второго удаления становится пустой."""
    shop = Storefront(page)

    with steps("открыть каталог и выбрать два разных товара",
               "через API получены два разных хэндла товаров стэнда"):
        picked = _pick_buyable_handles(api_client, 2)
        if len(picked) < 2:
            pytest.skip("на стэнде не находится двух покупаемых товаров — корзину не из чего собрать")
        (handle_a, title_a), (handle_b, title_b) = picked
        shop.goto_catalog()

    with steps("добавить в корзину обе позиции",
               "витрина получила два POST /line-items, в localStorage есть id корзины"):
        _add_product(page, shop, handle_a)
        shop.open_product(f"/rs/products/{handle_b}")
        _add_product(page, shop, handle_b)
        cart_id = shop.cart_id()
        assert cart_id, "после добавления позиций id корзины не появился в localStorage"
        _LEFTOVER_CARTS.append(cart_id)

    with steps("открыть корзину", "на странице корзины ровно две позиции"):
        shop.goto_cart()
        assert shop.wait_for_cart_lines(2), "в корзине не появились обе добавленные позиции"

    with steps("удалить первую позицию кнопкой удаления",
               "витрина приняла DELETE /line-items, позиция исчезла, второй на месте"):
        _remove_line(page, shop, title_a)
        assert shop.wait_for_cart_lines(1), "после удаления одной позиции в корзине осталась не одна"
        texts = [page.locator(CART_LINE).nth(i).inner_text() for i in range(page.locator(CART_LINE).count())]
        assert not any(title_a.lower() in t.lower() for t in texts), \
            f"удалённая позиция «{title_a}» всё ещё в корзине"
        assert any(title_b.lower() in t.lower() for t in texts), \
            f"вторая позиция «{title_b}» пропала вместе с удалённой"

    with steps("удалить вторую позицию",
               "витрина приняла DELETE /line-items и позиций в корзине не осталось"):
        _remove_line(page, shop, title_b)
        assert shop.wait_until(lambda: page.locator(CART_LINE).count() == 0, 10.0), \
            "после удаления второй позиции в корзине остались строки"

    with steps("посмотреть на пустую корзину",
               "витрина показывает пустое состояние корзины"):
        assert shop.cart_is_empty(), "корзина не перешла в пустое состояние после удаления всех позиций"
        empty = page.locator(CART_EMPTY)
        assert empty.count() > 0, "нет пустого состояния корзины"
        assert "prazna" in empty.first.inner_text().lower(), \
            f"пустое состояние корзины говорит не об этом: {empty.first.inner_text()[:120]!r}"
