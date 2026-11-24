# Tamir QA Automation

Автотесты QA для e-commerce платформы Tamir (витрина Next.js + Medusa 2.13.6).
Набор вырос из read-only дымовых проверок в **проверку пользовательских сценариев**:
дымовые проверки и контракты API (M1), браузерные E2E-путешествия покупателя
(Playwright, M2) и сценарии оформления заказа через витринный API (M3). Сценарии
работают со стендом на запись — создают корзины, заказы и тестовых покупателей, —
поэтому каждая такая сущность помечается и убирается (`docs/TEST-PLAN.md`, §16).

Репозиторий **публичный** (портфолио). Поэтому в нём нет и не будет секретов,
паролей, API-ключей и учётных данных: чувствительные значения существуют только
под **именами** (`MEDUSA_PUBLISHABLE_API_KEY`, `MEDUSA_ADMIN_EMAIL`, `TCMS_PASSWORD`)
и приходят из GitHub Actions secrets / переменных окружения CI.

## Стек

| Задача | Инструмент |
| --- | --- |
| Язык | Python 3.12 |
| Тестовый фреймворк | pytest |
| Браузерный E2E | Playwright (chromium, headless) |
| Отчёты | Allure (`allure-pytest`), шаги тестов — через fixture `steps` (`qa/steps.py`) |
| Управление тестированием | Kiwi TCMS (`https://tcms.tamir.rs`), JSON-RPC `/json-rpc/` — см. [docs/TCMS.md](docs/TCMS.md) |

## Запуск локально

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium   # нужен для e2e-сценариев
uv run pytest
```

Если активировать окружение (`source .venv/bin/activate`), команды выглядят
проще: `playwright install chromium` и `pytest`.

Выборочный запуск по маркерам (`unit`, `smoke`, `api`, `seo`, `catalog`, `e2e`,
`scenario`, `checkout`, `tcms` — они объявлены в `pytest.ini`):

```bash
pytest -m "unit or smoke or api or seo or catalog"  # как в job qa-smoke
pytest -m "e2e or scenario"                         # как в job qa-e2e
pytest -m smoke                  # только витрина
pytest -m api                    # только Medusa API (в том числе сценарии оформления)
pytest -m seo                    # закрытие стенда от индексации
pytest -m catalog                # поведение витрины: каталог, карточка, корзина, локализация
pytest -m e2e                    # браузерные пути покупателя
pytest -m checkout                # оформление заказа (браузер и API)
pytest -m tcms                   # интеграция с Kiwi TCMS, вручную (см. docs/TCMS.md)
```

Браузерные и сценарные прогоны дополнительно требуют доступ к админке Medusa
(`MEDUSA_ADMIN_EMAIL`, `MEDUSA_ADMIN_PASSWORD` — отмена тестовых заказов) и,
для проверки писем, к Mailpit (`MAILPIT_URL`, `MAILPIT_USER`, `MAILPIT_PASSWORD`;
на стенде он ещё и за HTTP Basic). Без этих переменных соответствующие проверки
скипаются, а не падают.

Для `tcms`-проверок нужны переменные `TCMS_URL`, `TCMS_USER`, `TCMS_PASSWORD`
(только из локального `.env` или из GitHub Actions secrets — в репозитории их нет).
Без учётки эти тесты корректно скипаются, поэтому обычный прогон от TCMS не зависит.

Allure-результаты складываются в `.tmp/allure-results` (см. `pytest.ini`),
локально их можно посмотреть командой `allure serve .tmp/allure-results`.

## Окружения

Тесты имеют право ходить **только на стенд** (`https://test.tamir.rs`,
`https://test-api.tamir.rs`). Список целей — в `docs/ENVIRONMENTS.md`.
Ключевое правило зашито в коде (`qa/config.py`, `qa/http.py`): хосты вида
`tamir.rs`, `www.tamir.rs`, `api.tamir.rs` (прод) запрещены, любой запрос туда
прерывается с `RuntimeError` — в том числе для сценариев, которые пишут данные.

## Где смотреть результаты

| Что | Где |
| --- | --- |
| Отчёты прогонов | `https://qa.tamir.rs` (последний — `https://qa.tamir.rs/latest/`) |
| Тест-планы и тест-кейсы | Kiwi TCMS, `https://tcms.tamir.rs` |
| Находки по витрине и API, переданные разработке | `docs/FINDINGS-frontend-backend.md` |

Схема «как прогон попадает на `qa.tamir.rs`» описана в `docs/REPORTING.md`.

## Документация

