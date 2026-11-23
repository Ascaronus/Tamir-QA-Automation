"""Юнит-проверки парсеров `qa/seo.py` (без сети).

Парсеры — единственное, между чем «сырой HTML стенда» и утверждениями тестов.
Если они врут, SEO-проверки M2 либо пропускают поломку, либо красят прогон на
ровном месте, поэтому здесь зафиксированы реальные формы ответов Next.
"""

from __future__ import annotations

import pytest

from qa.seo import (
    has_json_ld,
    hosts_of,
    is_html,
    normalize_url,
    parse_robots_directives,
    parse_robots_txt,
    parse_seo,
    parse_sitemap,
    path_of,
    same_url,
)

pytestmark = pytest.mark.unit

#: Фрагмент `<head>` страницы товара: Next эмитит `hrefLang` в верблюжьем
#: регистре, canonical — на публичный домен, JSON-LD — отдельным скриптом.
PRODUCT_HEAD = """
<html><head>
<link rel="canonical" href="https://tamir.rs/rs/products/crni-zimski-klobuk"/>
<link rel="alternate" hrefLang="sr" href="https://tamir.rs/rs/products/crni-zimski-klobuk"/>
<link rel='alternate' hreflang='en' href='https://tamir.rs/en/products/black-winter-hat-for-men'/>
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet"/>
<meta name="description" content="Zimski klopu"/>
<script type="application/ld+json">{"@type":"Product"}</script>
<title>Klobuk</title>
</head><body></body></html>
"""


def test_parse_seo_reads_canonical_and_alternates() -> None:
    seo = parse_seo(PRODUCT_HEAD)
    assert seo.canonical == "https://tamir.rs/rs/products/crni-zimski-klobuk"
    assert set(seo.alternates) == {"sr", "en"}
    assert seo.alternates["en"] == "https://tamir.rs/en/products/black-winter-hat-for-men"
    assert seo.description == "Zimski klopu"
    assert seo.has_json_ld is True


def test_parse_seo_reads_meta_robots_directives() -> None:
    seo = parse_seo(PRODUCT_HEAD)
    assert seo.meta_directives() == {"noindex", "nofollow", "noarchive", "nosnippet"}


def test_parse_seo_survives_page_without_seo_tags() -> None:
    """Страница 404 без SEO-тегов не должна ломать парсер."""
    seo = parse_seo("<html><body><h1>404</h1></body></html>")
    assert seo.canonical is None
    assert seo.alternates == {}
    assert seo.meta_robots is None
    assert seo.has_json_ld is False


def test_parse_seo_ignores_alternate_without_hreflang() -> None:
    """`rel=alternate` без `hreflang` (например `media`) не попадает в карту."""
    seo = parse_seo('<link rel="alternate" media="only screen and (max-width: 640px)" href="https://m.tamir.rs/rs"/>')
    assert seo.alternates == {}


def test_parse_seo_keeps_first_canonical() -> None:
    """Дубли canonical — берём первый, как это делает поисковый робот."""
    seo = parse_seo(
        '<link rel="canonical" href="https://tamir.rs/rs"/>'
        '<link rel="canonical" href="https://test.tamir.rs/rs"/>'
    )
    assert seo.canonical == "https://tamir.rs/rs"


def test_has_json_ld_accepts_other_attribute_order() -> None:
    assert has_json_ld('<script defer type="application/ld+json">{}</script>') is True
    assert has_json_ld('<script src="/ld.js"></script>') is False


def test_parse_robots_directives_handles_empty_and_spacing() -> None:
    assert parse_robots_directives(None) == frozenset()
    assert parse_robots_directives("") == frozenset()
    assert parse_robots_directives(" NOINDEX , nofollow ") == {"noindex", "nofollow"}


def test_parse_robots_txt_reads_rules_and_sitemap() -> None:
    body = (
        "User-Agent: *\n"
        "Disallow:\n"
        "Allow: /rs/\n"
        "\n"
        "# комментарий\n"
        "Sitemap: https://tamir.rs/sitemap.xml\n"
    )
    rules, sitemaps = parse_robots_txt(body)
    assert rules == {"*": ["disallow: ", "allow: /rs/"]}
    assert sitemaps == ["https://tamir.rs/sitemap.xml"]


def test_parse_robots_txt_without_sitemap_line() -> None:
    """Так выглядит ответ стенда 23.11.2026 — тест на `Sitemap:` это подхватит."""
    rules, sitemaps = parse_robots_txt("User-Agent: *\nAllow: /\n\n")
    assert rules == {"*": ["allow: /"]}
    assert sitemaps == []


def test_parse_sitemap_reads_urlset() -> None:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://tamir.rs/rs</loc></url>"
        "<url><loc>https://tamir.rs/en/products/x</loc></url>"
        "</urlset>"
    )
    urls, nested = parse_sitemap(body)
    assert urls == ["https://tamir.rs/rs", "https://tamir.rs/en/products/x"]
    assert nested == []


def test_parse_sitemap_reads_sitemapindex() -> None:
    body = (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://tamir.rs/sitemap-products.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    urls, nested = parse_sitemap(body)
    assert urls == []
    assert nested == ["https://tamir.rs/sitemap-products.xml"]


def test_parse_sitemap_accepts_empty_urlset_as_empty_result() -> None:
    """Пустой `<urlset/>` стенда — это пустой список, а не ошибка парсинга."""
    body = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    assert parse_sitemap(body) == ([], [])


def test_parse_sitemap_returns_empty_for_broken_xml() -> None:
    assert parse_sitemap("<urlset><url>") == ([], [])


def test_url_helpers_treat_trailing_slash_as_equal() -> None:
    assert same_url("https://test.tamir.rs/rs/", "https://test.tamir.rs/rs") is True
    assert same_url("https://test.tamir.rs/rs", "https://test.tamir.rs/en") is False
    assert normalize_url(None) == ""


def test_hosts_of_collects_unique_hosts() -> None:
    urls = [
        "https://tamir.rs/rs",
        "https://tamir.rs/en",
        "https://Test.Tamir.rs/x",
        "",
    ]
    assert hosts_of(urls) == {"tamir.rs", "test.tamir.rs"}


def test_path_of_ignores_host_and_query() -> None:
    assert path_of("https://tamir.rs/rs/catalog?page=2") == "/rs/catalog"


def test_is_html_accepts_next_content_type() -> None:
    assert is_html("text/html; charset=utf-8") is True
    assert is_html("application/json") is False
    assert is_html(None) is False
