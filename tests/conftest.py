"""Общие фикстуры прогона: конфигурация и HTTP-клиенты.

Фикстуры намеренно простые: секреты не читаются и не печатаются, ключ
Medusa подставляет сам `QaHttpClient` из переменной окружения.
"""

from __future__ import annotations

import pytest
from dotenv import load_dotenv

from qa.config import QaConfig, load_config
from qa.http import QaHttpClient

# Локальный .env (в .gitignore) удобен для ручных прогонов; в CI переменные
# окружения приходят из GitHub Actions.
load_dotenv()


@pytest.fixture(scope="session")
def config() -> QaConfig:
    """Конфигурация окружения прогона (staging по умолчанию)."""
    return load_config()


@pytest.fixture(scope="session")
def web_client(config: QaConfig) -> QaHttpClient:
    """Клиент витрины: https://test.tamir.rs в M1."""
    client = QaHttpClient(config.web_url)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def api_client(config: QaConfig) -> QaHttpClient:
    """Клиент Medusa API: https://test-api.tamir.rs в M1."""
    client = QaHttpClient(config.api_url)
    try:
        yield client
    finally:
        client.close()
