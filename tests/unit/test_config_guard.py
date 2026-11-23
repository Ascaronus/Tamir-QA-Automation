"""Юнит-проверки guard'а по хостам (без сети).

Здесь проверяется единственное правило M1: запросы только на стенд
(`test.tamir.rs`, `test-api.tamir.rs`, `192.168.1.143`), прод Tamir — запрещён.
"""

from __future__ import annotations

import pytest
import requests

from qa.config import is_allowed_target, require_allowed_target
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
