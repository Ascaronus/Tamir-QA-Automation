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
# ВНИМАНИЕ: голый <dialog> на витрине встречается и вне охраны (мобильное меню
# каталога, диалог «Proizvod je dodat u korpu»), поэтому шлюз ищут по ТЕКСТУ
# (CAPTCHA_TITLE_RE), а не по наличию элемента: см. Storefront.wait_for_captcha_gate.
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

# --- отзывы (ProductDetails.tsx + ProductReviews.tsx) -------------------------------
REVIEWS_SECTION = "#reviews"
REVIEWS_HEADING = ".review-section-heading h2"
# блок со списком и формой скрыт, пока не нажата кнопка «Prikaži»; переход по
# «...#reviews» раскрывает блок сам (useEffect в ProductReviews.tsx)
REVIEWS_EXPAND = "button[aria-controls='product-review-content']"
# раскрываемый блок со списком и формой отзыва (цель aria-controls у REVIEWS_EXPAND)
REVIEW_CONTENT = "div#product-review-content"
REVIEW_WRITE_ANCHOR = "#review-write"
REVIEW_WRITE_FORM = "#review-write form"
# radio рейтинга рендерятся только после гидратации — ждать в браузере, в SSR их нет
REVIEW_RATINGS = 'input[name="review-rating"]'
# у textarea отзыва нет name — держимся за форму внутри #review-write
REVIEW_COMMENT = "#review-write textarea"
REVIEW_SUBMIT = '#review-write button[type="submit"]'
REVIEW_ALERT = "#product-review-content p[role=alert]"
REVIEW_NOTICE = "#product-review-content p[role=status]"

# --- избранное (FavoriteButton.tsx, WishlistLink.tsx, WishlistPage.tsx) -------------
FAVORITE_BUTTON = "button.favorite-button"
HEADER_WISHLIST = "a.header-wishlist"
WISHLIST_COUNT = "span.wishlist-count"
WISHLIST_HEADING = "h1.commerce-title"
WISHLIST_EMPTY = "div.wishlist-empty"
WISHLIST_GRID = "ul.product-grid"
WISHLIST_ITEM = "ul.product-grid a.product-card-title"
WISHLIST_UNAVAILABLE = "li.wishlist-unavailable"
WISHLIST_MOVED = "div.wishlist-moved p[role=status]"

# --- пагинация каталога (Pagination.tsx) -------------------------------------------
PAGINATION_ACTIVE = "nav.pagination button[aria-current=page]"

# --- количество и остаток на карточке (ProductDetails.tsx) --------------------------
QUANTITY_STEPPER = "div.quantity-stepper"
QTY_BUTTON = "button.quantity-button"
QTY_VALUE_RE = re.compile(r"\d+")
QTY_STEP_BUTTON_RE = re.compile(r"^\s*[+\-−]\s*$")
# сообщение о лимите количества: «Možete dodati najviše N komada»
STOCK_ALERT = "span[role=alert]"
STOCK_LIMIT_RE = re.compile(r"najvi.+?e \d+ komada", re.I)
# строка остатка на PDP: «Ukupno N komada» — число в захватывающей группе
STOCK_AVAIL_RE = re.compile(r"ukupno\s+(\d+)\s+komada", re.I)
# ссылка на товар в каталоге: карточки ведут на /rs/products/<handle>
PDP_PRODUCT_LINK = "main a[href^='/rs/products/']"

# --- корзина: позиции и их количество (CartPageClient.tsx) ---------------------------
# у строки корзины класс «cart-line», количество — span.quantity-value между кнопками
# степпера; кнопка «−» заблокирована при quantity<=1 и при исчерпанном остатке
CART_QTY_BUTTON = "li.cart-line button.quantity-button"
CART_LINE_ALERT = "li.cart-line p[role=alert]"

