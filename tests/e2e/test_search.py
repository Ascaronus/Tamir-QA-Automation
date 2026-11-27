"""E2E-поиск витрины: форма в шапке, выдача без совпадений и прямой URL с ``q``.

Поиск на витрине — ``form.catalog-search`` в шапке (``GET``-форма на
``/rs/catalog``), отдельной страницы результатов нет: ``/rs/search`` на стэнде
404, выдача живёт на маршруте каталога. Поэтому все три сценария проверяют
``/rs/catalog`` — куда форма реально отправляет и где работают счётчик
``p.catalog-count``, сетка карточек и пустое состояние ``div.empty-state``.

Гость, ничего не создаём и не покупаем: вход для поиска не нужен, а стенд
разрешает не больше 5 входов в час (``tests/e2e/conftest.py``), так что
``logged_in_page`` тут не трогаем. Chromium, десктопный вьюпорт — defaults из
``tests/conftest.py``.

Замерено на ``test.tamir.rs`` (27.11.2026): ``šešir`` находит 9 товаров из 16,
``zzzqqqxxx`` — пустое состояние со счётчиком «0 proizvoda», ``?q=мушкарац`` —
200 без ошибки. Контракт ``q`` со стороны API разобран в
``tests/api/test_search_api.py``.
"""

from __future__ import annotations

import pytest

from qa.medusa_store import MedusaStoreClient
from qa.ui import (
    CART_EMPTY,
    CATALOG_COUNT,
    HEADER_SEARCH_FORM,
    HEADER_SEARCH_INPUT,
    HEADER_SEARCH_SUBMIT,
    PRODUCT_CARD,
    Storefront,
)

pytestmark = [pytest.mark.e2e, pytest.mark.catalog]

QUERY = "šešir"
"""Запрос, который на стэнде находит 9 из 16 товаров (подстрока в заголовке)."""

NEEDLE_HANDLE = "crni-polusezonski-sesir"
"""«Crni polusezonski šešir» — должен остаться в выдаче ``šešir``."""

NO_MATCH = "zzzqqqxxx"
"""Запрос без совпадений: на стэнде для пустой выдачи нет ни 404, ни ошибки."""

EMPTY_TEXT = "Nema proizvoda koji odgovaraju izabranim filterima."
"""Текст пустого состояния каталога (``div.empty-state p``)."""


def _api_handles(api_client: MedusaStoreClient, q: str) -> set[str]:
    """``handle`` товаров, которые Store API отдаёт на ``q`` — эталон для сетки.

    Счётчики поиска на стэнде стабильны (``šešir`` — 9 из 16), поэтому сверять
    витрину можно с живым API, а не с записанными числами.
    """
    region_id = api_client.first_region()["id"]
    body = api_client.client.get(
        "/store/products",
        params={"region_id": region_id, "q": q, "fields": "id,title,handle", "limit": 100},
    ).json()
    return {str(product["handle"]) for product in (body.get("products") or []) if product.get("handle")}


def _watch(page) -> tuple[Storefront, list[str]]:
    """``Storefront`` плюс список ошибок страницы, накопленный за тест.

    ``pageerror`` ловит необработанные исключения JS — тот способ, которым
    витрина «падает» на глазах у покупателя; обычные 404 ресурсов под него не
    попадают, так что список остаётся пустым на здоровой выдаче.
    """
    shop = Storefront(page)
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return shop, errors


def test_header_search_shows_matching_products(page, api_client: MedusaStoreClient, steps):
    """Поиск из шапки ведёт на каталог и оставляет только подходящие товары."""
    shop, errors = _watch(page)

    with steps("открыть витрину и найти форму поиска в шапке",
               "форма ``form.catalog-search`` видна, поле ``q`` пустое"):
        shop.goto("/")
        page.locator(HEADER_SEARCH_FORM).first.wait_for(state="visible", timeout=20_000)
        assert page.locator(HEADER_SEARCH_INPUT).first.input_value() == ""

    with steps(f"ввести ``{QUERY}`` в поле поиска и отправить форму",
               "URL каталога с ``q``, страница не упала"):
        page.locator(HEADER_SEARCH_INPUT).first.fill(QUERY)
        page.locator(HEADER_SEARCH_SUBMIT).first.click()
        assert shop.wait_until(lambda: "/catalog" in page.url and "q=" in page.url, timeout_s=20), \
            f"после отправки поиска URL остался прежним: {page.url}"

    with steps("прочитать счётчик и сетку карточек",
               f"счётчик > 0 и равен числу карточек, ``{QUERY}`` сократил выдачу"):
        page.locator(CATALOG_COUNT).first.wait_for(state="visible", timeout=20_000)
        found = shop.catalog_count()
        hrefs = shop.catalog_hrefs()
        assert found > 0, f"счётчик пустой при запросе {QUERY!r}: {page.url}"
        assert hrefs, f"счётчик {found}, а на сетке ни одной карточки"
        assert found == len(hrefs), f"счётчик {found} не совпал с {len(hrefs)} карточками"
        shop.goto_catalog()
        assert found < shop.catalog_count(), \
            f"поиск {QUERY!r} не сократил выдачу: {found} карточек, как без поиска"

    with steps("проверить выдачу",
               f"карточки совпадают с выдачей Store API по ``{QUERY}``, ``{NEEDLE_HANDLE}`` на месте"):
        expected = _api_handles(api_client, QUERY)
        assert expected, f"Store API не отдаёт ничего на {QUERY!r} — сверять не с чем"
        assert NEEDLE_HANDLE in expected, f"эталоном должен быть товар {NEEDLE_HANDLE}"
        shown = {href.rstrip("/").rsplit("/", 1)[-1] for href in hrefs}
        assert shown, "на сетке нет ни одной карточки со ссылкой"
        assert NEEDLE_HANDLE in shown, f"{NEEDLE_HANDLE} пропал из выдачи {QUERY!r}: {sorted(shown)}"
        unknown = shown - expected
        assert not unknown, \
            f"витрина показала товары, которых нет в выдаче API по {QUERY!r}: {sorted(unknown)}"
        assert not errors, f"ошибки JS на странице поиска: {errors}"


