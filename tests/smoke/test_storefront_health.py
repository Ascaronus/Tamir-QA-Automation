"""Read-only smoke витрины (M1).

Проверяем только то, что действительно наблюдалось на стенде: наличие
robots.txt/sitemap.xml, заголовок каталога и редирект корня на локаль.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import allure
import pytest

from qa.config import QaConfig
from qa.http import QaHttpClient

pytestmark = pytest.mark.smoke

_TITLE_RE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)


@allure.step("GET /robots.txt отвечает 200")
def test_robots_txt_is_available(web_client: QaHttpClient) -> None:
    response = web_client.get("/robots.txt")
    assert response.status_code == 200, (
        f"/robots.txt вернул {response.status_code}, ожидался 200"
    )


@allure.step("GET /sitemap.xml отвечает 200")
def test_sitemap_xml_is_available(web_client: QaHttpClient) -> None:
    response = web_client.get("/sitemap.xml")
    assert response.status_code == 200, (
        f"/sitemap.xml вернул {response.status_code}, ожидался 200"
    )


@allure.step("GET /rs отвечает 200 и содержит заголовок каталога")
def test_catalog_page_title(web_client: QaHttpClient) -> None:
    response = web_client.get("/rs")
    assert response.status_code == 200, f"/rs вернул {response.status_code}, ожидался 200"

    match = _TITLE_RE.search(response.text)
    assert match is not None, "в HTML страницы /rs не найден тег <title>"
    title = match.group("title").strip()
    assert "Katalog | Tamir" in title, (
        f"в <title> не найдено 'Katalog | Tamir', получено: {title!r}"
    )


@allure.step("GET / редиректит 308 на путь локали того же origin")
def test_root_redirects_to_locale(web_client: QaHttpClient, config: QaConfig) -> None:
    response = web_client.get("/", allow_redirects=False)
    assert response.status_code == 308, (
        f"/ вернул {response.status_code}, ожидался редирект 308"
    )

    location = response.headers.get("Location", "")
    assert location, "ответ 308 не содержит заголовок Location"

    base = urlsplit(config.web_url)
    expected_origin = f"{base.scheme}://{base.netloc}"
    assert location.startswith(expected_origin), (
        f"Location {location!r} ведёт не на тот же origin ({expected_origin})"
    )
    assert "/rs" in location or "/en" in location, (
        f"Location {location!r} не содержит путь локали /rs или /en"
    )
