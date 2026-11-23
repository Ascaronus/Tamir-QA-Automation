"""М2: локальный роутинг и SEO-атрибуты (canonical / hreflang) на стенде.

Ожидаемое поведение взято из `openspec/specs/seo-routing/spec.md` (главный
документ продукта) и подтверждено зондом staging 23.11.2026:

- `/` -> `308` на `/rs`; `/rs/` и `/en/` -> `308` без слэша; неизвестная локаль -> `404`;
- короткая ссылка `/rs/cart` -> `308` на канонический sr-адрес `/rs/korpa`;
- query строка при редиректе сохраняется;
- canonical и hreflang всегда на публичном origin (`https://tamir.rs`), а не на
  хосте стенда: `frontend/src/lib/seo.ts` берёт `PUBLIC_SITE_ORIGIN`, поэтому
  `NEXT_PUBLIC_SITE_URL=https://test.tamir.rs` на них не влияет;
- у legal-страниц sr-версия без префикса локали (`/privacy`), а `/rs/privacy` -> `404`.

Если staging когда-нибудь начнёт отдавать canonical со своим хостом, индекс
покроется дублями — за этим и следят проверки ниже.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest

from qa.seo import hosts_of, parse_seo, resolve_url, same_url, to_origin

pytestmark = pytest.mark.seo

#: Страницы, у которых canonical обязан указывать на себя.
SELF_CANONICAL_ROUTES = ("/rs", "/en", "/privacy", "/en/privacy")

#: Страницы с сопоставленными версиями на две локали.
#:
#: Listings здесь нет намеренно: страницы-списка товаров по адресу `/rs/products`
#: в продукте нет (`frontend/src/app/[countryCode]/products/[handle]` — только
#: карточка), листинг живёт на `/rs/catalog` и покрыт `tests/catalog/`.
BILINGUAL_ROUTES = ("/rs", "/en", "/privacy", "/en/privacy")

TRAILING_SLASH_ROUTES = ("/rs/", "/en/")
UNKNOWN_LOCALE_ROUTES = ("/sr", "/RS", "/de", "/xx", "/sr-Latn-ME")


def _final_target(response) -> str:
    """Абсолютный адрес из `Location` (Next отдаёт `/rs` относительным)."""
    location = response.headers.get("location") or ""
    return resolve_url(response.url, location) if location else ""


@pytest.mark.parametrize(
    ("route", "expected_status"),
    [("/", 308), ("/rs/", 308), ("/en/", 308), ("/rs/cart", 308)],
)
def test_locale_redirects_use_308(web_client, route, expected_status):
    """Редиректы роутинга — постоянные (308), а не временные.

    Спецификация различает «постоянный» и «временный» редирект; подмена locale
    в рамках одного адреса обязана быть постоянной, иначе поисковый индекс не
    сможет склеить сигналы.
    """
    response = web_client.get(route, allow_redirects=False)
    assert response.status_code == expected_status, (
        f"{route}: ожидался {expected_status}, получен {response.status_code}"
    )


def test_root_redirects_to_default_locale(web_client, config):
    """/` уводит на локаль по умолчанию (`sr` -> `/rs`), а не отдаёт HTML."""
    response = web_client.get("/", allow_redirects=False)
    target = _final_target(response)
    assert target, "редирект / без Location"
    assert same_url(target, f"{config.web_url}/rs"), f"цель редиректа / — {target!r}"


@pytest.mark.parametrize("route", TRAILING_SLASH_ROUTES)
def test_trailing_slash_redirects_to_canonical_url(web_client, config, route):
    """Слэш в конце -> 308 на адрес без слэша (иначе два индексируемых URL)."""
    response = web_client.get(route, allow_redirects=False)
    target = _final_target(response)
    assert same_url(target, f"{config.web_url}{route.rstrip('/')}"), (
        f"{route}: цель редиректа {target!r} ожидалась "
        f"{config.web_url + route.rstrip('/')!r}"
    )


def test_short_slug_redirects_to_canonical_url(web_client, config):
    """Короткий sr-адрес ведёт на канонический: `/rs/cart` -> `/rs/korpa`."""
    response = web_client.get("/rs/cart", allow_redirects=False)
    target = _final_target(response)
    assert same_url(target, f"{config.web_url}/rs/korpa"), (
        f"/rs/cart: цель редиректа {target!r}, ожидался /rs/korpa"
    )


def test_redirect_preserves_query_string(web_client, config):
    """Редирект не теряет параметры: `/?q=tee&page=2` -> `/rs?q=tee&page=2`."""
    response = web_client.get("/?q=tee&page=2", allow_redirects=False)
    target = _final_target(response)
    assert target, "редирект без Location"
    parts = urlsplit(target)
    assert parts.path == "/rs", f"цель {target!r}, ожидался путь /rs"
    assert "q=tee" in parts.query and "page=2" in parts.query, (
        f"редирект потерял query: {target!r}"
    )


@pytest.mark.parametrize("route", UNKNOWN_LOCALE_ROUTES)
def test_unknown_locale_has_no_page(web_client, route):
    """Неизвестная или недоступная локаль -> `404`, без «тихого» дубля."""
    response = web_client.get(route)
    assert response.status_code == 404, (
        f"{route}: ожидался 404, получен {response.status_code} "
        f"(доступные локали: sr=/rs, en=/en)"
    )


@pytest.mark.parametrize("route", BILINGUAL_ROUTES)
def test_seo_tags_never_point_to_staging_host(web_client, config, route):
    """Ни canonical, ни hreflang не должны указывать на хост стенда.

    Это инвариант, а не «текущая реальность»: `PUBLIC_SITE_ORIGIN` в продуктовом
    коде задан явно. Хост стенда в этих атрибутах означал бы, что staging учится
    индексироваться вместо боевого сайта.
    """
    response = web_client.get(route)
    assert response.status_code == 200, f"{route}: получен {response.status_code}"
    seo = parse_seo(response.text)

    public_host = urlsplit(config.site_origin).netloc
    staging_host = urlsplit(config.web_url).netloc

    urls = {url for url in ([seo.canonical] if seo.canonical else [])}
    urls |= set(seo.alternates.values())
    assert urls, f"{route}: нет ни canonical, ни hreflang — SEO-атрибуты потеряны"

    found = hosts_of(urls)
    assert staging_host not in found, (
        f"{route}: SEO-атрибуты указывают на стенд {sorted(found)} — "
        f"индекс будет отравлен staging-URL"
    )
    assert found == {public_host}, (
        f"{route}: ожидался только хост {public_host!r}, получены {sorted(found)}"
    )


@pytest.mark.parametrize("route", SELF_CANONICAL_ROUTES)
def test_pages_self_canonicalize_on_public_origin(web_client, config, route):
    """canonical страницы = публичный origin + путь этой же страницы."""
    response = web_client.get(route)
    seo = parse_seo(response.text)
    assert seo.canonical, f"{route}: canonical отсутствует"
    assert same_url(seo.canonical, f"{config.site_origin}{route}"), (
        f"{route}: canonical {seo.canonical!r}, ожидался "
        f"{config.site_origin + route!r}"
    )


@pytest.mark.parametrize("route", BILINGUAL_ROUTES)
def test_hreflang_covers_both_locales(web_client, config, route):
    """У bilingual-страниц есть `sr` и `en`, обе на публичном origin."""
    response = web_client.get(route)
    seo = parse_seo(response.text)
    assert {"sr", "en"} <= set(seo.alternates), (
        f"{route}: hreflang покрывает {sorted(seo.alternates)}, ожидалась пара sr/en"
    )
    public_host = urlsplit(config.site_origin).netloc
    assert hosts_of(seo.alternates.values()) == {public_host}, (
        f"{route}: hreflang ведёт не на {public_host}: {seo.alternates}"
    )


def _product_hrefs(html: str) -> list[str]:
    """Ссылки на карточки товаров в sr-локали из HTML страницы."""
    return sorted(set(re.findall(r'href="(/rs/products/[^"?#]+)"', html)))


def test_product_page_alternate_resolves_in_both_locales(web_client, config):
    """hreflang-пара товара ведёт на живые адреса в обеих локалях.

    Взять «твин» простой заменой префикса нельзя: sr-адрес `/rs/products/crni-...` и
    en-адрес `/en/products/demi-season-black-hat` различаются слагом, поэтому
    проверяем именно ссылку из `hreflang`. Тупик в одной из локалей = битая
    перелинковка для поиска и для пользователя.
    """
    listing = web_client.get("/rs")
    hrefs = _product_hrefs(listing.text)
    assert hrefs, "на странице /rs не найдено ни одной ссылки на товар"

    sr_route = hrefs[0]
    sr_page = web_client.get(sr_route)
    assert sr_page.status_code == 200, f"{sr_route}: получен {sr_page.status_code}"

    sr_seo = parse_seo(sr_page.text)
    assert sr_seo.canonical and same_url(sr_seo.canonical, f"{config.site_origin}{sr_route}"), (
        f"{sr_route}: canonical {sr_seo.canonical!r} не совпадает с адресом страницы"
    )
    assert sr_seo.has_json_ld, f"{sr_route}: нет JSON-LD (Product) на карточке товара"

    en_target = sr_seo.alternates.get("en")
    assert en_target, f"{sr_route}: у карточки нет en-версии в hreflang"

    # hreflang отдаётся на публичном домене, а трогать прод запретно: адрес
    # карточки переносится на тот же путь стенда.
    en_probe = to_origin(en_target, config.web_url)
    en_response = web_client.get(en_probe)
    assert en_response.status_code == 200, (
        f"en-версия товара {en_target!r} отвечает {en_response.status_code} — "
        f"hreflang ведёт в тупик"
    )
    en_seo = parse_seo(en_response.text)
    back = en_seo.alternates.get("sr")
    assert back and urlsplit(back).path == sr_route, (
        f"{en_target}: sr-обратная ссылка {back!r} не ведёт на {sr_route}"
    )
