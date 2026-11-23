"""Юнит-проверки индекса прогонов (без сети, во временном каталоге).

Здесь зафиксированы два реальных инцидента публикации:
дубли записей при повторном прогоне и файл `runs.json` с правами 0600
из-за `tempfile.mkstemp` (nginx отдавал 403 на /json/runs.json).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from qa.publish_runs_index import build_entry, load_existing, main, merge_entries


def _entry(run_number: int, status: str = "success") -> dict:
    return {"run_number": run_number, "status": status, "commit": "deadbeef"}


def test_same_run_number_replaces_entry() -> None:
    first = merge_entries([], _entry(7, "failure"))
    second = merge_entries(first, _entry(7, "success"))

    assert len(second) == 1
    assert second[0]["status"] == "success"


def test_entries_sorted_desc_and_capped() -> None:
    runs = [_entry(n) for n in range(1, 65)]
    merged = merge_entries(runs, _entry(66))

    assert len(merged) == 50
    assert merged[0]["run_number"] == 66
    assert [item["run_number"] for item in merged] == sorted(
        (item["run_number"] for item in merged), reverse=True
    )


def test_broken_index_is_tolerated(tmp_path: Path) -> None:
    index = tmp_path / "json" / "runs.json"
    index.parent.mkdir(parents=True)
    index.write_text("{ это не json", encoding="utf-8")

    assert load_existing(index) == []


def test_write_index_is_readable_by_others_on_posix(tmp_path: Path) -> None:
    """Права 0644 обязательны: qa.tamir.rs читает файл не от имени раннера."""
    if sys.platform == "win32":  # на Windows биты прав не показательны
        pytest.skip("права доступа проверяются на Linux (раннер на staging)")

    rc = main(
        [
            "--reports-dir",
            str(tmp_path),
            "--run-number",
            "42",
            "--branch",
            "main",
            "--commit",
            "abc1234",
            "--run-url",
            "https://github.com/Ascaronus/Tamir-QA-Automation/actions/runs/1",
            "--status",
            "success",
        ]
    )
    assert rc == 0

    index = tmp_path / "json" / "runs.json"
    mode = stat_mode(index)
    assert mode & 0o044, f"файл недоступен для чтения остальными: mode={oct(mode)}"

    payload = json.loads(index.read_text(encoding="utf-8"))
    assert payload["runs"][0]["run_number"] == 42
    assert payload["runs"][0]["id"] == "42"


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
