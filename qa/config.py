"""Конфигурация окружения для QA-прогонов.

Все значения берутся из переменных окружения, значения по умолчанию — только
публичные staging-хосты. Секретов здесь нет и быть не должно: репозиторий
публичный, ключи приходят только как переменные окружения CI.

`is_allowed_target` — единственная точка правды про «куда можно слать запросы»:
её вызывают и фикстуры, и `qa.http.QaHttpClient` (deny-list защита от продa).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from qa.net import ENV_VAR, parse_overrides
from urllib.parse import urlsplit

# Разрешённые хосты (M1): только стенд и локальная сеть стека на staging-хосте.
# `qa.tamir.rs` и `tcms.tamir.rs` — это сервисы самого QA-стека на staging
# (отчёты Allure и Kiwi TCMS), они нужны для интеграции в M2; прод-хосты Tamir
# по-прежнему запрещены.
ALLOWED_HOSTS: tuple[str, ...] = (
    "test.tamir.rs",
    "test-api.tamir.rs",
    "qa.tamir.rs",
    "tcms.tamir.rs",
    "192.168.1.143",
)

# Продовые хосты Tamir. Список не «запретительный» в смысле логики: запрещает
# всё, что не входит в ALLOWED_HOSTS, но нужен для понятной ошибки.
PRODUCTION_HOSTS: tuple[str, ...] = (
    "tamir.rs",
    "www.tamir.rs",
    "api.tamir.rs",
)

_PRODUCTION_FORBIDDEN = (
    "Запрещено: это production-хост Tamir. QA-прогоны M1 работают только со "
    "стендом ({allowed}); прод трогать нельзя."
)


def _host_of(url: str) -> str | None:
    """Возвращает lowercased hostname без порта либо None, если URL непонятный."""
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _is_production_host(host: str) -> bool:
    if host in ALLOWED_HOSTS:
        return False
    if host in PRODUCTION_HOSTS:
        return True
    return host == "tamir.rs" or host.endswith(".tamir.rs")


def _is_allowed_host(host: str) -> bool:
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


# Публичный origin, который приложение подставляет в canonical/hreflang и sitemap.
# Продуктовое SEO считает публичным только `tamir.rs` (см. `PUBLIC_SITE_ORIGIN`
# в `frontend/src/lib/seo.ts`), поэтому на стенде canonical и alternates всегда
# ведут на прод-домен, хотя сами запросы идут на `test.tamir.rs`. Проверять это
# надо по таблице, а не по хосту запроса. None (хоста нет в таблице) означает
# «canonical ожидается на том же origin, откуда отвечает сервер».
STAGING_SITE_ORIGINS: dict[str, str] = {
    "test.tamir.rs": "https://tamir.rs",
}


def site_origin_for(web_url: str) -> str | None:
    """Публичный origin для SEO-атрибутов данного web-адреса, либо None.

    None значит «атрибуты должны совпадать с самим `web_url`» — так ведут себя
    локальные прогоны против `localhost`.
    """
    host = _host_of(web_url)
    if not host:
        return None
    return STAGING_SITE_ORIGINS.get(host)


def is_allowed_target(url: str) -> bool:
    """True, если URL ведёт на разрешённый M1-хост.

    - разрешённые хосты (см. ALLOWED_HOSTS, включая поддомены) -> True;
    - любой production-хост Tamir (`tamir.rs`, `www.tamir.rs`, `api.tamir.rs`,
      прочие `*.tamir.rs`) -> `RuntimeError` с русским сообщением;
    - прочие чужие хосты -> False.

    Проверка идёт по метке домена, а не «подстрокой»: `evil-test.tamir.rs`
    разрешён не будет — это другой хост домена Tamir, он трактуется как
    production-подобный и даёт тот же `RuntimeError`.
    """
    host = _host_of(url)
    if not host:
        return False

    if _is_allowed_host(host):
        return True

    if _is_production_host(host):
        raise RuntimeError(
            _PRODUCTION_FORBIDDEN.format(allowed=", ".join(ALLOWED_HOSTS))
        )
    return False


class QaError(RuntimeError):
    """Любая наша ошибка: неверная цель запроса, недоступный сервис, сбой API."""


def require_allowed_target(url: str) -> str:
    """Как `is_allowed_target`, но любое неразрешённое направление — исключение."""
    host = _host_of(url)
    if host is None:
        raise QaError(f"Не удалось разобрать хост из URL: {url!r}")
    if not is_allowed_target(url):
        raise QaError(
            f"Хост {host!r} не входит в белый список QA-целей "
            f"({', '.join(ALLOWED_HOSTS)})."
        )
    return url


@dataclass(frozen=True)
class QaConfig:
    """Настройки прогона (из окружения, с безопасными staging-значениями)."""

    env: str
    web_url: str
    api_url: str
    tcms_url: str = "https://tcms.tamir.rs"
    reports_url: str = "https://qa.tamir.rs"
    #: имя сборки в Kiwi TCMS — одно и то же значение используют seed и sync
    build_name: str = "staging"
    #: закрепление хостов за адресами (обход сломанного системного DNS, см. qa/net.py)
    host_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def is_staging(self) -> bool:
        return self.env == "staging"

    @property
    def site_origin(self) -> str:
        """Origin, который должен стоять в canonical/hreflang этого web-адреса.

        На стенде это публичный `https://tamir.rs`, а не `test.tamir.rs`:
        SEO-атрибуты продуктовый код считает по публичному домену.
        """
        return site_origin_for(self.web_url) or self.web_url

    @property
    def expects_noindex(self) -> bool:
        """True, когда окружение должно быть закрыто от поисковых систем.

        Любой non-prod (в том числе staging по умолчанию) обязан отдавать
        `noindex`, иначе тестовый индекс начнёт каннибалить боевой.
        """
        return not os.getenv("QA_IS_PRODUCTION", "").strip() in ("1", "true", "yes")

    @property
    def rpc_url(self) -> str:
        """Точка RPC Kiwi TCMS: JSON-RPC 2.0 на `/json-rpc/`.

        Проверено на нашей сборке 17.2: `/json-rpc/` и `/xml-rpc/` работают,
        старый путь `/xml/` отвечает 400. JSON-RPC выбран как основной: он
        описан в документации самой инстанс (`/static/api/`), а ответы читаются
        без XML-декодирования.
        """
        return f"{self.tcms_url}/json-rpc/"


def _load_dotenv_once() -> None:
    """Подтягивает `.env` рядом с репозиторием (в CI переменные задаёт раннер)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - пакет в requirements, но не критично
        return
    load_dotenv(override=False)


def load_config() -> QaConfig:
    """Собирает конфигурацию из переменных окружения с staging-дефолтами."""
    _load_dotenv_once()
    config = QaConfig(
        env=os.getenv("QA_ENV", "staging").strip() or "staging",
        web_url=os.getenv("WEB_URL", "https://test.tamir.rs").rstrip("/"),
        api_url=os.getenv("API_URL", "https://test-api.tamir.rs").rstrip("/"),
        tcms_url=os.getenv("TCMS_URL", "https://tcms.tamir.rs").rstrip("/"),
        reports_url=os.getenv("REPORTS_URL", "https://qa.tamir.rs").rstrip("/"),
        build_name=os.getenv("QA_BUILD_NAME", "staging").strip() or "staging",
        host_overrides=parse_overrides(os.getenv(ENV_VAR)),
    )
    # Конфигурация не должна случайно указать на проду.
    require_allowed_target(config.web_url)
    require_allowed_target(config.api_url)
    require_allowed_target(config.tcms_url)
    require_allowed_target(config.reports_url)
    return config
