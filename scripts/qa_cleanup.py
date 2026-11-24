#!/usr/bin/env python3
"""Убирает тестовые данные, которые оставили сценарные прогоны на стэнде.

Сценарии ``tests/api`` и ``tests/e2e`` намеренно оформляют настоящие заказы и
заводят тестовых покупателей — иначе проверка «купить шапку» не настоящая.
После прогона этот скрипт находит всё созданное по метке и убирает:

* заказы — ``POST /admin/orders/{id}/cancel`` (удалить заказ в Medusa 2.13
  нельзя: ``DELETE /admin/orders/{id}`` отвечает 404);
* тестовых покупателей — ``DELETE /admin/customers/{id}``;
* корзины — убрать нечем: ни ``GET /admin/carts``, ни ``DELETE /store/carts/{id}``
  на этой сборке не существуют (404), поэтому брошенные корзины остаются жить
  свой срок хранения Medusa. Их специально не создаём пустыми без нужды.

Как ищем: ``metadata.qa_run`` у заказа (метка переходит из корзины) или почта
вида ``qa-*@example.com``. Фильтр ``?metadata.qa_run=`` сервер не понимает, так
что выбираем на клиенте и только среди того, что помечено, — данные, заведённые
руками, не трогаем.

Запуск (в CI — шаг после прогонов, локально — вручную):

    python scripts/qa_cleanup.py                # всё, что нашли
    python scripts/qa_cleanup.py --run-tag 41   # только заказы конкретного прогона
    python scripts/qa_cleanup.py --dry-run      # показать, ничего не меняя

Нужны MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD (в CI — секреты, локально —
.env). Значения нигде не печатаются; в лог идут только счётчики и id.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from qa.config import load_config  # noqa: E402
from qa.http import QaHttpClient  # noqa: E402
from qa.medusa_admin import MedusaAdminClient, MedusaAdminError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Уборка тестовых данных на стэнде")
    parser.add_argument("--run-tag", default=os.getenv("QA_RUN_TAG", ""),
                        help="метка прогона (по умолчанию QA_RUN_TAG; пусто = все тестовые данные)")
    parser.add_argument("--dry-run", action="store_true", help="только показать найденное")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")

    if not (os.getenv("MEDUSA_ADMIN_EMAIL") and os.getenv("MEDUSA_ADMIN_PASSWORD")):
        print("MEDUSA_ADMIN_EMAIL / MEDUSA_ADMIN_PASSWORD не заданы — уборка пропущена", file=sys.stderr)
        return 0

    config = load_config()
    with QaHttpClient(config.api_url) as client:
        admin = MedusaAdminClient(client)
        try:
            admin.login()
        except MedusaAdminError as error:
            print(str(error), file=sys.stderr)
            return 1

        orders = admin.find_qa_orders(args.run_tag)
        customers = [] if args.orders_only else admin.find_qa_customers()

        print(f"Найдено тестовых заказов: {len(orders)}, тестовых покупателей: {len(customers)}")
        for order in orders:
            print(f"  заказ {order.get('display_id')} ({order.get('id')}) "
                  f"status={order.get('status')}")
        for customer in customers:
            print(f"  покупатель {customer.get('email')} ({customer.get('id')})")

        if args.dry_run:
            print("--dry-run: ничего не меняем")
            return 0

        summary = admin.clean(run_tag=args.run_tag, already_canceled_ok=True)

        print("Готово: отменено заказов {orders_canceled}, пропущено {orders_skipped}, "
              "удалено покупателей {customers_deleted}".format(**summary))
        if summary.get("last_error"):
            print(f"Последняя ошибка админ-API: {summary['last_error']}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
