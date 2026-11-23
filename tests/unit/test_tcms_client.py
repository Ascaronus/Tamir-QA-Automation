"""Офлайновые проверки клиента Kiwi TCMS.

Сеть не используется: вместо `requests.Session` подставляется подделка, которая
возвращает готовые JSON-RPC ответы. Так видно, что клиент правильно формирует
`params` (позиционный список), разбирает `error` и не улетит в прод.
"""

from __future__ import annotations

import pytest

from qa import tcms as tcms_module
from qa.config import load_config
from qa.tcms import TcmsClient, TcmsError, TcmsUnavailable

pytestmark = pytest.mark.unit

FAKE_URL = "https://tcms.tamir.rs/json-rpc/"


class FakeResponse:
    def __init__(self, payload: dict | None, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = "empty" if payload is None else str(payload)

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Подделка `requests.Session`: помнит запросы и отдаёт заготовленный ответ."""

    def __init__(self, payload: dict | None, status_code: int = 200) -> None:
        self.requests: list[dict] = []
        self.payload = payload
        self.status_code = status_code

    def post(self, url: str, json: dict, timeout: int) -> FakeResponse:
        self.requests.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self.payload, self.status_code)


def make_client(payload: dict | None, status_code: int = 200) -> tuple[TcmsClient, FakeSession]:
    client = TcmsClient(FAKE_URL, "qa", "secret")
    session = FakeSession(payload, status_code)
    client._session = session
    return client, session


def test_login_sends_positional_credentials_and_reuses_session():
    client, session = make_client({"jsonrpc": "2.0", "id": 1, "result": "session-key"})

    assert client.login() == "session-key"
    body = session.requests[0]["json"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "Auth.login"
    # Позиционный список: именованные параметры TCMS отклоняет (-32600).
    assert body["params"] == ["qa", "secret"]
    assert body["id"]  # без id TCMS отвечает 204 без тела

    client._logged_in = True
    client.call("KiwiTCMS.version")
    methods = [call["json"]["method"] for call in session.requests]
    assert methods == ["Auth.login", "KiwiTCMS.version"]


def test_params_are_positional_and_query_defaults_to_empty_dict():
    client, session = make_client({"jsonrpc": "2.0", "id": 1, "result": []})
    client._logged_in = True

    client.rows("Product", {"name__exact": "Tamir"})
    client.rows("Version")
    assert session.requests[0]["json"]["params"] == [{"name__exact": "Tamir"}]
    assert session.requests[1]["json"]["params"] == [{}]


def test_update_puts_id_before_values():
    client, session = make_client({"jsonrpc": "2.0", "id": 1, "result": {"id": 7}})
    client._logged_in = True

    client.update("TestExecution", 7, {"status": 1})
    assert session.requests[0]["json"]["params"] == [7, {"status": 1}]


def test_jsonrpc_error_keeps_tcms_error_code():
    client, _ = make_client(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": tcms_module.AUTH_REQUIRED_CODE, "message": 'Authentication failed when calling "Product.filter"'},
        }
    )
    client._logged_in = True

    with pytest.raises(TcmsError) as exc:
        client.call("Product.filter", [{}])

    assert exc.value.code == tcms_module.AUTH_REQUIRED_CODE
    assert "Authentication failed" in str(exc.value)


def test_unknown_method_becomes_russian_error():
    client, _ = make_client(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": tcms_module.METHOD_NOT_FOUND_CODE, "message": 'Method not found: "TestCaseRun.filter"'},
        }
    )
    client._logged_in = True

    with pytest.raises(TcmsError) as exc:
        client.call("TestCaseRun.filter", [{}])

    assert exc.value.code == tcms_module.METHOD_NOT_FOUND_CODE
    assert "TestCaseRun.filter" in str(exc.value)


def test_notification_answer_without_body_becomes_error():
    client, _ = make_client(None, status_code=204)
    client._logged_in = True

    with pytest.raises(TcmsError) as exc:
        client.call("TestRun.filter", [{}])

    assert "204" in str(exc.value)


def test_http_error_becomes_tcms_error():
    client, _ = make_client({"detail": "boom"}, status_code=500)
    client._logged_in = True

    with pytest.raises(TcmsError) as exc:
        client.call("TestRun.filter", [{}])

    assert "500" in str(exc.value)


def test_find_returns_first_hit_or_none():
    client, _ = make_client({"jsonrpc": "2.0", "id": 1, "result": [{"id": 5, "name": "Tamir"}]})
    client._logged_in = True

    assert client.find("Product", name__exact="Tamir")["id"] == 5

    client._session = FakeSession({"jsonrpc": "2.0", "id": 2, "result": []})
    assert client.find("Product", name__exact="Нет такого") is None


def test_status_id_requires_known_name():
    client, _ = make_client({"jsonrpc": "2.0", "id": 1, "result": [{"id": 4, "name": "PASSED"}]})
    client._logged_in = True

    assert client.status_id("PASSED") == 4

    client._session = FakeSession({"jsonrpc": "2.0", "id": 2, "result": []})
    with pytest.raises(TcmsError):
        client.status_id("SOMETHING_ELSE")


def test_missing_credentials_means_skip():
    client = TcmsClient(FAKE_URL, "", "")
    with pytest.raises(TcmsError):
        client.login()


def test_from_environment_skips_without_credentials(monkeypatch):
    monkeypatch.delenv(tcms_module.TCMS_USER_ENV, raising=False)
    monkeypatch.delenv(tcms_module.TCMS_PASSWORD_ENV, raising=False)

    with pytest.raises(TcmsUnavailable):
        TcmsClient.from_environment(load_config())


def test_rejects_non_staging_tcms():
    with pytest.raises(RuntimeError) as exc:
        TcmsClient("https://tamir.rs/json-rpc/", "qa", "secret")

    assert "production" in str(exc.value).lower() or "белый список" in str(exc.value)


def test_rpc_url_points_to_json_rpc():
    """TCMS 17.2 принимает `/json-rpc/` и `/xml-rpc/`, старый `/xml/` — 400."""
    assert load_config().rpc_url.endswith("/json-rpc/")
