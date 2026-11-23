# Kiwi TCMS в этом проекте (M2)

Kiwi TCMS — система управления тестированием: тест-кейсы, тест-планы, тест-раны.
В этом репозитории с ней общается `qa/tcms.py` (клиент поверх JSON-RPC 2.0),
живёт она на стенде `https://tcms.tamir.rs`. Это направление — M2, и оно уже
работает: клиент, сидинг каталога (`scripts/tcms_seed.py`) и перенос результатов
прогонов (`scripts/tcms_sync_results.py`) вшиты в джобу `qa-smoke` на `main`.
Не закрыто только включение самих `tcms`-проверок в CI-набор — см.
«Что осталось незакрытым».

## Где живёт стенд

| Что | Значение |
| --- | --- |
| Публичный адрес | `https://tcms.tamir.rs` |
| Точка RPC | `POST https://tcms.tamir.rs/json-rpc/` (JSON-RPC 2.0) |
| Старый транспорт | `/xml-rpc/` тоже отвечает (XML-RPC-вызов доходит до TCMS и получает его ответ); клиент ходит по JSON-RPC |
| REST API | `/rest/` отвечает `404`: в 17.2 автоматизация ходит только по RPC |
| Контейнер | `kiwitcms/kiwitcms` в docker-compose-стеке `qa-stack` на staging-хосте |
| Версия | 17.2 — в compose образ указан тегом `latest`, фактическую версию даёт `KiwiTCMS.version` |
| База | `mariadb:11.4`, тот же compose-стек |
| Порт наружу | `192.168.1.143:8091` → `8443` внутри контейнера: сам TCMS на `8080` отвечает `301` на `8443`, поэтому наружу показан TLS-порт |
| Наружный доступ | тот же `cloudflared`-туннель `tamir-staging`, что и у `test.tamir.rs` / `qa.tamir.rs`; для TCMS добавлена отдельная ingress-запись `tcms.tamir.rs` |

Туннель — отдельный процесс хоста (systemd unit `tamir-staging-tunnel.service`,
конфиг `/etc/cloudflared/tamir-staging.yml`), а не контейнер в `qa-stack`.
Ingress для TCMS: `tcms.tamir.rs → https://192.168.1.143:8091` с
`originRequest.noTLSVerify: true` (сертификат в контейнере самоподписанный),
перед catch-all `http_status:404`.

IP-ограничений перед TCMS нет: allow-список из `/opt/qa-stack/nginx/default.conf`
относится только к раздаче отчётов (`qa.tamir.rs → 192.168.1.143:8090`). Cloudflare
Access для `tcms.tamir.rs` сегодня не включён, то есть адрес доступен снаружи.
Данных это не отдаёт: сам Kiwi TCMS требует логин, и без `Auth.login` любой метод
отвечает `-32098 Authentication failed` (проверено и на JSON-RPC, и на XML-RPC).
Что остаётся незакрытым в этом направлении — см. последний раздел.

Вложения TCMS (скриншоты дефектов) лежат в volume `tcms-attachments`
(`/mnt/kiwitcms` в контейнере) и раздаются тем же nginx-контейнером по адресу
`https://qa.tamir.rs/attachments/` — см. `docs/REPORTING.md`.

## Клиент

`qa/tcms.py` — одна сессия к `POST /json-rpc/` с логином через `Auth.login`.
Учётка берётся из окружения, адрес — из `QaConfig` (`cfg.rpc_url`), и обязательно
проходит проверку `require_allowed_target()`: случайный запрос на прод
(`api.tamir.rs`) поднимает `RuntimeError` ещё до обращения к TCMS.

```python
from qa.config import load_config
from qa.tcms import TcmsClient

client = TcmsClient.from_environment(load_config())

print(client.ping())                                    # версия и счётчики
print(client.call("KiwiTCMS.version"))                  # '17.2'
print(client.rows("TestCase", {"is_automated": True}))  # кейсы из каталога
print(client.find("Product", name__exact="Tamir"))      # PK продукта
print(client.status_id("PASSED"))                       # PK статуса исполнения
```

