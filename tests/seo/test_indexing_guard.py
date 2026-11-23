"""М2: non-prod витрина обязана быть закрыта от поисковых систем.

Правило из плана (§13, шаг 5): стенда не должно быть в индексе Google. Продуктовый
код закрывает страницы и заголовком `X-Robots-Tag`, и тегом `<meta name=robots>`
(`frontend/src/app/layout.tsx`), поэтому проверяем оба механизма: один может
исчезнуть при правке `next.config.ts`, второй — при правке метаданных.

Часть проверок специально сужена до факта, который держится всегда: `noindex` и
`nofollow`. Прочие директивы (`noarchive`, `nosnippet`) в текущей сборке есть, но
их отсутствие не было бы ошибкой окружения.
"""

from __future__ import annotations

import pytest

from qa.seo import parse_robots_directives, parse_robots_txt, parse_seo, same_url

pytestmark = pytest.mark.seo

#: Минимально обязательный набор директив для любой страницы non-prod.
REQUIRED_DIRECTIVES = frozenset({"noindex", "nofollow"})

#: Маршруты, которые отдают HTML со статусом 200.
HTML_ROUTES = (
    "/rs",
    "/en",
    "/rs/korpa",
    "/rs/nalog",
    "/rs/catalog",
    "/privacy",
    "/en/privacy",
)

#: Маршруты, у которых страницы нет (неизвестная локаль, несуществующий товар).
NOT_FOUND_ROUTES = (
    "/sr",
    "/de",
    "/xx",
    "/rs/net-takoj-stranice",
    "/rs/products/neta-takogo-tovara",
)

#: Маршруты, которые всегда уходят редиректом.
REDIRECT_ROUTES = ("/", "/rs/", "/en/", "/rs/cart")


def _require_closed(response, what: str) -> None:
    """Оба механизма закрытия от индексации на месте и не противоречат друг другу."""
    header = parse_robots_directives(response.headers.get("x-robots-tag"))
    assert REQUIRED_DIRECTIVES <= header, (
        f"{what}: заголовок `X-Robots-Tag` не закрывает страницу — "
        f"получено {response.headers.get('x-robots-tag')!r}"
    )
    meta_value = parse_seo(response.text).meta_robots
    assert REQUIRED_DIRECTIVES <= parse_robots_directives(meta_value), (
        f"{what}: `<meta name=robots>` не закрывает страницу — получено {meta_value!r}"
    )


@pytest.mark.parametrize("route", HTML_ROUTES)
def test_html_pages_closed_for_indexing(web_client, config, route):
    """Страницы стенда не должны попасть в индекс поисковых систем."""
    if not config.expects_noindex:
        pytest.skip("QA_ENV помечен как production — проверка noindex не применима")

    response = web_client.get(route)
    assert response.status_code == 200, f"{route}: ожидался 200, получен {response.status_code}"
    assert "html" in (response.headers.get("content-type") or "").lower(), (
        f"{route}: ожидался HTML, получен content-type "
        f"{response.headers.get('content-type')!r}"
    )
    _require_closed(response, route)


@pytest.mark.parametrize("route", NOT_FOUND_ROUTES)
def test_missing_pages_respond_404_and_are_not_indexable(web_client, route):
    """Нет страницы -> 404, и она всё равно закрыта от индексации."""
    response = web_client.get(route)
    assert response.status_code == 404, (
        f"{route}: ожидался 404, получен {response.status_code}"
    )
    header = parse_robots_directives(response.headers.get("x-robots-tag"))
    assert "noindex" in header, (
        f"{route}: страница 404 не закрыта от индексации — {response.headers.get('x-robots-tag')!r}"
    )


@pytest.mark.parametrize("route", REDIRECT_ROUTES)
def test_redirects_are_closed_for_indexing(web_client, route):
    """Редирект не должен индексироваться: заголовок ставится и на 3xx ответ."""
    response = web_client.get(route, allow_redirects=False)
    assert response.status_code in (301, 308), (
        f"{route}: ожидался редирект 301/308, получен {response.status_code}"
    )
    header = parse_robots_directives(response.headers.get("x-robots-tag"))
    assert "noindex" in header, (
        f"{route}: редирект не закрыт от индексации — {response.headers.get('x-robots-tag')!r}"
    )


def test_robots_txt_is_readable_and_does_not_block_the_site(web_client):
    """`robots.txt`: 200, читается, и при этом сайт не закрыт полностью.

    `Disallow:` пустое — разрешено всё. Если кто-то по ошибке поставит
    `Disallow: /`, тест поймает: тогда и `noindex`, и `robots.txt` закрывают
    стенд, и диагностика «почему Google ничего не видит» станет запутанной.
    """
    response = web_client.get("/robots.txt")
    assert response.status_code == 200, f"/robots.txt: получен {response.status_code}"

    rules, _ = parse_robots_txt(response.text)
    assert rules, f"/robots.txt: не разобрано ни одного правила — {response.text!r}"
    for user_agent, directives in rules.items():
        blocking = [d for d in directives if d.startswith("disallow: /")]
        assert not blocking, f"/robots.txt: {user_agent} закрыт целиком ({blocking})"


def test_robots_txt_references_sitemap(web_client, config):
    """Gap-тест: в `robots.txt` стенда нет строки `Sitemap:`.

    Проверено 23.11.2026: `/robots.txt` отдаёт `User-Agent: *` + `Allow: /` без
    указания sitemap. Найдены в продуктовом коде только `app/robots.ts` (текст
    без `Sitemap:`) и `app/sitemap.ts`, поэтому finding заводится как продуктовый
    баг, а не как поломка стенда: поиск не узнаёт про sitemap из robots.txt.
    Ветка `xfail` — проверка начнёт работать сама, когда `robots.ts` добавит `Sitemap`.
    """
    response = web_client.get("/robots.txt")
    _, sitemaps = parse_robots_txt(response.text)
    if not sitemaps:
        pytest.xfail(
            "PRODUCT GAP: в robots.txt нет строки Sitemap: — "
            "frontend/src/app/robots.ts не добавляет ссылку на sitemap"
        )

    assert any(
        same_url(item, f"{config.web_url}/sitemap.xml") for item in sitemaps
    ), f"Sitemap: указывает не на наш sitemap — {sitemaps}"
