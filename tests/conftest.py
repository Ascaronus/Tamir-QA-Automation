"""Shared fixtures for the QA suite: config, HTTP clients, browser, test data."""
from __future__ import annotations

import os
import time
import uuid

import pytest

from qa.config import QaConfig, load_config
from qa.http import QaHttpClient
from qa.steps import dump as dump_steps, has_recorded
from qa.steps import tracked_step


@pytest.fixture(scope="session")
def config() -> QaConfig:
    return load_config()


# --------------------------------------------------------------------------- шаги кейса


@pytest.fixture
def steps(request):
    """Шаги тест-кейса в одном месте: они идут и в Allure, и в каталог TCMS.

    Использование::

        with steps("Добавить товар в корзину", "В корзине одна позиция"):
            ...

    Реализацию см. в ``qa/steps.py``; seeder читает ``.tmp/steps.json``.
    """

    def factory(action: str, expected: str = ""):
        return tracked_step(request.node.nodeid, action, expected)

    return factory


def pytest_sessionfinish(session, exitstatus) -> None:
    """Отдаёт записанные шаги seeder'у TCMS (он работает отдельным процессом).

    Только если этот прогон и правда что-то записал: ``scripts/tcms_seed.py``
    собирает каталог через ``pytest --collect-only``, и такой сбор иначе стёр бы
    ``.tmp/steps.json`` до пустого файла прямо перед тем, как seeder его прочитает.
    """
    if has_recorded():
        dump_steps()


@pytest.fixture(scope="session")
def web_client(config: QaConfig) -> QaHttpClient:
    return QaHttpClient(config.web_url)


@pytest.fixture(scope="session")
def api_client(config: QaConfig) -> "MedusaStoreClient":
    """Витринный API магазина: им пользуются и контракты, и сценарии заказа."""
    from qa.medusa_store import MedusaStoreClient

    return MedusaStoreClient(config.api_url)


@pytest.fixture(scope="session")
def run_tag() -> str:
    """Tag written into the metadata of every cart/order this run creates."""
    return os.environ.get("QA_RUN_TAG") or os.environ.get("GITHUB_RUN_NUMBER") or f"local-{int(time.time())}"


# --------------------------------------------------------------------------- browser


@pytest.fixture(scope="session")
def base_url(config: QaConfig) -> str:
    """Playwright base URL: адрес стэнда, а не публичный домен из canonical.

    ``config.site_origin`` на стэнде равно ``https://tamir.rs`` (это поле про
    SEO-атрибуты), поэтому для браузера берём ``web_url``: ходить на прод-домен
    запрещает и guard, и здравый смысл.
    """
    return config.web_url


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict, config: QaConfig) -> dict:
    """Стартуем каждый контекст с уже принятым согласием на cookie.

    Без согласия баннер перекрывает низ экрана и не даёт кликнуть по корзине и
    оформлению заказа.
    """
    return {
        **browser_context_args,
        "locale": "sr-RS",
        "viewport": {"width": 1440, "height": 900},
        "storage_state": {
            "cookies": [
                {
                    "name": "tamir_consent_v1",
                    "value": "accepted",
                    "url": config.web_url,
                }
            ],
            "origins": [],
        },
    }


# ------------------------------------------------------------------- test data plumbing


@pytest.fixture(scope="session")
def admin_client(api_client: "MedusaStoreClient"):
    """Medusa admin client, or skip when admin credentials are not configured."""
    from qa.medusa_admin import MedusaAdminClient

    if not (os.getenv("MEDUSA_ADMIN_EMAIL") and os.getenv("MEDUSA_ADMIN_PASSWORD")):
        pytest.skip("нет доступа к админ-API: MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD не заданы")
    return MedusaAdminClient(api_client)


@pytest.fixture(scope="session")
def mailbox():
    """Mailpit reader (needed only by registered-customer scenarios)."""
    from qa.mailbox import Mailbox

    box = Mailbox()
    if not box.configured:
        pytest.skip("MAILPIT_URL is not configured: e-mail scenarios cannot run")
    return box


@pytest.fixture(scope="session")
def customer_credentials() -> dict:
    """Unique e-mail + strong password for the test client this session creates."""
    return {
        "email": f"qa-e2e-{uuid.uuid4().hex[:10]}@example.com",
        "password": f"Qa!{uuid.uuid4().hex}x",
        "first_name": "QA",
        "last_name": "Scenario",
        "phone": "+381 64 123 4567",
    }