Формы вызова: `rows()` / `find()` — чтение через `<Model>.filter`,
`create()` / `update(model, pk, values)` — запись через `<Model>.create`
и `<Model>.update`. Писать в 17.2 надо именно так: `TestExecution.create`
и `TestExecution.update` заменили `TestCaseRun.*` и `TestRun.store_*` из 15.x —
на этом стенде все старые имена отвечают `-32601`.

Ошибки: `TcmsError` (транспорт, HTTP или `error` в ответе JSON-RPC, код ошибки
в атрибуте `code`), `TcmsUnavailable` (нет учётки или TCMS недоступен — тест
скипается, а не падает).

## Особенности RPC — проверено на живом стенде

| Проверка | Факт |
| --- | --- |
| `GET /json-rpc/` | `405` — только `POST` |
| `POST /xml-rpc/` | работает (тот же набор методов), но `Auth.login` нужен и там |
| `POST /xml/` | `404` — старый путь Kiwi TCMS 15 в 17.2 не отвечает |
| `id` в запросе | обязателен: без него TCMS считает запрос уведомлением и отвечает `204` с пустым телом |
| Авторизация | только `Auth.login(login, password)`: в ответе токен, который сам по себе не нужен, — важна сессионная cookie (`sessionid`, `csrftoken`) |
| Basic auth | не принимается: `-32098 Authentication failed` на оба транспорта |
| Вызов без логина | `-32098 Authentication failed when calling "<метод>"` |
| Несуществующий метод | `-32601 Method not found` |
| Неверные `params` | `-32602 Invalid parameters: ...` |
| `params` | позиционный список (`[{"name__exact": "Tamir"}]`); именованные принимаются, если имена совпадают с сигнатурой (`{"query": {...}}` — ок, `{"name__exact": ...}` — `-32602 got an unexpected keyword argument`) |
| Фильтры `query` | Django lookups: `name`, `name__exact`, `name__in`, `name__icontains` и связи (`category__name__icontains`, `value__icontains`, `script__exact`) — все проверены |
| Поля в ответах | это словари Django: у `Version` поле `value` (не `name`), у `TestCase` есть связи вида `priority__value` и `category__name` |
| TLS | сертификат выдаёт Cloudflare, обычная проверка сертификата работает — `verify=False` клиенту не нужен |
| Самоописание API | `GET /static/api/llms.txt` и `GET /static/api/openapi.json` — оба `200`, 133 метода |

Сам инстанс рекомендует помечать запросы заголовками `Referer: <Method>@<host>`
и `User-Agent: <agent>/<version>` — это метаданные для логов, на семантику
JSON-RPC они не влияют. Клиент их пока не отправляет.

Тесты клиента офлайновые: `tests/unit/test_tcms_client.py` подставляет подделку
`requests.Session` и проверяет форму запроса — `Auth.login` с позиционными
логин/паролем, обязательный `id`, позиционные `params` у `filter`, порядок
аргументов у `update` (`id` раньше значений), разбор `error` с кодом TCMS,
ответ `204` без тела, скип без учётки и `RuntimeError` на адрес вне allow-листа.

## Что в стенде сейчас

Каталог засеван, счётчики из `ping()` на момент написания: продукт 1, версии 2
(`unspecified` и `staging`), категории 3 (`--default--`, `Storefront Smoke`,
`Medusa API`), тест-кейсов 8 (все `is_automated`), тест-ран 1, билдов 3.
Исполнений (`TestExecution`) в тест-ране 8 — все `PASSED`, билд `staging`, к
каждому ведут ссылки на отчёт (`https://qa.tamir.rs/7/`,
`https://qa.tamir.rs/latest/`), то есть перенос результатов уже работал из CI.
Цифры меняются от прогона к прогону — ориентироваться надо на
`scripts/tcms_seed.py` и `scripts/tcms_sync_results.py`, а не на список ниже.

