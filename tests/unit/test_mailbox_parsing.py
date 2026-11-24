"""Юнит-проверки читалки Mailpit ``qa/mailbox.py`` (без сети).

Сети здесь нет: вместо транспорта к сессии клиента примонтирована подставка,
которая отвечает записанными формами API — ровно так отвечает Mailpit стэнда
(проверено против живого сервера 24.11.2026).  На этом месте уже ломалось:
тело письма искали на ``/api/v1/messages/{id}/text`` и
``/api/v1/messages/{id}/source``, а эта сборка отвечает на них 404 — тело
живёт на ``/api/v1/message/{ID}``.  Ещё один риск: шестизначный код
регистрации и обратный отсчёт ``Kod ističe za 15:00 minuta`` сидят в одном
письме — тесты фиксируют, что кодом считается только код.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

import pytest
import requests

from qa import mailbox
from qa.config import QaError
from qa.mailbox import MailboxUnavailable

pytestmark = pytest.mark.unit

MAILPIT_URL = "http://127.0.0.1:8025"

#: ящик этого прогона и чужой ящик: читалка обязана их не перепутывать
MAILBOX = "qa-reg-7f3a9c@example.com"
DRUGI_YASHIK = "qa-other-1b2c3d@example.com"

#: тема реального письма регистрации стэнда
REG_SUBJECT = "TAMIR — Kod za registraciju / Registration code"

#: текст реального письма регистрации: шестизначный код плюс обратный отсчёт
REG_TEXT = (
    "Zdravo,\n"
    "\n"
    "Vaš kod za registraciju na TAMIR je: 482913\n"
    "\n"
    "Kod ističe za 15:00 minuta. Ako niste tražili registraciju, "
    "ignorirajte ovaj e-mail.\n"
)
REG_HTML = (
    '<html><body style="margin:0;background:#f4f4f5">'
    '<h1 style="font-size:24px">TAMIR</h1>'
    "<p>Vaš kod za registraciju:</p>"
    '<div style="font-size:32px;letter-spacing:6px"><strong>482913</strong></div>'
    '<p style="color:#6b7280">Kod ističe za 15:00 minuta</p>'
    "</body></html>"
)


def pismo(
    message_id: str,
    subject: str,
    snippet: str,
    to: list[str],
    cc: list[str] | None = None,
) -> dict:
    """Письмо в форме списка — так отвечает ``/api/v1/messages`` на стэнде."""
    entry = {
        "ID": message_id,
        "MessageID": f"<{message_id}.tamir@test.tamir.rs>",
        "Subject": subject,
        "Snippet": snippet,
        "From": {
            "Name": "Tamir",
            "Address": "noreply@test.tamir.rs",
            "ReturnPath": "bounce@staging.tamir.rs",
        },
        "To": [{"Name": "", "Address": address} for address in to],
        "Read": False,
        "CreatedAt": "2026-11-24T09:15:04.123Z",
        "MessageIDList": None,
        "Attachments": None,
        "Snippets": None,
    }
    if cc:
        entry["Cc"] = [{"Name": "", "Address": address} for address in cc]
    return entry


def _otvet(url: str, status: int, teg: dict | str) -> requests.Response:
    """Настоящий ``requests.Response`` с записанным телом: ``.json()`` работает."""
    otvet = requests.Response()
    otvet.status_code = status
    otvet.url = url
    otvet.encoding = "utf-8"
    body = json.dumps(teg, ensure_ascii=False) if isinstance(teg, dict) else teg
    otvet._content = body.encode("utf-8")
    return otvet


class FakeMailpit:
    """Подставка транспорта Mailpit: помнит каждый запрос клиента.

    Монтируется в ``session`` вместо сетевого адаптера, поэтому запросы
    проходят через ``requests.Session.send`` целиком — включая Basic-auth.
    """

    def __init__(self, messages: list[dict] | None = None, bodies: dict[str, dict] | None = None) -> None:
        self.messages = list(messages or [])
        self.bodies = dict(bodies or {})
        self.calls: list[requests.PreparedRequest] = []

    @property
    def puti(self) -> list[str]:
        """Пути с запросом, ровно как их отправил клиент."""
        naiti = []
        for zapros in self.calls:
            razobran = urlsplit(zapros.url)
            put = razobran.path
            if razobran.query:
                put = f"{put}?{razobran.query}"
            naiti.append(put)
        return naiti

    def send(self, zapros: requests.PreparedRequest, **deistvitelnye_parametry) -> requests.Response:
        self.calls.append(zapros)
        put = urlsplit(zapros.url).path
        if put == "/api/v1/messages":
            return _otvet(zapros.url, 200, {
                "messages": self.messages,
                "total": len(self.messages),
                "count": len(self.messages),
            })
        if put.startswith("/api/v1/message/"):
            telo = self.bodies.get(put.rsplit("/", 1)[-1])
            if telo is None:
                return _otvet(zapros.url, 404, {"error": "message not found"})
            return _otvet(zapros.url, 200, telo)
        return _otvet(zapros.url, 404, {"error": f"no route for {put}"})


@pytest.fixture
def pochta(monkeypatch):
    """Фабрика пары «клиент + подставка»: без остаточных переменных окружения."""
    for peremennaya in ("MAILPIT_URL", "MAILPIT_USER", "MAILPIT_PASSWORD"):
        monkeypatch.delenv(peremennaya, raising=False)

    def sozdat(messages: list[dict] | None = None, bodies: dict[str, dict] | None = None):
        podstavka = FakeMailpit(messages, bodies)
        box = mailbox.Mailbox(base_url=MAILPIT_URL)
        box.session.mount("http://", podstavka)
        return box, podstavka

    return sozdat


@pytest.fixture
def chasy(monkeypatch):
    """Электронные часы: ``sleep`` перематывает ``monotonic`` — тесты не спят."""

    class Chasy:
        def __init__(self) -> None:
            self.tekushchee = 0.0

        def monotonic(self) -> float:
            return self.tekushchee

        def sleep(self, sekundy: float) -> None:
            self.tekushchee += sekundy

    chasy = Chasy()
    monkeypatch.setattr(mailbox.time, "monotonic", chasy.monotonic)
    monkeypatch.setattr(mailbox.time, "sleep", chasy.sleep)
    return chasy


# ---------------------------------------------------------------------- список


def test_messages_for_keeps_only_letters_addressed_to_the_mailbox(pochta) -> None:
    """Адрес сравнивается без регистра; получатель в ``To`` и в ``Cc`` — оба засчитываются."""
    box, podstavka = pochta(messages=[
        pismo("m3chuzh", REG_SUBJECT, "Vaš kod za registraciju je: 112233", to=[DRUGI_YASHIK]),
        pismo("m3reg", REG_SUBJECT, "Vaš kod za registraciju je: 482913", to=[MAILBOX.upper()]),
        pismo("m3cc", "TAMIR - Porudzbina / Order #28", "Porudzbina je primljena",
              to=["gost@example.com"], cc=[MAILBOX]),
    ])

    naydennye = box.messages_for(MAILBOX, limit=10)

    assert [pismo_["ID"] for pismo_ in naydennye] == ["m3reg", "m3cc"]
    assert podstavka.puti == ["/api/v1/messages?limit=10"]


def test_messages_for_pros_the_list_with_the_requested_limit(pochta) -> None:
    """``limit`` уходит в запрос: на стэнде ящик набит старой почтой."""
    box, podstavka = pochta()

    assert box.messages_for(MAILBOX, limit=25) == []
    assert podstavka.puti == ["/api/v1/messages?limit=25"]


# ----------------------------------------------------------------------- тело


def test_body_of_reads_from_message_id_endpoint(pochta) -> None:
    """Тело — только на ``/api/v1/message/{ID}``; ``/messages/{id}/text`` и ``/source`` эта сборка не отдаёт (404)."""
    box, podstavka = pochta(bodies={"m3reg": {"Text": REG_TEXT, "HTML": REG_HTML}})

    assert box.body_of("m3reg") == REG_TEXT
    assert podstavka.puti == ["/api/v1/message/m3reg"]
    assert not any("/text" in put or "/source" in put for put in podstavka.puti)


def test_body_of_falls_back_to_html_when_text_empty(pochta) -> None:
    """У письма может не быть текстовой части — тогда читаем ``HTML``."""
    box, _ = pochta(bodies={"m3reg": {"Text": "", "HTML": REG_HTML}})

    assert box.body_of("m3reg") == REG_HTML


def test_body_of_reports_missing_message(pochta) -> None:
    """404 от эндпоинта тела — понятная ошибка, а не пустое письмо."""
    box, _ = pochta()

    with pytest.raises(MailboxUnavailable) as isklyuchenie:
        box.body_of("net-takogo")
    assert "HTTP 404" in str(isklyuchenie.value)


# ------------------------------------------------------------------- коды OTP


def test_wait_for_code_takes_code_from_text_body(pochta, chasy) -> None:
    """Код из текстовой части находится, даже когда рядом обратный отсчёт."""
    box, _ = pochta(
        messages=[pismo("m3reg", REG_SUBJECT, "Vaš kod za registraciju je: 482913", to=[MAILBOX])],
        bodies={"m3reg": {"Text": REG_TEXT, "HTML": ""}},
    )

    assert box.wait_for_code(MAILBOX, timeout=30, poll=3) == "482913"


def test_wait_for_code_falls_back_to_html_body(pochta, chasy) -> None:
    """Если ``Text`` пуст, код ищется в ``HTML``."""
    box, _ = pochta(
        messages=[pismo("m3reg", REG_SUBJECT, "Vaš kod za registraciju je: 482913", to=[MAILBOX])],
        bodies={"m3reg": {"Text": "", "HTML": REG_HTML}},
    )

    assert box.wait_for_code(MAILBOX, timeout=30, poll=3) == "482913"


def test_wait_for_code_prefers_text_over_html(pochta, chasy) -> None:
    """Код берётся из ``Text``, пока ``Text`` непустой: HTML не участвует."""
    box, _ = pochta(
        messages=[pismo("m3reg", REG_SUBJECT, "Vaš kod za registraciju je: 248119", to=[MAILBOX])],
        bodies={"m3reg": {"Text": "Vaš kod za registraciju: 248119", "HTML": "<b>911842</b>"}},
    )

    assert box.wait_for_code(MAILBOX, timeout=30, poll=3) == "248119"


def test_wait_for_code_does_not_take_countdown_as_code(pochta, chasy) -> None:
    """``Kod ističe za 15:00 minuta`` кодом не считается: письма без кода — не код."""
    box, podstavka = pochta(
        messages=[pismo("m3otschet", REG_SUBJECT, "Kod ističe za 15:00 minuta", to=[MAILBOX])],
        bodies={"m3otschet": {"Text": "Kod ističe za 15:00 minuta. Registracija ističe za 15:00 minuta.", "HTML": ""}},
    )

    with pytest.raises(MailboxUnavailable):
        box.wait_for_code(MAILBOX, timeout=10, poll=2)
    assert "/api/v1/message/m3otschet" in podstavka.puti


def test_wait_for_code_returns_code_of_the_newest_letter(pochta, chasy) -> None:
    """Mailpit отдаёт список свежими первыми: код берётся из свежего письма, не из старого."""
    box, podstavka = pochta(
        messages=[
            pismo("m3novoe", REG_SUBJECT, "Vaš kod za registraciju je: 915320", to=[MAILBOX]),
            pismo("m3staroe", REG_SUBJECT, "Vaš kod za registraciju je: 123456", to=[MAILBOX]),
        ],
        bodies={
            "m3novoe": {"Text": "Vaš kod za registraciju: 915320", "HTML": ""},
            "m3staroe": {"Text": "Vaš kod za registraciju: 123456", "HTML": ""},
        },
    )

    assert box.wait_for_code(MAILBOX, timeout=30, poll=3) == "915320"
    assert "/api/v1/message/m3staroe" not in podstavka.puti


def test_wait_for_code_ignores_letters_to_other_mailboxes(pochta, chasy) -> None:
    """Чужое письмо с кодом не отдаёт чужой код: клиент к нему даже не заглядывает."""
    box, podstavka = pochta(
        messages=[
            pismo("m3chuzh", REG_SUBJECT, "Vaš kod za registraciju je: 654321", to=[DRUGI_YASHIK]),
            pismo("m3svoe", REG_SUBJECT, "Dobrodošli na TAMIR", to=[MAILBOX]),
        ],
        bodies={
            "m3chuzh": {"Text": "Vaš kod za registraciju: 654321", "HTML": ""},
            "m3svoe": {"Text": "Dobrodošli na TAMIR. Vaš račun kreiran.", "HTML": ""},
        },
    )

    with pytest.raises(MailboxUnavailable) as isklyuchenie:
        box.wait_for_code(MAILBOX, timeout=6, poll=3)
    assert all("m3chuzh" not in put for put in podstavka.puti)
    assert MAILBOX in str(isklyuchenie.value)


def test_wait_for_code_timeout_names_the_mailbox(pochta, chasy) -> None:
    """Пустой ящик: ошибка называет адрес, лимит и число просмотренных писем."""
    box, podstavka = pochta()

    with pytest.raises(MailboxUnavailable) as isklyuchenie:
        box.wait_for_code(MAILBOX, timeout=10, poll=5)
    soobshchenie = str(isklyuchenie.value)
    assert MAILBOX in soobshchenie
    assert "10" in soobshchenie
    assert "просмотрено писем: 0" in soobshchenie
    assert isinstance(isklyuchenie.value, QaError)
    assert podstavka.puti.count("/api/v1/messages?limit=100") == 2


# ------------------------------------------------------- письмо о заказе


def test_order_email_search_matches_subject_and_snippet(pochta, chasy) -> None:
    """Фильтр темы отличает регистрационное письмо от письма о заказе (``Porudzbina``/``Order``)."""
    box, _ = pochta(messages=[
        pismo("m3reg", REG_SUBJECT, "Vaš kod za registraciju je: 482913", to=[MAILBOX]),
        pismo("m3ord", "TAMIR - Porudzbina / Order #28", "Porudzbina br. 28 je primljena", to=[MAILBOX]),
    ])

    found = box.wait_for_order_email(MAILBOX, timeout=10, poll=3)

    assert found is not None and found["ID"] == "m3ord"


def test_order_email_search_reads_the_snippet_too(pochta, chasy) -> None:
    """Нейтральная тема не мешает: упоминание заказа видно в ``Snippet``."""
    box, _ = pochta(messages=[
        pismo("m3ord", "TAMIR", "Porudzbina br. 31 je primljena", to=[MAILBOX]),
    ])

    found = box.wait_for_order_email(MAILBOX, timeout=10, poll=3)

    assert found is not None and found["ID"] == "m3ord"


def test_order_email_search_returns_none_when_there_is_no_letter(pochta, chasy) -> None:
    """Нет письма о заказе — честный ``None``: сценарий решит сам, красить или нет."""
    box, _ = pochta(messages=[
        pismo("m3reg", REG_SUBJECT, "Vaš kod za registraciju je: 482913", to=[MAILBOX]),
    ])

    assert box.wait_for_order_email(MAILBOX, timeout=10, poll=5) is None


# ---------------------------------------------------------- конфигурация


def test_basic_auth_header_is_sent_when_credentials_set(monkeypatch, pochta) -> None:
    """``MAILPIT_USER``/``MAILPIT_PASSWORD`` уходят как HTTP Basic в каждом запросе."""
    monkeypatch.setenv("MAILPIT_USER", "qa-runner")
    monkeypatch.setenv("MAILPIT_PASSWORD", "ne-nastoyashchiy-sekret")
    box, podstavka = pochta()

    box.messages_for(MAILBOX)

    ozhidaemoe = "Basic " + base64.b64encode(b"qa-runner:ne-nastoyashchiy-sekret").decode("ascii")
    assert podstavka.calls[0].headers.get("Authorization") == ozhidaemoe


def test_no_auth_header_without_credentials(pochta) -> None:
    """Без переменных окружения запрос уходит неаутентифицированным (API стэнда открыт внутри сети)."""
    box, podstavka = pochta()

    box.messages_for(MAILBOX)

    assert "Authorization" not in podstavka.calls[0].headers


def test_unconfigured_mailbox_reports_not_configured(monkeypatch) -> None:
    """Без ``MAILPIT_URL`` фикстура пропускает сценарий: ``configured`` ложно, запрос — ошибка."""
    monkeypatch.delenv("MAILPIT_URL", raising=False)
    box = mailbox.Mailbox()

    assert box.configured is False
    with pytest.raises(MailboxUnavailable) as isklyuchenie:
        box.messages_for(MAILBOX)
    assert "MAILPIT_URL" in str(isklyuchenie.value)


def test_configured_true_when_base_url_is_given(monkeypatch) -> None:
    """``base_url`` из параметра важнее пустого окружения; хвостовой слэш срезается."""
    monkeypatch.delenv("MAILPIT_URL", raising=False)
    box = mailbox.Mailbox(base_url=f"{MAILPIT_URL}/")

    assert box.configured is True
    assert box.base_url == MAILPIT_URL
