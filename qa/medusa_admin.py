"""Клиент админ-API Medusa: уборка тестовых данных, которые оставляют набеги.

Сценарные проверки (``tests/api``, ``tests/e2e``) намеренно оформляют настоящие
заказы на стэнде, поэтому каждый прогон обязан убрать за собой. Всё созданное
тестами помечено: ``metadata.qa_run`` у корзины (он переходит в заказ) и почта
вида ``qa-<что-то>@example.com`` у гостевых заказов и тестовых покупателей.

Что подтверждено на стэнде (Medusa 2.13.6):

* ``DELETE /admin/orders/{id}`` — 404, заказа удалить нельзя; единственный
  способ убрать мусор — ``POST /admin/orders/{id}/cancel``;
* повторная отмена уже отменённого заказа — ошибка, поэтому отменённые
  пропускаем;
* ``DELETE /admin/customers/{id}`` — работает;
* ``GET /admin/carts`` — 404, списка корзин в этом сборке нет (и ``DELETE
  /store/carts/{id}`` тоже 404): корзины остаются и умирают сами по сроку
  хранения Medusa, трогать их нечем;
* фильтр ``?metadata.qa_run=...`` сервер не понимает — ищем на клиенте.

Учётные данные берутся из окружения (``MEDUSA_ADMIN_EMAIL`` и
``MEDUSA_ADMIN_PASSWORD``) и никогда не попадают в отчёт.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from qa.http import QaHttpClient

# Почта тестовых сущностей. Обязательный префикс ``qa-`` — страховка от того,
# что уборка случайно тронет данные, заведённые руками.
TEST_EMAIL_PATTERN = re.compile(r"^qa-[a-z0-9_.-]+@example\.(com|test)$", re.IGNORECASE)

ORDER_FIELDS = "id,display_id,email,status,metadata,created_at,total,currency_code"
CUSTOMER_FIELDS = "id,email,created_at,metadata"


class MedusaAdminError(RuntimeError):
    """Админ-API ответил неожиданно."""


class MedusaAdminClient:
    """Тонкий клиент админ-API (логин почтой и паролем, токены не логируем)."""

    def __init__(self, client: Any) -> None:
        """Как у :class:`qa.medusa_store.MedusaStoreClient`: адрес или HTTP-клиент."""
        if isinstance(client, str):
            from qa.http import QaHttpClient as _Http

            client = _Http(client)
        elif not hasattr(client, "json_of") and hasattr(client, "client"):
            # нам передали клиента витрины — берём его HTTP-транспорт
            client = client.client
        self.client = client
        self._token: Optional[str] = None
        self.last_error: str = ""

    # ---------- вход ----------

    def login(self) -> str:
        if self._token:
            return self._token
        email = (os.getenv("MEDUSA_ADMIN_EMAIL") or "").strip()
        password = (os.getenv("MEDUSA_ADMIN_PASSWORD") or "").strip()
        if not email or not password:
            raise MedusaAdminError(
                "нет доступа к админ-API: задайте MEDUSA_ADMIN_EMAIL и MEDUSA_ADMIN_PASSWORD"
            )
        response = self.client.post(
            "/auth/user/emailpass",
            json={"email": email, "password": password},
            # Publishable-ключ витрины админу не нужен.
            publishable_key=False,
        )
        body = self.client.json_of(response)
        token = body.get("token") if isinstance(body, dict) else None
        if response.status_code not in (200, 201) or not token:
            # Никаких деталей ответа: там может быть письмо администратора.
            raise MedusaAdminError(f"вход в админ-API не удался (HTTP {response.status_code})")
        self._token = str(token)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.login()}"}

    def _fields(self, path: str, fields: str) -> str:
        return f"{path}{'&' if '?' in path else '?'}fields={fields}"

    def get(self, path: str) -> Any:
        response = self.client.get(path, headers=self._headers(), publishable_key=False)
        if response.status_code != 200:
            self.last_error = f"GET {path.split('?')[0]}: HTTP {response.status_code}"
            return None
        return self.client.json_of(response)

    def post(self, path: str, payload: Optional[dict[str, Any]] = None) -> Any:
        response = self.client.post(
            path, json=payload or {}, headers=self._headers(), publishable_key=False
        )
        if response.status_code not in (200, 201):
            self.last_error = f"POST {path.split('?')[0]}: HTTP {response.status_code}"
            return None
        return self.client.json_of(response)

    def delete(self, path: str) -> Any:
        response = self.client.delete(path, headers=self._headers(), publishable_key=False)
        if response.status_code not in (200, 204):
            self.last_error = f"DELETE {path.split('?')[0]}: HTTP {response.status_code}"
            return None
        return self.client.json_of(response)

    # ---------- поиск тестовых данных ----------

    def list_all(self, path: str, key: str, fields: str, limit: int = 50, max_pages: int = 20) -> list[dict]:
        """Собирает весь список постранично (``count`` есть, но страницы короче ``limit`` = конец)."""
        items: list[dict] = []
        for page in range(max_pages):
            body = self.get(self._fields(f"{path}?limit={limit}&offset={page * limit}", fields))
            batch = (body or {}).get(key) if isinstance(body, dict) else None
            if not batch:
                break
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < limit:
                break
        return items

    def find_qa_orders(self, run_tag: str = "") -> list[dict]:
        """Заказы прогона: метка ``metadata.qa_run`` или почта ``qa-*@example.com``."""
        found = []
        for order in self.list_all("/admin/orders", "orders", ORDER_FIELDS):
            metadata = order.get("metadata")
            tag = str(metadata.get("qa_run")) if isinstance(metadata, dict) else ""
            email = str(order.get("email") or "")
            if run_tag and tag != run_tag:
                continue
            if tag or TEST_EMAIL_PATTERN.match(email):
                found.append(order)
        return found

    def get_order(self, order_id: str) -> Optional[dict[str, Any]]:
        """Заказ по id — фильтр по id работает, сканировать страницы не нужно."""
        body = self.get(self._fields(f"/admin/orders/{order_id}", ORDER_FIELDS))
        order = body.get("order") if isinstance(body, dict) else None
        return order if isinstance(order, dict) else None

    def find_order_by_display_id(self, display_id: int) -> Optional[dict]:
        """Заказ по короткому номеру с витрины («Broj porudžbine: 21»).

        Фильтр ``?display_id=`` этот сборке не помогает, поэтому ищем по
        страницам — номера на стэнде короткие, страниц немного.
        """
        for order in self.list_all("/admin/orders", "orders", ORDER_FIELDS):
            if str(order.get("display_id")) == str(display_id):
                return order
        return None

    def find_qa_customers(self) -> list[dict]:
        """Тестовые покупатели: почта с префиксом ``qa-`` на домене для тестов."""
        return [
            customer
            for customer in self.list_all("/admin/customers", "customers", CUSTOMER_FIELDS)
            if TEST_EMAIL_PATTERN.match(str(customer.get("email") or ""))
        ]

    # ---------- уборка ----------

    def cancel_order(self, order_id: str) -> bool:
        """Заказ нельзя удалить, только отменить. Отменённый не трогаем."""
        body = self.post(f"/admin/orders/{order_id}/cancel", {})
        if isinstance(body, dict) and body.get("order"):
            return True
        return False

    def delete_customer(self, customer_id: str) -> bool:
        body = self.delete(f"/admin/customers/{customer_id}")
        return bool(isinstance(body, dict) and body.get("deleted"))

    def clean(self, run_tag: str = "", already_canceled_ok: bool = True) -> dict[str, Any]:
        """Отменяет тестовые заказы и удаляет тестовых покупателей.

        Возвращает сводку для отчёта (только счётчики и id, без писем).
        """
        summary: dict[str, Any] = {"orders_canceled": 0, "orders_skipped": 0, "customers_deleted": 0}

        for order in self.find_qa_orders(run_tag):
            status = str(order.get("status") or "")
            if already_canceled_ok and status == "canceled":
                summary["orders_skipped"] += 1
                continue
            if self.cancel_order(str(order.get("id"))):
                summary["orders_canceled"] += 1
            else:
                summary["orders_skipped"] += 1

        for customer in self.find_qa_customers():
            if self.delete_customer(str(customer.get("id"))):
                summary["customers_deleted"] += 1

        if self.last_error:
            summary["last_error"] = self.last_error
        return summary
