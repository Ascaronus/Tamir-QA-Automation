"""E2E-23: пустая форма оформления — одна отправка без заполнения показывает ошибки
обязательных полей, не раскрывает выбор доставки и не создаёт заказ, а гостевая корзина
очищается через API."""

from __future__ import annotations

import pytest

from qa.ui import (
    CART_PAGE_HEADING_TEXTS,
    CHECKOUT_CITY,
    CHECKOUT_CTA,
    CHECKOUT_FIELD_ERROR,
    CHECKOUT_FIRST_NAME,
    CHECKOUT_HEADING,
    CHECKOUT_PAYMENT,
    CHECKOUT_SHIPPING_INPUTS,
    CHECKOUT_SUBMIT,
    ORDER_RECEIPT_HEADING_TEXTS,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.checkout]

# Корзины, оставшиеся у теста (guest-корзина живёт в localStorage браузера, тест её не оформляет).
_LEFTOVER_CARTS: list[str] = []

# Обязательные поля формы оформления — id ошибок вида #checkout-<поле>-error замечены на стэнде.
_REQUIRED_FIELDS = ("firstName", "lastName", "phone", "postalCode", "city", "address1")


@pytest.fixture(scope="module", autouse=True)
def _cleanup_guest_carts(api_client):
    """Снимает резерв товара, оставшийся от незавершённых корзин этого модуля."""
    yield
    for cart_id in _LEFTOVER_CARTS:
        try:
            api_client.remove_all_items(cart_id)
        except Exception:  # noqa: BLE001 — корзина могла уйти в заказ; уборка не роняет тест
            pass


def _open_checkout_form(page, steps, shop: Storefront) -> None:
    """Кладёт первый товар из каталога в корзину и доводит гостя до формы оформления."""
    with steps("открыть каталог и положить первый товар в корзину",
               "на карточке товара позиция добавляется в корзину"):
        shop.goto_catalog()
        hrefs = shop.catalog_hrefs()
        assert hrefs, "в каталоге нет ни одной карточки товара"
        shop.open_product(hrefs[0])
        shop.add_to_cart()

    with steps("открыть корзину и нажать переход к оформлению",
               f"корзина ({CART_PAGE_HEADING_TEXTS[0]}) с позицией и ссылка {CHECKOUT_CTA} доступны"):
        shop.goto_cart()
        assert shop.cart_line_count() >= 1, \
            f"после добавления в корзине нет ни одной позиции ({shop.cart_line_count()})"
        heading = page.locator(CHECKOUT_HEADING).first.inner_text().strip()
        assert heading in CART_PAGE_HEADING_TEXTS, \
            f"заголовок корзины {heading!r} не совпал с {CART_PAGE_HEADING_TEXTS}"
        cta = page.locator(CHECKOUT_CTA)
        assert cta.count() >= 1, f"в корзине нет ссылки на оформление ({CHECKOUT_CTA})"
        assert (cta.first.get_attribute("aria-disabled") or "") != "true", \
            f"ссылка на оформление заблокирована: {CHECKOUT_CTA} aria-disabled=true"
        cart_id = shop.cart_id()
        assert cart_id, "не удалось прочитать id гостевой корзины из localStorage"
        _LEFTOVER_CARTS.append(cart_id)
        shop.go_to_checkout()

    assert "/checkout" in page.url, f"ожидалась страница оформления, а URL = {page.url}"


def test_checkout_form_opens_without_errors(page, steps) -> None:
    """До отправки форма оформления открыта, поля на месте и ошибок нет."""
    shop = Storefront(page)
    _open_checkout_form(page, steps, shop)

    with steps("осмотреть форму оформления до отправки",
               "поля имени, города, оплаты на месте, ошибок и выбора доставки нет"):
        assert page.locator(CHECKOUT_FIRST_NAME).count() == 1, \
            f"селектор {CHECKOUT_FIRST_NAME}: найдено {page.locator(CHECKOUT_FIRST_NAME).count()}, ожидалось одно поле"
        assert page.locator(CHECKOUT_CITY).count() == 1, \
            f"селектор {CHECKOUT_CITY}: найдено {page.locator(CHECKOUT_CITY).count()}, ожидался один город"
        assert page.locator(CHECKOUT_PAYMENT).count() >= 1, \
            f"в форме нет способа оплаты ({CHECKOUT_PAYMENT}: {page.locator(CHECKOUT_PAYMENT).count()})"
        errors = page.locator(CHECKOUT_FIELD_ERROR)
        assert errors.count() == 0, \
            f"до отправки формы уже есть ошибки {CHECKOUT_FIELD_ERROR}: {errors.count()}"
        shipping = page.locator(CHECKOUT_SHIPPING_INPUTS)
        assert shipping.count() == 0, \
            f"до отправки формы выбор доставки уже раскрыт ({CHECKOUT_SHIPPING_INPUTS}: {shipping.count()})"


def test_checkout_empty_submit_reports_field_errors(page, steps) -> None:
    """Одна отправка пустой формы: ошибки обязательных полей, доставка и заказ — не созданы."""
    shop = Storefront(page)
    _open_checkout_form(page, steps, shop)

    with steps("один раз отправить пустую форму оформления",
               f"появляются ошибки {CHECKOUT_FIELD_ERROR} у обязательных полей"):
        page.locator(CHECKOUT_SUBMIT).first.click()
        assert shop.wait_until(lambda: page.locator(CHECKOUT_FIELD_ERROR).count() > 0, 20), \
            f"после отправки пустой формы ошибка {CHECKOUT_FIELD_ERROR} так и не появилась"

    with steps("сверить ошибки с обязательными полями формы",
               "у каждого обязательного поля есть span.field-error и aria-invalid=true"):
        error_ids = page.evaluate(
            f"""() => Array.from(document.querySelectorAll('{CHECKOUT_FIELD_ERROR}'))
                .map(e => e.id)"""
        )
        assert error_ids, f"ошибки {CHECKOUT_FIELD_ERROR} без id: {error_ids}"
        for field in _REQUIRED_FIELDS:
            assert f"checkout-{field}-error" in error_ids, \
                f"у обязательного поля {field} нет ошибки checkout-{field}-error, есть: {error_ids}"
        assert "checkout-email-error" not in error_ids, \
            f"необязательное поле email получило ошибку: {error_ids}"

    with steps("проверить, что поля помечены невалидными",
               "aria-invalid=true стоит ровно на полях с ошибками"):
        invalid_ids = page.evaluate(
            """() => Array.from(document.querySelectorAll('[aria-invalid="true"]'))
                .map(e => e.id)"""
        )
        assert {CHECKOUT_FIRST_NAME.removeprefix("#"), CHECKOUT_CITY.removeprefix("#")} <= set(invalid_ids), \
            f"поля firstName/city без aria-invalid=true: {invalid_ids}"

    with steps("убедиться, что оформление не ушло дальше",
               "URL тот же, доставка не раскрыта, подтверждение заказа отсутствует"):
        assert "/checkout" in page.url, \
            f"после отправки с ошибками URL ушёл с формы оформления: {page.url}"
        shipping = page.locator(CHECKOUT_SHIPPING_INPUTS)
        assert shipping.count() == 0, \
            f"пустая форма раскрыла выбор доставки ({CHECKOUT_SHIPPING_INPUTS}: {shipping.count()})"
        body = page.inner_text("body")
        receipt = [t for t in ORDER_RECEIPT_HEADING_TEXTS if t in body]
        assert not receipt, f"стэнд подтвердил заказ при пустой форме: найдено {receipt}"
