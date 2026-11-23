#!/usr/bin/env python3
"""Заводит каталог Kiwi TCMS по автотестам репозитория.

Каталог строится из самого pytest: collect по маркерам `smoke`/`api` даёт список
кейсов, поэтому в TCMS не появляется ничего, чего нет в коде. Ключем связи
служит `script` = pytest nodeid (например
`tests/smoke/test_storefront_health.py::test_robots_txt_is_available`) — по нему
кейсы находятся при повторном запуске.

Скрипт идемпотентен: справочники и продукт ищутся по имени, кейсы — по `script`,
существующие кейсы обновляются (summary/text/script/приоритет), план и тест-ран
дополняются новыми кейсами. Повторный запуск не создаёт дублей.

Запуск (учётка бота берётся из `TCMS_URL`/`TCMS_USER`/`TCMS_PASSWORD`):

    python scripts/tcms_seed.py            # сеять
    python scripts/tcms_seed.py --dry-run  # только показать каталог
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from qa.config import load_config  # noqa: E402
from qa.tcms import TcmsClient, TcmsError  # noqa: E402

PRODUCT = "Tamir"
CLASSIFICATION = "Tamir Balkan"
VERSION = "staging"
PLAN_NAME = "Staging Smoke — Tamir"
PLAN_TYPE = "Smoke"
RUN_SUMMARY = "Staging Smoke — базовый прогон"
BUILD_NAME = "staging"

#: маркер pytest -> (категория в TCMS, приоритет)
AREAS: dict[str, tuple[str, str]] = {
    "smoke": ("Storefront Smoke", "P2"),
    "api": ("Medusa API", "P3"),
}

CASE_MARKERS = tuple(AREAS)


def collect_cases(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    """Собирает автоматизированные кейсы из коллектора pytest."""

    class Collector:
        def __init__(self) -> None:
            self.items: list[dict[str, str]] = []

        def pytest_collection_modifyitems(self, items: list[Any]) -> None:
            for item in items:
                markers = {marker.name for marker in item.iter_markers()}
                if "tcms" in markers:
                    # собственные проверки интеграции с TCMS в каталог тест-кейсов не идут
                    continue
                area = next((name for name in CASE_MARKERS if name in markers), None)
                if area is None:
                    continue
                doc = (item.function.__doc__ or "").strip() if hasattr(item, "function") else ""
                first_line = doc.splitlines()[0].strip() if doc else ""
                self.items.append(
                    {
                        "nodeid": item.nodeid,
                        "name": item.name,
                        "area": area,
                        "summary": first_line or item.name.replace("_", " "),
                        "doc": doc,
                    }
                )

        def pytest_collectreport(self, report: Any) -> None:  # pragma: no cover
            return None

    collector = Collector()
    code = pytest.main(
        ["--collect-only", "-q", "--no-header", "-p", "no:cacheprovider", "tests"],
        plugins=[collector],
    )
    if code not in (0, 4) or not collector.items:
        raise SystemExit(f"pytest не собрал кейсы (код {code}) — проверьте tests/ и маркеры")
    return collector.items


def ensure(client: TcmsClient, model: str, match: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Находит сущность по `match`, создаёт, если её нет."""
    found = client.rows(model, match)
    if found:
        return found[0]
    created = client.create(model, values)
    print(f"  + {model}: {values.get('name') or values.get('value') or values.get('summary')}")
    return created


def reference_id(client: TcmsClient, model: str, field: str, name: str) -> int:
    """PK записи справочника по имени — ID в TCMS не хардкодим."""
    rows = client.rows(model, {f"{field}__exact": name})
    if not rows:
        raise TcmsError(f"Kiwi TCMS не знает {model} {name!r}")
    return int(rows[0]["id"])


def sync_case(
    client: TcmsClient,
    case: dict[str, str],
    *,
    category_id: int,
    priority_id: int,
    status_id: int,
    product_id: int,
    author_id: int,
) -> dict[str, Any]:
    """Создаёт или обновляет кейс; ключ — `script` (pytest nodeid)."""
    values = {
        "summary": case["summary"][:255],
        "text": case["doc"] or case["nodeid"],
        "script": case["nodeid"],
        "is_automated": True,
        "category": category_id,
        "priority": priority_id,
        "case_status": status_id,
        "product": product_id,
        "author": author_id,
    }
    found = client.rows("TestCase", {"script__exact": case["nodeid"]})
    if not found:
        created = client.create("TestCase", values)
        print(f"  + TestCase {created['id']}: {case['nodeid']}")
        return created

    existing = found[0]
    updated = client.update("TestCase", int(existing["id"]), values)
    print(f"  ~ TestCase {existing['id']}: {case['nodeid']}")
    return updated


