"""Юнит-проверки обхода сломанного системного DNS (``qa/net.py``), без сети.

``QA_HOST_OVERRIDES`` закрепляет хосты за адресами для обеих сторон прогона:
Python-клиентам подменяется ``socket.getaddrinfo`` (фикстура ``_host_overrides``
в ``tests/conftest.py``), браузеру список превращается в флаг Chromium
``--host-resolver-rules`` (фикстура ``browser_type_launch_args``).  Здесь
проверяются только разбор строки, формирование флага и обёртка резолвера:
вместо настоящего ``getaddrinfo`` подставлен фиктивный, поэтому ни один вызов
не уходит в сеть и в системный резолвер (тот на рабочей машине и сломан).
"""

from __future__ import annotations

import socket

import pytest

from qa import net
from qa.config import QaConfig, load_config
from qa.net import ENV_VAR, chromium_args, install_getaddrinfo_patch, parse_overrides

pytestmark = pytest.mark.unit

# Пример из `.env` рабочей машины: хосты стенда закреплены за адресом,
# который отдаёт `nslookup`, когда системный резолвер молчит.
PRIMER = "test.tamir.rs=172.67.143.121,test-api.tamir.rs=172.67.143.121"
OVERRIDES = {
    "test.tamir.rs": "172.67.143.121",
    "test-api.tamir.rs": "172.67.143.121",
}


# ------------------------------------------------------------------- разбор


def test_parse_overrides_empty_value_yields_empty_dict() -> None:
    """Ни переменной, ни пустой строки, ни одних запятых — это «патча нет»."""
    for syroe_znachenie in (None, "", ","):
        assert parse_overrides(syroe_znachenie) == {}


def test_parse_overrides_single_pair() -> None:
    """Одна пара `host=ip` разбирается целиком."""
    assert parse_overrides("test.tamir.rs=172.67.143.121") == {
        "test.tamir.rs": "172.67.143.121"
    }


def test_parse_overrides_several_pairs() -> None:
    """Значение из `.env`: несколько пар через запятую."""
    assert parse_overrides(PRIMER) == OVERRIDES


def test_parse_overrides_normalizes_host_case() -> None:
    """Хост приводится к нижнему регистру, а значение остаётся как написано."""
    assert parse_overrides("Test.Tamir.RS=172.67.143.121") == {
        "test.tamir.rs": "172.67.143.121"
    }
    # IP — не хост: его регистр не нормализуем (для хостовых значений MAP
    # Chromium решает сам, нам важно не портить строку).
    assert parse_overrides("Test.Tamir.RS=Upstream.Host") == {
        "test.tamir.rs": "Upstream.Host"
    }


def test_parse_overrides_trims_whitespace() -> None:
    """Пробелы по краям фрагмента и вокруг адреса после `=` обрезаются."""
    assert parse_overrides(
        "  test.tamir.rs=172.67.143.121 , test-api.tamir.rs= 172.67.143.122 "
    ) == {
        "test.tamir.rs": "172.67.143.121",
        "test-api.tamir.rs": "172.67.143.122",
    }


def test_parse_overrides_skips_garbage_chunks() -> None:
    """Фрагменты без пары `host=ip` молча пропускаются, остальное разбирается."""
    assert parse_overrides("junk, test.tamir.rs=1.2.3.4, obryv=") == {
        "test.tamir.rs": "1.2.3.4"
    }
    for odpad in ("=1.2.3.4", "=", ",", "  ", "без знака"):
        assert parse_overrides(odpad) == {}


def test_parse_overrides_duplicate_host_last_wins() -> None:
    """Повторный хост — последнее значение (dict перезаписывает)."""
    assert parse_overrides("a.test=1.1.1.1,a.test=2.2.2.2") == {"a.test": "2.2.2.2"}


def test_parse_overrides_manual_mixed_example() -> None:
    """Тот же пример, что сверяли руками при отладке обхода DNS."""
    assert parse_overrides("Test.Tamir.rs=1.2.3.4, junk, test-api.tamir.rs=5.6.7.8") == {
        "test.tamir.rs": "1.2.3.4",
        "test-api.tamir.rs": "5.6.7.8",
    }


# --------------------------------------------------------------- флаг браузера


def test_chromium_args_empty_overrides_give_no_flags() -> None:
    """Пустой словарь — пустой список: в CI браузер стартует без изменений."""
    assert chromium_args({}) == []


def test_chromium_args_single_host_is_one_flag() -> None:
    """Один хост — ровно один аргумент ожидаемого вида (сверено руками)."""
    assert chromium_args({"test.tamir.rs": "172.67.143.121"}) == [
        "--host-resolver-rules=MAP test.tamir.rs 172.67.143.121"
    ]


def test_chromium_args_several_hosts_share_one_flag() -> None:
    """Несколько хостов — ОДИН флаг, правила через запятую (реализация net.py)."""
    args = chromium_args(OVERRIDES)
    assert args == [
        "--host-resolver-rules=MAP test.tamir.rs 172.67.143.121, "
        "MAP test-api.tamir.rs 172.67.143.121"
    ]


def test_chromium_args_follow_dict_order() -> None:
    """Порядок правил — порядок словаря (dict в Python сохраняет вставку)."""
    perevertynutyi = dict(reversed(list(OVERRIDES.items())))
    args = chromium_args(perevertynutyi)
    assert len(args) == 1
    flag = args[0].removeprefix("--host-resolver-rules=")
    assert flag.index("test-api.tamir.rs") < flag.index("test.tamir.rs ")


def test_parse_then_chromium_args_end_to_end() -> None:
    """Строка из `.env` целиком превращается в флаг браузера."""
    assert chromium_args(parse_overrides(PRIMER)) == [
        "--host-resolver-rules=MAP test.tamir.rs 172.67.143.121, "
        "MAP test-api.tamir.rs 172.67.143.121"
    ]


