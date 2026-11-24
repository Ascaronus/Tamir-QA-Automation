"""Read-only smoke витрины (M1).

Проверяем только то, что действительно наблюдалось на стенде: наличие
robots.txt/sitemap.xml, заголовок каталога и редирект корня на локаль.

Шаги обёрнуты в фикстуру `steps`, поэтому в Allure и в карточке тест-кейса в
Kiwi TCMS лежит проверка «действие → ожидание», которую можно повторить руками:
открыть адрес в браузере и посмотреть ответ.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest

from qa.config import QaConfig
from qa.http import QaHttpClient

pytestmark = pytest.mark.smoke

_TITLE_RE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)


def test_robots_txt_is_available(web_client: QaHttpClient, steps) -> None:
    """`/robots.txt` отдаётся — краулеры и карты сайта видят правила vitрины."""
    with steps("Открыть `/robots.txt`", "Файл отвечает 200"):
        response = web_client.get("/robots.txt")
        assert response.status_code == 200, (
            f"/robots.txt вернул {response.status_code}, ожидался 200"
        )


def test_sitemap_xml_is_available(web_client: QaHttpClient, steps) -> None:
    """`/sitemap.xml` отдаётся — страница карты сайта существует."""
    with steps("Открыть `/sitemap.xml`", "Файл отвечает 200"):
        response = web_client.get("/sitemap.xml")
        assert response.status_code == 200, (
            f"/sitemap.xml вернул {response.status_code}, ожидался 200"
        )


def test_catalog_page_title(web_client: QaHttpClient, steps) -> None:
    """`/rs` отдаёт страницу каталога с заголовком `Katalog | Tamir`."""
    with steps("Открыть страницу каталога `/rs`", "Страница отвечает 200"):
        response = web_client.get("/rs")
        assert response.status_code == 200, f"/rs вернул {response.status_code}, ожидался 200"

    with steps("Посмотреть заголовок вкладки браузера", "`<title>` содержит «Katalog | Tamir»"):
        match = _TITLE_RE.search(response.text)
        assert match is not None, "в HTML страницы /rs не найден тег <title>"
        title = match.group("title").strip()
        assert "Katalog | Tamir" in title, (
            f"в <title> не найдено 'Katalog | Tamir', получено: {title!r}"
        )


def test_root_redirects_to_locale(web_client: QaHttpClient, config: QaConfig, steps) -> None:
    """Корень `/` редиректит 308 на путь локали того же origin."""
    with steps("Открыть корень сайта `/` без следования редиректам", "Ответ 308 с заголовком Location"):
        response = web_client.get("/", allow_redirects=False)
        assert response.status_code == 308, (
            f"/ вернул {response.status_code}, ожидался редирект 308"
        )

        location = response.headers.get("Location", "")
        assert location, "ответ 308 не содержит заголовок Location"

    with steps("Проверить адрес переадресации", "Тот же адрес сайта и путь локали `/rs` или `/en`"):
        base = urlsplit(config.web_url)
        expected_origin = f"{base.scheme}://{base.netloc}"
        assert location.startswith(expected_origin), (
            f"Location {location!r} ведёт не на тот же origin ({expected_origin})"
        )
        assert "/rs" in location or "/en" in location, (
            f"Location {location!r} не содержит путь локали /rs или /en"
        )
