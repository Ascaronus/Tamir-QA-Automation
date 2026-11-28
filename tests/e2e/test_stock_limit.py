"""E2E-4. Лимит количества на карточке товара: степпер и ручной ввод не дают взять больше остатка

Тест идёт по **живому стэнду** ``https://test.tamir.rs``. Берёт из API
(``/store/products``) товар с остатком не меньше 3 и открывает его карточку.
Степпер количества изначально показывает 1, «−» заблокирована, витрина сообщает
«Na stanju». «+» увеличивает количество — несколько штук одного товара брать
можно. Если вручную ввести количество больше остатка, витрина обрезает значение
до доступного и показывает уведомление ``span[role=alert]`` с числом доступных
штук; повторное «+» на лимите количество не растит, «−» уменьшает на одну.
Корзина не открывается и товар в неё не добавляется — страница PDP только
считает количество на клиенте.
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    PRODUCT_QTY_INPUT,
    QTY_BUTTON,
    STOCK_ALERT,
    STOCK_AVAIL_RE,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

# Корзины, оставшиеся у теста (на всякий случай: тест корзину не создаёт, но
# гостевая корзина живёт в localStorage браузера, как в test_cart_quantity).
_LEFTOVER_CARTS: list[str] = []

_PRODUCT_FIELDS = (
    "id,handle,title,variants.id,variants.title,variants.manage_inventory,"
    "variants.allow_backorder,+variants.inventory_quantity"
)


def _pick_in_stock(api_client: MedusaStoreClient, need: int) -> tuple[str, str, int] | None:
    """Хэндл, заголовок и остаток товара стэнда с остатком не меньше ``need``.

    Как в ``test_cart_quantity``: на стэнде почти у всех вариантов остаток 1,
    поэтому берём товар с запасом — иначе конкурентный прогон обнулит остаток.
    """
    body = api_client._get(f"/store/products?limit=50&country_code=rs&fields={_PRODUCT_FIELDS}")
    for product in body.get("products") or []:
        handle = product.get("handle") or ""
        if not handle:
            continue
        for variant in product.get("variants") or []:
            stock = variant.get("inventory_quantity")
            if stock is not None and stock >= need:
                return handle, product.get("title") or handle, int(stock)
    return None


@pytest.fixture(scope="module", autouse=True)
def _cleanup_guest_carts(api_client: MedusaStoreClient):
    """Удаляет содержимое корзин, оставшихся от прерванного теста, — не мусорить на стэнде."""
    yield
    for cart_id in _LEFTOVER_CARTS:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — чистка не должна перекрывать результат теста
            pass


def test_pdp_quantity_capped_by_stock(page, steps, api_client: MedusaStoreClient):
    """Количество на карточке товара не превышает доступный остаток."""
    shop = Storefront(page)

    with steps("найти через API товар с остатком не меньше 3 и открыть его карточку",
               "на карточке степпер количества со значением 1"):
        picked = _pick_in_stock(api_client, 3)
        if not picked:
            pytest.skip("на стэнде нет товара с остатком >= 3 — лимит проверять не на чём")
        handle, _title, stock = picked
        shop.goto_catalog()
        shop.open_product(f"/rs/products/{handle}")
        page.locator(PRODUCT_QTY_INPUT).first.wait_for(state="visible", timeout=30_000)

    qty = page.locator(PRODUCT_QTY_INPUT)
    minus = page.locator(QTY_BUTTON).first
    plus = page.locator(QTY_BUTTON).last

    with steps("проверить исходное состояние степпера",
               "«−» заблокирована, индикатор остатка — «Na stanju»"):
        assert qty.input_value() == "1", "стартовое количество должно быть 1"
        assert minus.is_disabled(), "«−» при одной штуке должна быть заблокирована"
        assert "stanju" in shop.product_stock_text().lower()

    with steps("нажать «+» один раз", "количество стало 2 — несколько штук брать можно"):
        if plus.is_disabled():
            pytest.skip("весь остаток товара уже зарезервирован — «+» заблокирована")
        # Клик может прийти до завершения перерисовки степпера, поэтому кликаем
        # повторно, пока значение не станет 2 (не позже 10 секунд).
        plus.click()
        if not shop.wait_until(lambda: qty.input_value() == "2", timeout_s=5):
            plus.click()
            assert shop.wait_until(lambda: qty.input_value() == "2", timeout_s=10), \
                f"«+» должна увеличить количество до 2: {qty.input_value()!r}"
        assert page.locator(STOCK_ALERT).count() == 0, "в пределах остатка уведомления быть не должно"

    with steps(f"ввести в поле количества {stock + 15} — больше остатка",
               "значение обрезано до доступного, появилось уведомление о доступном остатке"):
        want = stock + 15
        qty.fill(str(want))
        alert = page.locator(STOCK_ALERT)
        assert shop.wait_until(lambda: alert.count() > 0, timeout_s=10), \
            "уведомление о лимите количества не появилось"
        match = STOCK_AVAIL_RE.search(alert.first.inner_text())
        assert match, f"в уведомлении нет числа доступных штук: {alert.first.inner_text()!r}"
        available = int(match.group(1))
        assert available >= 1
        assert shop.wait_until(lambda: qty.input_value() == str(available), timeout_s=10), \
            f"количество не обрезано до доступного {available}: {qty.input_value()!r}"
        assert qty.input_value() != str(want)

    with steps("нажать «+» ещё раз, когда количество на лимите", "количество не увеличилось"):
        if not plus.is_disabled():
            plus.click()
        assert qty.input_value() == str(available), "за доступный остаток выходить нельзя"

    with steps("нажать «−»", "количество уменьшилось на одну штуку"):
        if available < 2:
            pytest.skip("на стэнде осталась одна доступная штука — уменьшать нечего")
        minus.click()
        assert qty.input_value() == str(available - 1)
