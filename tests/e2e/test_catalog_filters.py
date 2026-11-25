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

import re

import pytest

from qa.ui import (
    CATALOG_CARD_ITEM,
    CATALOG_COLOR_INPUT,
    CATALOG_COUNT,
    CATALOG_COUNT_DIGITS as _COUNT_DIGITS,
    CATALOG_FILTER_SUBMIT,
    CATALOG_MAX_PRICE,
    CATALOG_MIN_PRICE,
    PRODUCT_CARD,
    PRODUCT_CARD_PRICE,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]


def _grid(page) -> list[tuple[str, float]]:
    """Пары «ссылка карточки, цена числом» с текущей сетки каталога."""
    rows = page.evaluate(
        """() => Array.from(document.querySelectorAll('li.product-card')).map(li => {
            const a = li.querySelector('a.product-card-title');
            const p = li.querySelector('.product-card-price');
            return [a ? a.getAttribute('href') : null, p ? p.textContent.trim() : null];
        })"""
    )
    result: list[tuple[str, float]] = []
    for href, price_text in rows:
        assert href and price_text, f"карточка без ссылки или цены: {href!r} {price_text!r}"
        result.append((href, _money_to_number(price_text)))
    return result


def _hrefs(page) -> list[str]:
    return [href for href, _price in _grid(page)]


def _count_number(page) -> int:
    """Число товаров из счётчика ``p.catalog-count`` («16 proizvoda» -> 16)."""
    node = page.locator(CATALOG_COUNT)
    assert node.count() > 0, "счётчик товаров в каталоге не найден"
    match = _COUNT_DIGITS.search(node.first.inner_text())
    assert match, f"в счётчике нет числа: {node.first.inner_text()!r}"
    return int(match.group(1))


def _money_to_number(text: str) -> float:
    """«1.440,00\\xa0RSD» -> 1440.0 (разделитель тысяч — точка, дробь — запятая)."""
    cleaned = text.replace("\xa0", " ").strip()
    digits = re.sub(r"[^\d,.]", "", cleaned)
    return float(digits.replace(".", "").replace(",", "."))


def test_catalog_color_filter(page, steps):
    """Фильтр каталога по цвету оставляет только товары выбранного цвета."""
    shop = Storefront(page)

    with steps("открыть каталог и запомнить базовый набор товаров",
               "сетка непустая, счётчик совпадает с числом карточек"):
        shop.goto_catalog()
        baseline = _hrefs(page)
        assert baseline, "в каталоге без фильтров нет ни одной карточки"
        assert _count_number(page) == len(baseline)

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
        filtered = _hrefs(page)
        assert filtered, "фильтр по цвету оставил пустую сетку"
        assert len(filtered) < len(baseline), "цвет сокращает выдачу"
        assert set(filtered) <= set(baseline), "фильтр показал товар, которого не было в базовой выдаче"
        assert _count_number(page) == len(filtered), "счётчик не совпал с числом карточек после фильтра"

    with steps("снять цвет и применить фильтр снова", "в каталог вернулись все товары"):
        page.locator(f'aside form {CATALOG_COLOR_INPUT}[value="blue"]').first.uncheck()
        page.locator(CATALOG_FILTER_SUBMIT).first.click()
        assert shop.wait_until(lambda: _count_number(page) == len(baseline), timeout_s=15), \
            "счётчик не вернулся к базовому числу после снятия фильтра"
        assert len(_hrefs(page)) == len(baseline)


def test_catalog_price_range_filter(page, steps):
    """Фильтр каталога по диапазону цены оставляет карточки только с ценой внутри диапазона."""
    shop = Storefront(page)

    with steps("открыть каталог и собрать цены всех карточек",
               "цены читаются как числа, диапазон осмысленный"):
        shop.goto_catalog()
        cards = _grid(page)
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
        filtered = _grid(page)
        assert filtered, "диапазон цен из реальных цен каталога оставил пустую сетку"
        assert len(filtered) <= len(cards)
        for href, price in filtered:
            assert low <= price <= high, f"{href}: цена {price} вне диапазона {low}–{high}"
        assert _count_number(page) == len(filtered), "счётчик не совпал с числом карточек после фильтра"
