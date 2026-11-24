"""Юнит-проверки парсеров `qa/seo.py` (без сети).

Парсеры — единственное, что стоит между «сырым ответом стенда» и утверждениями
единственной SEO-проверки («стенд закрыт от индекса»). Если они врут, проверка
либо пропускает открытие стенда индексации, либо красит прогон на ровном месте,
поэтому здесь зафиксированы реальные формы ответов staging.
"""

from __future__ import annotations

import pytest

from qa.seo import (
    RobotsTxt,
    host_of,
    page_robots_directives,
    parse_robots_directives,
    parse_robots_txt,
)

pytestmark = pytest.mark.unit

#: Так `<meta name="robots">` выглядит в SSR-ответе Next (порядок атрибутов и
#: кавычки реальные, из дампа `/rs/catalog` 23.11.2026).
PAGE_WITH_META = """
<html lang="sr"><head>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet"/>
</head><body><main></main></body></html>
"""


# ---------------------------------------------------------------- robots.txt


def test_parse_robots_txt_reads_rules_and_sitemap() -> None:
    body = (
        "User-Agent: *\n"
        "Disallow: /admin\n"
        "Allow: /rs/\n"
        "\n"
        "# комментарий\n"
        "Sitemap: https://tamir.rs/sitemap.xml\n"
    )
    parsed = parse_robots_txt(body)
    assert parsed.allow == ("/rs/",)
    assert parsed.disallow == ("/admin",)
    assert parsed.sitemaps == ("https://tamir.rs/sitemap.xml",)
    assert parsed.has_open_access is False


def test_parse_robots_txt_matches_staging_answer() -> None:
    """Ровно так отвечает `/robots.txt` на staging 23.11.2026.

    `Allow: /` без `Disallow` и без `Sitemap:` — намеренное поведение
    фронтенда (`frontend/src/app/robots.ts`): бот должен зайти на страницу,
    чтобы увидеть `noindex`. Парсер обязан это видеть, а не считать ошибкой.
    """
    parsed = parse_robots_txt("User-Agent: *\nAllow: /\n\n")
    assert parsed == RobotsTxt(allow=("/",), disallow=(), sitemaps=())
    assert parsed.has_open_access is True


def test_parse_robots_txt_ignores_empty_disallow() -> None:
    """`Disallow:` с пустым значением = «не запрещать ничего»."""
    parsed = parse_robots_txt("User-Agent: *\nDisallow:\n")
    assert parsed.disallow == ()
    assert parsed.has_open_access is False


def test_parse_robots_txt_keeps_only_general_agent() -> None:
    """Правила конкретной группы юзер-эйджентов не подмешиваются в общее."""
    body = (
        "User-Agent: Googlebot\n"
        "Disallow: /rs\n"
        "\n"
        "User-Agent: *\n"
        "Allow: /\n"
    )
    parsed = parse_robots_txt(body)
    assert parsed.allow == ("/",)
    assert parsed.disallow == ()


def test_parse_robots_txt_survives_bom_and_crlf() -> None:
    """BOM и CRLF из реальных ответов не должны ломать разбор."""
    parsed = parse_robots_txt("\ufeffUser-Agent: *\r\nDisallow: /korpa\r\n")
    assert parsed.disallow == ("/korpa",)


# ------------------------------------------------------------------ директивы


def test_parse_robots_directives_handles_empty_and_spacing() -> None:
    assert parse_robots_directives(None) == frozenset()
    assert parse_robots_directives("") == frozenset()
    assert parse_robots_directives(" NOINDEX , nofollow ") == {"noindex", "nofollow"}


def test_parse_robots_directives_keeps_unknown_values() -> None:
    """Незнакомая директива остаётся видимой — её видно в сообщении теста."""
    assert parse_robots_directives("noindex, max-snippet:-1") == {"noindex", "max-snippet:-1"}


def test_page_robots_directives_reads_header() -> None:
    """Закрытие заголовком `X-Robots-Tag` (так закрыты маршруты staging)."""
    headers = {"x-robots-tag": "noindex, nofollow, noarchive, nosnippet"}
    assert page_robots_directives(headers, "<html><body></body></html>") == {
        "noindex",
        "nofollow",
        "noarchive",
        "nosnippet",
    }


def test_page_robots_directives_reads_meta_tag() -> None:
    """Закрытие тегом `<meta name="robots">`, когда заголовка нет."""
    assert page_robots_directives({}, PAGE_WITH_META) == {
        "noindex",
        "nofollow",
        "noarchive",
        "nosnippet",
    }


def test_page_robots_directives_merges_both_mechanisms() -> None:
    """Заголовок и мета объединяются: достаточно, чтобы закрыл хоть один."""
    headers = {"x-robots-tag": "noindex"}
    html = '<meta name="robots" content="nofollow"/>'
    assert page_robots_directives(headers, html) == {"noindex", "nofollow"}


def test_page_robots_directives_ignores_other_meta_tags() -> None:
    """`description` и `google-site-verification` не должны давать директив."""
    html = (
        '<meta name="description" content="noindex"'
        '><meta name="google-site-verification" content="noindex"/>'
    )
    assert page_robots_directives({}, html) == frozenset()


def test_page_robots_directives_accepts_single_quotes_and_other_order() -> None:
    html = "<meta content='NOINDEX' name='Googlebot'>"
    assert page_robots_directives({}, html) == {"noindex"}


def test_page_robots_directives_empty_page_is_empty() -> None:
    """Страница без заголовка и без меты открыта — тест обязан это поймать."""
    assert page_robots_directives({}, "") == frozenset()
    assert page_robots_directives({}, "<html><body><h1>404</h1></body></html>") == frozenset()


# ---------------------------------------------------------------------- хосты


def test_host_of_reads_host_from_absolute_url() -> None:
    assert host_of("https://test.tamir.rs/sitemap.xml") == "test.tamir.rs"
    assert host_of("https://Test.Tamir.rs:443/sitemap.xml") == "test.tamir.rs"


def test_host_of_handles_relative_and_missing_values() -> None:
    assert host_of("/sitemap.xml") == ""
    assert host_of("tamir.rs/sitemap.xml") == "tamir.rs"
    assert host_of(None) == ""
