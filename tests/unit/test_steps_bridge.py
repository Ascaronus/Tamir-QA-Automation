"""Офлайновые проверки моста «шаги теста → текст кейса TCMS».

Здесь нет сети и нет живого TCMS: проверяется только то, как шаги доезжают из
фикстуры ``steps`` до поля ``case_text``.  На этом мосту уже ломалось два:
``pytest --collect-only`` затирал ``.tmp/steps.json`` пустым файлом, и seeder
терял настоящие шаги; а ``as_case_text`` без пути читал только реестр своего
процесса и в скриптах каталога всегда отдавал пусто.
"""

from __future__ import annotations

import json

import pytest

from qa import steps as steps_module
from scripts.steps_dump import (
    case_text_from_steps,
    fallback_text,
    load_recorded_steps,
)

pytestmark = pytest.mark.unit

NODEID = "tests/api/test_checkout_scenarios.py::test_guest_order_reaches_the_shop"


@pytest.fixture(autouse=True)
def _chistyy_refer_shagov():
    """Каждый тест начинает с пустого реестра шагов и оставляет его пустым."""
    zacherennoe = dict(steps_module.RECORDED)
    steps_module.RECORDED.clear()
    yield
    steps_module.RECORDED.clear()
    steps_module.RECORDED.update(zacherennoe)


def test_format_stroki_shagov_deystvie_ozhidanie():
    assert steps_module.format_steps([("Берём товар", "витрина отдала товар"), ("Платим", "")]) == (
        "Берём товар;витрина отдала товар\nПлатим"
    )


def test_as_case_text_chitaet_fail_progona(tmp_path):
    """Скрипт каталога — отдельный процесс: шаги он берёт из файла прогона."""
    put = tmp_path / "steps.json"
    put.write_text(
        json.dumps({NODEID: [["Открываем витрину", "есть товары"]]}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert steps_module.as_case_text(NODEID, str(put)) == "Открываем витрину;есть товары"


def test_as_case_text_bez_shagov_otdaet_pustuyu_stroku(tmp_path):
    """Пустой текст — честный ответ: сочинять шаги за тест фикстура не вправе."""
    assert steps_module.as_case_text(NODEID, str(tmp_path / "net-faila.json")) == ""


def test_dump_i_load_krug_progona(tmp_path):
    """Что записал прогон, то сидинг прочитает в том же виде."""
    put = tmp_path / "steps.json"
    steps_module.record(NODEID, "Смотрим остаток", "витрина отдала число")

    steps_module.dump(str(put))

    assert steps_module.load(str(put)) == {NODEID: [("Смотрим остаток", "витрина отдала число")]}


def test_dump_path_peredefiniyaetsya_peremenoy(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_STEPS_FILE", str(tmp_path / "shagi.json"))
    assert steps_module.dump_path() == str(tmp_path / "shagi.json")


def test_has_recorded_otrazhaet_zapisannye_shagi():
    """Без шагов писать нечего — фикстура по этому признаку и не трогает файл."""
    assert steps_module.has_recorded() is False
    steps_module.record(NODEID, "Шаг", "Ожидание")
    assert steps_module.has_recorded() is True


def test_load_recorded_steps_soobshchaet_ob_otsutstvennom_fayle(tmp_path, capsys):
    assert load_recorded_steps(str(tmp_path / "net-faila.json")) == {}
    assert "шагов прогона нет" in capsys.readouterr().err


def test_load_recorded_steps_terpit_bityy_fail(tmp_path):
    put = tmp_path / "steps.json"
    put.write_text("{ это не json", encoding="utf-8")

    assert load_recorded_steps(str(put)) == {}


def test_fallback_text_beret_pervuyu_stroku_dokstringa():
    assert fallback_text("test_guest_order", "Описание сценария.\nВторая строка.") == (
        "Описание сценария.;проверка выполняется автоматически (test_guest_order)"
    )


def test_fallback_text_bez_dokstringa_chitaet_imya_testa():
    tekst = fallback_text("test_robots_txt_is_available")
    assert tekst.startswith("test robots txt is available;")
    assert "test_robots_txt_is_available)" in tekst


def test_case_text_from_steps_pustoy_spisok_daget_pustuyu_stroku():
    assert case_text_from_steps([]) == ""
