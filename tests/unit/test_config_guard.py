"""Юнит-проверки guard'а по хостам (без сети).

Здесь проверяется единственное правило M1: запросы только на стенд
(`test.tamir.rs`, `test-api.tamir.rs`, `192.168.1.143`), прод Tamir — запрещён.
"""

from __future__ import annotations

import pytest
import requests

from qa.config import QaConfig, is_allowed_target, require_allowed_target, site_origin_for
from qa.http import QaHttpClient

pytestmark = pytest.mark.unit

ALLOWED_URLS = [
    "https://test.tamir.rs/rs",
    "https://test-api.tamir.rs/store/products",
    "http://192.168.1.143:8025/api/messages",
    # Сервисы самого QA-стека на staging: Kiwi TCMS (JSON-RPC в M2) и отчёты.
    "https://tcms.tamir.rs/xml-rpc/",
    "https://qa.tamir.rs/latest/",
]

PRODUCTION_URLS = [
    "https://tamir.rs/",
    "https://www.tamir.rs/",
    "https://api.tamir.rs/",
    "https://tamir.rs/admin",
]

FOREIGN_URLS = [
    "https://example.com/",
]

# Хосты внутри домена Tamir, но не staging: это не «просто чужой хост», а
# прод-подобное направление, поэтому ожидаем RuntimeError, а не False.
LOOKALIKE_URLS = [
    "https://evil-test.tamir.rs/",
    "https://shop.tamir.rs/",
]


@pytest.mark.parametrize("url", ALLOWED_URLS)
def test_allowed_targets(url: str) -> None:
    assert is_allowed_target(url) is True


@pytest.mark.parametrize("url", PRODUCTION_URLS)
def test_production_targets_are_forbidden(url: str) -> None:
    with pytest.raises(RuntimeError) as error:
        is_allowed_target(url)
    assert "production" in str(error.value)


@pytest.mark.parametrize("url", FOREIGN_URLS)
def test_foreign_targets_are_denied(url: str) -> None:
    assert is_allowed_target(url) is False


@pytest.mark.parametrize("url", LOOKALIKE_URLS)
def test_non_staging_tamir_hosts_are_forbidden(url: str) -> None:
    with pytest.raises(RuntimeError):
        is_allowed_target(url)


def test_require_allowed_target_passes_staging() -> None:
    assert require_allowed_target("https://test.tamir.rs/") == "https://test.tamir.rs/"


def test_require_allowed_target_rejects_foreign_host() -> None:
    with pytest.raises(RuntimeError):
        require_allowed_target("https://example.com/")


def test_http_client_refuses_production_base_url() -> None:
    with pytest.raises(RuntimeError):
        QaHttpClient("https://tamir.rs")


def test_http_client_refuses_production_path_after_construction() -> None:
    client = QaHttpClient("https://test-api.tamir.rs")
    with pytest.raises(RuntimeError):
        client.get("https://tamir.rs/admin")


def test_http_client_guard_rejects_redirect_to_production() -> None:
    """Даже если стенд ответит редиректом на проду, запрос должен быть помечен как запрещённый."""
    client = QaHttpClient("https://test-api.tamir.rs")
    redirected = requests.Response()
    redirected.url = "https://tamir.rs/admin"
    with pytest.raises(RuntimeError):
        client._guard_redirect(redirected)


# --- Публичный origin для SEO-атрибутов (M2) ---------------------------------
# Запросы идут на стенд, а canonical/hreflang продукт ставит на публичный
# домен, поэтому ожидания берутся из таблицы, а не из хоста запроса.

SITE_ORIGIN_CASES = [
    ("https://test.tamir.rs", "https://tamir.rs"),
    # Для хостов вне таблицы (локальный прогон) ожидания нет — сравнение идёт
    # с самим адресом, поэтому None.
    ("http://localhost:3000", None),
    ("https://qa.tamir.rs", None),
    ("не url", None),
]


@pytest.mark.parametrize(("web_url", "expected"), SITE_ORIGIN_CASES)
def test_site_origin_lookup(web_url: str, expected: str | None) -> None:
    assert site_origin_for(web_url) == expected


def test_config_site_origin_for_staging_is_public_domain() -> None:
    config = QaConfig(env="staging", web_url="https://test.tamir.rs", api_url="https://test-api.tamir.rs")
    assert config.site_origin == "https://tamir.rs"


def test_config_site_origin_falls_back_to_own_origin() -> None:
    config = QaConfig(env="dev", web_url="http://localhost:3000", api_url="http://localhost:9000")
    assert config.site_origin == "http://localhost:3000"


def test_config_expects_noindex_on_staging() -> None:
    config = QaConfig(env="staging", web_url="https://test.tamir.rs", api_url="https://test-api.tamir.rs")
    assert config.expects_noindex is True
