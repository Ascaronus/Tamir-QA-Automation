"""API-контракт поиска каталога: ``GET /store/products`` с параметром ``q``.

Отдельной ручки поиска на стэнде нет: поиск витрины (``form.catalog-search``
в шапке, см. ``tests/e2e/test_search.py``) идёт на ту же ``/store/products``,
только с ``q`` в query-строке. Файл называется ``test_search_api.py``, а не
``test_search.py``: каталоги тестов идут без ``__init__.py``, и два одноимённых
файла (в ``tests/api`` и ``tests/e2e``) ломали бы коллекцию pytest
(``import file mismatch``) — падали бы оба прогона CI, и api, и e2e.

Замерено запросами к ``test.tamir.rs`` (27.11.2026), гостем, только чтение.
Каталог стэнда — 16 товаров, все заголовки сербские и латиницей
(«Crni polusezonski šešir», «Muška zimska kapa u plavoj boji»). Счётчики
сверялись повторными прогонами: они стабильны.

* ``q`` отсутствует или пустой → 200 и **весь каталог**: ``count`` совпадает с
  ``count`` запроса без ``q``, ошибки нет.
* Нет совпадений → 200, ``count: 0`` и пустой ``products``; 404 для пустой
  выдачи не используется.
* ``q`` ищёт подстрокой и без учёта регистра: ``šešir`` = ``Šešir`` = ``ŠEŠIR``
  (9 товаров), ``plav`` находит 5 товаров, где в заголовке «plavoj»/«plave».
* Диакритика значима: ``sesir`` без чеканки не находит ничего, ``žešir`` — тоже.
* ``q`` длиннее поля поиска (``maxlength="200"``) не отсекается и не роняет API:
  200 и пустая выдача.
* Кириллица и румынская диакритика → 200 и пустая выдача: поиск не
  трансlитерирует. Это не ошибка, но для магазина, где поиск ожидают по-русски
  или по-румынски, — кандидат в баги.
* Пробельные и символьные ``q`` зафиксированы как есть: ``q=" "`` (один пробел)
  → **весь каталог**, ``q="  "`` → пусто, ``q="."`` и ``q="_"`` → весь каталог,
  ``q="-"`` → 6 товаров, в заголовках которых дефиса нет.
* Спецсимволы ``%`` и ``_`` работают как маски: ``q="%"`` → весь каталог,
  ``q="šešir%"`` → те же 9, что и ``šešir``.
* ``color``/``min_price``/``tags`` Store API не понимает → 400 (это фильтры
  витрины); пересечение проверяется на ``handle`` и ``title``.

Все факты — фактическое поведение стэнда, а не ожидаемое; отклонения помечены
в докстрингах и собраны в отчёте.
"""

import pytest

from qa.medusa_store import MedusaStoreClient

pytestmark = [pytest.mark.api]

BASELINE = 16
"""Товаров в каталоге стэнда на 27.11.2026 (``/store/products`` без ``q``)."""

HANDLE = "crni-polusezonski-sesir"
"""Заголовок «Crni polusezonski šešir» — 9 товаров содержат подстроку ``šešir``."""

FIELDS = "id,title,handle"


@pytest.fixture(scope="module")
def region_id(api_client) -> str:
    """Регион стэнда: ``/store/products`` без ``region_id`` ничего не отдаёт."""
    return api_client.first_region()["id"]


def _search(api_client, region_id: str, q: str | None = None, **params) -> tuple:
    """``GET /store/products`` с ``q`` и произвольными фильтрами → (код, тело)."""
    query = {"region_id": region_id, "fields": FIELDS, **params}
    if q is not None:
        query["q"] = q
    response = api_client.client.get("/store/products", params=query)
    return response.status_code, response.json()


def _ids(body: dict) -> list:
    """``id`` товаров выдачи — чтобы сравнивать страницы между собой."""
    return [product["id"] for product in (body.get("products") or [])]


