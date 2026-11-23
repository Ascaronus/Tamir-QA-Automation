"""Разбор SEO-атрибутов из HTML, `robots.txt`, sitemap и SEO-заголовков.

Здесь только парсинг и нормализация — никаких запросов и никаких ожиданий.
Ожидания живут в тестах, поэтому модуль проверяется офлайн юнит-тестами.
Регэкспы терпимы к регистру имён атрибутов и к одинарным кавычкам: Next
эмитит `hrefLang`, а в HTML из срезов/других версий порядок атрибутов свой.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

TAG_RE = re.compile(r"<(?:link|meta)\b[^>]*>", re.I)
JSON_LD_RE = re.compile(r"<script\b[^>]*application/ld\+json", re.I)
_ATTR_NAMES = ("rel", "href", "hreflang", "name", "property", "content")


@dataclass(frozen=True)
class SeoTags:
    """SEO-атрибуты одной HTML-страницы."""

    canonical: str | None = None
    #: hreflang -> href (`x-default`, если эмитирован, лежит здесь же)
    alternates: dict[str, str] = field(default_factory=dict)
    meta_robots: str | None = None
    title: str | None = None
    description: str | None = None
    has_json_ld: bool = False

    def meta_directives(self) -> frozenset[str]:
        """`meta name=robots` как множество директив в нижнем регистре."""
        return parse_robots_directives(self.meta_robots)


def _tag_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for name in _ATTR_NAMES:
        match = re.search(
            rf'\b{name}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'<>]+))', tag, re.I
        )
        if match:
            attrs[name] = next((g for g in match.groups() if g is not None), "")
    return attrs


def parse_robots_directives(value: str | None) -> frozenset[str]:
    """`"noindex, nofollow"` -> `{"noindex", "nofollow"}`."""
    if not value:
        return frozenset()
    return frozenset(p.strip().lower() for p in value.split(",") if p.strip())


def has_json_ld(html: str) -> bool:
    """Есть ли на странице `<script type="application/ld+json">`."""
    return bool(JSON_LD_RE.search(html))


def parse_seo(html: str) -> SeoTags:
    """Собирает canonical, hreflang, meta robots, description и факт JSON-LD."""
    canonical: str | None = None
    alternates: dict[str, str] = {}
    meta_robots: str | None = None
    description: str | None = None

    for match in TAG_RE.finditer(html):
        attrs = _tag_attrs(match.group(0))
        if not attrs:
            continue
        rel = attrs.get("rel", "").strip().lower()
        name = attrs.get("name", "").strip().lower()
        if rel == "canonical" and canonical is None:
            canonical = attrs.get("href")
        elif rel == "alternate" and attrs.get("hreflang") and attrs.get("href"):
            alternates.setdefault(attrs["hreflang"].strip().lower(), attrs["href"])
        elif name == "robots" and meta_robots is None:
            meta_robots = attrs.get("content")
        elif name == "description" and description is None:
            description = attrs.get("content")

    return SeoTags(
        canonical=canonical,
        alternates=alternates,
        meta_robots=meta_robots,
        description=description,
        has_json_ld=has_json_ld(html),
    )


def is_html(content_type: str | None) -> bool:
    """Признак того, что тело ответа — HTML (а не JSON/картинка)."""
    return "html" in (content_type or "").lower()


def parse_robots_txt(body: str) -> tuple[dict[str, list[str]], list[str]]:
    """`robots.txt` -> `(правила по user-agent, список из Sitemap:)`."""
    rules: dict[str, list[str]] = {}
    sitemaps: list[str] = []
    current: str | None = None
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            current = value.lower()
            rules.setdefault(current, [])
        elif key == "sitemap":
            sitemaps.append(value)
        elif key in ("allow", "disallow") and current is not None:
            rules[current].append(f"{key}: {value.lower()}")
    return rules, sitemaps


def _local(tag: str) -> str:
    """Имя XML-тега без namespace-префикса, в нижнем регистре."""
    return tag.rsplit("}", 1)[-1].lower()


def _locs(element) -> list[str]:
    return [
        child.text.strip()
        for child in element.iter()
        if _local(child.tag) == "loc" and child.text and child.text.strip()
    ]


def parse_sitemap(body: str) -> tuple[list[str], list[str]]:
    """XML sitemap -> `(страницы, ссылки sitemapindex)`.

    `<urlset>` даёт страницы во первом списке, `<sitemapindex>` — вложенные
    sitemap'ы во втором. Нерабираемый XML — оба списка пустые (тест сам
    решает, ошибка это или нет).
    """
    try:
        root = ElementTree.fromstring(body.strip())
    except ElementTree.ParseError:
        return [], []
    if _local(root.tag) == "sitemapindex":
        return [], _locs(root)
    return _locs(root), []


def normalize_url(value: str | None) -> str:
    """URL для сравнения: нижний регистр, без следего слэша и query-порядка."""
    if not value:
        return ""
    return value.rstrip("/").lower()


def resolve_url(base: str, target: str | None) -> str:
    """`Location` из редиректаNext отдаёт относительным (`/rs`) — приводим к URL."""
    if not target:
        return ""
    return urljoin(base, target)


def to_origin(url: str | None, origin: str) -> str:
    """Тот же адрес, но на другом origin.

    Нужно, чтобы проверить живость ссылки, которую продукт отдаёт на публичном
    домене: canonical/hreflang всегда `https://tamir.rs/...`, а трогать прод
    запретно (`require_allowed_target`), поэтому адрес переносится на стенд.
    """
    if not url:
        return ""
    parts = urlsplit(url)
    base = urlsplit(origin)
    rebuilt = parts._replace(scheme=base.scheme, netloc=base.netloc)
    return urlunsplit(rebuilt)


def same_url(left: str | None, right: str | None) -> bool:
    """Сравнение URL «как страницы» (слэш в конце не значим)."""
    return normalize_url(left) == normalize_url(right)


def hosts_of(urls: Iterable[str]) -> set[str]:
    """Хосты списка URL — чтобы проверить одно происхождение ссылок."""
    return {urlsplit(url).netloc.lower() for url in urls if url}


def path_of(url: str) -> str:
    """Путь URL (для проверок вида «каталог без параметров в списке»)."""
    return urlsplit(url).path
