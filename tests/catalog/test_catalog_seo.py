"""Функциональные проверки витрины (локаль `sr`): каталог, карточка, корзина.

Это не SEO-проверки: здесь ничего не ищется в `<title>`, canonical или
og:атрибутах. Проверяется поведение, которое видит пользователь:

- адрес каталога (`/rs/catalog`) отвечает и содержит список товаров;
- товаров в списке ровно столько, сколько обещает счётчик на странице;
- у каждой карточки в списке есть цена в местном формате (`1.440,00 RSD`);
- поиск по каталогу сужает список (`?q=sesir`);
- карточка товара, открытая по ссылке из каталога, содержит цену и контроль
  «добавить в корзину»;
- страницы `/rs/korpa` и `/rs/checkout` открываются;
- одна и та же страница на `sr` и `en` отдаёт разные тексты интерфейса.

Все ожидания взяты из реальных ответов staging 23.11.2026, «селекторы» — из
разметки этих ответов, ничего не выдумано.

Почему нет браузерных проверок: `korpa` и `checkout` на SSR отдают только
заглушку `Učitavanje…`, пустое состояние и состав корзины рисует клиент.
Проверять их можно только в браузере — это зона `tests/e2e/`, здесь проверяем
статус и SSR-оболочку страницы.

Важно: `<html lang>` и тексты сравниваются по отрисованному `<main>`, потому
что Next инлайнит полный каталог сообщений локали в RSC-payload страницы — по
всему HTML любые строки находились бы всегда, и проверка была бы пустой.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.catalog

#: Ожидаемые тексты интерфейса по локалям. Источник — каталог сообщений
#: фронтенда (`frontend/src/messages/{sr,en}.json`) и фактический SSR staging.
EXPECTED_CHROME = {
    "sr": {
        "path": "/rs/catalog",
        "h1": "Svi proizvodi",
        "filters": "Filteri",
    },
    "en": {
        "path": "/en/catalog",
        "h1": "All products",
        "filters": "Filters",
    },
}

#: Цена в сербской локали: разделитель тысяч `.`, десятичный `,`, суффикс RSD.
PRICE_SR_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}\s?RSD$")

HTML_LANG_RE = re.compile(r"<html\b[^>]*\blang=\"([^\"]+)\"", re.I)
MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.I | re.S)
SCRIPT_RE = re.compile(r"<script\b.*?</script>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
CARD_TITLE_RE = re.compile(r"<a\b[^>]*class=\"product-card-title\"[^>]*href=\"([^\"]+)\"", re.I)
CARD_PRICE_RE = re.compile(r"<p\b[^>]*class=\"product-card-price\"[^>]*>(.*?)</p>", re.I | re.S)
COUNT_RE = re.compile(r"class=\"catalog-count-number\"[^>]*>(\d+)<", re.I)
ADD_TO_CART_RE = re.compile(
    r"<button\b[^>]*class=\"[^\"]*button-primary[^\"]*\"[^>]*>(.*?)</button>", re.I | re.S
)
PRODUCT_PRICE_RE = re.compile(r"<p\b[^>]*class=\"product-price[^\"]*\"[^>]*>(.*?)</p>", re.I | re.S)


def _main_body(html: str) -> str:
    """Содержимое отрисованного `<main>` — только SSR-разметка, не RSC-payload."""
    block = MAIN_RE.search(html or "")
    assert block, "в ответе нет `<main>` — страница не отдала SSR-разметку"
    return SCRIPT_RE.sub(" ", block.group(1))


def _main_text(html: str) -> str:
    """Текст отрисованного `<main>`: только то, что пользователь видит до гидратации."""
    return TAG_RE.sub(" ", _main_body(html))


def _main_products(html: str) -> tuple[list[str], list[str]]:
    """Ссылки на товары и цены карточек внутри `<main>`."""
    body = _main_body(html)
    return CARD_TITLE_RE.findall(body), CARD_PRICE_RE.findall(body)


def _text(fragment: str) -> str:
    """Текст HTML-фрагмента без разметки и неразрывных пробелов."""
    return TAG_RE.sub("", fragment).replace("\xa0", " ").strip()


def _lang(html: str) -> str | None:
    """Значение `<html lang>`."""
    match = HTML_LANG_RE.search(html or "")
    return match.group(1).lower() if match else None


# --------------------------------------------------------------------------- каталог


def test_katalog_otkryvaetsya_i_soderzhit_spisok_tovarov(web_client):
    """`/rs/catalog` отвечает 200 и отдаёт непустой список товаров."""
    response = web_client.get("/rs/catalog")
    assert response.status_code == 200, (
        f"/rs/catalog: получен {response.status_code} — витрина недоступна"
    )

    hrefs, prices = _main_products(response.text)
    assert hrefs, "на /rs/catalog нет ни одной карточки товара — пользователь видит пустую витрину"
    assert all(href.startswith("/rs/products/") for href in hrefs), (
        f"карточки ведут не на страницы товаров: {sorted(set(hrefs))[:3]}"
    )
    assert prices, "ни у одной карточки в списке нет цены"


def test_chislo_tovarov_v_spiske_soovaet_schetchiku(web_client):
    """Счётчик «N proizvoda» равен числу карточек: пользователь видит честное число."""
    response = web_client.get("/rs/catalog")
    assert response.status_code == 200, f"/rs/catalog: получен {response.status_code}"

    hrefs, prices = _main_products(response.text)
    match = COUNT_RE.search(_main_body(response.text))
    assert match, (
        "в каталоге нет счётчика товаров (`catalog-count-number`) — число "
        "найденных товаров неоткуда взять"
    )
    declared = int(match.group(1))
    assert declared == len(hrefs), (
        f"счётчик обещает {declared} товаров, а в списке карточек {len(hrefs)} — "
        "список обрезан или счётчик показывает неверное число"
    )
    assert len(prices) == len(hrefs), (
        f"карточек {len(hrefs)}, а цен в списке {len(prices)} — у части товаров цена не отрисована"
    )


def test_ceny_v_spiske_tovarov_v_mestnom_formate(web_client):
    """Цены в списке — местный формат `1.440,00 RSD`, а не `RSD 1,440.00`."""
    response = web_client.get("/rs/catalog")
    assert response.status_code == 200, f"/rs/catalog: получен {response.status_code}"

    _, prices = _main_products(response.text)
    assert prices, "в списке товаров нет ни одной цены"
    formatted = [_text(price) for price in prices]
    bad = [price for price in formatted if not PRICE_SR_RE.match(price)]
    assert not bad, f"цены не в сербском формате: {bad[:5]} — ожидался вид `1.440,00 RSD`"


def test_poisk_po_katalogu_suzhaet_spisok_tovarov(web_client):
    """Поиск `/rs/catalog?q=...` возвращает подмножество товаров каталога."""
    full = web_client.get("/rs/catalog")
    assert full.status_code == 200, f"/rs/catalog: получен {full.status_code}"
    full_hrefs, _ = _main_products(full.text)
    assert full_hrefs, "в каталоге нет товаров — сравнивать поиск не с чем"

    found = web_client.get("/rs/catalog", params={"q": "sesir"})
    assert found.status_code == 200, (
        f"/rs/catalog?q=sesir: получен {found.status_code} — поиск по каталогу не отвечает"
    )
    found_hrefs, _ = _main_products(found.text)
    assert found_hrefs, "поиск по «sesir» не вернул ни одного товара"
    assert len(found_hrefs) < len(full_hrefs), (
        f"поиск вернул {len(found_hrefs)} товаров из {len(full_hrefs)} — фильтр "
        "по названию не применяется"
    )
    extra = set(found_hrefs) - set(full_hrefs)
    assert not extra, f"поиск показал товары, которых нет в каталоге: {sorted(extra)[:3]}"


def test_stranica_tovara_soderzhit_cenu_i_knopku_v_korpu(web_client):
    """Карточка товара по ссылке из каталога: цена и контроль «добавить в корзину»."""
    listing = web_client.get("/rs/catalog")
    assert listing.status_code == 200, f"/rs/catalog: получен {listing.status_code}"
    hrefs, _ = _main_products(listing.text)
    assert hrefs, "в каталоге нет ссылок на товары"

    href = hrefs[0]
    product = web_client.get(href)
    assert product.status_code == 200, f"{href}: страница товара отвечает {product.status_code}"

    price = PRODUCT_PRICE_RE.search(product.text)
    assert price, f"{href}: на странице товара нет цены (`product-price`)"
    assert PRICE_SR_RE.match(_text(price.group(1))), (
        f"{href}: цена {_text(price.group(1))!r} не в формате `1.290,00 RSD`"
    )

    button = ADD_TO_CART_RE.search(product.text)
    assert button, f"{href}: на странице товара нет кнопки добавления в корзину"
    label = _text(button.group(1))
    assert "korpu" in label.lower(), (
        f"{href}: главная кнопка товара называется {label!r}, ожидался «U korpu»"
    )


# ------------------------------------------------------------------- korpa / checkout


def test_korpa_otkryvaetsya_v_srpskoj_lokali(web_client):
    """`/rs/korpa` отвечает 200 и показывает сербскую SSR-оболочку."""
    response = web_client.get("/rs/korpa")
    assert response.status_code == 200, f"/rs/korpa: получен {response.status_code} — корзина недоступна"

    lang = _lang(response.text)
    assert lang and lang.startswith("sr"), (
        f"/rs/korpa: `<html lang>` = {lang!r}, ожидалась сербская локаль"
    )
    main = _main_text(response.text)
    assert "Korpa" in main, "/rs/korpa: в `<main>` нет заголовка «Korpa» — страница корзины не собралась"
    # Пустое состояние и состав корзины рисует клиент, поэтому проверяется
    # только SSR-заглушка: без неё до гидратации пользователь видит пустоту.
    assert "Učitavanje…" in main, (
        "/rs/korpa: SSR не показывает состояние загрузки — до гидратации страница пустая"
    )


def test_checkout_otkryvaetsya_v_srpskoj_lokali(web_client):
    """`/rs/checkout` отвечает 200 — оформление заказа доступно."""
    response = web_client.get("/rs/checkout")
    assert response.status_code == 200, (
        f"/rs/checkout: получен {response.status_code} — оформление заказа недоступно"
    )
    lang = _lang(response.text)
    assert lang and lang.startswith("sr"), (
        f"/rs/checkout: `<html lang>` = {lang!r}, ожидалась сербская локаль"
    )
    assert _main_text(response.text).strip(), (
        "/rs/checkout: `<main>` пуст — страница оформления не отдала SSR-контент"
    )


# ------------------------------------------------------------------------- lokalizaciya


@pytest.mark.parametrize("locale", ("sr", "en"))
def test_katalog_localizuet_interfeys(web_client, locale):
    """Одна и та же страница каталога на `sr` и `en` отдаёт свой `<html lang>` и свой текст."""
    expected = EXPECTED_CHROME[locale]
    path = expected["path"]
    response = web_client.get(path)
    assert response.status_code == 200, f"{path}: получен {response.status_code}"

    lang = _lang(response.text)
    assert lang == locale, f"{path}: `<html lang>` = {lang!r}, ожидалась локаль {locale!r}"

    main = _main_text(response.text)
    assert expected["h1"] in main, (
        f"{path}: в `<main>` нет заголовка {expected['h1']!r} — интерфейс не переведён на {locale}"
    )
    assert expected["filters"] in main, (
        f"{path}: в `<main>` нет подписи {expected['filters']!r} — панель фильтров не локализована"
    )

    other = EXPECTED_CHROME["en" if locale == "sr" else "sr"]
    assert other["h1"] not in main, (
        f"{path}: в `<main>` встречается чужой заголовок {other['h1']!r} — в интерфейс попадает чужая локаль"
    )
