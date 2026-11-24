# Kiwi TCMS: стенд и интеграция

В репозитории два разных TCMS-контуре, и их легко перепутать:

| Контур | Что проверяет | Куда ходит | Маркер |
| --- | --- | --- | --- |
| `qa/tcms.py` + `tests/unit/test_tcms_client.py` | сам клиент JSON-RPC: модель, форматы, разбор ошибок | никуда, офлайн | `unit` |
| `tests/api/test_tcms_api.py` | живой Kiwi TCMS стенда: авторизация, модели, связи | `TCMS_URL` | `api`, `tcms` |

Первый контур защищает второй от тихой деградации: если клиент начнёт путать
`TestExecution` с `TestCase`, это видно на unit-проверках, а не в момент, когда
ночной прогон решит написать лишние тест-кейсы в боевой каталог.

## Стенд

| | |
| --- | --- |
| Публичный адрес | `https://tcms.tamir.rs` (Cloudflare Tunnel → `http://127.0.0.1:8091`) |
| Внутри сети стенда | `192.168.1.143:8091` → TLS `8443` контейнера |
| Образ | `kiwitcms/kiwitcms:latest` + `mariadb:11.4` |
| Версия | Kiwi TCMS 17.2 |
| Доступ | **Cloudflare Access пока не включён** — вход только учётной записью TCMS |

Туннель поднимается systemd-юнитом `cloudflared-tunnel.service` (не
`docker compose`): контейнеру не нужен ни `network_mode: host`, ни сеть
`qa-stack_default` — он ходит в `127.0.0.1:8091` хоста. Домен туннеля задаётся
флагом `--url`, а не `ingress` в `config.yml`: именно так `cloudflared` сам
прописывает DNS-запись и маршрут в зоне Cloudflare.

**Не публикуйте порт 8091 наружу.** `TCMS_ADMIN_TOKEN` в compose-файле помечен
комментарием, потому что REST API с API-токеном обходит Cloudflare Access.
Доступ к стенду — только SSH.

Учётные данные сервера (`TCMS_URL`, `TCMS_USER`, `TCMS_PASSWORD`) приходят из
переменных окружения и секретов CI. Значений в репозитории нет и быть не должно.

## Что и как пишет сидинг

`scripts/tcms_seed.py` собирает кейсы из коллектора pytest
(`pytest --collect-only -q tests`) и приводит TCMS к ожидаемому состоянию:
сущность ищут по ключу, при отсутствии создают. Никаких `insert_or_bust` с
жёстко зашитыми номерами.

В collect-only участвуют только тесты `tests/`; тесты с маркером `tcms`
(собственные проверки интеграции) в каталог кейсов не попадают.

### Структура каталога: категории и приоритеты из маркеров

Маркер pytest — это «линия ответственности», поэтому категория и приоритет в
TCMS берутся из него, а не придумываются вручную. Порядок словаря `AREAS`
важен: у теста может быть несколько маркеров, выигрывает **первый** из списка,
и сценарии (`e2e`, `scenario`) стоят раньше `smoke`/`api`, чтобы браузерное
путешествие покупателя не осело в категории смоук-проверок.

| Маркер | Категория TCMS | Приоритет |
| --- | --- | --- |
| `e2e` | `User Journeys E2E` | P1 |
| `scenario` | `Checkout Scenarios` | P1 |
| `smoke` | `Storefront Smoke` | P2 |
| `api` | `Medusa API` | P3 |
| `seo` | `Storefront SEO` | P2 |
| `catalog` | `Catalog & Search` | P3 |

Справочники при этом **не заводятся заново** по имени: `TestBuild`,
`TestPlanType` и `TestClassification` в Kiwi TCMS 17.2 не имеют публичного API
(`TestBuild.create` и `TestPlan.new` отвечают `-32601 Method Not Found`). Их
находят фильтрами — `value` у `TestBuild`/`TestClassification` и `name` у
`TestPlanType` — и если записи нет, создают её напрямую через Django ORM. Тот же
подход, что в официальном `tcms/testing/new.py`.

