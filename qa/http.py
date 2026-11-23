"""Тонкая обёртка над `requests.Session` для QA-запросов.

Никакой бизнес-логики: базовый URL, таймаут, проверка цели запроса и
опциональная подстановка publishable-ключа из переменной окружения.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from qa.config import require_allowed_target

# Имя переменной окружения и заголовок Medusa. Значение нигде не логируется
# и не имеет значения по умолчанию: без переменной запрос уходит без заголовка
# (это нужно для негативной проверки контракта).
PUBLISHABLE_KEY_ENV = "MEDUSA_PUBLISHABLE_API_KEY"
PUBLISHABLE_KEY_HEADER = "x-publishable-api-key"

DEFAULT_TIMEOUT = 15.0


class QaHttpClient:
    """HTTP-клиент с guard'ом по хостам и опциональным заголовком Medusa."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        # Проверка цели выполняется и здесь, и перед каждым запросом.
        require_allowed_target(base_url)
        self.base_url: str = base_url.rstrip("/")
        self.timeout: float = timeout
        self.session: requests.Session = requests.Session()
        self.session.verify = True
        # Ретраев по умолчанию нет (transport adapter не меняем): smoke-проверки
        # должны падать честно, а не «самоизлечиваться».

    def build_url(self, path: str) -> str:
        """Склеивает base_url и path (path может быть с ведущим слэшем)."""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _publishable_headers(self) -> dict[str, str]:
        value = os.getenv(PUBLISHABLE_KEY_ENV)
        if value is None or not value.strip():
            return {}
        return {PUBLISHABLE_KEY_HEADER: value.strip()}

    def _guard_redirect(self, response: requests.Response, *_args: Any, **_kwargs: Any) -> requests.Response:
        """Проверяет итоговый адрес после редиректов: стенд не должен «увести» на проду."""
        require_allowed_target(response.url)
        return response

    def request(
        self,
        method: str,
        path: str,
        *,
        publishable_key: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        url = self.build_url(path)
        # Guard перед каждым запросом: случайный редирект/опечатка не уведёт на прод.
        require_allowed_target(url)

        headers = dict(kwargs.pop("headers", None) or {})
        if publishable_key:
            for name, value in self._publishable_headers().items():
                headers.setdefault(name, value)
        else:
            # Негативная ветка контракта: заголовка не должно быть, даже если
            # ключ задан в окружении (в CI он приходит как секрет).
            headers = {k: v for k, v in headers.items() if k.lower() != PUBLISHABLE_KEY_HEADER}

        kwargs.setdefault("timeout", self.timeout)
        hooks = dict(kwargs.pop("hooks", None) or {})
        on_response = hooks.get("response")
        if on_response is None:
            hooks["response"] = self._guard_redirect
        else:
            hooks["response"] = [on_response, self._guard_redirect]
        return self.session.request(method, url, headers=headers, hooks=hooks, **kwargs)

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "QaHttpClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
