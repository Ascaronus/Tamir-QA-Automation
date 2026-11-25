"""E2E-7. Сортировка каталога: по цене возрастает и убывает, «новые» возвращают исходный порядок

Тест идёт по **живому стэнду** ``https://test.tamir.rs``. В панели каталога
``/rs/catalog`` есть ``<select>`` сортировки (значения ``newest``, ``price_asc``,
``price_desc``, ``popularity``). Тест переключает сортировку по цене вверх и
вниз и проверяет порядок прямо по сетке: цены карточек
``p.product-card-price`` идут монотонно, набор товаров при этом не меняется
(ничто не теряется и не дублируется), счётчик ``p.catalog-count`` остаётся
прежним. Возврат ``newest`` восстанавливает базовый порядок карточек.
Корзины и учётные записи не создаются.
"""

from __future__ import annotations

import pytest

from qa.ui import CATALOG_SORT, Storefront

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]


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
        base = shop.catalog_rows()
        assert len(base) > 2, "мало товаров, сортировку проверять не на чём"
        assert page.locator(CATALOG_SORT).count() > 0, "в каталоге нет select сортировки"
        assert not _is_non_decreasing([price for _href, price in base]), \
            "базовый порядок уже отсортирован по возрастанию — проверка потеряет смысл"

    with steps("выбрать сортировку «сначала дешевле» (price_asc)",
               "цены карточек идут по возрастанию, товаров столько же"):
        shop.sort_catalog("price_asc")
        assert shop.wait_until(lambda: _is_non_decreasing(shop.catalog_prices()), timeout_s=20), \
            f"после price_asc цены не по возрастанию: {shop.catalog_prices()}"
        hrefs = shop.catalog_hrefs()
        assert sorted(hrefs) == sorted(href for href, _price in base), \
            "сортировка потеряла или добавила товары"
        assert shop.catalog_count() == len(hrefs), "счётчик не совпал с числом карточек"

    with steps("выбрать сортировку «сначала дороже» (price_desc)",
               "цены карточек идут по убыванию"):
        shop.sort_catalog("price_desc")
        assert shop.wait_until(lambda: _is_non_increasing(shop.catalog_prices()), timeout_s=20), \
            f"после price_desc цены не по убыванию: {shop.catalog_prices()}"
        prices = shop.catalog_prices()
        assert prices[0] > prices[-1], "есть разные цены — порядок обязан быть строго разным по краям"

    with steps("вернуть сортировку «сначала новые» (newest)",
               "порядок карточек совпал с базовым"):
        shop.sort_catalog("newest")
        assert shop.wait_until(
            lambda: shop.catalog_hrefs() == [href for href, _price in base], timeout_s=20
        ), "после newest порядок карточек не вернулся к базовому"
