"""Офлайновые проверки того, что сидинг отправляет в Kiwi TCMS.

Сеть и живой TCMS здесь не нужны: проверяется форма payload'а и решение, какие
поля вообще отправлять при обновлении.  Оба места уже ломались: seeder писал
шаги в поле `case_text`, которого у JSON-RPC-модели `TestCase` нет (Kiwi
незнакомый ключ молча выбрасывает), а сравнение полей при обновлении считало
`is_automated` всегда изменённым — и каждый прогон пересохранял кейс, плодя
версию текста.
"""

from __future__ import annotations

import pytest

from scripts.tcms_seed import case_payload, is_changed

pytestmark = pytest.mark.unit

NODEID = "tests/api/test_checkout_scenarios.py::test_guest_order_reaches_the_shop"

#: Заголовок таблицы: Kiwi рендерит текст кейса из Markdown, и без этих строк
#: шаги останутся просто текстом, а не инструкцией.
TABLE_HEAD = "| # | Действие | Ожидание |\n| --- | --- | --- |"

SPRAWOCHNIKI = {
    "category_id": 1,
    "priority_id": 1,
    "status_id": 1,
    "product_id": 1,
    "author_id": 1,
}


def _payload(case: dict[str, str], case_text: str) -> dict[str, object]:
    return case_payload(case, case_text=case_text, **SPRAWOCHNIKI)


def test_case_payload_pishet_text_a_ne_case_text():
    """Регресс на главную поломку: поля `case_text` у Kiwi TCMS нет.

    Сериализатор незнакомое поле не роняет, а выбрасывает молча, поэтому шаги
    тест-кейса до TCMS не доезжали вообще.  Держим имя поля под тестом.
    """
    payload = _payload(
        {
            "nodeid": NODEID,
            "summary": "Гостевой заказ доходит до магазина",
            "doc": "Гость кладёт товар в корзину и оформляет заказ.",
        },
        TABLE_HEAD,
    )

    assert "case_text" not in payload
    assert payload["text"] == TABLE_HEAD
    assert payload["script"] == NODEID
    assert payload["is_automated"] is True


def test_case_payload_bez_shagov_ne_ostavlyaet_keys_bez_teksta():
    """Кейса без текста в каталоге быть не должно: берём docstring, потом nodeid."""
    s_shagami = _payload({"nodeid": NODEID, "summary": "S", "doc": "Гость оформляет заказ."}, "")
    assert s_shagami["text"] == "Гость оформляет заказ."

    bez_docstringa = _payload({"nodeid": NODEID, "summary": "S", "doc": ""}, "")
    assert bez_docstringa["text"] == NODEID


def test_case_payload_rezhet_summary_dliny_sto_dvuh_simvolov():
    """`summary` в Kiwi ограничен 255 символами, и длиннее поле не пройдёт форму."""
    payload = _payload({"nodeid": NODEID, "summary": "Д" * 400, "doc": ""}, "")
    assert len(str(payload["summary"])) == 255


def test_is_changed_ne_pishet_povtorno_iz_za_booleva():
    """Kiwi отдаёт `1`, мы шлём `True`: без нормализации кейс пишется каждый прогон."""
    otriatsatelnoe = {"is_automated": 1, "text": "шаги", "script": NODEID}

    assert is_changed(otriatsatelnoe, "is_automated", True) is False
    assert is_changed(otriatsatelnoe, "is_automated", False) is True

    nuli = {"is_automated": 0}
    assert is_changed(nuli, "is_automated", False) is False


def test_is_changed_sravnivaet_chisla_i_stroki():
    tekushchee = {"category": 12, "case_status": 2, "summary": "Старая формулировка"}

    assert is_changed(tekushchee, "category", 12) is False
    assert is_changed(tekushchee, "case_status", 3) is True
    assert is_changed(tekushchee, "summary", "Новая формулировка") is True


def test_is_changed_ne_trogayet_polye_kotorogo_net_v_otvete():
    """Сравнить не с чем — ложная запись дороже недостающего обновления."""
    assert is_changed({"summary": "S"}, "text", "шаги") is False