def test_search_without_matches_shows_empty_state(page, steps):
    """Выдача без совпадений — пустое состояние каталога, а не ошибка и не 404."""
    shop, errors = _watch(page)

    with steps(f"отправить из шапки запрос без совпадений ``{NO_MATCH}``",
               "URL каталога с ``q``, страница ответила"):
        shop.goto("/")
        page.locator(HEADER_SEARCH_INPUT).first.wait_for(state="visible", timeout=20_000)
        page.locator(HEADER_SEARCH_INPUT).first.fill(NO_MATCH)
        page.locator(HEADER_SEARCH_SUBMIT).first.click()
        assert shop.wait_until(lambda: "q=" in page.url, timeout_s=20), \
            f"поиск из шапки не привёл на страницу результатов: {page.url}"
        page.locator(CATALOG_COUNT).first.wait_for(state="visible", timeout=20_000)

    with steps("посмотреть на пустую выдачу",
               "пустое состояние видно, счётчик 0, карточек нет, ошибок JS нет"):
        empty = page.locator(CART_EMPTY)
        assert empty.count() > 0, "блок пустого состояния не отрисован"
        assert EMPTY_TEXT in empty.first.inner_text(), \
            f"в пустом состоянии другой текст: {empty.first.inner_text()!r}"
        assert shop.catalog_count() == 0, "счётчик nonzero при запросе без совпадений"
        assert shop.catalog_hrefs() == [], "при пустом состоянии на сетке есть карточки"
        assert not errors, f"ошибки JS на пустой выдаче: {errors}"

    with steps("кликнуть ссылку в пустом состоянии",
               "из поиска можно вернуться к обычному каталогу"):
        reset = empty.first.locator("a")
        assert reset.count() > 0, "в пустом состоянии нет ни одной ссылки — выход только через URL"
        reset.first.click()
        assert shop.wait_until(lambda: shop.catalog_count() > 0, timeout_s=20), \
            f"ссылка из пустого состояния не вернула товары: {page.url}"


def test_direct_cyrillic_url_renders_without_error(page, web_client, steps):
    """Прямой заход ``/rs/catalog?q=<кириллица>``: страница отвечает и не падает.

    Фиксирует фактическое поведение; кандидат в баги. Заголовки стэнда
    записаны латиницей, транслитерации в поиске нет, поэтому ``мушкарац``
    (русское «мушкарац» для «šešir») остаётся без совпадений. Покупатель видит
    пустое состояние с текстом про **фильтры**, хотя никаких фильтров он не
    выбирал, — выдача пустая именно из-за запроса.
    """
    shop, errors = _watch(page)
    query = "мушкарац"

    with steps(f"HTTP-запрос ``/rs/catalog?q={query}``", "200, не редирект и не 4xx/5xx"):
        response = web_client.get("/rs/catalog", params={"q": query})
        assert response.status_code == 200, (response.status_code, response.text[:300])

    with steps("открыть тот же адрес в браузере",
               "страница собралась: счётчик и пустое состояние видны"):
        shop.goto(f"/rs/catalog?q={query}")
        page.locator(CATALOG_COUNT).first.wait_for(state="visible", timeout=20_000)
        assert page.locator(PRODUCT_CARD).count() == 0, \
            "кирилличный запрос всё-таки нашёл карточки — стенд передумал не транслитерировать"
        assert shop.catalog_count() == 0, "счётчик nonzero при кирилличном запросе"
        assert page.locator(CART_EMPTY).count() > 0, "для пустой кирилличной выдачи нет пустого состояния"

    with steps("прочитать, что говорит пустое состояние",
               "текст про фильтры (факт): про запрос покупатель не получает ничего"):
        text = page.locator(CART_EMPTY).first.inner_text()
        assert EMPTY_TEXT in text, f"пустое состояние изменилось: {text!r}"
        assert query not in text, \
            f"пустое состояние не упоминает запрос: {text!r}"
        assert not errors, f"ошибки JS на кирилличном запросе: {errors}"