# --- диалог «Proizvod je dodat u korpu» (AddedToCartDialog.tsx) ----------------------
# модальный: перекрывает шапку, поэтому перед наведением на корзину его нужно закрыть
CART_ADDED_DIALOG = "dialog.cart-added-dialog"
CART_ADDED_CONTINUE = "div.cart-added-actions button.button-secondary"
CART_ADDED_GO_TO_CART = "div.cart-added-actions a.button-primary"
CART_ADDED_NAME = "p.cart-added-name"

# --- предпросмотр корзины в шапке (CartPreview.tsx) ---------------------------------
CART_PREVIEW_WRAP = "div.header-cart-preview"
# href у ссылки триггера на стэнде то «/rs/cart», то «/rs/korpa» — держимся за класс
CART_PREVIEW_TRIGGER = "div.header-cart-preview a.header-action"
CART_PREVIEW = "section.cart-preview"
CART_PREVIEW_ROW = "li.cart-preview-row"
CART_PREVIEW_NAME = "span.cart-preview-name"
CART_PREVIEW_REMOVE = "button.cart-preview-remove"
CART_PREVIEW_TOTAL = "div.cart-preview-total strong"
# пустая корзина в поповере — это <p role="status">, класса у него нет
CART_PREVIEW_EMPTY = "section.cart-preview p[role=status]"

# --- фильтры и сортировка каталога (CatalogFilters.tsx, CatalogClient.tsx) ----------
CATALOG_COLOR_CHOICE = "label.catalog-color-choice"
CATALOG_COLOR_INPUT = 'label.catalog-color-choice input[type="checkbox"]'
CATALOG_MIN_PRICE = 'input[name="min_price"]'
CATALOG_MAX_PRICE = 'input[name="max_price"]'
CATALOG_FILTER_SUBMIT = "aside form button[type=submit]"
CATALOG_COUNT = "p.catalog-count"  # текст вида «16 proizvoda»
CATALOG_COUNT_DIGITS = re.compile(r"(\d+)")  # число из счётчика каталога
# карточка товара в сетке каталога и её цена
CATALOG_CARD_ITEM = "li.product-card"
PRODUCT_CARD_PRICE = "p.product-card-price"
CATALOG_SORT = "label.catalog-sort select"
# значения <select> совпадают с catalogSorts из frontend/src/lib/store/catalog.ts
CATALOG_SORTS = ("newest", "price_asc", "price_desc", "popularity")

# --- профиль в личном кабинете (AccountPageClient.tsx, AccountProfileForm.tsx) ------
# панель профиля открывается ссылкой «?panel=profile»; сама форма грузится асинхронно
PROFILE_FORM = "form.profile-form"
PROFILE_SUBMIT = "form.profile-form button[type=submit]"
PROFILE_ALERT = "form.profile-form div[role=alert]"
PROFILE_SAVED = "form.profile-form div[role=status]"
PROFILE_SAVE_RE = re.compile(r"sa.+?uvaj izmene", re.I)
# flash «Podaci su sačuvani.» (account.profileSaved); витрина отдаёт текст без диакритики
PROFILE_SAVED_RE = re.compile(r"podaci su s[ae]?[cč]uvani", re.I)
PROFILE_FIELDS = {
    "firstName": "#profile-firstName",
    "lastName": "#profile-lastName",
    "phone": "#profile-phone",
    "postalCode": "#profile-postalCode",
    "city": "#profile-city",
    "address1": "#profile-address1",
    "notes": "#profile-notes",
}


def profile_field_error(selector: str) -> str:
    """Селектор подписи с ошибкой поля по селектору самого поля.

    AccountProfileForm.tsx рендерит под каждым полем
    ``<span id="profile-<имя>-error" class="field-error">``.
    """
    return f"{selector}-error.field-error"


