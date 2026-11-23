"""Интеграция с Kiwi TCMS: проверяем, что API стенда отвечает нашему боту.

Тесты помечены `tcms` и пока не входят в CI-набор — включим в M2, когда будут
секреты `TCMS_URL`/`TCMS_USER`/`TCMS_PASSWORD` и политика Cloudflare Access для
`tcms.tamir.rs`. Без секретов тесты пропускаются, поэтому обычный прогон от них
не зависит.

Смысл проверки: подтвердить, что тестовый инстанс Kiwi TCMS действительно
доступен для автоматизации, что вход бота проходит и что каталог (`Product`
`Tamir`, справочники) заведён — иначе результаты прогонов некуда складывать.

Содержимое каталога создаёт `scripts/tcms_seed.py` (идемпотентно).
"""

from __future__ import annotations

import pytest

from qa.config import load_config
from qa.tcms import TcmsClient, TcmsUnavailable

pytestmark = pytest.mark.tcms


def client_or_skip() -> TcmsClient:
    config = load_config()
    try:
        return TcmsClient.from_environment(config)
    except TcmsUnavailable as exc:
        pytest.skip(str(exc))


def test_tcms_is_reachable_and_logged_in():
    """RPC API TCMS отвечает, вход бота проходит, версия — линейки 17."""
    summary = client_or_skip().ping()

    assert str(summary["version"]).startswith("17"), f"неожиданная версия TCMS: {summary['version']}"


def test_tcms_is_the_staging_instance():
    """Мы подключаемся к QA-инстансу на стенде, а не к чему-то ещё."""
    client = client_or_skip()

    assert "tcms.tamir.rs" in client.url
    assert client.url.endswith("/json-rpc/")


def test_tamir_product_is_registered():
    """Продукт `Tamir` заведён — к нему привязаны кейсы и прогоны."""
    product = client_or_skip().find("Product", name__exact="Tamir")

    assert product is not None, "продукт Tamir не найден — нужен scripts/tcms_seed.py"


def test_case_catalog_is_seeded():
    """В продукте есть автоматизированные кейсы, а план и запуск — на стенде."""
    client = client_or_skip()
    cases = client.rows("TestCase", {"is_automated": True})

    assert cases, "нет ни одного автоматизированного кейса — нужен scripts/tcms_seed.py"

    plan = client.find("TestPlan", name__contains="Staging")
    assert plan is not None, "нет тест-плана для смока стенда"

    runs = client.rows("TestRun", {"summary__contains": "Staging Smoke"})
    assert runs, "нет ни одного тест-рана для смока стенда"


def test_execution_status_reference_is_usable():
    """Справочник статусов исполнений читается — на нём строятся результаты."""
    client = client_or_skip()
    names = {status["name"] for status in client.rows("TestExecutionStatus")}

    assert {"IDLE", "PASSED", "FAILED"} <= names, f"неожиданный справочник статусов: {names}"
    # ID статусов в TCMS 17 не фиксированы, поэтому берёмся только через фильтр.
    assert client.status_id("PASSED") > 0