| Модель | Статус в 17.2 |
| --- | --- |
| `Product`, `Version`, `Category`, `TestPlan`, `TestRun`, `TestCase`, `Build`, `TestExecution` | методы `.filter` отвечают, записи есть |
| `Classification`, `PlanType`, `Priority` | `.filter` отвечают — из них берутся ID для создания сущностей; `Priority` — 5 записей (`P1`…`P5`, поле `value`) |
| `User` | `.filter` отвечает, учётка 1 (сервисная); содержимое этой модели в отчётах и логах не показывается |
| `TestExecutionStatus` | 8 записей: `IDLE` (1), `RUNNING` (2), `PAUSED` (3), `PASSED` (4), `FAILED` (5), `BLOCKED` (6), `ERROR` (7), `WAIVED` (8) — ID брать через `status_id()`, в коде их не хардкодить |
| `TestCaseStatus` | 4 записи: `PROPOSED` (1), `CONFIRMED` (2), `DISABLED` (3), `NEED_UPDATE` (4) |
| `TestExecution.create` / `TestExecution.update` | есть: аргументы `values` и `execution_id` соответственно |
| `TestPlan.add_case` / `TestRun.add_case` | есть: `plan_id` + `case_id` и `run_id` + `case_id` |
| `TestExecution.add_link` / `TestExecution.get_links` | есть — так к исполнению приклеивается ссылка на Allure-отчёт; у `TestCase` такого метода нет (`-32601`) |
| `TestBuild` | `-32601` — в 17.2 модель называется `Build` |
| `TestPlan.new` / `TestRun.new` | `-32601` — старые конструкторы удалены, создавать надо через `.create` |
| `user.userId` | `-32601` — такого метода в 17.2 нет |

## Каталог тест-кейсов: `scripts/tcms_seed.py`

Содержимое TCMS не заводится руками — его создаёт `scripts/tcms_seed.py`, и он
же поддерживает его в актуальном состоянии.

```bash
python scripts/tcms_seed.py --dry-run   # только показать, что попадёт в TCMS
python scripts/tcms_seed.py             # посеять и обновить
```

Как он работает:

- Каталог собирается самим pytest (`--collect-only` по маркерам `smoke` и `api`),
  поэтому в TCMS не попадает ничего, чего нет в коде; собственные `tcms`-тесты
  в каталог не пишутся.
- Ключ связи — поле `script` = pytest nodeid
  (`tests/smoke/test_storefront_health.py::test_robots_txt_is_available`). По нему
  кейсы находятся при повторном запуске, дублей нет.
- Идемпотентен: справочники, продукт, версия, категории, план, билд и ран ищутся
  по имени и создаются только при отсутствии; существующие кейсы обновляются
  (`summary`/`text`/`script`/приоритет), к плану и рану довязываются новые.
- Фиксированные имена: классификация `Tamir Balkan`, продукт `Tamir`, версия
  `staging`, тип плана `Smoke`, план `Staging Smoke — Tamir`, ран
  `Staging Smoke — базовый прогон`, билд `staging`; категории `Storefront Smoke`
  (маркер `smoke`) и `Medusa API` (маркер `api`).
- Приоритет (`P2` у смоков, `P3` у API) в 17.2 принадлежит тест-кейсу, а не
  категории — у `Category` поля `priority` в RPC-ответе нет.
- ID справочников (`TestCaseStatus`, `PlanType`, `Priority`) берутся фильтром по
  имени, а не константами.

## Результаты прогонов: `scripts/tcms_sync_results.py`

Источник правды — Allure result-файлы (`.tmp/allure-results`): их пишет
`allure-pytest` в том же прогоне, и именно они публикуются на `qa.tamir.rs`.

```bash
python scripts/tcms_sync_results.py --dry-run                       # показать план
python scripts/tcms_sync_results.py --report-url https://qa.tamir.rs/latest/
```

Как он работает:

- Каждый результат сопоставляется с кейсом каталога по `script` (pytest nodeid,
  который собирает seeder). Результат вне каталога (например, собственные
  `tcms`-тесты) в TCMS не пишется — он попадает только в счётчик «вне каталога».
