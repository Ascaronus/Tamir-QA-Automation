"""Клиент Kiwi TCMS поверх JSON-RPC 2.0.

Kiwi TCMS 17 отдаёт RPC на `POST /json-rpc/` (есть и `/xml-rpc/`, старый путь
`/xml/` не работает). Выбран JSON-RPC: этот транспорт описан в документации
самого инстанса (`/static/api/llms.txt`, `/static/api/openapi.json`), а ответы
читаются без XML-декодирования.

Две особенности TCMS 17.2, которые нельзя забыть:

- авторизация — только вызовом `Auth.login`, после него Django держит сессионную
  cookie. Basic auth инстанс не принимает, а на неавторизованный вызов отвечает
  ошибкой `-32098 Authentication failed`;
- `params` в JSON-RPC передаются **позиционным списком**: `Product.filter`
  принимает `[{"name__exact": "Tamir"}]`, а не `{"query": {...}}` — именованные
  параметры инстанс отклоняет с `-32602 Invalid parameters`.

Учётка бота берётся из секретов `TCMS_USER` / `TCMS_PASSWORD` (в CI) или из
`.env` (локально); значения в репозиторий не попадают.

Модуль умеет и читать (проверка, что интеграция жива, и данные для сверки), и
писать результаты (`TestExecution.create` / `TestExecution.update`) — в нашей
сборке 17.2 другого пути нет: `TestCaseRun.*` и `TestRun.store_*` отвечают
`-32601 Method not found`.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from qa.config import QaConfig, require_allowed_target

TCMS_USER_ENV = "TCMS_USER"
TCMS_PASSWORD_ENV = "TCMS_PASSWORD"

#: Таймаут одного RPC-вызова, сек.
TIMEOUT_SECONDS = 30

#: Коды ошибок Kiwi TCMS, которые полезны в тестах и диагностике.
AUTH_REQUIRED_CODE = -32098
METHOD_NOT_FOUND_CODE = -32601

#: Что дёргает `ping()`: имена методов проверены на живом инстансе 17.2.
COUNT_MODELS: tuple[str, ...] = ("Product", "Version", "Category", "TestCase", "TestRun", "Build")


class TcmsUnavailable(AssertionError):
    """Нет учётки или TCMS недоступен — тест скипается, а не падает."""


class TcmsError(RuntimeError):
    """Ошибка RPC: транспорт, HTTP или `error` в ответе JSON-RPC."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def read_credentials() -> tuple[str, str] | None:
    """Читает `TCMS_USER`/`TCMS_PASSWORD`; None, если их нет (скип тестов)."""
    user = os.getenv(TCMS_USER_ENV, "").strip()
    password = os.getenv(TCMS_PASSWORD_ENV, "").strip()
    if not user or not password:
        return None
    return user, password


class TcmsClient:
    """Одна сессия к `POST /json-rpc/` с логином через `Auth.login`."""

    def __init__(self, url: str, user: str, password: str) -> None:
        self.url = require_allowed_target(url)
        self.user = user
        self.password = password
        self._session = requests.Session()
        self._request_id = 0
        self._logged_in = False

    @classmethod
    def from_config(cls, config: QaConfig, user: str, password: str) -> "TcmsClient":
        return cls(config.rpc_url, user, password)

    @classmethod
    def from_environment(cls, config: QaConfig) -> "TcmsClient":
        credentials = read_credentials()
        if credentials is None:
            raise TcmsUnavailable(
                "нет TCMS_USER/TCMS_PASSWORD — интеграция с Kiwi TCMS недоступна"
            )
        user, password = credentials
        return cls.from_config(config, user, password)

    # ------------------------------------------------------------------ RPC

    def call(self, method: str, *params: Any) -> Any:
        """Позиционный вызов метода RPC (`Product.filter`, `{}` — пустой query)."""
        if not self._logged_in:
            self.login()
        return self._request(method, *params)

    def login(self) -> str:
        """`Auth.login` — токен в ответе не нужен, важна сессионная cookie."""
        if not self.user or not self.password:
            raise TcmsError("не заданы TCMS_USER/TCMS_PASSWORD для Kiwi TCMS")
        token = self._request("Auth.login", self.user, self.password)
        self._logged_in = True
        return token if isinstance(token, str) else ""

    def _request(self, method: str, *params: Any) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            # `id` обязателен: без него TCMS считает запрос уведомлением и
            # отвечает 204 с пустым телом.
            "params": list(params),
        }
        try:
            response = self._session.post(self.url, json=payload, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise TcmsUnavailable(f"Kiwi TCMS {self.url} недоступен: {exc}") from exc

        if response.status_code == 204:
            raise TcmsError(f"Kiwi TCMS ответил 204 без тела на вызов {method!r}")
        if response.status_code >= 400:
            raise TcmsError(
                f"HTTP {response.status_code} при вызове {method!r}: "
                f"{response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise TcmsError(
                f"Kiwi TCMS вернул не JSON на вызов {method!r}: {response.text[:300]}"
            ) from exc

        error = data.get("error")
        if error:
            code = error.get("code")
            message = error.get("message") or "без описания"
            raise TcmsError(f"Kiwi TCMS {method!r}: {message}", code=code)
        return data.get("result")

    # ------------------------------------------------------- удобные обёртки

    def rows(self, model: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """`<Model>.filter` списком; query обязателен, поэтому `{}` по умолчанию."""
        return self.call(f"{model}.filter", query or {}) or []

    def find(self, model: str, **query: Any) -> dict[str, Any] | None:
        """Первая запись по фильтру или None — так ищем сущности идемпотентно."""
        hits = self.rows(model, query)
        return hits[0] if hits else None

    def create(self, model: str, values: dict[str, Any]) -> dict[str, Any]:
        """`<Model>.create` — TCMS возвращает созданный объект целиком."""
        return self.call(f"{model}.create", values)

    def update(self, model: str, pk: int, values: dict[str, Any]) -> dict[str, Any]:
        """`<Model>.update(id, values)` — позиции: сначала id, потом поля."""
        return self.call(f"{model}.update", pk, values)

    def status_id(self, name: str) -> int:
        """PK статуса исполнения по имени — в TCMS 17 ID статусов не фиксированы."""
        row = self.find("TestExecutionStatus", name__exact=name)
        if row is None:
            raise TcmsError(f"Kiwi TCMS не знает статус исполнения {name!r}")
        return int(row["id"])

    def ping(self) -> dict[str, Any]:
        """Живость и содержательные счётчики (только то, что реально есть в 17.2)."""
        info: dict[str, Any] = {"version": self.call("KiwiTCMS.version")}
        for model in COUNT_MODELS:
            info[f"{model.lower()}_count"] = len(self.rows(model))
        return info
