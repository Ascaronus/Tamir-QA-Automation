#!/usr/bin/env python3
"""Заводит каталог Kiwi TCMS по автотестам репозитория.

Каталог строится из самого pytest: collect по маркерам из `AREAS` (`e2e`,
`scenario`, `smoke`, `api`, `seo`, `catalog`) даёт список кейсов, поэтому в TCMS не
появляется ничего, чего нет в коде. Ключем связи
служит `script` = pytest nodeid (например
`tests/smoke/test_storefront_health.py::test_robots_txt_is_available`) — по нему
кейсы находятся при повторном запуске.

Скрипт идемпотентен: справочники и продукт ищутся по имени, кейсы — по `script`,
существующие кейсы обновляются (summary/text/script/приоритет — только изменившиеся
поля), план и тест-ран дополняются новыми кейсами. Повторный запуск не создаёт
дублей и не плодит версии истории текста.

Текст кейса (поле `text`) берётся из шагов, которые прогон оставил в
`.tmp/steps.json` (его пишет `pytest_sessionfinish`, см. `tests/conftest.py` и
`qa/steps.py`): это Markdown-таблица « Действие | Ожидание », и карточка кейса в
Kiwi рендерит её как таблицу. Порядок такой: сначала прогон, потом сидинг — и в
каталоге оказываются настоящие шаги сценария, а не пересказ докстринга. Если шагов
этого теста в файле нет, seeder откатывается на первую строку докстринга, а без
докстринга — на имя теста: кейса без текста в каталоге быть не должно.

Запуск (учётка бота берётся из `TCMS_URL`/`TCMS_USER`/`TCMS_PASSWORD`):

    python scripts/tcms_seed.py                        # сеять
    python scripts/tcms_seed.py --dry-run              # только показать каталог
    python scripts/tcms_seed.py --steps-file .tmp/steps.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from qa import net  # noqa: E402
from qa.config import load_config  # noqa: E402
from qa.tcms import TcmsClient, TcmsError  # noqa: E402
from scripts.steps_dump import (  # noqa: E402
    DEFAULT_STEPS_FILE,
    case_text_from_steps,
    fallback_text,
    load_recorded_steps,
)

PRODUCT = "Tamir"
CLASSIFICATION = "Tamir Balkan"
VERSION = "staging"
PLAN_NAME = "Staging Regression — Tamir"
PLAN_TEXT = (
    "Регресс тестового стенда Tamir: браузерные пользовательские сценарии (E2E), "
    "сценарии оформления заказа на бэкенде и проверки витрины/API. Каталог кейсов "
    "синхронизирован scripts/tcms_seed.py, исполнения пишет scripts/tcms_sync_results.py."
)
RUN_SUMMARY = "Staging Regression — пользовательские сценарии и проверки"
BUILD_NAME = "staging"

#: старые имена плана и рана: их не плодим заново, а переименовываем существующие
LEGACY_PLAN_NAMES = ("Staging Smoke — Tamir",)
LEGACY_RUN_SUMMARIES = ("Staging Smoke — базовый прогон",)

#: маркер pytest -> (категория в TCMS, приоритет)
#: порядок важен: у теста может быть несколько маркеров, выигрывает первый из списка,
#: поэтому сценарии (e2e/scenario) важнее smoke/api-проверок того же теста
AREAS: dict[str, tuple[str, str]] = {
    "e2e": ("User Journeys E2E", "P1"),
    "scenario": ("Checkout Scenarios", "P1"),
    "smoke": ("Storefront Smoke", "P2"),
    "api": ("Medusa API", "P3"),
    "seo": ("Storefront SEO", "P2"),
    "catalog": ("Catalog & Search", "P3"),
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
                        # текст кейса заполняет attach_case_texts: он знает про
                        # шаги прогона, а коллектор — только про код
                        "case_text": "",
                        "text_source": "",
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


def ensure(
    client: TcmsClient,
    model: str,
    match: dict[str, Any],
    values: dict[str, Any],
    *,
    legacy: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Находит сущность по `match`, создаёт, если её нет.

    `legacy` — старые имена (для `name`/`summary`): вместо второй записи с тем же
    смыслом переименовываем старую, чтобы план и ран сохранили историю
    исполнений и ссылки на отчёты.
    """
    found = client.rows(model, match)
    if found:
        return found[0]
    if legacy and len(match) == 1:
        (field, _wanted), = match.items()
        base = field.split("__", 1)[0]
        for old in legacy:
            stale = client.rows(model, {f"{base}__exact": old})
            if not stale:
                continue
            renamed = client.update(model, int(stale[0]["id"]), {base: values[base]})
            print(f"  ~ {model} {renamed['id']}: переименован из {old!r}")
            return renamed
    created = client.create(model, values)
    print(f"  + {model}: {values.get('name') or values.get('value') or values.get('summary')}")
    return created


