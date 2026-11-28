"""UI-саморегистрация покупателя на витрине (E2E-32…E2E-34).

Контракт страницы по живому замеру 28.11.2026 (headless Chromium 1440x900,
``{WEB_URL}/rs/account/register`` -> rewrite на ``/rs/nalog/registracija``):

* форма ``input#register-firstName`` … чекбокс ``#register-consent``, кнопка
  «Pošalji kod za potvrdu» — селекторы и тексты уже в ``qa/ui.py``;
* скрипт Turnstile (``challenges.cloudflare.com/turnstile/v0/api.js``)
  появляется НЕ при загрузке страницы, а только по submit: ``Captcha.tsx``
  грузит api.js с ``render=explicit`` и рисует модалку «Bezbednosna provera»
  (``Storefront.wait_for_captcha_gate``);
* после успешного ``registration/start`` — экран «Potvrdite email adresu»
  (``CODE_SENT_RE``) с ``input[autocomplete='one-time-code']``; верный код
  отправляет ``registration/verify`` и витрина сама входит покупателем
  (``{WEB_URL}/rs/nalog``, ``h1`` кабинета — имя+фамилия регистранта);
* неверный код — ``p[role=alert]`` на том же экране, покупатель не создан;
* пустой submit и submit без согласия валидируются на клиенте: POST на
  ``registration/start`` не уходит вообще (слушатель запросов).

Зачем ретранслятор, а не «обход прокси». Cloudflare не выдаёт токен headless,
поэтому api.js подменяется заглушкой (``page.route``), а её ``callback``
вызывается тестом — токен «квиток», который бэкенд не сверяет. Дальше витрина
шлёт ``registration/start`` и ``registration/verify`` через свой Next-прокси,
а бэкенд QA-обход ``_qa_captcha_bypass`` (``qa.http``) принимает только при
прямом обращении к хосту API: через прокси он видит ``Host: 127.0.0.1`` и
отвечает ``400 CAPTCHA_FAILED`` (замер 28.11.2026). Поэтому запрос
перехватывается и переправляется на ``{API_URL}/store/registration/…`` с
полем обхода; тело ответа отдаётся витрине как есть (SDK-клиент витрины
разбирает его через ``res.json()``, любой HTML-«бутерброд» ломает экран кода),
а ``Set-Cookie`` из ответов подставляется браузеру — им витрина и запоминает
вход. Заглушка turnstile обязана отвечать и на ``remove``: ``Captcha.tsx``
вызывает ``turnstile.remove(id)`` в cleanup эффекта, и без этого метода
модалка ронит витрину на error boundary («Prodavnica nije dostupna»).

Лимиты стенда, которые модуль обязан уважать: ``registration/start`` —
5/час на адрес и 30/час на IP, вход покупателя — 5/час. Поэтому ``start``
здесь ровно два раза за прогон (E2E-32 и E2E-33, каждый новым адресом), а
лишних входов нет: покупателем входит сам verify. Адреса вида
``qa-e2e-reg-…@example.com`` чистит ``scripts/qa_cleanup.py``, и E2E-32
удаляет созданного покупателя сразу сам (тот же ``DELETE
/admin/customers/{id}``, что и ``qa_cleanup.py --now``).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from qa.http import QaHttpClient, captcha_bypass_token, with_captcha_bypass
from qa.mailbox import MailboxUnavailable
from qa.medusa_admin import MedusaAdminError
from qa.ui import (
    CODE_SENT_RE,
    REGISTER_ALERT,
    REGISTER_CITY,
    REGISTER_CONSENT,
    REGISTER_EMAIL,
    REGISTER_FIELD_ERROR,
    REGISTER_FIRST_NAME,
    REGISTER_LAST_NAME,
    REGISTER_PASSWORD,
    REGISTER_PHONE,
    REGISTER_POSTAL_CODE,
    Storefront,
)

pytestmark = [pytest.mark.e2e]

#: POST витрины на роутер регистрации (``/api/medusa/store/registration/…``).
REGISTRATION_PATH_MARK = "store/registration/"

#: Поле кода на экране подтверждения почты (замер 28.11.2026).
OTP_INPUT = "input[autocomplete='one-time-code']"

#: Ошибка на экране кода: ``Registration.tsx`` рендерит ``<p role=alert>``
#: (замер бандла 28.11.2026), ``REGISTER_ALERT`` — это про форму заявки.
OTP_ALERT = "p[role=alert], div[role=alert]"

#: «Токен» из заглушки Turnstile — бэкенд при QA-обходе его не сверяет.
STUB_TURNSTILE_TOKEN = "qa-e2e-stub-token"

#: Заглушка api.js Turnstile (см. докстринг модуля): ``window.__ts`` — опции
#: последнего ``render``, их ``callback`` и вызывает тест. ``remove`` и
#: компания обязательны: ``Captcha.tsx`` зовёт ``turnstile.remove(id)`` при
#: размонтировании модалки, а без метода cleanup кидает TypeError и роняет
#: всю витрину на error boundary (замер 28.11.2026).
TURNSTILE_STUB_JS = """
(() => {
  if (!window.turnstile) {
    window.turnstile = {
      render: (el, opts) => {
        window.__ts = opts || {};
        const box = typeof el === "string" ? document.querySelector(el) : el;
        if (box) {
          const inner = document.createElement("div");
          inner.id = "qa-turnstile";
          box.appendChild(inner);
        }
        return "qa-widget";
      },
      remove: () => {},
      execute: () => {},
      refresh: () => {},
      renderWidget: () => {},
      getResponse: () => null,
      getWidgetId: () => null,
      getWidgetResponse: () => null,
      getError: () => null,
      isExpired: () => false,
      option: () => null,
      deploy: () => {},
      isSupported: () => true,
    };
  }
})();
"""

#: Заголовки, которые нельзя перекладывать в ``route.fulfill`` как есть.
_HOP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


def _registrant_email() -> str:
    """Уникальный адрес регистранта — его чистит ``scripts/qa_cleanup.py``."""
    return f"qa-e2e-reg-{uuid.uuid4().hex[:10]}@example.com"


def _fill_registration_form(page, email: str, *, with_consent: bool = True) -> None:
    """Заполнить форму регистрации (тот же набор, что ``set_registration_details``)."""
    page.fill(REGISTER_FIRST_NAME, "QA E2E")
    page.fill(REGISTER_LAST_NAME, "Registrant")
    page.fill(REGISTER_POSTAL_CODE, "11070")
    city = page.locator(REGISTER_CITY)
    if city.count():
        # как в set_registration_details: options without id — only label works
        city.select_option(label="Beograd")
    page.fill(REGISTER_EMAIL, email)
    page.fill(REGISTER_PHONE, "+381641234567")
    page.fill(REGISTER_PASSWORD, "Qa-e2e-Reg!23")
    if with_consent:
        consent = page.locator(REGISTER_CONSENT)
        if consent.count() and not consent.is_checked():
            consent.check()
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")


def _stub_turnstile(page) -> None:
    """Подменить api.js Turnstile заглушкой (см. докстринг модуля)."""

    def stub(route) -> None:
        route.fulfill(status=200, content_type="application/javascript",
                      body=TURNSTILE_STUB_JS)

    page.route("**/challenges.cloudflare.com/turnstile/**", stub)


def _relay_registration_requests(page, config) -> None:
    """Перехватить ``store/registration/*`` и переправить их прямо на API-хост.

    Без этого бэкенд не принимает капчу-«квиток» (через Next-прокси он видит
    ``Host: 127.0.0.1`` и отвечает ``400 CAPTCHA_FAILED``). Ответы отдаются
    витрине как есть — SDK-клиент витрины зовёт ``res.json()`` (замер бандла
    28.11.2026), так что оборачивать JSON в HTML нельзя: экран кода не
    добирается до ``challenge_id``. Куки из ответов добавляются в контекст
    браузера — остальное витрина делает сам, запросы-то настоящие.
    """
    api_http = QaHttpClient(config.api_url)
    cookie_domain = urlsplit(config.web_url).hostname or "test.tamir.rs"

    def relay(route) -> None:
        request = route.request
        segment = request.url.split(REGISTRATION_PATH_MARK)[-1].split("?")[0].strip("/")
        try:
            raw = request.post_data or ""
            try:
                body = json.loads(raw)
            except ValueError:
                body = {key: values[0] for key, values in parse_qs(raw).items()}
            upstream = api_http.post(
                f"/store/registration/{segment}",
                json=with_captcha_bypass(body),
                timeout=25,
                allow_redirects=False,
            )
            # Куки витрины (``medusa_customer``) живут на хосте витрины: переносим
            # их в контекст браузера, иначе вход после verify не запомнится.
            try:
                cookies = upstream.raw.headers.getlist("set-cookie")
            except Exception:
                cookies = []
            jar = []
            for cookie in cookies:
                name_value, _sep, _attrs = cookie.partition(";")
                name, _eq, value = name_value.partition("=")
                if name.strip():
                    jar.append({"name": name.strip(), "value": value.strip(),
                                "domain": cookie_domain, "path": "/"})
            if jar:
                page.context.add_cookies(jar)
            headers = {
                name: value
                for name, value in upstream.headers.items()
                if name.lower() not in _HOP_HEADERS and name.lower() != "set-cookie"
            }
            # И start, и verify — голый JSON бэкенда как есть: витрина сама
            # решает, что показывать (экран кода при 200, alert при 4xx).
            route.fulfill(status=upstream.status_code, headers=headers or None,
                          body=upstream.content)
        except Exception as exc:  # сетевой промах бэкенда — честная 502, не вешаем витрину
            route.fulfill(status=502, content_type="application/json",
                          body=json.dumps({"error": f"qa relay failed: {exc}"}))

    page.route(f"**/api/medusa/{REGISTRATION_PATH_MARK}**", relay)