Фиксированные имена, которые сидинг гарантирует:

| Сущность | Имя |
| --- | --- |
| продукт | `Tamir` |
| классификация | `Tamir Balkan` |
| build | `staging` |
| тип плана | `Smoke` |
| тест-план | `Staging Regression — Tamir` |
| тест-ран | `Staging Regression — пользовательские сценарии и проверки` |

План и ран **не создаются вторыми экземплярами**: если в TCMS лежат старые имена
(`Staging Smoke — Tamir`, `Staging Smoke — базовый прогон`), сидинг их
переименовывает, чтобы сохранилась история исполнений и ссылки на отчёты
(`LEGACY_PLAN_NAMES` / `LEGACY_RUN_SUMMARIES`).

### Ключ кейса — `script` (pytest nodeid)

`TestCase` ищется и обновляется по полю `script`, в котором лежит полный nodeid
(`tests/api/test_checkout_scenarios.py::test_guest_order_reaches_the_shop`).
Повторный сидинг обновляет кейс, а не множит его — `TestCase.create` сам по себе
не идемпотентен, и без этого ключа ночной прогон приносил бы по копии на каждый
кейс.

Поля кейса: `summary` (первая строка docstring, иначе имя теста), `text`
(docstring целиком, иначе nodeid), `case_text` (шаги, см. ниже), `is_automated
= True`, категория, приоритет, `case_status`, продукт, автор.

### Текст кейса: шаги прогона, а не догадки

Шаги сценария описаны в тесте один раз — фикстурой `steps`
(`tests/conftest.py` → `qa/steps.py`). Прогон пишет их в `.tmp/steps.json`
(`pytest_sessionfinish`; путь меняется переменной `QA_STEPS_FILE`). Сидинг и
перенос результатов — отдельные процессы, своих шагов они не видят, поэтому
читают файл через `scripts/steps_dump.py`:

```python
recorded = load_recorded_steps(args.steps_file)
text = case_text_from_steps(recorded.get(nodeid, []))   # "" если шагов нет
```

В `case_text` попадает формат Kiwi TCMS «одна строка = один шаг»,
`действие;ожидание`. Так кейс читается как инструкция, а не как строка из вывода
pytest, и тот же самый набор шагов виден в Allure (`allure.step`).

Запасной вариант (`fallback_text`) есть только у сидинга: сначала первая строка
docstring, потом имя теста — кейса без текста в каталоге быть не должно. Перенос
результатов запасной текст **не пишет**: ему нечем его подтвердить, поэтому без
настоящих шагов он оставляет в TCMS то, что уже есть.

Флаг `--steps-file` у сидинга позволяет указать другой файл шагов (по умолчанию
`scripts.steps_dump.DEFAULT_STEPS_FILE`).

### Связи плана, рана и кейсов

`TestPlan.add_case` идемпотентен, `TestRun.add_case` — нет, поэтому для рана
сначала читают `TestExecution.get_cases`, а недостающие добавляют через
`TestRun.add_case`. `TestPlan.new` и `TestRun.new` в Kiwi TCMS 17.2 отвечают
`-32601`, так что обе привязки делают прямыми методами моделей.

Ссылка на Allure добавляется **на ран**, а не на план:
`TestExecution.add_link({execution_id, name, url})`, а не
`TestPlan.add_link` (такого метода в Kiwi TCMS нет). Одна ссылка на ран
закрывает вопрос «где отчёт этого прогона».

### Запуск

```bash
export TCMS_URL=... TCMS_USER=... TCMS_PASSWORD=...
python scripts/tcms_seed.py --dry-run     # сверка без записей
python scripts/tcms_seed.py               # каталог + тест-ран
```

## Перенос результатов

`scripts/tcms_sync_results.py` разбирает сырые Allure-результаты (по умолчанию
`--results .tmp/allure-results`) и обновляет исполнения в тест-ране:

| Allure status | Исполнение | Статус (`TestExecutionStatus`) |
| --- | --- | --- |
| `passed` | создано или обновлено | `Accept` |
| `failed`, `broken` | создано или обновлено | `Break` |
| `skipped` | **не пишется** | — |

