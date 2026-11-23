# Tamir QA Automation

Автотесты QA для e-commerce платформы Tamir (витрина + Medusa API). M1 — это
**read-only smoke проверки и API-контракты**: никаких мутаций данных, никаких
сценариев оформления заказа.

Репозиторий **публичный** (портфолио). Поэтому в нём нет и не будет секретов,
паролей, API-ключей и учётных данных: чувствительные значения существуют только
под **именами** (`MEDUSA_PUBLISHABLE_API_KEY`) и приходят из GitHub Actions
secrets / переменных окружения CI.

## Стек

| Задача | Инструмент |
| --- | --- |
| Язык | Python 3.12 |
| Тестовый фреймворк | pytest |
| Браузерный E2E | Playwright (M2) |
| Отчёты | Allure (`allure-pytest`) |
| Управление тестированием | Kiwi TCMS (`https://tcms.tamir.rs`), JSON-RPC `/json-rpc/` — см. [docs/TCMS.md](docs/TCMS.md) |

## Запуск локально

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium   # нужен для e2e в M2; в M1 не запускается
uv run pytest
```

Если активировать окружение (`source .venv/bin/activate`), команды выглядят
проще: `playwright install chromium` и `pytest`.

Выборочный запуск по маркерам (`unit`, `smoke`, `api`, `tcms` — они объявлены в `pytest.ini`):

```bash
pytest -m "unit or smoke or api" # как в CI
pytest -m smoke                  # только витрина
pytest -m api                    # только Medusa API
pytest -m tcms                   # интеграция с Kiwi TCMS, вручную (см. docs/TCMS.md)
```

Для `tcms`-проверок нужны переменные `TCMS_URL`, `TCMS_USER`, `TCMS_PASSWORD`
(только из локального `.env` или из GitHub Actions secrets — в репозитории их нет).
Без учётки эти тесты корректно скипаются, поэтому обычный прогон от TCMS не зависит.

Allure-результаты складываются в `.tmp/allure-results` (см. `pytest.ini`),
локально их можно посмотреть командой `allure serve .tmp/allure-results`.

## Окружения

В M1 тесты работают **только со стендом**. Список целей — в `docs/ENVIRONMENTS.md`.
Ключевое правило зашито в коде (`qa/config.py`, `qa/http.py`): хосты вида
`tamir.rs`, `www.tamir.rs`, `api.tamir.rs` (прод) запрещены, любой запрос туда
прерывается с `RuntimeError`.

## Где смотреть результаты

| Что | Где |
| --- | --- |
| Отчёты прогонов | `https://qa.tamir.rs` |
| Тест-планы и тест-кейсы | Kiwi TCMS, `https://tcms.tamir.rs` |

Схема «как прогон попадает на `qa.tamir.rs`» описана в `docs/REPORTING.md`.

## Документация

| Документ | О чём |
| --- | --- |
| `docs/ENVIRONMENTS.md` | куда имеют право ходить прогоны и как устроен guard по хостам |
| `docs/REPORTING.md` | как прогон попадает на `qa.tamir.rs`: Allure, `runs.json`, права, почему версии действий зафиксированы |
| `docs/TCMS.md` | Kiwi TCMS на `tcms.tamir.rs`: JSON-RPC `/json-rpc/`, клиент, сидинг каталога, `TCMS_*`, маркер `tcms`, отличия API 17.2 |

## Область M1

- smoke витрины: `robots.txt`, `sitemap.xml`, заголовок страницы каталога,
  редирект корня на локаль;
- контракт Medusa: отсутствие `x-publishable-api-key` даёт `400 not_allowed`,
  роут `/api/health` отсутствует (`404`) — health-пробы опираются на рабочую точку
  `/health` (`200`);
- юнит-проверки guard'а по хостам;
- CI: `qa-smoke` (self-hosted runner на staging) → `qa-report` (GitHub-hosted,
  там есть Java) → `qa-publish` (выкладывает отчёт в каталог отчётов на staging).

Интеграция с Kiwi TCMS работает: JSON-RPC-клиент, сидинг каталога и синхронизация
результатов из Allure в тестовый прогон (`scripts/tcms_sync_results.py`), читающие
проверки (`pytest -m tcms`). Seed и sync выполняются в CI на прогонах `main`.
Playwright-сценарии и почтовые проверки (Mailpit) — M2.

## Структура

```
qa/            конфигурация, HTTP-клиент, клиент Kiwi TCMS, публикация индекса прогонов
scripts/       сидинг каталога Kiwi TCMS и перенос результатов прогонов в TCMS
tests/
  smoke/       read-only проверки витрины
  api/         контракты Medusa API и проверки Kiwi TCMS (маркер `tcms`)
  unit/        чистые юнит-проверки guard'а и клиента TCMS
docs/          окружения, схема отчётности и Kiwi TCMS
```