def test_missing_or_empty_query_returns_whole_catalog(api_client: MedusaStoreClient, region_id: str, steps):
    """``q`` отсутствует или пустой → 200 и весь каталог, без ошибки."""
    with steps("Запрос без ``q``", f"200, count = {BASELINE}"):
        code, body = _search(api_client, region_id)
        assert code == 200, body
        assert body.get("count") == BASELINE, body
        baseline = body["count"]

    with steps("Запрос с пустым ``q``", "200, тот же count, что без ``q``"):
        code, body = _search(api_client, region_id, q="")
        assert code == 200, body
        assert body.get("count") == baseline, body
        assert len(body.get("products") or []) == baseline, body

    with steps("Запрос ``?q=`` без значения", "200, не ошибка"):
        # ``requests`` выбрасывает параметры со значением ``None``, поэтому строку
        # собираем руками — иначе проверяли бы запрос без ``q`` вообще.
        query = f"region_id={region_id}&fields={FIELDS}&q="
        response = api_client.client.get("/store/products", params=query)
        assert response.status_code == 200, response.text[:300]
        assert response.json().get("count") == baseline, response.text[:300]


def test_no_match_returns_empty_list_not_404(api_client: MedusaStoreClient, region_id: str, steps):
    """Нет совпадений → 200, ``count: 0`` и пустой список, а не 404."""
    for q in ("zzzqqqxxx", "takav_proizvod_na_stendu_nema"):
        with steps(f"q={q}", "200 и пустая выдача"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, body
            assert body.get("count") == 0, body
            assert body.get("products") == [], body


def test_query_with_unknown_collection_returns_nothing(api_client: MedusaStoreClient, region_id: str, steps):
    """``q`` + несуществующая коллекция → 200 и пусто: фильтрация не падает."""
    with steps("q=šešir + collection_id несуществующей коллекции", "200, count = 0"):
        code, body = _search(api_client, region_id, "šešir", collection_id="coll_ne_schestvuet")
        assert code == 200, body
        assert body.get("count") == 0, body
        assert body.get("products") == [], body


def test_search_is_substring_and_case_insensitive(api_client: MedusaStoreClient, region_id: str, steps):
    """Подстрока и регистронезависимость: ``šešir`` = ``ŠEŠIR``, ``plav`` внутри слов."""
    with steps("``šešir`` и его регистры", "200, по 9 товаров, одни и те же id"):
        found = {}
        for q in ("šešir", "Šešir", "ŠEŠIR", "šeŠIr"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == 9, (q, body)
            found[q] = _ids(body)
        assert len(found["šešir"]) == 9, found["šešir"]
        for q, ids in found.items():
            assert sorted(ids) == sorted(found["šešir"]), (q, ids)

    with steps("``plav`` находит слово «plavoj» внутри заголовка", "200, 5 товаров"):
        code, body = _search(api_client, region_id, "plav")
        assert code == 200, body
        assert body.get("count") == 5, body
        assert all("plav" in (p.get("title") or "").lower() for p in body.get("products") or []), body

    with steps("``Crni polusezonski šešir`` целиком", "200, ровно 1 товар"):
        code, body = _search(api_client, region_id, "Crni polusezonski šešir")
        assert code == 200, body
        assert body.get("count") == 1, body
        assert body["products"][0]["handle"] == HANDLE, body


def test_diacritics_and_overlong_query_find_nothing(api_client: MedusaStoreClient, region_id: str, steps):
    """Фиксирует фактическое поведение; кандидат в баги.

    ``šešir`` находит 9 товаров, но ``sesir`` без чеканки — ноль, ``žešir`` —
    ноль, ``šešir`` + 40 букв — ноль, заголовок с пробелами по краям — ноль.
    Поиск сравнивает строки побуквенно: ни игнорировать диакритику, ни искать
    по словам он не умеет. Покупателю, который печатает ``sesir`` (так же
    записан handle товара), ничего не найдётся.
    """
    for q, expected in (
        ("sesir", 0),
        ("SEŠIR", 0),
        ("žešir", 0),
        ("šešir" + "a" * 40, 0),
        (" Crni polusezonski šešir ", 0),
    ):
        with steps(f"q={q[:40]!r}", f"200, count = {expected}"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == expected, (q, body)
            assert body.get("products") == [], (q, body)


def test_overlong_query_is_not_truncated_or_rejected(api_client: MedusaStoreClient, region_id: str, steps):
    """``q`` длиннее ``maxlength`` поля поиска → 200, а не 400/5xx; поле не отсекает."""
    with steps("q из 1000 символов", "200, count = 0"):
        code, body = _search(api_client, region_id, "a" * 1000)
        assert code == 200, body
        assert body.get("count") == 0, body

    with steps("q из 3000 символов", "200, count = 0: лимита по длине нет"):
        code, body = _search(api_client, region_id, "a" * 3000)
        assert code == 200, body
        assert body.get("count") == 0, body

    with steps("``šešir`` + 1000 символов", "200, count = 0: хвост не отсекается"):
        code, body = _search(api_client, region_id, f"šešir{'a' * 1000}")
        assert code == 200, body
        assert body.get("count") == 0, body

    with steps("1000 символов + ``šešir``", "200, count = 0: префикс тоже не отсекается"):
        code, body = _search(api_client, region_id, f"{'a' * 1000}šešir")
        assert code == 200, body
        assert body.get("count") == 0, body


def test_blank_and_punctuation_queries(api_client: MedusaStoreClient, region_id: str, steps):
    """Фиксирует фактическое поведение; кандидат в баги.

    Пробельные и символьные запросы ведут себя некак: ``q=" "`` (один пробел)
    возвращает **весь каталог**, а ``q="  "`` — пусто; ``q="."``, ``q="_"`` и
    ``q="%"`` тоже отдают весь каталог; ``q="-"`` — 6 товаров, в заголовках
    которых дефиса нет (исключения — ``Tamnoplava kapa-balaklava``). ``q="*"``
    и ``q="\\t"`` пустые. Похоже на подстановку ``q`` в SQL-маску без
    экранирования и с обрезкой ровно одного пробела по краям. Проверено
    повторными прогонами — числа стабильны. Ошибки нет (везде 200), но выдачу
    не объяснить ничем, кроме дефекта.
    """
    cases = (
        (" ", BASELINE),
        ("  ", 0),
        ("\t", 0),
        ("\n", 5),
        (".", BASELINE),
        ("_", BASELINE),
        ("%", BASELINE),
        ("-", 6),
        ("*", 0),
    )
    for q, expected in cases:
        with steps(f"q={q!r}", f"200, count = {expected}"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == expected, (q, body)
            assert len(body.get("products") or []) == expected, (q, body)


def test_wildcards_in_query_are_not_escaped(api_client: MedusaStoreClient, region_id: str, steps):
    """Фиксирует фактическое поведение; кандидат в баги.

    ``%`` и ``_`` в ``q`` работают как SQL-маски, а не как символы запроса:
    ``q="%"`` и ``q="_"`` отдают весь каталог, ``q="šešir%"`` — те же 9
    товаров, что и ``šešir``. Значит ``q`` попадает в запрос как есть.
    """
    with steps("``%`` и ``_`` как запрос", "200, count = весь каталог"):
        for q in ("%", "_"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == BASELINE, (q, body)

    with steps("``šešir%``", "200, count = 9, как у ``šešir``"):
        code, body = _search(api_client, region_id, "šešir%")
        assert code == 200, body
        assert body.get("count") == 9, body


def test_cyrillic_query_is_not_an_error(api_client: MedusaStoreClient, region_id: str, steps):
    """Фиксирует фактическое поведение; кандидат в баги.

    Заголовки стэнда сербские и записаны латиницей, транслитерации в поиске
    нет: ``q=мушкарац``/``q=кепа``/``q=шешир`` и смесь латиницы с кириллицей
    дают 200 с пустой выдачей, тогда как латиница находит (``muška`` → 8,
    ``kapa`` → 10). Без чеканки не находится и латиница: ``muskarac`` → 0.
    """
    with steps("Латиничные заголовки находятся", "200, muška → 8, kapa → 10"):
        for q, expected in (("muška", 8), ("kapa", 10)):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == expected, (q, body)

    with steps("Кириллица и смешанный алфавит", "200, count = 0, пустой список"):
        for q in ("мушкарац", "кепа", "шешир", "kaпа"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == 0, (q, body)
            assert body.get("products") == [], (q, body)

    with steps("Та же латиница без чеканки", "200, count = 0"):
        code, body = _search(api_client, region_id, "muskarac")
        assert code == 200, body
        assert body.get("count") == 0, body


def test_romanian_queries_are_not_an_error(api_client: MedusaStoreClient, region_id: str, steps):
    """Фиксирует фактическое поведение; кандидат в баги.

    Румынские слова и румынская диакритика (``cămașă``, ``pantaloni``,
    ``căciulă``, ``šeșir`` — румынское ``ș`` вместо сербского ``š``) не находят
    ничего: ``ș`` не приравнивается к ``š``. Каталог стэнда сербский, поэтому
    счётчик честно пустой; ошибки нет.
    """
    for q in ("cămașă", "pantaloni", "căciulă", "šeșir"):
        with steps(f"q={q}", "200, count = 0, пустой список"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            assert body.get("count") == 0, body
            assert body.get("products") == [], body


def test_quotes_markup_and_emoji_do_not_break_search(api_client: MedusaStoreClient, region_id: str, steps):
    """Кавычки, SQL-подобные строки, ``<script>`` и эмодзи → 200, ни одного 5xx."""
    hostile = (
        "\"šešir\"",
        "'",
        '"',
        "š\"eš\"ir",
        "<script>alert(1)</script>",
        "'; DROP TABLE products; --",
        "šešir OR 1=1",
        "šešir --",
        "šešir UNION SELECT",
        "šešir AND",
        "šešir*",
        "šešir 🔥",
        "🔥",
        "\U0001f452\U0001f9e2\U0001f9e6",
    )
    with steps("Подозрительные ``q``", "200 и пустая выдача, ни одного 5xx"):
        for q in hostile:
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, code, body)
            assert body.get("count") == 0, (q, body)
            assert body.get("products") == [], (q, body)

    with steps("``šešir`` в кавычках ищется как символы, а не как фраза", "200, count = 0"):
        code, body = _search(api_client, region_id, '"šešir"')
        assert code == 200, body
        assert body.get("count") == 0, body

    with steps("Каталог после подозрительных запросов", "200, count тот же"):
        code, body = _search(api_client, region_id)
        assert code == 200, body
        assert body.get("count") == BASELINE, body


def test_query_combined_with_filters_is_intersection(api_client: MedusaStoreClient, region_id: str, steps):
    """``q`` + фильтр → пересечение; ``color``/``min_price`` Store API не знает."""
    with steps(f"``q=šešir`` + ``handle={HANDLE}``", "200, ровно 1 товар"):
        code, body = _search(api_client, region_id, "šešir", handle=HANDLE)
        assert code == 200, body
        assert body.get("count") == 1, body
        assert body["products"][0]["handle"] == HANDLE, body

    with steps("``q=kapa`` + handle товара со ``šešir``", "200, count = 0: это AND, а не OR"):
        code, body = _search(api_client, region_id, "kapa", handle=HANDLE)
        assert code == 200, body
        assert body.get("count") == 0, body

    with steps("``title`` — точное совпадение, не подстрока", "200: заголовок целиком → 1, часть → 0"):
        code, body = _search(api_client, region_id, "šešir", title="Crni polusezonski šešir")
        assert code == 200, body
        assert body.get("count") == 1, body
        code, body = _search(api_client, region_id, "šešir", title="šešir")
        assert code == 200, body
        assert body.get("count") == 0, body

    with steps("Фильтры витрины в Store API", "400 ``Unrecognized fields``"):
        for field in ("color", "min_price", "max_price", "tags", "material", "sku"):
            code, body = _search(api_client, region_id, "šešir", **{field: "x"})
            assert code == 400, (field, code, body)
            assert "Unrecognized fields" in str(body.get("message", "")), (field, body)


def test_limit_is_page_size_and_count_is_total(api_client: MedusaStoreClient, region_id: str, steps):
    """``count`` — размер всей выдачи, ``products`` — только страница."""
    with steps("``q=šešir`` без ``limit``", "200, count = 9 и 9 товаров"):
        code, full = _search(api_client, region_id, "šešir")
        assert code == 200, full
        assert full.get("count") == 9, full
        assert len(full.get("products") or []) == 9, full
        total = full["count"]

    with steps("``limit=2``", "count = 9, но 2 товара, ``limit`` echoed"):
        code, page = _search(api_client, region_id, "šešir", limit=2)
        assert code == 200, page
        assert page.get("count") == total, page
        assert page.get("limit") == 2, page
        assert len(page.get("products") or []) == 2, page
        first = _ids(page)

    with steps("``limit=2&offset=2``", "count = 9, следующие 2, без повторов"):
        code, page = _search(api_client, region_id, "šešir", limit=2, offset=2)
        assert code == 200, page
        assert page.get("count") == total, page
        assert page.get("offset") == 2, page
        second = _ids(page)
        assert len(second) == 2, page
        assert set(first).isdisjoint(second), (first, second)

    with steps("``offset`` за концом выдачи", "200, count = 9 и пустая страница"):
        code, page = _search(api_client, region_id, "šešir", offset=20)
        assert code == 200, page
        assert page.get("count") == total, page
        assert page.get("products") == [], page

    with steps("``limit=0`` и ``limit=101``", "200: 0 товаров и весь ответ, count тот же"):
        code, page = _search(api_client, region_id, "šešir", limit=0)
        assert code == 200, page
        assert page.get("count") == total, page
        assert page.get("products") == [], page
        code, page = _search(api_client, region_id, "šešir", limit=101)
        assert code == 200, page
        assert page.get("count") == total, page
        assert len(page.get("products") or []) == 9, page


def test_count_agrees_with_page_for_every_useful_query(api_client: MedusaStoreClient, region_id: str, steps):
    """Без пагинации ``count`` == длина ``products``, у выдачи есть ``title`` и ``handle``."""
    for q in ("šešir", "kapa", "plav", "muška", "Crni", ""):
        with steps(f"q={q!r}", "count == len(products), поля title/handle заполнены"):
            code, body = _search(api_client, region_id, q)
            assert code == 200, (q, body)
            products = body.get("products") or []
            assert body.get("count") == len(products), (q, body.get("count"), len(products))
            for product in products:
                assert product.get("title"), (q, product)
                assert product.get("handle"), (q, product)


def test_invalid_numeric_and_sort_parameters(api_client: MedusaStoreClient, region_id: str, steps):
    """Фиксирует фактическое поведение; кандидат в баги.

    ``limit=abc`` → 400 с внятным сообщением, а ``limit=-1`` и ``order=x`` →
    **500** ``An unknown error occurred``. Так что 5xx на стэнде всё-таки
    бывают, но не от ``q``, а от некорректной пагинации и сортировки: сам ``q``
    их не провоцирует (см. остальные проверки файла).
    """
    with steps("``limit=abc``", "400 про ожидаемый тип числа"):
        code, body = _search(api_client, region_id, "šešir", limit="abc")
        assert code == 400, body
        assert "number" in str(body.get("message", "")), body

    with steps("``limit=-1``", "500 (факт, не норма)"):
        code, body = _search(api_client, region_id, "šešir", limit=-1)
        assert code == 500, (code, body)

    with steps("``order=x``", "500 (факт, не норма)"):
        code, body = _search(api_client, region_id, "šešir", order="x")
        assert code == 500, (code, body)

    with steps("Каталог после битых параметров", "200, count не изменился"):
        code, body = _search(api_client, region_id)
        assert code == 200, body
        assert body.get("count") == BASELINE, body
