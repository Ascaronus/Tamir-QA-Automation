#!/usr/bin/env python3
"""Обновляет индекс прогонов: {reports-dir}/json/runs.json.

Схема файла ровно такая:

    {"runs": [{"id": "<run_number>", "date": "<ISO8601 UTC>", "branch": "...",
               "commit": "<sha>", "run_url": "...", "run_number": 123,
               "status": "success"}]}

Записи новые сверху, максимум MAX_RUNS записей. Повторный прогон с тем же
run_number заменяет существующую запись, а не дублирует её.

Отчёт прогона раздаётся из корня: `https://qa.tamir.rs/<run_number>/` и
`https://qa.tamir.rs/latest/` (каталоги `/opt/qa-stack/reports/<run_number>`
и `/opt/qa-stack/reports/latest`), префикса `/runs/` нет.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_RUNS = 50
# Корень раздачи qa.tamir.rs: внутри него лежат <run_number>/, latest/ и json/runs.json.
DEFAULT_REPORTS_DIR = "/opt/qa-stack/reports"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Добавляет запись о прогоне в индекс runs.json для qa.tamir.rs."
    )
    parser.add_argument(
        "--reports-dir",
        default=DEFAULT_REPORTS_DIR,
        help="Корень раздачи отчётов, внутри него пишется json/runs.json "
        f"(по умолчанию {DEFAULT_REPORTS_DIR})",
    )
    parser.add_argument("--run-number", type=int, required=True, help="GITHUB_RUN_NUMBER")
    parser.add_argument("--branch", required=True, help="Ветка прогона (GITHUB_REF_NAME)")
    parser.add_argument("--commit", required=True, help="SHA коммита (GITHUB_SHA)")
    parser.add_argument("--run-url", required=True, help="Ссылка на прогон в GitHub Actions")
    parser.add_argument(
        "--status",
        required=True,
        help="Итог прогона: success / failure / cancelled (conclusion upstream-джоба)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Дата в ISO8601 UTC (по умолчанию — текущее время запуска скрипта)",
    )
    return parser.parse_args(argv)


def load_existing(index_path: Path) -> list[dict[str, Any]]:
    """Читает предыдущие записи; битый или отсутствующий файл = пустой список."""
    if not index_path.exists():
        return []
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"runs.json не прочитан ({exc}), начинаем индекс заново")
        return []
    runs = raw.get("runs") if isinstance(raw, dict) else None
    if not isinstance(runs, list):
        return []
    return [entry for entry in runs if isinstance(entry, dict) and "run_number" in entry]


def build_entry(args: argparse.Namespace) -> dict[str, Any]:
    date = args.date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        "id": str(args.run_number),
        "date": date,
        "branch": args.branch,
        "commit": args.commit,
        "run_url": args.run_url,
        "run_number": int(args.run_number),
        "status": args.status,
    }


def merge_entries(existing: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Заменяет запись с тем же run_number, сортирует по убыванию, режет до MAX_RUNS."""
    merged = [item for item in existing if item.get("run_number") != entry["run_number"]]
    merged.append(entry)
    merged.sort(key=lambda item: int(item["run_number"]), reverse=True)
    return merged[:MAX_RUNS]


def write_index(index_path: Path, runs: list[dict[str, Any]]) -> None:
    """Атомарная запись: во временный файл, затем os.replace."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"runs": runs}, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(index_path.parent), prefix=".runs-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.replace(tmp_name, index_path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    index_path = Path(args.reports_dir) / "json" / "runs.json"
    entry = build_entry(args)
    runs = merge_entries(load_existing(index_path), entry)
    write_index(index_path, runs)
    print(f"Обновлён {index_path}: прогонов {len(runs)}, последний #{entry['run_number']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
