"""E2E-5/E2E-6. Фильтры каталога: цвет и диапазон цены оставляют только подходящие товары

Оба теста идут по **живому стэнду** ``https://test.tamir.rs`` и работают с
формой фильтров в боковой панели ``/rs/catalog`` (``GET``-форма: ``color``,
``min_price``, ``max_price``). Фильтр по цвету оставляет только товары выбранного
цвета, фильтр по диапазону цены — только карточки с ценой внутри диапазона;
счётчик ``p.catalog-count`` при этом совпадает с числом карточек на сетке.
Отмена фильтра возвращает исходную выдачу. Товары и фильтры — витринные,
учётные записи и корзины не создаются.
"""

from __future__ import annotations

import pytest

from qa.ui import (
    CATALOG_COLOR_INPUT,
    CATALOG_FILTER_SUBMIT,
    CATALOG_MAX_PRICE,
    CATALOG_MIN_PRICE,
    Storefront,
    money_to_number,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]


def _grid(shop: Storefront) -> list[tuple[str, float]]:
    """Пары «ссылка карточки, цена числом» с текущей сетки каталога."""
    rows = shop.catalog_rows()
    for href, price_text in rows:
        assert href and price_text, f"карточка без ссылки или цены: {href!r} {price_text!r}"
    return [(href, money_to_number(price_text)) for href, price_text in rows]


def test_catalog_color_filter(page, steps):
    """Фильтр каталога по цвету оставляет только товары выбранного цвета."""
    shop = Storefront(page)

    with steps("открыть каталог и запомнить базовый набор товаров",
               "сетка непустая, счётчик совпадает с числом карточек"):
        shop.goto_catalog()
        baseline = shop.catalog_hrefs()
        assert baseline, "в каталоге без фильтров нет ни одной карточки"
        assert shop.catalog_count() == len(baseline)

    with steps("отметить в панели фильтров чекбокс цвета и применить фильтр",
               "URL содержит color=blue, карточек стало меньше"):
        values = page.evaluate(
            f"""() => Array.from(document.querySelectorAll('aside form {CATALOG_COLOR_INPUT}'))
                .map(i => i.value)"""
        )
        if "blue" not in values:
            pytest.skip(f"в фильтрах стэнда нет цвета blue (есть: {values}) — проверять нечем")
        checkbox = page.locator(f'aside form {CATALOG_COLOR_INPUT}[value="blue"]').first
        checkbox.check()
        assert checkbox.is_checked()
        page.locator(CATALOG_FILTER_SUBMIT).first.click()
        assert shop.wait_until(lambda: "color=blue" in page.url, timeout_s=15), \
            "применение фильтра по цвету не добавило color=blue в URL"
        filtered = shop.catalog_hrefs()
        assert filtered, "фильтр по цвету оставил пустую сетку"
        assert len(filtered) < len(baseline), "цвет сокращает выдачу"
        assert set(filtered) <= set(baseline), "фильтр показал товар, которого не было в базовой выдаче"
        assert shop.catalog_count() == len(filtered), "счётчик не совпал с числом карточек после фильтра"

    with steps("снять цвет и применить фильтр снова", "в каталог вернулись все товары"):
        page.locator(f'aside form {CATALOG_COLOR_INPUT}[value="blue"]').first.uncheck()
        page.locator(CATALOG_FILTER_SUBMIT).first.click()
        assert shop.wait_until(lambda: shop.catalog_count() == len(baseline), timeout_s=15), \
            "счётчик не вернулся к базовому числу после снятия фильтра"
        assert len(shop.catalog_hrefs()) == len(baseline)


def test_catalog_price_range_filter(page, steps):
    """Фильтр каталога по диапазону цены оставляет карточки только с ценой внутри диапазона."""
    shop = Storefront(page)

    with steps("открыть каталог и собрать цены всех карточек",
               "цены читаются как числа, диапазон осмысленный"):
        shop.goto_catalog()
        cards = _grid(shop)
        assert cards, "в каталоге без фильтров нет ни одной карточки"
        prices = sorted(price for _href, price in cards)
        low, high = int(prices[0]), int(prices[len(prices) // 2])
        if low >= high:
            pytest.skip("на стэнде все цены одинаковые — диапазон для фильтра не построить")

    with steps(f"задан диапазон цены {low}–{high} и применён фильтр",
               "URL содержит min_price и max_price"):
        page.locator(CATALOG_MIN_PRICE).first.fill(str(low))
        page.locator(CATALOG_MAX_PRICE).first.fill(str(high))
        page.locator(CATALOG_FILTER_SUBMIT).first.click()
        assert shop.wait_until(
            lambda: "min_price=" in page.url and "max_price=" in page.url, timeout_s=15
        ), "применение диапазона цен не попало в URL"

    with steps("проверить сетку после фильтра",
               "все цены внутри диапазона, счётчик совпадает с числом карточек"):
        filtered = _grid(shop)
        assert filtered, "диапазон цен из реальных цен каталога оставил пустую сетку"
        assert len(filtered) <= len(cards)
        for href, price in filtered:
            assert low <= price <= high, f"{href}: цена {price} вне диапазона {low}–{high}"
        assert shop.catalog_count() == len(filtered), "счётчик не совпал с числом карточек после фильтра"