def reference_id(client: TcmsClient, model: str, field: str, name: str) -> int:
    """PK записи справочника по имени — ID в TCMS не хардкодим."""
    rows = client.rows(model, {f"{field}__exact": name})
    if not rows:
        raise TcmsError(f"Kiwi TCMS не знает {model} {name!r}")
    return int(rows[0]["id"])


def case_payload(
    case: dict[str, str],
    *,
    case_text: str,
    category_id: int,
    priority_id: int,
    status_id: int,
    product_id: int,
    author_id: int,
) -> dict[str, Any]:
    """Поля кейса для `TestCase.create` / `TestCase.update`.

    Текст кейса — поле `text`: в Kiwi TCMS 17 это обычное поле модели `TestCase`
    (история правок — `TestCase.history`), и именно оно показывается на карточке
    как «Описание», отрендеренное из Markdown.  Отдельного поля `case_text` у
    JSON-RPC нет: сериализатор его молча выбрасывает, и шаги до TCMS не доезжают
    (проверено на живом стенде: `text` создаёт новую версию истории, `case_text`
    — нет).  Поэтому имя поля здесь защищено юнит-тестом.
    """
    return {
        "summary": case["summary"][:255],
        "text": case_text or case["doc"] or case["nodeid"],
        "script": case["nodeid"],
        "is_automated": True,
        "category": category_id,
        "priority": priority_id,
        "case_status": status_id,
        "product": product_id,
        "author": author_id,
    }


def is_changed(existing: dict[str, Any], key: str, wanted: Any) -> bool:
    """Поменялось ли поле: Kiwi отдаёт числа/строки, а мы шлём `True`.

    Сравнение по ``str`` без нормализации всегда считает ``is_automated``
    изменённым (``1`` против ``True``), и тогда каждый прогон пишет кейс заново,
    плодя версию текста.  Поля, которого в ответе фильтра нет, не трогаем:
    сравнить не с чем, а ложная запись дороже, чем недостающее обновление.
    """
    if key not in existing:
        return False
    current = existing[key]
    if isinstance(wanted, bool):
        return str(current) != ("1" if wanted else "0")
    return str(current) != str(wanted)


def sync_case(
    client: TcmsClient,
    case: dict[str, str],
    *,
    case_text: str,
    category_id: int,
    priority_id: int,
    status_id: int,
    product_id: int,
    author_id: int,
) -> dict[str, Any]:
    """Создаёт или обновляет кейс; ключ — `script` (pytest nodeid)."""
    values = case_payload(
        case,
        case_text=case_text,
        category_id=category_id,
        priority_id=priority_id,
        status_id=status_id,
        product_id=product_id,
        author_id=author_id,
    )
    found = client.rows("TestCase", {"script__exact": case["nodeid"]})
    if not found:
        created = client.create("TestCase", values)
        print(f"  + TestCase {created['id']}: {case['nodeid']}")
        return created

    existing = found[0]
    # Обновляем только изменившиеся поля: каждое сохранение кейса в Kiwi создаёт
    # строку в истории, и полная запись на каждом прогоне раздувала бы историю
    # текста без пользы.
    changes = {k: v for k, v in values.items() if is_changed(existing, k, v)}
    if not changes:
        return existing
    updated = client.update("TestCase", int(existing["id"]), changes)
    print(f"  ~ TestCase {existing['id']}: {case['nodeid']} ({', '.join(sorted(changes))})")
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


