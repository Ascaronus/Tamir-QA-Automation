"""М2: каталог, поиск и sitemap витрины на стенде.

Здесь проверяется контракт листинга (пагинация, фильтры, поиск) и состояние
`sitemap.xml`. Факт по sitemap зафиксирован зондом 23.11.2026: staging отдаёт
пустой `<urlset/>`, потому что `frontend/src/app/sitemap.ts` берёт товарные
фиды с **боевого** `api.tamir.rs` (в коде нет `staging.api.tamir.rs`), а на
стенде эти адреса недоступны. Это продуктовый gap, он помечен `xfail` и
описан в `docs/TEST-PLAN.md`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest

from qa.seo import hosts_of, parse_seo, parse_sitemap, resolve_url, same_url

pytestmark = pytest.mark.catalog

CATALOG_ROUTE = "/rs/catalog"
#: Публичный sr-адрес каталога НЕ локализуется: в `frontend/src/lib/i18n/paths.ts`
#: список `servicePaths` накрывает только `account*` и `cart` -> `korpa`. Значит
#: `/rs/katalog` не должен рождать дубль, который начнёт индексироваться.
CATALOG_UNLOCALIZED_SLUG_ROUTE = "/rs/katalog"

#: Сколько ссылок на товар проверяем на доступность (чтобы прогон не раздувался).
PRODUCT_LINKS_TO_CHECK = 3


def _product_hrefs(html: str) -> list[str]:
    return sorted(set(re.findall(r'href="(/(?:rs|en)/products/[^"?#]+)"', html)))


def test_sitemap_is_valid_xml_from_one_origin(web_client, config):
    """`sitemap.xml` — валидный XML и все ссылки одного происхождения."""
    response = web_client.get("/sitemap.xml")
    assert response.status_code == 200, f"/sitemap.xml: получен {response.status_code}"
    assert "xml" in (response.headers.get("content-type") or "").lower(), (
        f"/sitemap.xml: content-type {response.headers.get('content-type')!r}"
    )

    urls, nested = parse_sitemap(response.text)
    assert response.text.lstrip().startswith("<?xml"), (
        f"/sitemap.xml: ответ не похож на XML: {response.text[:120]!r}"
    )
    if urls or nested:
        public_host = urlsplit(config.site_origin).netloc
        assert hosts_of(urls + nested) == {public_host}, (
            f"/sitemap.xml: ссылки ведут не на {public_host}: "
            f"{sorted(hosts_of(urls + nested))}"
        )


@pytest.mark.xfail(
    reason=(
        "PRODUCT GAP: frontend/src/app/sitemap.ts строит sitemap с боевого api.tamir.rs, "
        "на стенде он недоступен -> staging отдаёт пустой <urlset/>"
    ),
    strict=False,
)
def test_sitemap_lists_localized_storefront_pages(web_client):
    """В sitemap должны быть локализованные страницы витрины (ожидание M2).

    Пока проверка `xfail` и не красит прогон; когда продукт научит sitemap
    читать API стенда, тест станет обычным и начнёт охранять состав URL.
    """
    response = web_client.get("/sitemap.xml")
    urls, _ = parse_sitemap(response.text)
    assert urls, "sitemap.xml отдаёт пустой <urlset/>"

    paths = {urlsplit(url).path for url in urls}
    assert any(path.startswith("/rs") for path in paths), f"нет sr-страниц: {sorted(paths)[:10]}"
    assert any(path.startswith("/en") for path in paths), f"нет en-страниц: {sorted(paths)[:10]}"


def test_catalog_page_is_available_and_on_public_origin(web_client, config):
    """Каталог открывается, а его canonical лежит на публичном origin.

    canonical каталога в текущей сборке указывает на `/rs` (в макете лежит
    canonical главной страницы), поэтому проверяется инвариант «тот же хост, что
    и у канонического домена», а не точное совпадение с адресом страницы.
    """
    response = web_client.get(CATALOG_ROUTE)
    assert response.status_code == 200, f"{CATALOG_ROUTE}: получен {response.status_code}"

    seo = parse_seo(response.text)
    assert seo.canonical, f"{CATALOG_ROUTE}: canonical отсутствует"
    assert hosts_of([seo.canonical]) == {urlsplit(config.site_origin).netloc}, (
        f"{CATALOG_ROUTE}: canonical {seo.canonical!r} вне публичного домена"
    )


def test_catalog_slug_is_not_localized(web_client):
    """`/rs/katalog` -> `404`: у каталога нет локализованного адреса.

    В `frontend/src/lib/i18n/paths.ts` локалиуются только `account*` и
    `cart` -> `korpa`; каталог остаётся на `/rs/catalog`. Проверка держит
    это как инвариант: если кто-то добавит `catalog` -> `katalog`, появятся
    два индексируемых адреса одного листинга, и тест это поймает.
    """
    response = web_client.get(CATALOG_UNLOCALIZED_SLUG_ROUTE)
    assert response.status_code == 404, (
        f"{CATALOG_UNLOCALIZED_SLUG_ROUTE}: получен {response.status_code} — "
        f"у листинга появился второй адрес (дубль для индекса)"
    )


def test_catalog_trailing_slash_redirects_to_canonical_url(web_client, config):
    """`/rs/catalog/` -> 308 на `/rs/catalog` (нормализация следа)."""
    response = web_client.get(f"{CATALOG_ROUTE}/", allow_redirects=False)
    assert response.status_code == 308, (
        f"{CATALOG_ROUTE}/: получен {response.status_code}, ожидался 308"
    )
    location = response.headers.get("location") or ""
    assert resolve_url(response.url, location) and same_url(
        resolve_url(response.url, location), f"{config.web_url}{CATALOG_ROUTE}"
    ), f"{CATALOG_ROUTE}/: локация {location!r}"


def test_catalog_links_resolve(web_client):
    """Ссылки на товары из каталога ведут на живые карточки."""
    catalog = web_client.get(CATALOG_ROUTE)
    hrefs = _product_hrefs(catalog.text)
    assert hrefs, f"{CATALOG_ROUTE}: в HTML нет ни одной ссылки на товар"

    for href in hrefs[:PRODUCT_LINKS_TO_CHECK]:
        response = web_client.get(href)
        assert response.status_code == 200, (
            f"{CATALOG_ROUTE}: ссылка на товар {href!r} отвечает {response.status_code}"
        )


def test_catalog_beyond_last_page_returns_404(web_client):
    """Страница за последней страницей пагинации не существует."""
    response = web_client.get(f"{CATALOG_ROUTE}?page=99")
    assert response.status_code == 404, (
        f"{CATALOG_ROUTE}?page=99: ожидался 404, получен {response.status_code} — "
        f"пагинация отдаёт пустые страницы, которые плодятся в индексе"
    )


def test_catalog_unknown_category_returns_404(web_client):
    """Фильтр по несуществующей категории -> `404`, а не пустой «каталог»."""
    response = web_client.get(f"{CATALOG_ROUTE}?category_id=bogus")
    assert response.status_code == 404, (
        f"{CATALOG_ROUTE}?category_id=bogus: ожидался 404, получен {response.status_code}"
    )


def test_catalog_search_results_are_not_indexed(web_client):
    """Страница результатов поиска закрыта от индексации (иначе индекс мусором)."""
    response = web_client.get(f"{CATALOG_ROUTE}?q=tee")
    assert response.status_code == 200, f"?q=tee: получен {response.status_code}"

    header = (response.headers.get("x-robots-tag") or "").lower()
    assert "noindex" in header, (
        f"{CATALOG_ROUTE}?q=tee: страница поиска доступна индексации — "
        f"X-Robots-Tag={response.headers.get('x-robots-tag')!r}"
    )
