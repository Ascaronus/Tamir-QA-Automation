"""Юнит-проверки заголовка Medusa в `QaHttpClient` (без сети).

Здесь фиксируется контракт, из-за которого реальный прогон на стенде уже падал:
в CI ключ приходит секретом `MEDUSA_PUBLISHABLE_API_KEY`, поэтому негативная
проверка обязана явно отказываться от заголовка, иначе «запрос без ключа»
тихо уходит с ключом и получает 200.
"""

from __future__ import annotations

import requests

from qa.http import PUBLISHABLE_KEY_ENV, PUBLISHABLE_KEY_HEADER, QaHttpClient


class _Recorder:
    """Заменяет сетевой слой и собирает переданные заголовки."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, method: str, url: str, **kwargs) -> requests.Response:
        self.calls.append({"method": method, "url": url, "headers": kwargs.get("headers") or {}})
        response = requests.Response()
        response.status_code = 200
        response.url = url
        return response

    @property
    def headers(self) -> dict:
        return self.calls[-1]["headers"]


def _client_with_recorder(monkeypatch) -> tuple[QaHttpClient, _Recorder]:
    monkeypatch.setenv(PUBLISHABLE_KEY_ENV, "pk_test_value")
    client = QaHttpClient("https://test-api.tamir.rs")
    recorder = _Recorder()
    monkeypatch.setattr(client.session, "request", recorder)
    return client, recorder


def test_key_header_is_added_by_default(monkeypatch) -> None:
    client, recorder = _client_with_recorder(monkeypatch)
    client.get("/store/products?limit=1")

    assert recorder.headers.get(PUBLISHABLE_KEY_HEADER) == "pk_test_value"


def test_publishable_key_false_sends_no_key_even_with_env(monkeypatch) -> None:
    client, recorder = _client_with_recorder(monkeypatch)
    client.get("/store/products?limit=1", publishable_key=False)

    sent_names = {name.lower() for name in recorder.headers}
    assert PUBLISHABLE_KEY_HEADER not in sent_names


def test_publishable_key_false_strips_explicitly_passed_key(monkeypatch) -> None:
    """Явно переданный заголовок тоже снимается — иначе негативная ветка нечестная."""
    client, recorder = _client_with_recorder(monkeypatch)
    client.get(
        "/store/products?limit=1",
        headers={PUBLISHABLE_KEY_HEADER: "pk_explicit"},
        publishable_key=False,
    )

    sent_names = {name.lower() for name in recorder.headers}
    assert PUBLISHABLE_KEY_HEADER not in sent_names


def test_no_key_header_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv(PUBLISHABLE_KEY_ENV, raising=False)
    client = QaHttpClient("https://test-api.tamir.rs")
    recorder = _Recorder()
    monkeypatch.setattr(client.session, "request", recorder)

    client.get("/store/products?limit=1")

    sent_names = {name.lower() for name in recorder.headers}
    assert PUBLISHABLE_KEY_HEADER not in sent_names
