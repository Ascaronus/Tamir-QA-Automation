"""Обход сломанного системного DNS на рабочей машине.

Такое нужно только локально: на Windows-машине служба `Dnscache` иногда
перестаёт отвечать (`Resolve-DnsName` → «DNS-сервер не может обработать
запрос»), и тогда не резолвятся ни `requests`, ни Chromium
(`net::ERR_NAME_NOT_RESOLVED`), хотя `nslookup <server>` выдаёт адрес.
В CI переменная не задана, и модуль не делает ничего.

Значение задают в `.env`:

    QA_HOST_OVERRIDES=test.tamir.rs=172.67.143.121,test-api.tamir.rs=172.67.143.121

Один и тот же список покрывает обе стороны прогона: Python-клиентам он
подменяет `socket.getaddrinfo`, браузеру превращается в флаг Chromium
`--host-resolver-rules`. SNI и заголовок `Host` при этом остаются
настоящими, поэтому Cloudflare/Traefik маршрутизируют запрос как обычно.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping

ENV_VAR = "QA_HOST_OVERRIDES"


def parse_overrides(raw: str | None) -> dict[str, str]:
    """`host=ip,host=ip` → `{host: ip}`; пустое значение → `{}`."""
    overrides: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        host, sep, ip = chunk.strip().partition("=")
        if not sep or not host or not ip:
            continue
        overrides[host.lower()] = ip.strip()
    return overrides


def chromium_args(overrides: Mapping[str, str]) -> list[str]:
    """Флаг Chromium, повторяющий те же соответствия хостов внутри браузера."""
    if not overrides:
        return []
    rules = ", ".join(f"MAP {host} {ip}" for host, ip in overrides.items())
    return [f"--host-resolver-rules={rules}"]


def install_getaddrinfo_patch(overrides: Mapping[str, str]) -> None:
    """Закрепляет перечисленные хосты за адресами вместо системного резолвера.

    Идемпотентно: повторный вызов не оборачивает `getaddrinfo` дважды.
    """
    if not overrides or getattr(socket.getaddrinfo, "_qa_overrides", False):
        return

    original = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        key = str(host).lower() if host else host
        return original(overrides.get(key, host), port, *args, **kwargs)

    getaddrinfo._qa_overrides = True  # type: ignore[attr-defined]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