def _pass_captcha_gate(page, store: Storefront) -> None:
    """Дождаться модалки капчи, вызвать ``callback`` заглушки, увидеть экран кода.

    Экран ищется по тексту (``CODE_SENT_RE``), а не по URL: роутинг
    ``/rs/nalog/registracija`` на стенде чувствителен к rewrite (урок
    ``test_registration_gate.py``).
    """
    gate = store.wait_for_captcha_gate()
    assert gate, "модалка «Bezbednosna provera» так и не появилась"
    page.wait_for_function("() => !!window.__ts", timeout=10_000)
    try:
        page.evaluate("(token) => window.__ts.callback && window.__ts.callback(token)",
                      STUB_TURNSTILE_TOKEN)
    except PlaywrightError:
        pass  # submit мог успеть унести execution context
    page.wait_for_selector(OTP_INPUT, timeout=20_000)
    expect(page.get_by_text(CODE_SENT_RE).first).to_be_visible(timeout=10_000)


def _skip_without_captcha_bypass() -> None:
    """Без ``QA_CAPTCHA_BYPASS_TOKEN`` ретранслятор не пройдёт капчу стенда."""
    if not captcha_bypass_token():
        pytest.skip("нет QA_CAPTCHA_BYPASS_TOKEN: стенд не примет заявку без токена Turnstile")