- Nodeid восстанавливается из Allure label `package` + `name`
  (`tests.smoke.test_x` → `tests/smoke/test_x.py::test_y`).
- Статус маппится на справочник `TestExecutionStatus`:
  `passed→PASSED`, `failed→FAILED`, `broken→ERROR`, `skipped→BLOCKED`,
  `unknown→IDLE`; PK статусов берутся фильтром по имени, не константами.
- Исполнение ищется фильтром `TestExecution.filter {run, case}` и обновляется
  (`TestExecution.update`), иначе создаётся (`TestExecution.create`). Другого
  пути в 17.2 нет: `TestCaseRun.*` и `TestRun.store_*` отвечают `-32601`.
- К каждому исполнению добавляется ссылка на отчёт прогона
  (`TestExecution.add_link`), чтобы из карточки можно было открыть лог и скриншоты.
- В CI оба скрипта (seed и sync) выполняются в конце джобы `qa-smoke` на прогонах
  `main`, с `continue-on-error: true`: сбой Kiwi TCMS не перекрашивает зелёный
  прогон тестов, но виден в логе шага. Это уже работает: в исполнениях лежат
  ссылки на отчёт конкретного прогона (`https://qa.tamir.rs/7/`) и на
  `https://qa.tamir.rs/latest/` — их добавляют сами скрипты.

## Переменные окружения

| Переменная | Назначение |
| --- | --- |
| `TCMS_URL` | адрес Kiwi TCMS, по умолчанию `https://tcms.tamir.rs`; клиент ходит на `<TCMS_URL>/json-rpc/` |
| `TCMS_USER` | логин сервисной учётки |
| `TCMS_PASSWORD` | пароль сервисной учётки — только в секретах CI или в локальном `.env` (он в `.gitignore`) |

В `.env.example` эти переменные перечислены без значений. Локально они кладутся
в `.env` (он в `.gitignore`) — без них `scripts/tcms_seed.py` и `pytest -m tcms`
просто скипаются. В CI значения живут в секретах репозитория (`TCMS_URL`,
`TCMS_USER`, `TCMS_PASSWORD`) и приходят в джобу `qa-smoke`. Учётка — сервисный
пользователь Kiwi TCMS, заведённый при развёртывании стека.

## Как проверять

```bash
# только офлайновые проверки клиента
pytest -m unit -k tcms

# живой стенд (нужны TCMS_USER/TCMS_PASSWORD в окружении или в .env)
pytest -m tcms
```

Живые проверки — `tests/api/test_tcms_api.py`: доступность RPC и версия, что
адрес действительно `tcms.tamir.rs/json-rpc/`, что продукт `Tamir` и каталог
засеваны и что справочник статусов исполнений читается. Маркер `tcms`
зарегистрирован в `pytest.ini`, в CI-набор (`-m "unit or smoke or api"`) он пока
не входит. Без учётки тесты скипаются (`TcmsUnavailable`), а не падают, поэтому
отсутствие секретов прогон не ломает. Быстрая проверка руками:

```bash
python -c "from qa.config import load_config; from qa.tcms import TcmsClient; print(TcmsClient.from_environment(load_config()).ping())"
```

## Что осталось незакрытым

1. Cloudflare Access: у `tcms.tamir.rs` своя ingress-запись в туннеле, и сегодня
   Access для неё не включён (без логина RPC всё равно ничего не отдаёт, но
   сам адрес доступен из интернета). Когда Access включат, политику нужно
   настроить так, чтобы она не перекрывала RPC-вызовы из CI: либо bypass-правило
   по пути `/json-rpc/`, либо service token (`TCMS_ACCESS_*` в секретах) —
   так же, как для `test.tamir.rs` в Playwright-джобе.
2. Маркер `tcms` в CI-набор пока не входит (`-m "unit or smoke or api"`), поэтому
   читающие проверки интеграции остаются локальными. При этом сама работа с TCMS
   из CI идёт с того же self-hosted runner: шаги сидинга и переноса результатов
   в `qa-smoke` работают и секретов репозитория им хватает.
