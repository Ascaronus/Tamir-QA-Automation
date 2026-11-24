#!/usr/bin/env python3
"""Переносит результаты pytest-прогона в Kiwi TCMS.

Источник правды — Allure result-файлы (``.tmp/allure-results``): их пишет
``allure-pytest`` во время прогона, и именно они публикуются на qa.tamir.rs.
Каждый результат сопоставляется с тест-кейсом каталога по полю ``script``
(= pytest nodeid, который в каталог кладёт ``tcms_seed.py``), и в тестовом прогоне
создаётся или обновляется исполнение: ``TestExecution.create`` /
``TestExecution.update`` — в TCMS 17 это единственный путь (``TestCaseRun.*``
удалены в 15.3).

Ссылка на Allure-отчёт добавляется через ``TestExecution.add_link``, чтобы из
карточки исполнения было видно лог и скриншоты.

Заодно прогон обновляет текст кейсов (поле ``text``): шаги, которые тест описал
фикстурой ``steps``, лежат в ``.tmp/steps.json``, и именно они показываются в
карточке кейса как таблица «Действие → Ожидание».  Запасной текст отсюда не
пишется — подтвердить его нечем, поэтому без настоящих шагов кейс остаётся как
есть.  Исполнениям возвращаем ``case_text_version = "latest"``, иначе Kiwi
показывает ту версию текста, что была на момент создания исполнения.

Запуск (локально или в CI):

    python scripts/tcms_sync_results.py --report-url https://qa.tamir.rs/latest/

Параметры:

    --results DIR     каталог Allure results (по умолчанию .tmp/allure-results)
    --steps-file F    шаги из прогона (по умолчанию .tmp/steps.json)
    --report-url URL  ссылка на отчёт; добавляется к исполнениям
    --build NAME      имя сборки (по умолчанию BUILD_NAME из конфига = "staging")
    --dry-run         только показать, что будет сделано

Нужны TCMS_URL / TCMS_USER / TCMS_PASSWORD (в CI — секреты, локально — .env).
Значения нигде не печатаются. Результаты вне каталога (например, сами tcms-тесты)
в TCMS не пишутся, но попадают в итоговую статистику.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from qa import net  # noqa: E402
from qa.config import load_config  # noqa: E402
from qa.tcms import TcmsClient, TcmsError, TcmsUnavailable  # noqa: E402
from scripts.steps_dump import (  # noqa: E402
    DEFAULT_STEPS_FILE,
    case_text_from_steps,
    load_recorded_steps,
)
from scripts.tcms_seed import LEGACY_PLAN_NAMES, PLAN_NAME  # noqa: E402

#: статусы Allure -> имена TestExecutionStatus (PK берём из справочника по имени)
STATUS_BY_ALLURE = {
    "passed": "PASSED",
    "failed": "FAILED",
    "broken": "ERROR",
    "skipped": "BLOCKED",
    "unknown": "IDLE",
}


def load_results(results_dir: Path) -> list[dict[str, Any]]:
    """Читает Allure results, оставляя последний результат по каждому historyId."""
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted(results_dir.glob("*-result.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  ! пропускаю повреждённый файл {path.name}", file=sys.stderr)
            continue
        key = obj.get("historyId") or obj.get("uuid") or path.name
        prev = latest.get(key)
        if prev is None or (obj.get("stop") or 0) >= (prev.get("stop") or 0):
            latest[key] = obj
    return list(latest.values())


def script_of(result: dict[str, Any]) -> str:
    """Восстанавливает pytest nodeid из Allure labels: label ``package`` + имя."""
    package = next(
        (lb["value"] for lb in result.get("labels", []) if lb.get("name") == "package"),
        "",
    )
    name = result.get("name") or ""
    if not package or not name:
        return ""
    return f"{package.replace('.', '/')}.py::{name}"


def latest_text_version(client: TcmsClient, case_id: int) -> int:
    """Id последней версии текста кейса — на него ссылается новое исполнение.

    В Kiwi TCMS 17 историей текста правит ``simple_history``: ``case_text_version``
    это ``history_id`` строки ``HistoricalTestCase``, а не счётчик внутри кейса.
    ``TestExecution.update`` понимает слово ``latest``, но ``create`` — нет, поэтому
    для нового исполнения номер спрашиваем отдельно.  Если история не читается,
    возвращаем 1: исполнение с устаревшим текстом лучше, чем несозданное.
    """
    try:
        history = client.call("TestCase.history", case_id) or []
    except TcmsError as exc:
        print(f"  ! не удалось прочитать историю TestCase {case_id}: {exc}", file=sys.stderr)
        return 1
    versions = [row.get("history_id") for row in history if row.get("history_id")]
    return max(versions) if versions else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="pytest -> Kiwi TCMS")
    parser.add_argument("--results", default=".tmp/allure-results")
    parser.add_argument(
        "--steps-file",
        default=DEFAULT_STEPS_FILE,
        help=f"шаги из прогона, пишет pytest_sessionfinish (по умолчанию {DEFAULT_STEPS_FILE})",
    )
    parser.add_argument("--report-url", default="")
    parser.add_argument("--build", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    config = load_config()
    net.install_from_config(config)
    results_dir = Path(args.results)
    if not results_dir.is_dir():
        print(f"Нет каталога результатов: {results_dir}", file=sys.stderr)
        return 2

    results = load_results(results_dir)
    if not results:
        print(f"В {results_dir} нет *-result.json", file=sys.stderr)
        return 2

    # Шаги этого же прогона: они же текст кейсов.  Файла может не быть (например,
    # тесты запускали без фикстуры steps) — тогда тексты не трогаем.
    recorded = load_recorded_steps(args.steps_file)

    try:
        client = TcmsClient.from_environment(config)
        client.login()
    except (TcmsUnavailable, TcmsError) as exc:
        print(f"Kiwi TCMS недоступен: {exc}", file=sys.stderr)
        return 2

    status_pk = {name: client.status_id(name) for name in set(STATUS_BY_ALLURE.values())}

    plan = client.find("TestPlan", name__exact=PLAN_NAME)
    if plan is None:
        # Живой план мог остаться со старым именем: сидинг переименовывает его,
        # а не создаёт второй. Ищем по старому имени, чтобы перенос результатов
        # не падал до сидинга и писал в тот же план с историей исполнений.
        for old_name in LEGACY_PLAN_NAMES:
            plan = client.find("TestPlan", name__exact=old_name)
            if plan is not None:
                print(f"  ! план ещё называется {old_name!r} — запустите scripts/tcms_seed.py", file=sys.stderr)
                break
    if plan is None:
        print(f"Нет тест-плана {PLAN_NAME!r} — сначала scripts/tcms_seed.py", file=sys.stderr)
        return 2
    run = client.find("TestRun", plan=plan["id"])
    if run is None:
        print(f"У плана {plan['id']} нет прогона — сначала scripts/tcms_seed.py", file=sys.stderr)
        return 2

    build_name = args.build or config.build_name
    build = client.find("Build", name__exact=build_name)
    if build is None:
        print(f"Нет сборки {build_name!r} — сначала scripts/tcms_seed.py", file=sys.stderr)
        return 2

    created = updated = linked = unmapped = 0
    texts = 0
    for result in results:
        script = script_of(result)
        if not script:
            unmapped += 1
            continue
        case = client.find("TestCase", script__exact=script)
        if case is None:
            # теста нет в каталоге: либо он не помечен smoke/api, либо это
            # собственная проверка интеграции с TCMS
            unmapped += 1
            continue

        # Текст кейса обновляем до исполнения: исполнение показывает тот текст,
        # против которого тест шёл.  Пишем только настоящие шаги прогона — запасной
        # текст тут не подтверждён ничем.  Поле называется `text`: поля `case_text`
        # у JSON-RPC нет, а ответ `filter` отдаёт текущий текст в том же `text`,
        # поэтому сверяемся по нему.
        steps_text = case_text_from_steps(recorded.get(script, []))
        if steps_text and steps_text != (case.get("text") or ""):
            texts += 1
            if args.dry_run:
                print(f"  text: TestCase {case['id']} <- {len(steps_text.splitlines())} строк шагов прогона")
            else:
                client.update("TestCase", int(case["id"]), {"text": steps_text})

        status_name = STATUS_BY_ALLURE.get(result.get("status"), "IDLE")
        values = {"status": status_pk[status_name], "build": build["id"]}

        execution = client.find("TestExecution", run=run["id"], case=case["id"])
        if args.dry_run:
            action = "update" if execution else "create"
            print(f"  {action}: TestCase {case['id']} {script} -> {status_name}")
            continue

        if execution:
            # "latest" — штатное значение Kiwi: сервер сам подставит id последней
            # версии текста, и исполнение начнёт показывать свежие шаги.
            values["case_text_version"] = "latest"
            execution_id = client.update("TestExecution", execution["id"], values)["id"]
            updated += 1
        else:
            # Для нового исполнения номер версии текста обязателен (это IntegerField),
            # и "latest" здесь не разбирается — берём последнюю версию сами.
            values["case_text_version"] = latest_text_version(client, int(case["id"]))
            execution_id = client.create("TestExecution", {"run": run["id"], "case": case["id"], **values})["id"]
            created += 1

        if args.report_url:
            try:
                client.call("TestExecution.add_link", {"execution": execution_id, "url": args.report_url})
                linked += 1
            except TcmsError as exc:
                # ссылка не критична: статус уже записан
                print(f"  ! ссылка не добавлена для {execution_id}: {exc}", file=sys.stderr)

    print(
        f"TCMS: план {plan['id']}, прогон {run['id']}, сборка {build['id']}; "
        f"создано {created}, обновлено {updated}, ссылок {linked}, "
        f"текст кейсов обновлён {texts}, вне каталога {unmapped}, результатов {len(results)}"
    )
    print(f"Смотреть в Kiwi TCMS: Test Runs -> {run['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