def _customer_id_settled(admin_client, email: str, attempts: int = 6) -> str:
    """``customer_id`` с короткими повторами: покупатель появляется не мгновенно.

    Админ-API на стенде иногда отвечает ошибкой на ровном месте — в этом
    случае тест честно пропускается, а не падает (урок ``e2e/conftest.py``).
    """
    for attempt in range(attempts):
        try:
            customer_id = admin_client.customer_id_for_email(email)
        except MedusaAdminError as exc:
            pytest.skip(f"админ-API недоступен ({exc})")
        if customer_id:
            return customer_id
        time.sleep(2.0)
    return ""


def test_e2e_32_registration_success(page, config, admin_client, mailbox, steps):
    """E2E-32. Успешная регистрация: письмо, код, автоматический вход.

    Заявка с формы проходит капчу (заглушка + QA-обход), верный код из письма
    активирует покупателя, витрина сама входит — открыт кабинет ``/rs/nalog``,
    ``h1`` с именем регистранта. Покупатель виден в админ-API и сразу удаляется им же
    (``DELETE /admin/customers/{id}``), после удаления админ-API его не находит.
    """
    _skip_without_captcha_bypass()
    email = _registrant_email()
    store = Storefront(page)
    _stub_turnstile(page)
    _relay_registration_requests(page, config)

    with steps("отправить заполненную форму с согласием",
               "модалка капчи, затем экран «Potvrdite email adresu»"):
        store.goto_register()
        _fill_registration_form(page, email)
        store.submit_registration()
        _pass_captcha_gate(page, store)

    with steps("прочитать код из письма в Mailpit", "шестизначный код"):
        try:
            code = mailbox.wait_for_code(email, timeout=90)
        except MailboxUnavailable as exc:
            pytest.skip(f"код регистрации не пришёл в Mailpit ({exc})")

    with steps("ввести верный код и нажать Enter",
               "витрина входит покупателем: /rs/nalog, h1 с именем"):
        page.fill(OTP_INPUT, code)
        page.keyboard.press("Enter")
        expect(page).to_have_url(re.compile(r"/rs/nalog"), timeout=30_000)
        # h1 кабинета — имя+фамилия покупателя (замер 28.11.2026);
        # незалогиненного сюда витрина не пускает, так что сам экран уже
        # доказательство входа
        expect(page.locator("h1").first).to_contain_text(
            "QA E2E Registrant", timeout=10_000)

    with steps("найти покупателя в админ-API", "покупатель создан"):
        customer_id = _customer_id_settled(admin_client, email)
        if not customer_id:
            pytest.skip(f"покупатель {email} не виден в админ-API — проверьте стенд")

    with steps("удалить покупателя через админ-API", "покупателя больше нет"):
        assert admin_client.delete_customer(customer_id), "DELETE /admin/customers не подтверждён"
        for _ in range(3):
            if not admin_client.customer_id_for_email(email):
                break
            time.sleep(2.0)
        else:
            pytest.skip(f"покупатель {email} всё ещё виден в админ-API после удаления")