# ------------------------------------------------------------- обёртка резолвера


class FikativnyyRezolver:
    """Подставка ``socket.getaddrinfo``: помнит запросы, в сеть не ходит."""

    def __init__(self) -> None:
        self.zaprosy: list[tuple[object, int]] = []

    def __call__(self, host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.zaprosy.append((host, port))
        return [("AF", "SOCK", 6, "TCP", (f"otvet::{host}", port))]


@pytest.fixture
def rezolver(monkeypatch):
    """На время теста ``socket.getaddrinfo`` — фиктивный, после — как было.

    Автофикстура ``_host_overrides`` из ``tests/conftest.py`` может оставить
    в процессе патч, установленный из ``.env`` (на локальной машине он задан),
    поэтому исходное состояние не «оригинал», а то, что было: monkeypatch
    вернёт ровно его и другие тесты не отравит.
    """
    beza = FikativnyyRezolver()
    monkeypatch.setattr(socket, "getaddrinfo", beza)
    return beza


def test_install_getaddrinfo_patch_empty_does_nothing(rezolver) -> None:
    """Пустые overrides — обёртка не надевается: `getaddrinfo` не меняется."""
    bylo = socket.getaddrinfo
    install_getaddrinfo_patch({})
    assert socket.getaddrinfo is bylo
    assert not getattr(socket.getaddrinfo, "_qa_overrides", False)


def test_install_getaddrinfo_patch_substitutes_address(rezolver) -> None:
    """Закреплённый хост уходит в резолвер уже адресом — DNS не запрашивается."""
    install_getaddrinfo_patch({"test.tamir.rs": "172.67.143.121"})
    otvet = socket.getaddrinfo("test.tamir.rs", 443)
    assert rezolver.zaprosy == [("172.67.143.121", 443)]
    assert otvet[-1][4] == ("otvet::172.67.143.121", 443)


def test_getaddrinfo_patch_matches_host_case_insensitively(rezolver) -> None:
    """Регистр запрошенного хоста не важен: закрепление сработает."""
    install_getaddrinfo_patch({"test.tamir.rs": "172.67.143.121"})
    socket.getaddrinfo("Test.Tamir.RS", 80)
    assert rezolver.zaprosy == [("172.67.143.121", 80)]


def test_getaddrinfo_patch_passes_other_hosts_through(rezolver) -> None:
    """Чужие хосты (и `None`) идут исходному резолверу без изменений."""
    install_getaddrinfo_patch({"test.tamir.rs": "172.67.143.121"})
    socket.getaddrinfo("drugoy.test", 80)
    socket.getaddrinfo(None, 8080)
    assert rezolver.zaprosy == [("drugoy.test", 80), (None, 8080)]


def test_install_getaddrinfo_patch_is_idempotent(rezolver) -> None:
    """Повторный вызов не оборачивает резолвер дважды: первое закрепление в силе."""
    install_getaddrinfo_patch({"test.tamir.rs": "172.67.143.121"})
    obertka = socket.getaddrinfo
    assert getattr(obertka, "_qa_overrides", False) is True

    install_getaddrinfo_patch({"test-api.tamir.rs": "5.6.7.8"})
    assert socket.getaddrinfo is obertka  # второй раз не навернули

    # второй словарь не подставляется, первый продолжает работать
    socket.getaddrinfo("test-api.tamir.rs", 443)
    socket.getaddrinfo("test.tamir.rs", 443)
    assert rezolver.zaprosy == [("test-api.tamir.rs", 443), ("172.67.143.121", 443)]


def test_conftest_style_double_install_keeps_one_wrapper(rezolver) -> None:
    """Фикстура может вызываться повторно (сессия/переиспользование) — без наложений."""
    for _ in range(3):
        net.install_getaddrinfo_patch(OVERRIDES)
    assert getattr(socket.getaddrinfo, "_qa_overrides", False) is True
    socket.getaddrinfo("test.tamir.rs", 443)
    assert rezolver.zaprosy == [("172.67.143.121", 443)]


# ------------------------------------------------------------------ конфиг


def test_qa_config_host_overrides_empty_by_default() -> None:
    """Без переменной окружения поле пустое: ``default_factory=dict``."""
    config = QaConfig(
        env="staging",
        web_url="https://test.tamir.rs",
        api_url="https://test-api.tamir.rs",
    )
    assert config.host_overrides == {}


def test_qa_config_host_overrides_instances_are_independent() -> None:
    """Значение по умолчанию не общее на все конфиги (не класс-атрибут)."""
    pervy = QaConfig(env="staging", web_url="https://test.tamir.rs", api_url="https://test-api.tamir.rs")
    vtoroy = QaConfig(env="staging", web_url="https://test.tamir.rs", api_url="https://test-api.tamir.rs")
    pervy.host_overrides["a.test"] = "1.2.3.4"
    assert vtoroy.host_overrides == {}


def test_load_config_reads_host_overrides(monkeypatch) -> None:
    """``load_config`` берёт ``QA_HOST_OVERRIDES`` из окружения.

    ``monkeypatch.setenv`` ставит переменную ДО того, как ``load_config``
    подтянет ``.env`` (``load_dotenv(override=False)``), поэтому значение из
    теста важнее значения из файла — сам ``.env`` не трогаем.
    """
    monkeypatch.setenv(ENV_VAR, PRIMER)
    assert load_config().host_overrides == OVERRIDES


def test_load_config_empty_env_var_gives_empty_overrides(monkeypatch) -> None:
    """Пустая переменная — пустые закрепления, даже если `.env` что-то содержит."""
    monkeypatch.setenv(ENV_VAR, "")
    assert load_config().host_overrides == {}
