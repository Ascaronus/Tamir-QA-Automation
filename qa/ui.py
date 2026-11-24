"""Страницы витрины TAMIR (Next.js, staging) — селекторы, получившиеся из исходников
``frontend/src/components/{product,cart,checkout,auth}`` и проверенные на живом стенде.

Правила, которые ломают тесты, если их забыть:

* маршруты на стенде англоязычные (``/rs/catalog``, ``/rs/cart``, ``/rs/checkout``,
  ``/rs/account``); ``/rs/korpa`` и ``/rs/nalog`` — это ``next.config`` rewrite, а
  ``/rs/katalog`` — 404. В браузере адрес может остаться «старым», поэтому проверять
  нужно содержимое страницы, а не URL;
* ``data-testid`` в витрине нет — только тексты и классы;
* тексты в ``messages/sr.json`` без диакритики («Korpa», «Naruci»), но страница умеет их
  выводить и с ней; для кнопок — регулярки, покрывающие оба варианта;
* cookie согласния ``tamir_consent_v1`` (значение ``accepted``) обязана быть выставлена до
  первого перехода, иначе поверх макета баннер и клики падают по таймауту;
* «Naruci» на ``/rs/checkout`` — двухшаговая кнопка: сначала «Potvrdi adresu i prikazi
  dostavu» (адрес + доставка), потом ``input[name=shipping]`` появляется и только тогда
  кнопка становится «Naruci»;
* в форме регистрации у ``<select>`` города нет ни ``id``, ни ``name`` — единственный
  устойчивый рычаг это ``autocomplete="address-level2"``, а выбор — по ``label``.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# --- витрина: каталог и карточка --------------------------------------------------
CATALOG_HEADING = "h1"
PRODUCT_CARD = "a.product-card-title"
PRODUCT_TITLE = "h1"
PRODUCT_PRICE = ".product-price"
PRODUCT_STOCK = ".product-stock"
PRODUCT_QTY_INPUT = "#product-quantity"
ADD_TO_CART = "button.button-primary"

# --- корзина ----------------------------------------------------------------------
CART_HEADING = "h1"
CART_LINE = "li.cart-line"
CART_LINE_TITLE = "a.cart-line-title"
CART_LINE_PRICE = "p.cart-line-price"
CART_QTY_VALUE = "span.quantity-value"
CART_REMOVE = "button.cart-remove"
CART_EMPTY = "div.empty-state"
CHECKOUT_CTA = 'aside.order-summary a[href*="checkout"]'

# --- оформление -------------------------------------------------------------------
CHECKOUT_HEADING = "h1"
CHECKOUT_FIRST_NAME = "#checkout-firstName"
CHECKOUT_CITY = "#checkout-city"
CHECKOUT_SHIPPING = "input[name=shipping]"
CHECKOUT_PAYMENT = "input[name=payment]"
CHECKOUT_CONSENT = "#checkout-consent"
CHECKOUT_SUBMIT = "button.checkout-submit"
CHECKOUT_ALERT = "div[role=alert]"
CHECKOUT_FIELD_ERROR = "span.field-error"

# --- подтверждение и аккаунт ------------------------------------------------------
ORDER_CONFIRMED = "h1"
ORDER_NUMBER = "p"
ACCOUNT_ORDERS = "div#account-orders"
ACCOUNT_TABS = "div#account-orders, div#account-profile"
ACCOUNT_TAB_ORDERS = "button:has-text('orud')"

# --- регистрация -------------------------------------------------------------------
REGISTER_FIRST_NAME = "#register-firstName"
REGISTER_LAST_NAME = "#register-lastName"
REGISTER_POSTAL_CODE = "#register-postalCode"
REGISTER_EMAIL = "#register-email"
REGISTER_PHONE = "#register-phone"
REGISTER_PASSWORD = "#register-password"
REGISTER_CONSENT = "#register-consent"
# у <select> города нет id/name — держимся за autocomplete (в оформлении есть #checkout-city)
REGISTER_CITY = 'select[autocomplete="address-level2"]'
REGISTER_SUBMIT = "button[type=submit]"
REGISTER_ALERT = "div[role=alert]"
REGISTER_FIELD_ERROR = "span.field-error"
# Captcha.tsx открывает проверку модальным <dialog> (showModal) c h2 «Bezbednosna provera».
# Атрибута role у него нет, поэтому берём сам тег, а не [role=dialog].
CAPTCHA_DIALOG = "dialog"

ADD_TO_CART_RE = re.compile(r"korpu|cart", re.I)
CONFIRMED_RE = re.compile(r"porud.?bina je primljena", re.I)
# vitrina умеет печатать надписи и с диакритикой, поэтому гласные — через «.»
ORDER_NUMBER_RE = re.compile(r"broj porud.?bine\D*(\d+)", re.I)
ORDER_NUMBER_FALLBACK_RE = re.compile(r"#(\d{2,})")

REGISTER_TITLE_RE = re.compile(r"napravi nalog", re.I)
REGISTER_SEND_RE = re.compile(r"po.+?alji kod", re.I)
# vitrina умеет печатать и с диакритикой, поэтому гласные — через «.»;
# первая ветка — ошибка «проверка не пройдена», вторая — само окно Turnstile
CAPTCHA_TITLE_RE = re.compile(r"bezbednosna prover|proveravamo, da niste bot", re.I)
CODE_SENT_RE = re.compile(r"potvrdite\s+e-?mail", re.I)

CART_ID_KEY = "tb_cart_id"


class Storefront:
    """Одна страница браузера, поочерёдно «ходящая» по витрине."""

    def __init__(self, page: Page) -> None:
        self.page = page

    # --- helpers ------------------------------------------------------------------
    def _click_first(self, selector: str, name_re: re.Pattern | None = None) -> None:
        """Первая кнопка/ссылка по селектору (опционально — с нужным текстом)."""
        target = self.page.locator(selector)
        if name_re is not None:
            target = target.filter(has_text=name_re)
        target.first.wait_for(state="visible", timeout=20_000)
        target.first.click()

    # --- каталог ------------------------------------------------------------------
    def goto_catalog(self) -> None:
        self.page.goto("/rs/catalog", wait_until="domcontentloaded")
        self.page.locator(PRODUCT_CARD).first.wait_for(state="visible", timeout=30_000)

    def product_cards(self) -> Locator:
        return self.page.locator(PRODUCT_CARD)

    def first_product_card(self) -> Locator:
        return self.page.locator(PRODUCT_CARD).first

    def open_product(self, href: str) -> None:
        self.page.goto(href, wait_until="domcontentloaded")
        self.page.locator(PRODUCT_TITLE).first.wait_for(state="visible", timeout=30_000)

    # --- карточка товара ----------------------------------------------------------
    def product_title(self) -> str:
        return self.page.locator(PRODUCT_TITLE).first.inner_text().strip()

    def product_price(self) -> str:
        return self.page.locator(PRODUCT_PRICE).first.inner_text().strip()

    def product_stock_text(self) -> str:
        node = self.page.locator(PRODUCT_STOCK)
        return node.first.inner_text().strip() if node.count() else ""

    def add_to_cart(self, quantity: int | None = None) -> None:
        """«U korpu»: опционально выставляет счётчик количества и ждёт добавления позиции.

        ВАЖНО: витрина создаёт пустую гостевую корзину ещё на загрузке каталога, поэтому
        id корзины в ``localStorage`` — не признак добавления. Ждём ответ
        ``POST .../carts/<id>/line-items``: так делает сама витрина (проверено на стэнде).
        """
        if quantity is not None:
            self.page.fill(PRODUCT_QTY_INPUT, str(quantity))
        with self.page.expect_response(
            lambda r: "/line-items" in r.url and r.request.method == "POST", timeout=30_000
        ):
            self._click_first(ADD_TO_CART, ADD_TO_CART_RE)
        # витрина пересчитывает состояние асинхронно
        self.page.wait_for_timeout(800)

    # --- корзина ------------------------------------------------------------------
    def goto_cart(self, timeout_s: float = 20.0) -> None:
        """Открывает корзину и ждёт, пока она реально отрисуется.

        Витрина отдаёт пустую корзину на SSR (id гостевой корзины лежит только в
        ``localStorage``), поэтому «пусто» в первые миллисекунды — не признак
        пустой корзины: сначала ждём позиции.
        """
        self.page.goto("/rs/cart", wait_until="domcontentloaded")
        self.page.locator(CART_HEADING).first.wait_for(state="visible", timeout=20_000)
        if not self.wait_until(lambda: self.cart_line_count() > 0, timeout_s):
            self.wait_until(lambda: self.cart_is_empty(), 5.0)

    def cart_lines(self) -> Locator:
        return self.page.locator(CART_LINE)

    def cart_line_count(self) -> int:
        return self.cart_lines().count()

    def wait_until(self, probe, timeout_s: float = 15.0) -> bool:
        """Ждёт, пока ``probe()`` станет истинным: корзина пересчитывается асинхронно."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if probe():
                return True
            self.page.wait_for_timeout(400)
        return False

    def wait_for_cart_lines(self, expected: int, timeout_s: float = 15.0) -> bool:
        return self.wait_until(lambda: self.cart_line_count() == expected, timeout_s)

    def wait_for_cart_quantity(self, expected: int, line: int = 0, timeout_s: float = 15.0) -> bool:
        return self.wait_until(lambda: self.cart_quantity(line) == expected, timeout_s)

    def cart_quantity(self, line: int = 0) -> int:
        node = self.page.locator(CART_QTY_VALUE).nth(line)
        try:
            return int(node.inner_text().strip())
        except Exception:  # noqa: BLE001 — позиции ещё нет в DOM
            return -1

    def increase_cart_quantity(self, line: int = 0, expected: int = 2) -> None:
        """«+» в счётчике позиции: ждём ответ витрины и пересчёт количества."""
        with self.page.expect_response(
            lambda r: "/line-items/" in r.url and r.request.method == "POST", timeout=30_000
        ):
            self.page.locator(CART_LINE).nth(line).locator("button.quantity-button").last.click()
        self.wait_for_cart_quantity(expected, line=line)

    def cart_line_price(self, line: int = 0) -> str:
        return self.page.locator(CART_LINE_PRICE).nth(line).inner_text().strip()

    def cart_is_empty(self) -> bool:
        return self.page.locator(CART_EMPTY).count() > 0

    def cart_id(self) -> str | None:
        """Имя гостевой корзины из localStorage — нужно, чтобы убрать за собой."""
        return self.page.evaluate(f"() => localStorage.getItem({CART_ID_KEY!r})")

    def go_to_checkout(self) -> None:
        """Ссылка на оформление из блока «order-summary»."""
        cta = self.page.locator(CHECKOUT_CTA)
        cta.first.wait_for(state="visible", timeout=20_000)
        # ссылка «оформить» блокируется, пока корзина пересчитывается или есть дефицит
        def _ready() -> bool:
            try:
                return (cta.first.get_attribute("aria-disabled") or "") != "true"
            except Exception:  # noqa: BLE001 — страница перерисовывается
                return False

        self.wait_until(_ready, 20.0)
        cta.first.click()
        # форма получателя — единственный признак того, что оформление открылось
        self.page.locator(CHECKOUT_FIRST_NAME).first.wait_for(state="visible", timeout=30_000)

    # --- оформление ---------------------------------------------------------------
    def set_checkout_details(self, data: dict) -> None:
        self.page.fill("#checkout-firstName", data["firstName"])
        self.page.fill("#checkout-lastName", data["lastName"])
        self.page.fill("#checkout-email", data["email"])
        self.page.fill("#checkout-phone", data["phone"])
        self.page.fill("#checkout-address1", data["address1"])
        if data.get("postalCode"):
            self.page.fill("#checkout-postalCode", data["postalCode"])
        city = self.page.locator(CHECKOUT_CITY)
        if city.count():
            grad = data.get("city") or "Beograd"
            try:
                city.select_option(label=grad)
            except Exception:  # noqa: BLE001 — города в списке без диакритики
                city.select_option(index=1)
        pay = self.page.locator(CHECKOUT_PAYMENT)
        if pay.count() and not pay.first.is_checked():
            pay.first.check()
        consent = self.page.locator(CHECKOUT_CONSENT)
        if consent.count() and not consent.is_checked():
            consent.check()

    def prepare_delivery(self) -> None:
        """Первый шаг «Naruci»: адрес + способы доставки."""
        self.page.locator(CHECKOUT_SUBMIT).first.click()
        self.page.locator(CHECKOUT_SHIPPING).first.wait_for(state="attached", timeout=45_000)

    def checkout_errors(self) -> str:
        """Сообщения витрины об отказе (нужны, чтобы падать с внятной причиной)."""
        bits = []
        for sel in (CHECKOUT_ALERT, CHECKOUT_FIELD_ERROR):
            node = self.page.locator(sel)
            for i in range(node.count()):
                bits.append(node.nth(i).inner_text().strip())
        return " | ".join(b for b in bits if b)

    def order_confirmed(self) -> bool:
        """Признак того, что заказ создан: страница подтверждения по тексту."""
        if "/order/" in self.page.url:
            return True
        try:
            return bool(CONFIRMED_RE.search(self.page.locator("main").inner_text()))
        except Exception:  # noqa: BLE001 — страница ещё перерисовывается
            return False

    def place_order(self, timeout_s: float = 90.0) -> None:
        """Второй шаг: корзина дооформляется в заказ, страница подтверждения следом.

        Заказ создаётся ровно один раз, поэтому повторный клик — только если витрина
        явно осталась на ``/rs/checkout`` и показала причину отказа.
        """
        self.page.locator(CHECKOUT_SUBMIT).first.click()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.order_confirmed():
                return
            if self.checkout_errors():
                return
            self.page.wait_for_timeout(500)

    # --- подтверждение ------------------------------------------------------------
    def confirmation_text(self) -> str:
        self.page.locator(ORDER_CONFIRMED).first.wait_for(state="visible", timeout=30_000)
        return self.page.locator(ORDER_CONFIRMED).first.inner_text().strip()

    def order_number(self) -> str:
        body = self.page.locator("main").inner_text()
        match = ORDER_NUMBER_RE.search(body) or ORDER_NUMBER_FALLBACK_RE.search(body)
        return match.group(1) if match else ""

    def order_id_from_url(self) -> str:
        return self.page.url.rstrip("/").split("/")[-1]

    # --- аккаунт ------------------------------------------------------------------
    def login(self, email: str, password: str) -> None:
        self.page.goto("/rs/account/login", wait_until="domcontentloaded")
        self.page.locator("input[name=email]").first.wait_for(state="visible", timeout=20_000)
        self.page.fill("input[name=email]", email)
        self.page.fill("input[name=password]", password)
        self.page.locator("form button[type=submit]").first.click()
        self.page.wait_for_timeout(1500)

    def is_logged_in(self) -> bool:
        """Аккаунт открыт и не показал форму входа."""
        return self.page.locator(ACCOUNT_TABS).count() > 0

    def open_account_orders(self) -> None:
        self.page.goto("/rs/account", wait_until="domcontentloaded")
        tab = self.page.locator(ACCOUNT_TAB_ORDERS)
        if tab.count():
            tab.first.click()
        self.page.wait_for_timeout(1200)

    # --- регистрация ---------------------------------------------------------------
    def goto_register(self) -> None:
        """Страница регистрации: адрес может остаться «старым» (rewrite), ждём саму форму."""
        self.page.goto("/rs/account/register", wait_until="domcontentloaded")
        self.page.locator(REGISTER_FIRST_NAME).first.wait_for(state="visible", timeout=30_000)

    def registration_title(self) -> str:
        return self.page.locator("h1").first.inner_text().strip()

    def set_registration_details(self, data: dict) -> None:
        self.page.fill(REGISTER_FIRST_NAME, data["firstName"])
        self.page.fill(REGISTER_LAST_NAME, data["lastName"])
        self.page.fill(REGISTER_POSTAL_CODE, data["postalCode"])
        city = self.page.locator(REGISTER_CITY)
        if city.count():
            # опции города без id/name и без порядка списка — выбор только по label
            city.select_option(label=data.get("city") or "Beograd")
        self.page.fill(REGISTER_EMAIL, data["email"])
        self.page.fill(REGISTER_PHONE, data["phone"])
        self.page.fill(REGISTER_PASSWORD, data["password"])
        consent = self.page.locator(REGISTER_CONSENT)
        if consent.count() and not consent.is_checked():
            consent.check()

    def selected_city(self) -> str:
        """Значение <select> города — ловит промах «выбор по индексу» на живом стенде."""
        return self.page.locator(REGISTER_CITY).first.input_value()

    def registration_errors(self) -> str:
        """Ошибки витрины по полям — чтобы падение было с внятной причиной."""
        bits = []
        for sel in (REGISTER_ALERT, REGISTER_FIELD_ERROR):
            node = self.page.locator(sel)
            for i in range(node.count()):
                bits.append(node.nth(i).inner_text().strip())
        return " | ".join(b for b in bits if b)

    def submit_registration(self) -> None:
        """«Pošalji kod za potvrdu»: сначала валидация полей, потом капча Turnstile."""
        self._click_first(REGISTER_SUBMIT, REGISTER_SEND_RE)

    def wait_for_captcha_gate(self, timeout_ms: int = 20_000) -> str:
        """Ждёт состояния проверки «вы не бот» и возвращает её текст.

        Витрина показывает либо модальное окно <dialog> с виджетом Turnstile
        («Proveravamo, da niste bot»), либо — когда токен не выдан — ошибку
        «Bezbednosna provera nije uspela». Пустая строка: за отведённое время
        проверка не появилась вообще.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            for selector in (CAPTCHA_DIALOG, REGISTER_ALERT):
                nodes = self.page.locator(selector)
                for i in range(nodes.count()):
                    text = nodes.nth(i).inner_text().strip()
                    if text and CAPTCHA_TITLE_RE.search(text):
                        return text
            time.sleep(0.5)
        return ""

    def registration_otp_requested(self) -> bool:
        """Появился ли экран подтверждения почты (поле для кода из письма)."""
        return self.page.locator("input[autocomplete='one-time-code']").count() > 0
