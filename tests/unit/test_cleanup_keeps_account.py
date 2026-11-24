"""Уборка тестовых данных не должна трогать стендовый аккаунт покупателя.

Стендовый покупатель, под которым :mod:`tests.e2e.test_registered_customer_journey`
входит на сайт, формально подходит под шаблон ``qa-*@example.com`` — тот же, по
которому :mod:`qa.medusa_admin` находит данные прогона. Заведён он один раз и
переживает прогоны, поэтому правило «не удаляем» живёт в самом клиенте, а не в
головах автора теста.
"""

from __future__ import annotations

from qa.medusa_admin import MedusaAdminClient, keep_emails

STAGING_ACCOUNT = {"id": "cus_staging", "email": "qa-e2e-20261124@example.com"}
RUN_CUSTOMER = {"id": "cus_run", "email": "qa-20261124T120000@example.com"}
MANUAL_CUSTOMER = {"id": "cus_manual", "email": "manager@tamir.rs"}


class _NoTransport:
    """HTTP не нужен: список покупателей подменяем целиком."""


def _admin(customers) -> MedusaAdminClient:
    admin = MedusaAdminClient(_NoTransport())
    admin.list_all = lambda path, key, fields, limit=50, max_pages=20: list(customers)  # type: ignore[assignment]
    return admin


def test_keep_emails_from_environment(monkeypatch) -> None:
    """``QA_WEB_EMAIL`` — тот же секрет, что читает e2e-джоб, и он в списке неприкосновенных."""
    monkeypatch.setenv("QA_WEB_EMAIL", "QA-E2E-20261124@example.com")
    monkeypatch.setenv("QA_KEEP_EMAILS", "qa-old@example.com, qa-third@example.test")
    assert keep_emails() == {
        "qa-e2e-20261124@example.com",
        "qa-old@example.com",
        "qa-third@example.test",
    }


def test_staging_account_is_not_removable(monkeypatch) -> None:
    """Стендовый аккаунт возвращается отдельно и в список удаления не попадает."""
    monkeypatch.setenv("QA_WEB_EMAIL", STAGING_ACCOUNT["email"])
    admin = _admin([STAGING_ACCOUNT, RUN_CUSTOMER, MANUAL_CUSTOMER])

    removable, kept = admin.find_qa_customers()
    assert [c["id"] for c in removable] == [RUN_CUSTOMER["id"]]
    assert [c["id"] for c in kept] == [STAGING_ACCOUNT["id"]]


def test_clean_skips_protected_and_orders_only(monkeypatch) -> None:
    """``clean`` удаляет только данные прогона, а ``--orders-only`` — вообще никого."""
    monkeypatch.setenv("QA_WEB_EMAIL", STAGING_ACCOUNT["email"])
    admin = _admin([STAGING_ACCOUNT, RUN_CUSTOMER])
    admin.find_qa_orders = lambda run_tag="": []  # type: ignore[assignment]

    deleted: list[str] = []
    monkeypatch.setattr(admin, "delete_customer", lambda cid: deleted.append(cid) or True)

    summary = admin.clean()
    assert deleted == [RUN_CUSTOMER["id"]]
    assert summary["customers_deleted"] == 1
    assert summary["customers_kept"] == 1

    deleted.clear()
    summary = admin.clean(orders_only=True)
    assert deleted == []
    assert summary["customers_deleted"] == 0