def prune_stale_cases(
    client: TcmsClient,
    *,
    plan_id: int,
    run_id: int,
    keep: set[str],
    note: str,
) -> int:
    """Отвязывает от плана и рана кейсы, тестов которых больше нет в репозитории.

    Иначе отчёт врёт: в прогоне висят проверки, которые мы сознательно убрали
    (например свёрнутая SEO-проверка), и они вечно стоят IDLE/BLOCKED.
    `TestCase.delete` в XML-RPC Kiwi 17 нет, поэтому кейс остаётся в базе
    с пометкой в `notes` — история исполнений сохраняется, а план и ран
    соответствуют текущему набору тестов.
    """
    attached = {int(row["id"]) for row in client.rows("TestCase", {"plan__exact": plan_id})}
    attached |= {int(row["case"]) for row in client.rows("TestExecution", {"run": run_id})}
    stale = [
        (int(case["id"]), script)
        for case in client.rows("TestCase", {"is_automated": 1})
        if int(case["id"]) in attached
        and (script := (case.get("script") or "").strip())
        and script not in keep
    ]
    for case_id, script in stale:
        for method, parent_id in (("TestPlan.remove_case", plan_id), ("TestRun.remove_case", run_id)):
            try:
                # в Kiwi 17 remove_case принимает один id, список даёт Internal error
                client.call(method, parent_id, case_id)
            except TcmsError as exc:
                print(f"  ! {method}({parent_id}, {case_id}): {exc}")
        try:
            client.update("TestCase", case_id, {"notes": note})
        except TcmsError as exc:
            print(f"  ! TestCase.update({case_id}, notes): {exc}")
        print(f"  - TestCase {case_id}: {script}")
    return len(stale)


ARCHIVE_PREFIX = "Архив: "


def archive_other_plans(client: TcmsClient, *, keep_plan_id: int, keep_run_id: int, note: str) -> int:
    """Уводит планы и раны, которые сидер больше не ведёт, в архив.

    Исторические прогоны (например ранний набор smoke-проверок) остаются в
    базе — по ним видно, что и когда падало, но в списке активных они больше
    не мешаются: план становится неактивным, а в названии рана префикс
    «Архив: ». Иначе в TCMS два «главных» прогона, и непонятно, какой актуален.
    """
    archived = 0
    for plan in client.rows("TestPlan", {}):
        plan_id = int(plan["id"])
        if plan_id == keep_plan_id:
            continue
        try:
            client.update("TestPlan", plan_id, {"is_active": False, "notes": note})
        except TcmsError as exc:
            print(f"  ! TestPlan.update({plan_id}): {exc}")
            continue
        for row in client.rows("TestRun", {"plan": plan_id}):
            run_id = int(row["id"])
            if run_id == keep_run_id:
                continue
            summary = (row.get("summary") or "").strip()
            payload: dict[str, object] = {"notes": note}
            if not summary.startswith(ARCHIVE_PREFIX):
                payload["summary"] = f"{ARCHIVE_PREFIX}{summary}"
            try:
                client.update("TestRun", run_id, payload)
            except TcmsError as exc:
                print(f"  ! TestRun.update({run_id}): {exc}")
        print(f"  архив: план {plan_id} ({plan.get('name')})")
        archived += 1
    return archived