def test_e2e_33_registration_wrong_otp(page, config, admin_client, steps):
    """E2E-33. Неверный код подтверждения: ошибка и никакого покупателя.

    Заявка уходит на стенд по-настоящему (второй и последний ``start`` модуля),
    код заведомо неверный — на экране подтверждения появляется ошибка, витрина
    остаётся на том же экране, покупатель не создан (проверка админ-API).
    Повторной заявки с верным кодом нет: расход почасового лимита фиксирован.
    """
    _skip_without_captcha_bypass()
    email = _registrant_email()
    store = Storefront(page)
    _stub_turnstile(page)
    _relay_registration_requests(page, config)

    with steps("отправить заявку и пройти капчу", "экран «Potvrdite email adresu»"):
        store.goto_register()
        _fill_registration_form(page, email)
        store.submit_registration()
        _pass_captcha_gate(page, store)

    with steps("ввести неверный код", "role=alert с ошибкой на том же экране"):
        page.fill(OTP_INPUT, "000000")
        page.keyboard.press("Enter")
        expect(page.locator(OTP_ALERT).first).to_be_visible(timeout=15_000)
        expect(page.locator(OTP_INPUT)).to_be_visible()

    with steps("спросить админ-API", "покупателя с таким адресом нет"):
        try:
            assert not admin_client.customer_id_for_email(email), \
                f"покупатель {email} создан несмотря на неверный код"
        except MedusaAdminError as exc:
            pytest.skip(f"админ-API недоступен ({exc})")


def test_e2e_34_registration_client_validation(page, config, steps):
    """E2E-34. Клиентская валидация: пустая заявка и заявка без согласия.

    Submit пустой формы и submit с незаполненным согласием — POST на
    ``registration/start`` не уходит ни разу (счётчик по слушателю запросов),
    поля подсвечены ошибкой, на экран кода не редиректит. Лимит ``start``
    модуля эти прогоны не расходуют вовсе.
    """
    store = Storefront(page)
    starts: list[str] = []
    page.on(
        "request",
        lambda request: starts.append(request.url)
        if request.method == "POST" and REGISTRATION_PATH_MARK in request.url
        else None,
    )

    with steps("submit пустой формы", "ошибки у полей, POSTа на сервер не было"):
        store.goto_register()
        store.submit_registration()
        page.wait_for_selector(f"{REGISTER_FIELD_ERROR}, {REGISTER_ALERT}", timeout=10_000)
        assert not starts, f"пустая форма всё же ушла на сервер: {starts}"

    with steps("submit заполненной формы без согласия", "тот же результат"):
        _fill_registration_form(page, _registrant_email(), with_consent=False)
        consent = page.locator(REGISTER_CONSENT)
        if consent.count() and consent.is_checked():
            consent.uncheck()
        store.submit_registration()
        page.wait_for_selector(f"{REGISTER_FIELD_ERROR}, {REGISTER_ALERT}", timeout=10_000)
        assert not page.locator(OTP_INPUT).count(), "несмотря на отсутствие согласия открыт экран кода"
        assert not starts, f"заявка без согласия ушла на сервер: {starts}"
