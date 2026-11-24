#!/usr/bin/env python3
"""Текст тест-кейса для Kiwi TCMS: шаги, которые оставил прогон.

Тест описывает шаги сценария один раз — фикстурой ``steps`` (``tests/conftest.py``
и ``qa/steps.py``), — а прогон складывает их в ``.tmp/steps.json`` на
``pytest_sessionfinish``.  Сидинг каталога и перенос результатов запускаются
отдельной командой, поэтому в реестре шагов своего процесса они не видят ничего
и читают файл отсюда:

    from scripts.steps_dump import DEFAULT_STEPS_FILE, case_text_from_steps

    recorded = load_recorded_steps(args.steps_file)
    text = case_text_from_steps(recorded.get(nodeid, []))   # "" если шагов нет

Запасной текст (``fallback_text``) нужен только сидингу: кейса без текста в
каталоге быть не должно.  Перенос результатов запасной текст не пишет — ему нечем
его подтвердить, поэтому без настоящих шагов он оставляет в TCMS то, что уже есть.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable

from qa.steps import format_steps
from qa.steps import load as load_steps_file

#: Файл шагов по умолчанию: тот же путь и та же переменная, что пишет
#: ``pytest_sessionfinish`` в ``tests/conftest.py``.
DEFAULT_STEPS_FILE = os.environ.get("QA_STEPS_FILE") or os.path.join(".tmp", "steps.json")


def load_recorded_steps(path: str = DEFAULT_STEPS_FILE) -> dict[str, list[tuple[str, str]]]:
    """Шаги прогона по nodeid теста; пустой словарь, если файла нет или он битый.

    Отсутствующий файл — штатная ситуация (каталог можно засеять и до прогона),
    но молча потерять настоящие шаги нельзя, поэтому о файле сообщаем.
    """
    if not path or not os.path.exists(path):
        print(f"  ! шагов прогона нет: {path} — текст кейсов возьмём из докстринга", file=sys.stderr)
        return {}
    recorded = load_steps_file(path)
    print(f"  шаги прогона: {path}, тестов со шагами {len(recorded)}")
    return recorded


def case_text_from_steps(steps: Iterable[tuple[str, str]]) -> str:
    """Текст кейса из шагов: строка ``действие;ожидание`` на шаг.

    Пустая строка, если шагов нет — сочинять текст за тест здесь негде, решает
    вызывающий код: сидинг берёт докстринг, перенос результатов оставляет то,
    что уже лежит в TCMS.
    """
    return format_steps(steps)


def fallback_text(name: str, doc: str = "") -> str:
    """Запасной текст кейса: первая строка докстринга, иначе — имя теста.

    От настоящего текста шагов он отличается намеренно: в нём нет пары
    «действие → ожидание», только пересказ докстринга и пометка, что проверка
    автоматическая.  Так по каталогу видно, какие кейсы описаны сценарием, а какие
    ждут, когда их прогонят.
    """
    for line in (doc or "").splitlines():
        line = line.strip()
        if line:
            return f"{line};проверка выполняется автоматически ({name})"
    readable = (name or "").replace("_", " ").strip()
    return f"{readable or 'кейс не описан'};проверка выполняется автоматически ({name})"
