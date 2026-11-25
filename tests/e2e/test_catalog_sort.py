"""E2E-7. Сортировка каталога: по цене возрастает и убывает, «новые» возвращают исходный порядок

Тест идёт по **живому стэнду** ``https://test.tamir.rs``. В панели каталога
``/rs/catalog`` есть ``<select>`` сортировки (``newest``, ``price_asc``,
``price_desc``, ``popularity``). Тест переключает сортировку по цене вверх и
вниз и проверяет порядок прямо по сетке: цены карточек
``p.product-card-price`` идут монотонно, набор товаров при этом не меняется
(ничто не теряется и не дублируется), счётчик ``p.catalog-count`` остаётся
прежним. Возврат ``newest`` восстанавливает базовый порядок карточек.
Корзины и учётные записи не создаются.
"""

from __future__ import annotations

import re

import pytest

from qa.ui import (
    CATALOG_CARD_ITEM,
    CATALOG_COUNT,
    CATALOG_COUNT_DIGITS as _COUNT_DIGITS,
    CATALOG_SORT,
    PRODUCT_CARD_PRICE,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]


def _grid(page) -> list[tuple[str, float]]:
    """Пары «ссылка карточки, цена числом» с текущей сетки каталога.

    Во время перерисовки страницы ``evaluate`` может поймать уничтоженный
    контекст — возвращаем пустой список, чтобы ``wait_until`` просто подождал.
    """
    try:
        rows = page.evaluate(
            """() => Array.from(document.querySelectorAll('li.product-card')).map(li => {
                const a = li.querySelector('a.product-card-title');
                const p = li.querySelector('.product-card-price');
                return [a ? a.getAttribute('href') : null, p ? p.textContent.trim() : null];
            })"""
        )
    except Exception:  # noqa: BLE001 — страница перерисовывается, probe() просто не готов
        return []
    return [(href, _money_to_number(price_text)) for href, price_text in rows]


def _prices(page) -> list[float]:
    return [price for _href, price in _grid(page)]


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


def _is_non_decreasing(values: list[float]) -> bool:
    return bool(values) and all(a <= b for a, b in zip(values, values[1:]))


def _is_non_increasing(values: list[float]) -> bool:
    return bool(values) and all(a >= b for a, b in zip(values, values[1:]))


def test_catalog_sort_by_price(page, steps):
    """Сортировка каталога по цене упорядочивает карточки, набор товаров не меняется."""
    shop = Storefront(page)

    with steps("открыть каталог и запомнить базовый порядок карточек",
               "на сетке больше двух товаров, select сортировки доступен"):
        shop.goto_catalog()
        base = _grid(page)
        assert len(base) > 2, "мало товаров, сортировку проверять не на чём"
        assert page.locator(CATALOG_SORT).count() > 0, "в каталоге нет select сортировки"
        assert not _is_non_decreasing([price for _h, price in base]), \
            "базовый порядок уже отсортирован по возрастанию — проверка потеряет смысл"

    with steps("выбрать сортировку «сначала дешевле» (price_asc)",
               "цены карточек идут по возрастанию, товаров столько же"):
        page.select_option(CATALOG_SORT, "price_asc")
        assert shop.wait_until(lambda: _is_non_decreasing(_prices(page)), timeout_s=20), \
            f"после price_asc цены не по возрастанию: {_prices(page)}"
        hrefs = _hrefs(page)
        assert hrefs[0] != base[0][0] or _prices(page)[0] < _prices(page)[-1], \
            "порядок не изменился — сортировка не применилась"
        assert sorted(hrefs) == sorted(href for href, _p in base), \
            "сортировка потеряла или добавила товары"
        assert _count_number(page) == len(hrefs), "счётчик не совпал с числом карточек"

    with steps("выбрать сортировку «сначала дороже» (price_desc)",
               "цены карточек идут по убыванию"):
        page.select_option(CATALOG_SORT, "price_desc")
        assert shop.wait_until(lambda: _is_non_increasing(_prices(page)), timeout_s=20), \
            f"после price_desc цены не по убыванию: {_prices(page)}"
        prices = _prices(page)
        assert prices[0] > prices[-1], "есть разные цены — порядок обязан быть строго разным по краям"

    with steps("вернуть сортировку «сначала новые» (newest)",
               "порядок карточек совпал с базовым"):
        page.select_option(CATALOG_SORT, "newest")
        assert shop.wait_until(lambda: _hrefs(page) == [href for href, _p in base], timeout_s=20), \
            "после newest порядок карточек не вернулся к базовому"
