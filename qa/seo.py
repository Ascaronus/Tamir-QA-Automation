"""Разбор `robots.txt` и директив «не индексировать» из ответа стенда.

Здесь только парсинг и нормализация — никаких запросов и никаких ожиданий.
Ожидания живут в тестах, поэтому модуль проверяется офлайн юнит-тестами.

Обрезано до того, что нужно единственной SEO-проверке стенда («закрыт от
индекса»): канонические URL, hreflang, og:/twitter: и sitemap здесь больше не
разбираются — проверки этих атрибутов убраны из набора.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: Директивы `X-Robots-Tag` / `<meta name="robots">`, которые мы умеем видеть.
#: Неизвестные значения не выбрасываются: их видно в сообщении теста.
_META_TAGS_RE = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""(\w+)\s*=\s*("([^"]*)"|'([^']*)')""")
_ROBOTS_NAMES = ("robots", "googlebot")


@dataclass(frozen=True)
class RobotsTxt:
    """Разобранный `robots.txt`."""

    #: Правила для групп юзер-эйджентов: `*` — общее правило.
    allow: tuple[str, ...] = ()
    disallow: tuple[str, ...] = ()
    #: Строки `Sitemap:` как в файле (обычно абсолютные URL).
    sitemaps: tuple[str, ...] = field(default_factory=tuple)
    #: Для какой группы правила: `*`, `googlebot`, ...
    agent: str = "*"

    @property
    def has_open_access(self) -> bool:
        """Файл разрешает обход без ограничений (`Allow: /` и ни одного `Disallow`)."""
        return bool(self.allow) and not self.disallow


def parse_robots_txt(text: str) -> RobotsTxt:
    """Правила группы `User-Agent: *` и строки `Sitemap:`.

    Файл читается посимвольно-по-строкам: BOM, комментарии и CRLF из
    реальных ответов не должны ломать разбор. Правила других юзер-эйджентов
    игнорируются — тест смотрит общее правило.
    """
    allow: list[str] = []
    disallow: list[str] = []
    sitemaps: list[str] = []
    current_agent = "*"
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if name == "user-agent":
            current_agent = value or "*"
        elif current_agent != "*":
            continue
        elif name == "allow" and value:
            allow.append(value)
        elif name == "disallow":
            # `Disallow:` с пустым значением = «не запрещать ничего».
            if value:
                disallow.append(value)
        elif name == "sitemap" and value:
            sitemaps.append(value)
    return RobotsTxt(allow=tuple(allow), disallow=tuple(disallow), sitemaps=tuple(sitemaps))


def parse_robots_directives(value: str | None) -> frozenset[str]:
    """Директивы из `X-Robots-Tag` (регистр не важен, значения через запятую)."""
    if not value:
        return frozenset()
    return frozenset(part.strip().lower() for part in value.split(",") if part.strip())


def page_robots_directives(headers, html: str) -> frozenset[str]:
    """Директивы закрытия страницы из обоих механизмов сразу.

    Стенд закрывает страницы и заголовком `X-Robots-Tag`, и тегом
    `<meta name="robots">`. Тесту важно, чтобы страница была закрыта хоть
    чем-то, поэтому директивы объединяются; откуда именно — видно по
    значению заголовка в сообщении теста.
    """
    found = parse_robots_directives(headers.get("x-robots-tag"))
    for tag in _META_TAGS_RE.findall(html or ""):
        attrs = {}
        for match in _ATTR_RE.finditer(tag):
            attrs[match.group(1).lower()] = match.group(3) if match.group(3) is not None else match.group(4)
        if attrs.get("name", "").lower() in _ROBOTS_NAMES:
            found |= parse_robots_directives(attrs.get("content"))
    return found


def host_of(url: str | None) -> str:
    """Хост адреса из `Sitemap:` (без порта) — чтобы отличить стенд от прода."""
    if not url:
        return ""
    target = url if "://" in url else f"//{url}"
    return urlsplit(target).netloc.lower().split(":")[0]
