"""Шаги тест-кейса: один источник для Allure-отчёта и каталога Kiwi TCMS.

Тест-кейс, который нельзя повторить руками, не является тест-кейсом. Поэтому
каждый шаг сценария пишется один раз — через фикстуру ``steps`` (см.
tests/conftest.py) — и попадает в два места:

* в Allure как шаг теста (его видно в отчёте ``https://qa.tamir.rs/<n>/``);
* в Kiwi TCMS как текст тест-кейса (``action;expected`` — формат, который TCMS
  показывает как таблицу «действие → ожидание»).

Чтобы seeder видел шаги, тест должен быть выполнен: pytest складывает записанные
шаги в ``.tmp/steps.json`` (см. ``pytest_sessionfinish`` в tests/conftest.py), a
``scripts/tcms_seed.py`` читает этот файл. Если кейс в этом прогоне не запускался,
seeder откатывается на первую строку docstring.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Iterator

import allure

#: nodeid теста -> [(действие, ожидание), ...]
RECORDED: dict[str, list[tuple[str, str]]] = {}


def record(nodeid: str, action: str, expected: str = "") -> None:
    """Записывает шаг теста (в порядок вызова)."""
    RECORDED.setdefault(nodeid, []).append((action.strip(), (expected or "").strip()))


def steps_for(nodeid: str) -> list[tuple[str, str]]:
    return RECORDED.get(nodeid, [])


def format_steps(steps: Iterable[tuple[str, str]]) -> str:
    """Одна строка на шаг: ``действие;ожидание`` (пустое ожидание — само действие)."""
    lines: list[str] = []
    for action, expected in steps:
        lines.append(f"{action};{expected}" if expected else action)
    return "\n".join(lines)


def as_case_text(nodeid: str, path: str | os.PathLike[str] | None = None) -> str:
    """Текст тест-кейса для поля ``case_text`` Kiwi TCMS.

    TCMS разбирает строки вида ``действие;ожидание`` как шаги автоматизированного
    кейса, поэтому в каталоге это выглядит как инструкция, а не как nodeid.

    Шаги берутся из реестра этого процесса.  Если их здесь нет — а скрипты каталога
    ``scripts/tcms_seed.py`` и ``scripts/tcms_sync_results.py`` запускаются отдельной
    командой, после прогона, — текст дочитывается из ``path``: того самого файла,
    который пишет ``dump`` (по умолчанию ``dump_path()``).  Пустая строка
    возвращается, когда шагов нет и в файле: сочинять текст за тест ни фикстура,
    ни скрипты не вправе.
    """
    steps = steps_for(nodeid)
    if not steps:
        steps = load(path or dump_path()).get(nodeid, [])
    return format_steps(steps)


def dump_path() -> str:
    """Куда прогон кладёт шаги — тот же путь читают ``scripts/tcms_seed.py`` и
    ``scripts/tcms_sync_results.py``; переопределяется ``QA_STEPS_FILE``."""
    return os.environ.get("QA_STEPS_FILE") or os.path.join(".tmp", "steps.json")


def has_recorded() -> bool:
    """Записаны ли в этом процессе хоть одни шаги."""
    return bool(RECORDED)


def dump(path: str | os.PathLike[str] | None = None) -> None:
    """Сохраняет записанные шаги, чтобы их увидел seeder из другого процесса.

    Пишется только то, что записал этот процесс.  Вызывать его вслепую (например
    из ``pytest_sessionfinish`` при ``--collect-only``) не стоит: пустой прогон
    затрёт шаги предыдущего.  Поэтому фикстура в ``tests/conftest.py`` сначала
    смотрит на :func:`has_recorded`.
    """
    path = str(path or dump_path())
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({k: [list(s) for s in v] for k, v in RECORDED.items()}, handle,
                  ensure_ascii=False, indent=1)


def load(path: str) -> dict[str, list[tuple[str, str]]]:
    """Читает ``.tmp/steps.json``; пустой словарь, если файла нет."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {nodeid: [(a, e) for a, e in pairs] for nodeid, pairs in raw.items()}


@contextmanager
def tracked_step(nodeid: str, action: str, expected: str = "") -> Iterator[None]:
    """Шаг, который виден и в Allure, и в каталоге TCMS."""
    record(nodeid, action, expected)
    with allure.step(action):
        yield