Пропуск — это не результат тест-ранирования, это «проверка сегодня не
работала»: такие тесты выводятся отдельным списком, а в TCMS попадает только
реальная работа стенда. Статусы берутся по имени через фильтр
(`TestExecutionStatus.value`), а не по номерам: в Kiwi TCMS 17.2 `Accept`,
`Break` и `Skip` живут в одной записи со `value == "Default"`, и различаются
только `weight` (2, 4 и 1). Реальные PK закрывает `qa.tcms.status_id` — этот
код закрыт тестами `tests/unit/test_tcms_client.py`, а проверка `TestStatus
4→Accept`, `2→Break` на живом стенде — в `tests/api/test_tcms_api.py`.

Тест-ран ищется ровно так же, как его создал сидинг: `product` и `build` по
`value`, `plan` по `name`, `summary__exact`. `case__exact` — единственный фильтр
`TestExecution`, который Kiwi TCMS 17.2 не понимает (`-32098`), поэтому
исполнения читают `TestExecution.get_cases` и ищут кейс в ответе.

nodeid кейса восстанавливается из пары полей Allure: `suite`/`name` недостаточны
и неоднозначны (в разных файлах встречаются одноимённые тесты), поэтому
используются `package` + `name`. `package` Allure пишет путями с `/`
(`tests/api/test_checkout_scenarios`), а pytest-nodeid использует `\` под
Windows и `/` под Linux, поэтому путь нормализуется (`as_posix`), а не
подставляется как есть: иначе кейсы, сидящие в TCMS с windows-nodeid, никогда не
нашлись бы.

Результат: `TestExecution.update` с `status_id`, `assignee_id` (тот же сервисный
пользователь, что и автор кейсов) и `close_old=True`, плюс `notes`, если у
теста есть сообщение об ошибке. Ссылка на отчёт прогона — аргумент
`--report-url` (`https://qa.tamir.rs/<run_number>/`).

```bash
python scripts/tcms_sync_results.py \
  --report-url "https://qa.tamir.rs/${GITHUB_RUN_NUMBER}/"
python scripts/tcms_sync_results.py --dry-run        # сверка без записей
```

## Известные ограничения Kiwi TCMS 17.2

Формально это quirks версии, а не ошибки реализации; здесь они собраны, чтобы
их не пришлось переоткрывать.

### Реестр RPC-моделей: у моделей нет публичного API

Kiwi TCMS 17.2 не публикует реестр моделей: `TestBuild.list`,
`Classification.list`, `PlanType.list` и даже `TestCaseType.list` и
`Priority.list` отвечают `-32601 Method Not Found`, а
`meta/list_models` отдаёт пустой ответ `-32603 Internal Error` — то есть
«узнаем, какие модели есть», нельзя в принципе. Рабочий путь:

| Справочник | Как получить |
| --- | --- |
| `TestBuild`, `TestClassification` | фильтр по `value` |
| `TestPlanType`, `TestPriority`, `TestCaseCategory`, `TestCaseStatus` | фильтр по `name` |
| `TestCaseType` | **только** `get_filter_names` и его `default` |

`TestCaseType` — единственный справочник, чей PK вообще доступен из RPC:
`TestCase.get_filter_names` возвращает `{"value": {"default": <id>,
"choices": []}}` (список пустой), поэтому сидинг берёт `default` и не хранит
номер в коде. Именно так это делает сам Kiwi TCMS в `tcms/testcases/filters.py`:
`case_type` обязателен при создании кейса, `choices` там заполняются только для
пользовательского формы, а не для API.

`TestCase.update` **не может** изменить `case_type`: метод описан через
`UpdateTestCase`, в котором полей `case_type`, `is_confirmed` и `is_automated`
нет. Меняются `summary`, `text`, `case_text`, `script`, `is_automated`,
`category`, `priority`, `case_status`, `product` — этого хватает, чтобы
обновлять кейсы по nodeid.

### `id` в JSON-RPC обязателен

