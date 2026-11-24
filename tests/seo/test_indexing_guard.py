"""Единственная SEO-проверка стенда: `test.tamir.rs` закрыт от поисковых систем.

Смысл одной проверки: staging не должен попасть в индекс. Всё остальное
(canonical / hreflang / og: / twitter:, ключевые слова в `<title>` и H1,
посчитанный sitemap) сюда не входит — см. `docs/TEST-PLAN.md` и находки в
отчётах прогонов.

Что подтверждает зонд staging 23.11.2026:

- все HTML-маршруты (и `404`-страницы) отдают `X-Robots-Tag:
  noindex, nofollow, noarchive, nosnippet` и `<meta name="robots"
  content="noindex, ...">`;
- `/` уводит `308` на `/rs`, поэтому редиректы проверяются до финальной
  страницы — закрытие должно дожить до неё;
- `/robots.txt` = `User-Agent: *` + `Allow: /`, без `Sitemap:`. Это сделано
  намеренно (`frontend/src/app/robots.ts`): бот должен зайти на страницу,
  чтобы увидеть `noindex`. Поэтому «разрешающий» robots.txt сам по себе не
  ошибка — ошибка, если он начнёт показывать индексной карте sitemap стенда.

`config.expects_noindex` — единственная точка правды о том, что текущее
окружение обязано быть закрытым от индекса.
"""

from __future__ import annotations

import pytest

from qa.seo import host_of, page_robots_directives, parse_robots_txt

pytestmark = pytest.mark.seo

#: Маршруты, которые обязаны быть закрыты от индекса. Часть из них уводит
#: редиректом (`/` -> `/rs`, `/rs/` -> `/rs`) — проверяется финальный ответ.
KEY_ROUTES = (
    "/",
    "/rs",
    "/en",
    "/rs/catalog",
    "/rs/korpa",
    "/rs/checkout",
    "/rs/nalog",
    "/privacy",
)

#: Страниц, «случайно» найденных ботом, тоже быть не должно: `404` тоже
#: закрывается директивами, иначе индекс собирает мусор из несуществующих URL.
NOT_FOUND_ROUTES = ("/xx", "/rs/net-takoj-stranice")

#: Директива, без которой страница считается открытой.
CLOSE_DIRECTIVE = "noindex"


@pytest.mark.seo
def test_staging_zakryt_ot_indeksacii(config, web_client):
    """Стенд закрыт от поисковых систем: `noindex` на ключевых маршрутах.

    Один тест-кейс, две стороны одной гарантии:

    1. каждый ключевой маршрут (и `404`) отвечает `noindex` — заголовком
       `X-Robots-Tag` или тегом `<meta name="robots">`;
    2. `robots.txt` не отдаёт поисковикам sitemap стенда — единственный
       способ «разрешить обход», который превращает закрытый стенд в
       индексированный.
    """
    assert config.expects_noindex, (
        f"окружение {config.web_url!r} не обязано быть закрытым от индекса — "
        "проверка стенда здесь не применима"
    )

    #: Пробелы в закрытии собираются все, а не только первый: так из одного
    #: прогона видно полную картину.
    open_routes: list[str] = []

    for route in (*KEY_ROUTES, *NOT_FOUND_ROUTES):
        response = web_client.get(route)
        directives = page_robots_directives(response.headers, response.text)
        if CLOSE_DIRECTIVE not in directives:
            open_routes.append(
                f"{route} (статус {response.status_code}, финальный адрес "
                f"{response.url!r}, X-Robots-Tag="
                f"{response.headers.get('x-robots-tag')!r}, разобранные "
                f"директивы {sorted(directives) or 'нет'})"
            )

    assert not open_routes, (
        f"{config.web_url} открыт индексации: на маршрутах нет `noindex` — "
        + "; ".join(open_routes)
    )

    robots = web_client.get("/robots.txt")
    assert robots.status_code == 200, (
        f"/robots.txt отвечает {robots.status_code} — нечем закрыть обход стенда"
    )
    parsed = parse_robots_txt(robots.text)
    staging_hosts = {host_of(config.web_url), host_of(config.api_url)}
    staging_sitemaps = [sm for sm in parsed.sitemaps if host_of(sm) in staging_hosts]
    assert not staging_sitemaps, (
        "/robots.txt указывает поисковым системам sitemap стенда "
        f"{staging_sitemaps} — страницы закрыты `noindex`, но их адреса всё "
        "равно попадут в индекс"
    )