def attach_case_texts(cases: list[dict[str, str]], steps_file: str | None) -> dict[str, int]:
    """Готовит текст кейсов (`text` в TCMS): шаги прогона, иначе докстринг, иначе имя теста.

    Возвращает счётчик источников — по нему видно, сколько кейсов действительно
    описаны шагами, а сколько живут на запасном варианте.
    """
    recorded = load_recorded_steps(steps_file)
    counts: dict[str, int] = {}
    for case in cases:
        text = case_text_from_steps(recorded.get(case["nodeid"], []))
        source = "steps"
        if not text:
            text = fallback_text(case["name"], case["doc"])
            source = "docstring" if case["doc"] else "name"
        case["case_text"] = text
        case["text_source"] = source
        counts[source] = counts.get(source, 0) + 1
    return counts


def plan_type_id(client: TcmsClient) -> int:
    """Тип плана: регресс, но без жёсткой привязки к справочнику Kiwi."""
    for name in ("Regression Sys. Test", "Regression", "Smoke Test", "Smoke"):
        try:
            return reference_id(client, "PlanType", "name", name)
        except TcmsError:
            continue
    raise TcmsError("Kiwi TCMS не знает ни одного подходящего PlanType")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="сеет каталог Kiwi TCMS по автотестам")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет в TCMS")
    parser.add_argument(
        "--steps-file",
        default=DEFAULT_STEPS_FILE,
        help=f"шаги из прогона, пишет pytest_sessionfinish (по умолчанию {DEFAULT_STEPS_FILE})",
    )
    parser.add_argument(
        "--keep-stale",
        action="store_true",
        help="не отвязывать от плана и рана кейсы, тестов которых больше нет в репозитории",
    )
    args = parser.parse_args(argv)

    cases = collect_cases()
    counts = attach_case_texts(cases, args.steps_file)
    print(f"собрано кейсов: {len(cases)}, шаги: {counts}")
    if args.dry_run:
        for case in cases:
            first = case["case_text"].splitlines()[0] if case["case_text"] else ""
            print(f"  [{case['area']}/{case['text_source']}] {case['nodeid']}\n      {first}")
        return 0

    config = load_config()
    net.install_from_config(config)
    client = TcmsClient.from_environment(config)
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
    ptype_id = plan_type_id(client)

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
            case_text=case["case_text"],
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
            "type": ptype_id,
            "author": author_id,
            "text": PLAN_TEXT,
        },
        legacy=LEGACY_PLAN_NAMES,
    )
    plan_id = int(plan["id"])
    try:
        # план мог быть переименован из старого — описание синхронизируем всегда
        client.call("TestPlan.update", plan_id, {"text": PLAN_TEXT, "is_active": True})
    except TcmsError as exc:
        print(f"  ! TestPlan.update({plan_id}, text): {exc}")
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
        legacy=LEGACY_RUN_SUMMARIES,
    )
    run_id = int(run["id"])
    run_cases = {int(row["case"]) for row in client.rows("TestExecution", {"run": run_id})}
    print(f"  кейсов в ране {run_id}: было {len(run_cases)}, добавлено {attach_cases(client, 'TestRun.add_case', run_id, case_ids, run_cases)}")

    if args.keep_stale:
        print("  устаревшие кейсы оставлены (--keep-stale)")
    else:
        note = (
            "Теста больше нет в репозитории, кейс отвязан от плана и тест-рана "
            "этим сидером. Исторические исполнения сохранены."
        )
        print(
            f"  устаревших кейсов отвязано: "
            f"{prune_stale_cases(client, plan_id=plan_id, run_id=run_id, keep={case['nodeid'] for case in cases}, note=note)}"
        )

    archive_note = "План и его прогоны больше не ведёт tcms_seed; оставлены ради истории исполнений."
    print(f"  ушло в архив планов: {archive_other_plans(client, keep_plan_id=plan_id, keep_run_id=run_id, note=archive_note)}")

    summary = client.ping()
    print("итог:", ", ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