`id` должен быть в каждом запросе. Без него Kiwi TCMS отвечает **`204 No
Content`** вместо `200` с телом, и клиент теряет возможность отличить «модель не
найдена» (`-32601`) от «метод не поддерживает вызов» (`-32098`). В ответах
всегда есть `"id": <запрошенный>`.

### Авторизация: сессия, не Basic auth

`Auth.login` (JSON-RPC) ставит куку `srver`; клиент хранит её и передаёт в
`Cookie`. Логин и пароль идут телом JSON, поэтому **нет** пароля ни в URL, ни в
заголовках. Это не косметика: Basic auth в Kiwi TCMS 17.2 не работает —
`/xml-rpc/` отвечает `403 Access denied`, `/json-rpc/` — `401 Unauthorized`
(`Authentication credentials were not provided`).

### Коды ошибок и их смысл

| Код | Сообщение | Когда встречается |
| --- | --- | --- |
| `-32601` | `Method Not Found` | у модели нет публичного `create`/`list` (`TestBuild`, `TestClassification`, `TestPlanType`), `TestPlan.new`, `TestRun.new` |
| `-32098` | `This method doesn't support calls` | несуществующие фильтры: `TestPlan.plan_type`, `TestExecution.case__exact` |
| `-32602` | `Invalid params name 'build'` | `TestPlan.filter` по полю, которого в плановых фильтрах нет |
| `404` по `url` | страница не найдена | `TestPlan.add_link` (метода нет) |

Разница между `-32601` и `-32098` принципиальна: первая — «такого метода нет,
нужен прямой путь», вторая — «метод есть, но фильтр неверный». Оба проверяются
живыми тестами, иначе клиент мог бы принять одну за другую и начать создавать
дубли.

### Фильтры, доступные прямо из RPC

| Модель | Фильтры |
| --- | --- |
| `TestCase` | `category`, `category__in`, `priority`, `priority__in`, `script__exact`, `summary__icontains` |
| `TestCaseCategory` | `name__exact`, `product__exact` |
| `TestClassification` | `name__exact`, `product__exact`, `value__exact` |
| `TestPlanType` | `name__exact` |
| `TestPlan` | `name`, `name__exact`, `parent`, `product`, `product__exact` |
| `TestExecution` | `assignee`, `build`, `build__exact`, `close_date`, `plan`, `plan__exact`, `product`, `product__exact`, `summary`, `testing_time` |

У `TestPlan.filter` нет `plan_type`: чтобы не упираться в `-32602`, тип плана
сверяется по уже найденному плану (`plan["plan_type"] == expected`) или
считывается напрямую из Django ORM.

Ссылка на Kiwi TCMS, описывающая расхождение REST и RPC и то, как официальный
код создаёт отсутствующие справочники:
<https://github.com/KiwiTCMS/kiwitcms/issues/1616>

### Приложение вложений

Вложения (`TestExecution.attach_file`) кладут файл в хранилище TCMS, но UI
показывает его как `http://qa.tamir.rs/attachments/...`, то есть адрес лежит вне
домена TCMS. Если раздавать вложения с самого TCMS, ссылка будет битой, поэтому
используется отдельная раздача `https://qa.tamir.rs/attachments/`.

## Что пока не закрыто

Сидинг каталога и перенос результатов запускаются **только в джобе `qa-smoke`**,
а перенос читает только `.tmp/allure-results`. Браузерный прогон `qa-e2e` пишет
результаты в `.tmp/allure-e2e` и свой артефакт `allure-results-e2e`, поэтому:

- `User Journeys E2E` уже есть в каталоге TCMS и в плане (его сидит `tcms_seed.py`,
  который смотрит в коллектор, а не в результаты),
- **исполнения** e2e-сценариев в тест-ран пока не попадают — их шаги и статусы
  видны только в Allure-отчёте.

Практический шаг, чтобы это починить, — запускать `tcms_seed.py` и
`tcms_sync_results.py --results .tmp/allure-e2e` и в `qa-e2e` тоже (сейчас в
нём только уборка тестовых данных). Скрипты к этому готовы: `--results` и
`--steps-file` у них уже есть.
