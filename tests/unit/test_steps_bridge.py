"""Офлайновые проверки моста «шаги теста → текст кейса TCMS».

Здесь нет сети и нет живого TCMS: проверяется только то, как шаги доезжают из
фикстуры ``steps`` до поля ``text``.  На этом мосту уже ломалось три:
``pytest --collect-only`` затирал ``.tmp/steps.json`` пустым файлом, и seeder
терял настоящие шаги; ``as_case_text`` без пути читал только реестр своего
процесса и в скриптах каталога всегда отдавал пусто; и, наконец, seeder писал
поля ``case_text``, которого у JSON-RPC Kiwi TCMS нет, — сериализатор молча
выбрасывал его, и в каталоге оставались пересказы докстринга вместо шагов.
"""

from __future__ import annotations

import json

import pytest

from qa import steps as steps_module
from qa.tcms import TcmsError
from scripts.steps_dump import (
    case_text_from_steps,
    fallback_text,
    load_recorded_steps,
)
from scripts.tcms_seed import case_payload
from scripts.tcms_sync_results import latest_text_version

pytestmark = pytest.mark.unit

NODEID = "tests/api/test_checkout_scenarios.py::test_guest_order_reaches_the_shop"

#: Заголовок таблицы: Kiwi рендерит текст кейса из Markdown, и без этих строк
#: шаги останутся просто текстом, а не инструкцией.
TABLE_HEAD = "| # | Действие | Ожидание |\n| --- | --- | --- |"


@pytest.fixture(autouse=True)
def _chistyy_refer_shagov():
    """Каждый тест начинает с пустого реестра шагов и оставляет его пустым."""
    zacherennoe = dict(steps_module.RECORDED)
    steps_module.RECORDED.clear()
    yield
    steps_module.RECORDED.clear()
    steps_module.RECORDED.update(zacherennoe)


def test_format_stroki_shagov_daget_markdown_tablicu():
    assert steps_module.format_steps([("Берём товар", "витрина отдала товар"), ("Платим", "")]) == (
        f"{TABLE_HEAD}\n| 1 | Берём товар | витрина отдала товар |\n| 2 | Платим | — |"
    )


def test_format_stroki_ne_lomaet_tablicu_vertikalnoy_chertoy():
    """Вертикальная черта в шаге не должна рвать таблицу Markdown."""
    tekst = steps_module.format_steps([("Открыть `A | B`", "окно с `A | B`")])

    stroki = tekst.splitlines()
    assert all(stroka.count(r"\|") == 2 for stroka in stroki[2:]), stroki
    reshetka = stroki[2].replace(r"\|", "")
    assert reshetka.count("|") == 4, reshetka
    assert reshetka.startswith("| 1 | ") and reshetka.endswith(" |"), reshetka


def test_as_case_text_chitaet_fail_progona(tmp_path):
    """Скрипт каталога — отдельный процесс: шаги он берёт из файла прогона."""
    put = tmp_path / "steps.json"
    put.write_text(
        json.dumps({NODEID: [["Открываем витрину", "есть товары"]]}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert steps_module.as_case_text(NODEID, str(put)) == (
        f"{TABLE_HEAD}\n| 1 | Открываем витрину | есть товары |"
    )


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
        "Описание сценария.\n\n*Проверка выполняется автоматически: `test_guest_order`.*"
    )


def test_fallback_text_bez_dokstringa_chitaet_imya_testa():
    tekst = fallback_text("test_robots_txt_is_available")
    assert tekst.startswith("test robots txt is available\n\n")
    assert "`test_robots_txt_is_available`" in tekst


def test_case_text_from_steps_pustoy_spisok_daget_pustuyu_stroku():
    assert case_text_from_steps([]) == ""


def test_case_payload_pishet_text_a_ne_case_text():
    """Регресс на главную поломку: у JSON-RPC Kiwi TCMS поля ``case_text`` нет.

    Сериализатор незнакомое поле не роняет, а выбрасывает молча, поэтому шаг
    тест-кейса до TCMS не доезжал вообще.  Держим имя поля под тестом.
    """
    keys = {
        "nodeid": NODEID,
        "summary": "Гостевой заказ доходит до магазина",
        "doc": "Гость кладёт товар в корзину и оформляет заказ.",
    }
    payload = case_payload(
        keys,
        case_text=TABLE_HEAD,
        category_id=1,
        priority_id=1,
        status_id=1,
        product_id=1,
        author_id=1,
    )

    assert "case_text" not in payload
    assert payload["text"] == TABLE_HEAD
    assert payload["script"] == NODEID
    assert payload["is_automated"] is True


def test_case_payload_bez_shagov_ne_ostavlyaet_keys_bez_teksta():
    """Кейса без текста в каталоге быть не должно: берём докстринг, потом имя."""
    payload = case_payload(
        {"nodeid": NODEID, "summary": "S", "doc": "Гость оформляет заказ."},
        case_text="",
        category_id=1,
        priority_id=1,
        status_id=1,
        product_id=1,
        author_id=1,
    )
    assert payload["text"] == "Гость оформляет заказ."

    bez_dokstringa = case_payload(
        {"nodeid": NODEID, "summary": "S", "doc": ""},
        case_text="",
        category_id=1,
        priority_id=1,
        status_id=1,
        product_id=1,
        author_id=1,
    )
    assert bez_dokstringa["text"] == NODEID


class _KlientIstorii:
    """Заглушка TCMS: отдаёт историю текста кейса или падает, как живой клиент."""

    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else []
        self.error = error

    def call(self, method, case_id):
        assert method == "TestCase.history"
        if self.error:
            raise self.error
        return self.rows


def test_latest_text_version_beryet_svejuyu_versiyu_teksta():
    """`case_text_version` — это history_id строки истории, а не счётчик кейса."""
    klient = _KlientIstorii(rows=[{"history_id": 25}, {"history_id": 24}, {"history_id": 1}])

    assert latest_text_version(klient, 1) == 25


def test_latest_text_version_na_sete_ne_roniyaet_sinhronizaciyu():
    """Исполнение с устаревшим текстом лучше, чем несозданное исполнение."""
    broken = _KlientIstorii(error=TcmsError("tunnel flap"))
    assert latest_text_version(broken, 1) == 1

    pusto = _KlientIstorii(rows=[])
    assert latest_text_version(pusto, 1) == 1
