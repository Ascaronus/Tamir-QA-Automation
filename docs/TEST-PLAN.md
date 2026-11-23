# План тестирования

План растёт по вехам: M1 — стенд, отчётность и дымовые проверки (см. `docs/ENVIRONMENTS.md`,
`docs/REPORTING.md`), M2 — SEO-контур витрины и каталог. Разделы с номерами, на которые
ссылаются докстринги тестов (`§13`), — нормативные: тест и план не должны расходиться.
Пока зафиксирован раздел 13, остальные добавляются по мере появления вех.

## 13. SEO и индексация витрины

Правило (шаг 5 плана, на него ссылается `tests/seo/test_indexing_guard.py`): стенда не должно
быть в индексе поисковых систем. Продуктовый код закрывает страницы и заголовком
`X-Robots-Tag`, и тегом `<meta name=robots>` (`frontend/src/app/layout.tsx`), поэтому проверяем
оба механизма: один может исчезнуть при правке `next.config.ts`, второй — при правке метаданных.
Ожидаемое поведение роутинга и SEO-атрибутов взято из спецификации продукта
`openspec/specs/seo-routing/spec.md`.

### 13.1 Дополнение от 23.11.2026

В план добавлен SEO-контур витрины: закрытие non-prod от индексации, контракт редиректов
локалей и `canonical`/`hreflang`, состояние `sitemap.xml` и контракт листинга каталога.
Ожидания сверены с зондом staging 23.11.2026 — его результаты зафиксированы таблицей в 13.2,
чтобы новое поведение сборки не выглядело поломкой стенда.

### 13.2 M2: SEO и каталог (23.11.2026)

Что проверяется на стенде:

- non-prod витрина закрыта от индексации: `X-Robots-Tag` **и** `<meta name=robots>` на HTML-страницах,
  на `3xx`-редиректах и на ответах `404`. Обязательный минимум — `noindex` + `nofollow`;
  `noarchive` и `nosnippet` в текущей сборке есть, но их отсутствие не считается ошибкой окружения;
- `robots.txt`: `200`, правила читаются и при этом сайт не закрыт целиком (`Disallow: /` ловится);
- `sitemap.xml`: `200`, валидный XML, все ссылки одного происхождения (публичный домен из `QaConfig.site_origin`);
- редиректы роутинга постоянные (`308`), ведут на канонический адрес и не теряют query:
  `/` → `/rs`, `/rs/` и `/en/` → адрес без слэша, `/rs/cart` → `/rs/korpa`, `/?q=tee&page=2` → `/rs?q=tee&page=2`;
- неизвестная локаль (`/sr`, `/RS`, `/de`, `/xx`, `/sr-Latn-ME`) → `404`, без «тихого» дубля;
- `canonical` и `hreflang` никогда не указывают на хост стенда: `frontend/src/lib/seo.ts` берёт
  `PUBLIC_SITE_ORIGIN`, поэтому `NEXT_PUBLIC_SITE_URL=https://test.tamir.rs` на них не влияет;
  у bilingual-страниц есть пара `sr`/`en`, карточка товара имеет `JSON-LD` и живую `en`-версию;
- каталог: `/rs/catalog` открывается и его `canonical` на публичном домене, ссылки на товары из
  листинга отвечают `200`, `?page=99` → `404`, `?category_id=bogus` → `404`, страница поиска
  `?q=tee` закрыта `noindex`; адрес с хвостовым слэшем `/rs/catalog/` → `308` на `/rs/catalog`.
  Страницы-списка по адресу `/rs/products` в продукте нет (`[countryCode]/products/[handle]` —
  только карточка), поэтому в SEO-наборе его нет. Публичный sr-адрес каталога не
  локализуется: в `frontend/src/lib/i18n/paths.ts` (`servicePaths`) накрываются только
  `account*` и `cart` → `korpa`, поэтому `/rs/katalog` обязан отвечать `404` — иначе
  у листинга появляется второй индексируемый адрес.

Файлы и маркеры:

