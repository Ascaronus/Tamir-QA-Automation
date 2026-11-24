"""Повторы в Kiwi TCMS-клиенте: обрыв у Cloudflare не должен ронять seed/sync.

Оффлайн: сеть подменяется фиктивной сессией, реального TCMS нет.
"""

from __future__ import annotations

import pytest
import requests

from qa.tcms import (
    ATTEMPTS_FOR_READS,
    TcmsClient,
    TcmsError,
    TcmsGatewayError,
    TcmsUnavailable,
)

TCMS_URL = "https://tcms.tamir.rs"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Паузы повторов в тестах не нужны — иначе suite тормозит на 7 секунд."""
    monkeypatch.setattr("qa.tcms.RETRY_BACKOFF_SECONDS", (0.0, 0.0))


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"jsonrpc": "2.0", "id": 1, "result": []}
        self.text = text or "ответ"

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Отдаёт заранее заготовленные ответы и считает вызовы."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def post(self, url: str, json: dict | None = None, timeout: int | None = None) -> FakeResponse:
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_with(responses: list[object]) -> tuple[TcmsClient, FakeSession]:
    session = FakeSession(responses)
    client = TcmsClient(TCMS_URL, "qa-bot", "secret", session=session)  # type: ignore[arg-type]
    return client, session


def test_read_is_retried_after_connection_drop() -> None:
    ok = FakeResponse(payload={"jsonrpc": "2.0", "id": 1, "result": [{"id": 7}]})
    client, session = client_with([requests.exceptions.ConnectionError("reset"), ok])

    assert client._request("TestCaseCategory.filter", {}) == [{"id": 7}]
    assert session.calls == 2


def test_read_is_retried_after_gateway_status() -> None:
    ok = FakeResponse(payload={"jsonrpc": "2.0", "id": 1, "result": []})
    client, session = client_with([FakeResponse(status_code=502, text="Bad Gateway"), ok])

    assert client._request("TestRun.filter", {}) == []
    assert session.calls == 2


def test_read_gives_up_after_three_attempts() -> None:
    client, session = client_with([requests.exceptions.ConnectionError("reset")] * ATTEMPTS_FOR_READS)

    with pytest.raises(TcmsUnavailable):
        client._request("TestCase.filter", {})
    assert session.calls == ATTEMPTS_FOR_READS


def test_write_is_never_retried() -> None:
    """`create` после обрыва мог выполниться на сервере — повтор задвоит запись."""
    client, session = client_with([requests.exceptions.ConnectionError("reset")])

    with pytest.raises(TcmsUnavailable):
        client._request("TestCase.create", {"summary": "x"})
    assert session.calls == 1


def test_gateway_error_is_also_unavailable() -> None:
    """Тесты, которые скипываются на `TcmsUnavailable`, скипаются и на 502."""
    client, _ = client_with([FakeResponse(status_code=503, text="Service Unavailable")] * ATTEMPTS_FOR_READS)

    with pytest.raises(TcmsGatewayError):
        client._request("Build.filter", {})
    assert issubclass(TcmsGatewayError, TcmsUnavailable)


def test_business_error_is_not_retried() -> None:
    client, session = client_with(
        [FakeResponse(payload={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no"}})]
    )

    with pytest.raises(TcmsError) as caught:
        client._request("TestCaseRun.filter", {})
    assert caught.value.code == -32601
    assert session.calls == 1