| Документ | О чём |
| --- | --- |
| `docs/ENVIRONMENTS.md` | куда имеют право ходить прогоны, как устроен guard по хостам, маркеры и тестовые данные |
| `docs/TEST-PLAN.md` | план тестирования по вехам: §13 — SEO и каталог, §14 — браузерные E2E, §15 — сценарии оформления через API, §16 — тестовые данные и уборка |
| `docs/REPORTING.md` | как прогон попадает на `qa.tamir.rs`: Allure, шаги тестов, `runs.json`, права, почему версии действий зафиксированы |
| `docs/TCMS.md` | Kiwi TCMS на `tcms.tamir.rs`: JSON-RPC `/json-rpc/`, клиент, сидинг каталога, `TCMS_*`, маркер `tcms`, отличия API 17.2 |
| `docs/FINDINGS-frontend-backend.md` | находки на стыке фронтенда и бэкенда, подтверждённые на стенде, и как их обходят тесты |

## Что покрывается

- **M1 — дымовые проверки и контракты (не меняют данных):**
  - smoke витрины: `robots.txt`, `sitemap.xml`, редирект корня на локаль, `/rs/catalog`;
  - контракт Medusa: отсутствие `x-publishable-api-key` даёт `400 not_allowed`,
    роут `/api/health` отсутствует (`404`) — health-пробы опираются на рабочую точку
    `/health` (`200`);
  - закрытие стенда от индексации (`noindex`, `robots.txt` без `Sitemap:`);
  - поведение витрины: список каталога, карточка товара, корзина и оформление (SSR),
    поиск, локализация `sr`/`en`;
  - юнит-проверки guard'а по хостам, HTTP-клиента, парсеров и клиента Kiwi TCMS.
- **M2 — браузерные пути покупателя (Playwright):**
  - путь гостя: каталог → карточка товара → корзина → двухшаговое оформление →
    страница заказа → отмена заказа админкой (`tests/e2e/test_guest_shop_journey.py`);
  - путь зарегистрированного покупателя: вход готового тестового аккаунта, заказ со
    страницы товара, отмена — `tests/e2e/test_registered_shop_journey.py`; сама
    регистрация покрыта только до шага с капчей (Cloudflare Turnstile не выдаёт
    токен headless-браузеру, см. `docs/FINDINGS-frontend-backend.md`).
- **M3 — сценарии оформления через витринный API (без браузера):**
  `tests/api/test_checkout_scenarios.py`: заказ гостя доходит до магазина и письма,
  количество сверх остатка отвергается, у каждого товара каталога работает страница.
- **CI:** `qa-unit` (GitHub-hosted, на pull request) → `qa-smoke` (self-hosted runner
  на staging, unit + smoke + api + seo + catalog) → `qa-e2e` (self-hosted, e2e +
  scenario, и уборка тестовых данных после него) → `qa-report` (GitHub-hosted, там
  есть Java) → `qa-publish` (выкладывает отчёт в каталог отчётов на staging).
- **Kiwi TCMS:** JSON-RPC-клиент, сидинг каталога и синхронизация результатов из
  Allure в тестовый прогон (`scripts/tcms_seed.py`, `scripts/tcms_sync_results.py`),
  читающие проверки (`pytest -m tcms`). Seed и sync выполняются в CI на прогонах
  `main` в джобе `qa-report`, куда стекаются результаты обоих прогонов — и `qa-smoke`,
  и `qa-e2e`. В каталог TCMS попадают только тесты пользовательских сценариев
  (24 из 142): что именно и почему, описано в `docs/TCMS.md`.

## Структура

```
qa/            конфигурация, HTTP-клиент, обёртки витринного и админского API,
               клиент Kiwi TCMS, перехватчик почты, правила витрины, шаги тестов
  seo.py       парсеры `robots.txt`, `X-Robots-Tag` и `<meta name=robots>`
  ui.py        правила поиска элементов на витрине (роли, подписи-регулярки)
  steps.py     fixture `steps`: текст шагов уходит в Allure и в Kiwi TCMS
scripts/       сидинг каталога Kiwi TCMS, перенос результатов в TCMS, уборка QA-данных
tests/
  smoke/       read-only проверки доступности витрины
  api/         контракты Medusa API, сценарии оформления, проверки Kiwi TCMS (`tcms`)
  e2e/         браузерные пути покупателя (`e2e`, `checkout`)
  seo/         закрытие non-prod от индексации (маркер `seo`)
  catalog/     поведение витрины: каталог, карточка, корзина, локализация (`catalog`)
  unit/        чистые юнит-проверки guard'а, парсеров, HTTP-клиента и клиента TCMS
docs/          окружения, план тестирования, отчётность, Kiwi TCMS, находки
```