| Файл | Маркер | Что охраняет |
| --- | --- | --- |
| `tests/seo/test_indexing_guard.py` | `seo` | non-prod не попадает в индекс: `X-Robots-Tag` + `<meta name=robots>` на `200`, `3xx` и `404`; `robots.txt` |
| `tests/seo/test_locale_routing_seo.py` | `seo` | редиректы локалей, `canonical`, `hreflang`, `JSON-LD` карточки |
| `tests/catalog/test_catalog_seo.py` | `catalog` | `sitemap.xml` и контракт листинга: пагинация, фильтр по категории, поиск |
| `qa/seo.py` | — | парсеры `X-Robots-Tag`, `<meta>`/`canonical`/`hreflang`, `robots.txt`, `sitemap.xml` (без BeautifulSoup) |
| `tests/unit/test_seo_parsing.py` | `unit` | те же парсеры офлайн — ожидания M2 не зависят от доступности стенда |

Факт зонда (23.11.2026) ↔ как проверяется:

| Факт со стенда | Проверка |
| --- | --- |
| `/` → `308 /rs`, `/rs/` и `/en/` → `308` без слэша | `test_locale_redirects_use_308`, `test_root_redirects_to_default_locale`, `test_trailing_slash_redirects_to_canonical_url` |
| `/rs/cart` → `308 /rs/korpa` | `test_short_slug_redirects_to_canonical_url` |
| `/de`, `/sr`, `/xx` → `404` | `test_unknown_locale_has_no_page`, `test_missing_pages_respond_404_and_are_not_indexable` |
| `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet` на всех ответах, включая `3xx` и `404` | `test_html_pages_closed_for_indexing`, `test_redirects_are_closed_for_indexing` |
| `robots.txt` = `User-Agent: *` + `Allow: /`, без `Sitemap:` | `test_robots_txt_is_readable_and_does_not_block_the_site`, `test_robots_txt_references_sitemap` |
| `sitemap.xml` → `200`, валидный XML, пустой `<urlset/>` | `test_sitemap_is_valid_xml_from_one_origin`, `test_sitemap_lists_localized_storefront_pages` |
| юридическая sr-страница живёт без префикса локали: `/privacy` → `200`, `/rs/privacy` → `404` | `SELF_CANONICAL_ROUTES`/`BILINGUAL_ROUTES` в `test_locale_routing_seo.py` |
| `canonical` и `hreflang` — только `https://tamir.rs` | `test_seo_tags_never_point_to_staging_host`, `test_pages_self_canonicalize_on_public_origin`, `test_hreflang_covers_both_locales` |

Продуктовые разрывы, найденные QA (прогон не красят, заводятся как продуктовые баги):

| Разрыв | Где в продукте | Тест | Как помечен |
| --- | --- | --- | --- |
| `sitemap.xml` на стенде пустой (`<urlset/>`): фиды берутся с боевого `api.tamir.rs`, на стенде он недоступен | `frontend/src/app/sitemap.ts` (альтернативы `staging.api.tamir.rs` в коде нет) | `tests/catalog/test_catalog_seo.py::test_sitemap_lists_localized_storefront_pages` | `xfail(strict=False)` |
| в `robots.txt` нет строки `Sitemap:` — поиск не узнаёт про sitemap | `frontend/src/app/robots.ts` | `tests/seo/test_indexing_guard.py::test_robots_txt_references_sitemap` | `pytest.xfail` внутри теста |
| `canonical` у `/rs/catalog` указывает на `…/rs` (в макете лежит canonical главной) | layout каталога | `tests/catalog/test_catalog_seo.py::test_catalog_page_is_available_and_on_public_origin` | ожидание сужено до «публичный домен», наблюдение |

Как это попадает в TCMS и Allure:

- области TCMS берутся из маркера (`scripts/tcms_seed.py::AREAS`): `seo` → `Storefront SEO` (P2),
  `catalog` → `Catalog & Search` (P3); по одному кейсу на тест, статусы после прогона проставляет
  `scripts/tcms_sync_results.py` (см. `docs/TCMS.md`);
- в Allure проверки видны по маркерам `seo` и `catalog`, продуктовые разрывы остаются `xfail`
  с указанием файла продукта в причине; результаты публикуются на `https://qa.tamir.rs`
  по схеме из `docs/REPORTING.md`.
