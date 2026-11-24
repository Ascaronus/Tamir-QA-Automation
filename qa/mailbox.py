"""Читалка почтового ящика стэнда (Mailpit).

Стэндинг никогда не отправляет настоящие письма: SMTP-транспорт направлен в
Mailpit, чей HTTP-API доступен внутри сети стэнда.  Сценариям с зарегистрирован-
ным покупателем нужен шестизначный код регистрации, а сценарию заказа — письмо о
заказе; оба берутся отсюда, а не подделываются.  Секретов здесь нет: API
внутренний и используется только для чтения.

Переменные окружения: ``MAILPIT_URL`` (на раннере, который сидит на самом
стэнде, это ``http://127.0.0.1:8025``), плюс ``MAILPIT_USER`` /
``MAILPIT_PASSWORD``, если включён HTTP Basic.  Без ``MAILPIT_URL`` сценарии,
которым нужно письмо, пропускаются.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

import requests

from qa.config import QaError
from qa.http import DEFAULT_USER_AGENT

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

#: письмо о заказе: витрина пишет «TAMIR - Porudzbina / Order #28»
ORDER_SUBJECT_RE = re.compile(r"porud|order", re.IGNORECASE)


class MailboxUnavailable(QaError):
    """Mailpit не настроен или недоступен."""


def recipients_of(message: dict[str, Any]) -> list[str]:
    """Все адреса получателей письма (Mailpit складывает их в ``To``/``Cc``/``Bcc``)."""
    found: list[str] = []
    for field in ("To", "Cc", "Bcc", "Recipient"):
        for entry in message.get(field) or []:
            if isinstance(entry, dict):
                address = str(entry.get("Address") or "")
            else:
                address = str(entry or "")
            if address:
                found.append(address.lower())
    return found


class Mailbox:
    """Небольшой клиент Mailpit: коды регистрации и письма о заказах."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 15.0) -> None:
        self.base_url = (base_url or os.environ.get("MAILPIT_URL", "")).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = DEFAULT_USER_AGENT
        user = os.environ.get("MAILPIT_USER", "")
        password = os.environ.get("MAILPIT_PASSWORD", "")
        if user:
            self.session.auth = (user, password)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _get(self, path: str) -> tuple[int, object]:
        if not self.configured:
            raise MailboxUnavailable("MAILPIT_URL не задан")
        try:
            response = self.session.get(self.base_url + path, timeout=self.timeout)
        except requests.RequestException as exc:
            raise MailboxUnavailable(f"Mailpit не отвечает на {self.base_url}: {exc}") from exc
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    def messages_for(self, address: str, limit: int = 100) -> list[dict]:
        """Письма на адрес ``address``, свежие первыми."""
        status, payload = self._get(f"/api/v1/messages?limit={limit}")
        if status != 200 or not isinstance(payload, dict):
            raise MailboxUnavailable(f"Mailpit не отдал список писем (HTTP {status})")
        address = address.lower()
        return [m for m in payload.get("messages") or [] if address in recipients_of(m)]

    def body_of(self, message_id: str) -> str:
        """Тело письма: у Mailpit это ``GET /api/v1/message/{ID}`` с полями ``Text``/``HTML``."""
        status, payload = self._get(f"/api/v1/message/{message_id}")
        if status == 200:
            if isinstance(payload, dict):
                text = str(payload.get("Text") or "")
                return text or str(payload.get("HTML") or "")
            return payload if isinstance(payload, str) else str(payload)
        raise MailboxUnavailable(f"Mailpit не отдал тело письма (HTTP {status})")

    def wait_for_code(self, address: str, timeout: float = 120.0, poll: float = 3.0) -> str:
        """Ждёт письмо о регистрации на ``address`` и возвращает код из него."""
        deadline = time.monotonic() + timeout
        seen: set[str] = set()
        while time.monotonic() < deadline:
            for message in self.messages_for(address):
                message_id = str(message.get("ID") or "")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                codes = _CODE_RE.findall(self.body_of(message_id))
                if codes:
                    return codes[0]
            time.sleep(poll)
        raise MailboxUnavailable(
            f"за {int(timeout)}с нет письма о регистрации на {address} "
            f"(просмотрено писем: {len(seen)})"
        )

    def wait_for_order_email(self, address: str, timeout: float = 90.0, poll: float = 3.0) -> Optional[dict]:
        """Ждёт письмо о заказе на ``address``; ``None``, если за время ожидания его нет."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in self.messages_for(address):
                text = f"{message.get('Subject') or ''} {message.get('Snippet') or ''}"
                if ORDER_SUBJECT_RE.search(text):
                    return message
            time.sleep(poll)
        return None