# --- вход и восстановление пароля (LoginForm.tsx, paths.ts) -------------------------
LOGIN_PATH = "/rs/account/login"
# ссылка с формы входа ведёт на /rs/account/forgot-password (rewrite «nalog/zaboravljena-lozinka»);
# на стэнде в href живёт кириллический слаг, поэтому принимаем оба варианта
RESET_LINK = 'a[href="/rs/nalog/zaboravljena-lozinka"], a[href="/rs/account/forgot-password"]'
# live-форма рендерит <input type="email" required> без name и id
RESET_EMAIL = 'input[type="email"]'
RESET_SUBMIT = "form button[type=submit]"
RESET_TITLE_RE = re.compile(r"(promena|reset|obnova).*lozinke?", re.I)
# адрес страницы восстановления — и кириллический слаг стэнда, и англоязычный rewrite
RESET_PATH_RE = re.compile(r"/rs/(nalog/zaboravljena-lozinka|account/forgot-password)")
# ошибка формы, относящаяся к адресу («Unesite ispravnu imejl adresu»)
EMAIL_ERROR_RE = re.compile(r"(imejl|email|e-mail|adresa)", re.I)
# общая подсказка об ошибке поля в формах аккаунта (проверено на живом стенде)
FORM_ALERT = "div[role=alert], span[role=alert], span.field-error"