def attach_cases(
    client: TcmsClient,
    method: str,
    parent_id: int,
    case_ids: list[int],
    already: set[int],
) -> int:
    """Привязывает кейсы к плану или рану, уже привязанные пропускает."""
    added = 0
    for case_id in case_ids:
        if case_id in already:
            continue
        try:
            client.call(method, parent_id, case_id)
            added += 1
        except TcmsError as exc:
            print(f"  ! {method}({parent_id}, {case_id}): {exc}")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="сеет каталог Kiwi TCMS по автотестам")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет в TCMS")
    args = parser.parse_args(argv)

    cases = collect_cases()
    print(f"собрано кейсов: {len(cases)}")
    if args.dry_run:
        for case in cases:
            print(f"  [{case['area']}] {case['nodeid']}")
        return 0

    client = TcmsClient.from_environment(load_config())
    print(f"Kiwi TCMS {client.call('KiwiTCMS.version')} — {client.url}")

    user = client.rows("User", {"username__exact": client.user})
    if not user:
        raise SystemExit(f"пользователь {client.user!r} не найден в TCMS")
    author_id = int(user[0]["id"])

    classification = ensure(
        client,
        "Classification",
        {"name__exact": CLASSIFICATION},
        {"name": CLASSIFICATION, "description": "Продукты Tamir Balkan"},
    )
    product = ensure(
        client,
        "Product",
        {"name__exact": PRODUCT},
        {"name": PRODUCT, "classification": int(classification["id"]), "description": "Tamir — витрина и Medusa"},
    )
    product_id = int(product["id"])
    version = ensure(
        client,
        "Version",
        {"product": product_id, "value__exact": VERSION},
        {"product": product_id, "value": VERSION},
    )
    version_id = int(version["id"])

    confirmed_id = reference_id(client, "TestCaseStatus", "name", "CONFIRMED")
    plan_type_id = reference_id(client, "PlanType", "name", PLAN_TYPE)

    categories: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for area, (category_name, priority_name) in AREAS.items():
        category = ensure(
            client,
            "Category",
            {"name__exact": category_name, "product": product_id},
            {"name": category_name, "product": product_id},
        )
        categories[area] = int(category["id"])
        priorities[area] = reference_id(client, "Priority", "value", priority_name)

    case_ids: list[int] = []
    for case in cases:
        created = sync_case(
            client,
            case,
            category_id=categories[case["area"]],
            priority_id=priorities[case["area"]],
            status_id=confirmed_id,
            product_id=product_id,
            author_id=author_id,
        )
        case_ids.append(int(created["id"]))

    plan = ensure(
        client,
        "TestPlan",
        {"name__exact": PLAN_NAME},
        {
            "name": PLAN_NAME,
            "product": product_id,
            "product_version": version_id,
            "type": plan_type_id,
            "author": author_id,
            "text": "Смок-проверки тестового стенда Tamir; кейсы синхронизированы scripts/tcms_seed.py.",
        },
    )
    plan_id = int(plan["id"])
    plan_cases = {int(row["id"]) for row in client.rows("TestCase", {"plan__exact": plan_id})}
    print(f"  кейсов в плане {plan_id}: было {len(plan_cases)}, добавлено {attach_cases(client, 'TestPlan.add_case', plan_id, case_ids, plan_cases)}")

    # В TCMS 17 Build привязан к версии продукта (не к продукту напрямую).
    build = ensure(
        client,
        "Build",
        {"name__exact": BUILD_NAME},
        {"name": BUILD_NAME, "version": version_id, "is_active": True},
    )

    run = ensure(
        client,
        "TestRun",
        {"summary__exact": RUN_SUMMARY},
        {
            "summary": RUN_SUMMARY,
            "plan": plan_id,
            "build": int(build["id"]),
            "manager": client.user,
        },
    )
    run_id = int(run["id"])
    run_cases = {int(row["case"]) for row in client.rows("TestExecution", {"run": run_id})}
    print(f"  кейсов в ране {run_id}: было {len(run_cases)}, добавлено {attach_cases(client, 'TestRun.add_case', run_id, case_ids, run_cases)}")

    summary = client.ping()
    print("итог:", ", ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