# --- повторная покупка (AccountPageClient.tsx) ---------------------------------------
# список заказов кабинета: <ul class="mt-4 divide-y..."><li class="py-4">…</li></ul>
ORDER_CARD = "div#account-orders ul li"
REPEAT_ORDER_BUTTON_RE = re.compile(r"Ponovi poru", re.I)
# ошибка кнопки «Ponovi porudžbinu» (account.repeatPartial) — <p role=alert> в панели заказов
REPEAT_ALERT = "div#account-orders p[role=alert]"


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
    def goto(self, path: str) -> None:
        """Просто перейти по адресу витрины."""
        self.page.goto(path, wait_until="domcontentloaded")

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
        self.page.goto(LOGIN_PATH, wait_until="domcontentloaded")
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

    # --- избранное (зона 2) --------------------------------------------------

    def open_wishlist(self) -> None:
        """Переход на страницу избранного."""
        self.page.goto("/rs/wishlist", wait_until="domcontentloaded")
        self.page.locator(WISHLIST_HEADING).first.wait_for(state="visible", timeout=20_000)

    def wishlist_header_count(self) -> int:
        """Счётчик у иконки избранного в шапке; -1, если списка ещё нет."""
        node = self.page.locator(WISHLIST_COUNT)
        if node.count() == 0:
            return -1
        digits = re.search(r"\d+", node.first.inner_text())
        return int(digits.group()) if digits else -1

    def wishlist_is_empty(self) -> bool:
        """Показан ли блок «избранного нет»."""
        return self.page.locator(WISHLIST_EMPTY).count() > 0

    def wishlist_item_count(self) -> int:
        """Сколько карточек товаров показано в избранном."""
        return self.page.locator(WISHLIST_ITEM).count()

    def favorite_state(self) -> bool:
        """Состояние сердечка на открытой карточке товара (aria-pressed)."""
        node = self.page.locator(FAVORITE_BUTTON).first
        return node.get_attribute("aria-pressed") == "true"

    def wait_favorite_state(self, expected: bool, timeout_s: float = 15.0) -> bool:
        """Ждёт, пока сердечко не примет нужное состояние."""
        return self.wait_until(lambda: self.favorite_state() is expected, timeout_s)

    def toggle_favorite(self) -> None:
        """Клик по сердечку на открытой карточке товара."""
        self.page.locator(FAVORITE_BUTTON).first.wait_for(timeout=20_000)
        self.page.locator(FAVORITE_BUTTON).first.click()

    # --- профиль (зона 8) ----------------------------------------------------

    def open_profile_panel(self) -> None:
        """Открывает панель профиля в кабинете (форма с именем и телефоном)."""
        self.page.goto("/rs/account", wait_until="domcontentloaded")
        self._click_first("nav.account-nav button", re.compile(r"profil", re.I))
        self.page.locator(PROFILE_FORM).first.wait_for(state="visible", timeout=20_000)

    def profile_value(self, name: str) -> str:
        """Текущее значение поля профильной формы (firstName, phone, ...)."""
        return self.page.locator(f"#profile-{name}").input_value().strip()

    def set_profile_value(self, name: str, value: str) -> None:
        """Заполняет поле профильной формы."""
        self.page.locator(f"#profile-{name}").fill(value)

    def save_profile(self) -> None:
        """Клик «Sačuvaj izmene» и ожидание ответа."""
        self._click_first(PROFILE_SUBMIT, PROFILE_SAVE_RE)
        self.page.wait_for_load_state("networkidle")

    def profile_saved_flash(self) -> bool:
        """Появилось ли сообщение «Podaci su sačuvani.»."""
        return bool(PROFILE_SAVED_RE.search(self.page.locator("body").inner_text()))

    def profile_error_text(self) -> str:
        """Текст ошибки профильной формы ('', если её нет)."""
        node = self.page.locator(PROFILE_ALERT)
        return node.first.inner_text().strip() if node.count() else ""

    # --- повтор заказа (зона 3) ----------------------------------------------

    def open_orders_panel(self) -> None:
        """Открывает список заказов в кабинете."""
        self.open_account_orders()

    def order_cards(self) -> int:
        """Сколько заказов показано в списке."""
        return self.page.locator(ORDER_CARD).count()

    def repeat_first_order(self) -> None:
        """Клик «Ponovi porudžbinu» у первого заказа."""
        self._click_first(ORDER_CARD, REPEAT_ORDER_BUTTON_RE)

    # --- отзывы (зона 1) -----------------------------------------------------

    def reviews_heading_text(self) -> str:
        """Заголовок блока отзывов вместе с количеством."""
        node = self.page.locator(REVIEWS_HEADING)
        return node.first.inner_text().strip() if node.count() else ""

    def review_form_is_visible(self) -> bool:
        """Есть ли на странице форма написания отзыва."""
        return self.page.locator(REVIEW_WRITE_FORM).count() > 0

    def review_gate_text(self) -> str:
        """Что витрина показывает вместо формы (подсказка войти / ошибка)."""
        node = self.page.locator(REVIEW_WRITE_ANCHOR)
        return node.first.inner_text().strip() if node.count() else ""

    def fill_review_form(self, rating: int, text: str) -> None:
        """Заполняет форму отзыва, но не отправляет её (отправку блокирует капча)."""
        self.page.locator(f'{REVIEW_RATINGS}[value="{rating}"]').first.check()
        self.page.locator(REVIEW_COMMENT).first.fill(text)

    def review_form_notice(self) -> str:
        """Текст служебного сообщения под формой отзыва."""
        node = self.page.locator(REVIEW_ALERT)
        return node.first.inner_text().strip() if node.count() else ""

    # --- превью корзины (зона 5) ---------------------------------------------

    def open_cart_preview(self, timeout_s: float = 20.0) -> bool:
        """Наводит курсор на корзину в шапке и ждёт появления поповера."""
        trigger = self.page.locator(CART_PREVIEW_TRIGGER).first
        trigger.wait_for(timeout=int(timeout_s * 1000))
        trigger.hover()
        return self.wait_until(lambda: self.page.locator(CART_PREVIEW).count() > 0,
                               timeout_s)

    def cart_preview_row_count(self) -> int:
        """Сколько позиций показывает поповер корзины."""
        return self.page.locator(CART_PREVIEW_ROW).count()

    def cart_preview_total_text(self) -> str:
        """Сумма в подвале поповера (как её видит покупатель)."""
        node = self.page.locator(CART_PREVIEW_TOTAL)
        return node.first.inner_text().strip() if node.count() else ""

    def cart_preview_empty_text(self) -> str:
        """Текст пустого поповера корзины."""
        node = self.page.locator(CART_PREVIEW_EMPTY)
        return node.first.inner_text().strip() if node.count() else ""
