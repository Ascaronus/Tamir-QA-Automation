# Как прогон попадает на https://qa.tamir.rs

Пять джобов в `.github/workflows/qa-smoke.yml` образуют конвейер:

```
qa-unit ─(pull request)→ офлайн-проверки репозитория, без отчёта

qa-smoke ─┐
          ├→ qa-report → qa-publish → https://qa.tamir.rs/<n>/
qa-e2e  ──┘
```

`qa-smoke` и `qa-e2e` гоняют тесты на staging-раннере, `qa-report` собирает из их
результатов один Allure-отчёт, `qa-publish` кладёт его в каталог раздачи.

Расписание и запуск: `push` и `pull_request` в `main`, `workflow_dispatch`
(с input `headed` — браузер с интерфейсом) и ночной cron `0 5 * * 1-5` по будням.

## 0. `qa-unit` — GitHub-hosted, только `pull_request`

`pytest -m unit` на `ubuntu-latest`: офлайн-проверки самого репозитория, без сети,
без секретов и без доступа к стенду. Это тот статус-чек, который требует защита
ветки перед auto-merge. Отчёта он не публикует.

## 1. `qa-smoke` — self-hosted runner на staging

Runner с лейблами `[self-hosted, linux, tamir-qa]` живёт на том же хосте, где
развернут стек отчётов (`/opt/qa-stack`). Он:

1. ставит зависимости через `uv` (`uv venv --python 3.12`, `uv pip install -r requirements.txt`)
   и `playwright install chromium`;
2. запускает **одним** вызовом `pytest -m "unit or smoke or api or seo or catalog" --junitxml=.tmp/junit.xml`.
   Один вызов важен: `--clean-alluredir` из `addopts` (`pytest.ini`) стирал бы
   результаты предыдущего запуска;
3. пишет в `.tmp/allure-results` файлы `environment.properties` и `executor.json` —
   описание окружения (`Web`, `Api`, `Reports`, `TCMS`, `Mailpit`, `Runner`) и ссылка
   на прогон GitHub Actions прямо внутри Allure-отчёта;
4. складывает сырые Allure-результаты в `.tmp/allure-results` (это делает
   `--alluredir` из `pytest.ini`) и загружает их артефактом `allure-results`
   с `if: always()`, чтобы отчёт был даже при упавших тестах;
5. загружает артефактом `steps-smoke` файл шагов `.tmp/steps.json` (путь задан явно
   через `QA_STEPS_FILE`) — по нему Kiwi TCMS получает тексты шагов кейсов.

Сидинг и перенос результатов в Kiwi TCMS из этого джоба убраны: тесты больше не
держат секреты `TCMS_*`, а каталог кейсов строится один раз в `qa-report` по
слитым шагам обоих прогонов (см. п. 3).

Сам Allure-отчёт на staging **не** собирается: на staging-хосте нет Java,
поэтому сборка уезжает на GitHub-hosted джоб.

## 2. `qa-e2e` — self-hosted runner, браузер и сценарии

Отдельный job рядом с `qa-smoke`, а не шаг внутри него: прогоны долгие
(`timeout-minutes: 30` — зависший Chromium иначе блокировал бы очередь на
единственном раннере), результаты пишутся в свой каталог, а уборка тестовых
данных должна идти даже при падении тестов.

1. те же `uv` + `playwright install chromium` (без `--with-deps` и без sudo:
   системные зависимости Chromium установлены один раз на машине);
2. `pytest -m "e2e or scenario" --alluredir=.tmp/allure-e2e` — отдельный
   allure-каталог ровно из-за `--clean-alluredir`: рядом с прогоном `qa-smoke`
   эти же вызовы затирали бы чужие результаты;
3. артефакт `allure-results-e2e` с `if: always()` и артефакт `steps-e2e` — файл
   шагов `.tmp/steps.json` (`QA_STEPS_FILE` задан явно), иначе у браузерных кейсов
   в TCMS не было бы текста шагов;
4. шаг `python scripts/qa_cleanup.py` с `if: always()` и `continue-on-error: true` —
   отменяет QA-заказы и удаляет тестовые корзины/покупателей (см.
   [TEST-PLAN.md](TEST-PLAN.md), §16).

Chromium ставится в каталог внутри workspace джоба (`PLAYWRIGHT_BROWSERS_PATH`),
а перед pytest отдельный шаг печатает `playwright --version`: если браузер не
поднялся на раннере, это видно в логе до первой ошибки теста, а не внутри
`Page.goto`.

Секреты этого джоба — только имена переменных: `MEDUSA_PUBLISHABLE_API_KEY`,
`MEDUSA_ADMIN_EMAIL`, `MEDUSA_ADMIN_PASSWORD`, `MAILPIT_URL`, `MAILPIT_USER`,
`MAILPIT_PASSWORD`, `QA_WEB_EMAIL`, `QA_WEB_PASSWORD`; адреса стенда приходят
из `vars.WEB_URL` / `vars.API_URL`.

## 3. `qa-report` — GitHub-hosted (ubuntu-latest)

`needs: [qa-smoke, qa-e2e]`. Ставит Java (`actions/setup-java`, Temurin 17) и CLI
Allure (`npm i -g allure-commandline`), скачивает результаты **обоих** прогонов:

```yaml
uses: actions/download-artifact
with:
  pattern: allure-results*      # allure-results + allure-results-e2e
  path: .tmp/allure-by-job      # каждый артефакт в свой каталог
```

`pattern` нужен потому, что результатов два: у `qa-smoke` — `unit`/`smoke`/`api`/
`seo`/`catalog`, у `qa-e2e` — `e2e`/`scenario`. Каталоги не сливаются в один,
а склеиваются шагом «Объединение результатов для отчёта» (`cp -a` обоих в
`.tmp/allure-results`), после чего:

```bash
allure generate .tmp/allure-results -o .tmp/allure-report --clean
```

Результат — артефакт `allure-report`. То есть в одном отчёте прогона лежат и
read-only проверки, и браузерные путешествия покупателя, со своими шагами и
скриншотами.

В этом же джобе (после генерации и загрузки отчёта) живёт Kiwi TCMS: артефакты
шагов обоих прогонов (`pattern: steps*`) сливаются в один `.tmp/steps.json`,
затем на `main` выполняются `scripts/tcms_seed.py --steps-file .tmp/steps.json`
и `scripts/tcms_sync_results.py` — отдельно для `allure-results` и для
`allure-results-e2e`, с `--report-url https://qa.tamir.rs/${GITHUB_RUN_NUMBER}/`.
Шаги идут с `continue-on-error: true`: сбой интеграции с TCMS не должен ломать
публикацию отчёта. Seed стоит **после** сборки отчёта, потому что коллекция
внутри сида тоже подхватывает `--clean-alluredir` и стёрла бы результаты.

Размещение в GitHub-hosted джобе означает, что `tcms.tamir.rs` должен быть
доступен из публичного интернета. Как только на него включится Cloudflare Access,
эти шаги нужно переносить на self-hosted джоб (например `qa-tcms`), иначе JSON-RPC
начнёт отвечать `403`.

## 4. `qa-publish` — self-hosted runner, только `main`

`needs: [qa-report, qa-smoke, qa-e2e]`. Скачивает `allure-report` и обычными
файловыми операциями раскладывает отчёт по фиксированным путям:

- `/opt/qa-stack/reports/${GITHUB_RUN_NUMBER}` — каталог конкретного прогона
  (подкаталога `runs/` нет, id прогона лежит прямо в `reports`);
- `/opt/qa-stack/reports/latest` — копия отчёта последнего прогона
  (перед копированием каталог очищается `rm -rf`).

Шаг публикации дословно делает:

```bash
RUN_DIR="/opt/qa-stack/reports/${GITHUB_RUN_NUMBER}"
LATEST_DIR="/opt/qa-stack/reports/latest"
mkdir -p "${RUN_DIR}"
rm -rf "${RUN_DIR:?}/"*
cp -a .tmp/allure-report/. "${RUN_DIR}/"
rm -rf "${LATEST_DIR}"
mkdir -p "${LATEST_DIR}"
cp -a .tmp/allure-report/. "${LATEST_DIR}/"
```

После этого шаг вызывает `qa/publish_runs_index.py`, который обновляет
`/opt/qa-stack/reports/json/runs.json` — индекс прогонов для фронтенда
`https://qa.tamir.rs` (он же отдаётся как `https://qa.tamir.rs/json/runs.json`).
Отдельного «локального скрипта публикации» в репозитории нет: публикация — это
шаги джоба плюс `qa/publish_runs_index.py`.

Отдельная деталь, о которой надо помнить: статус в индекс передаётся как
`--status "${{ needs.qa-smoke.result }}"`, то есть **по результату `qa-smoke`**.
Падение `qa-e2e` в индексе прогонов отдельно не подсвечивается — его видно по
самому Allure-отчёту (в merged-результатах падающие тесты помечены) и по логу
джобы.

### Шаги теста внутри отчёта

Шаги сценария описаны один раз в тесте — fixture `steps` (`tests/conftest.py`)
оборачивает их в `qa/steps.py::tracked_step`, который делает `allure.step(action)`.
В отчёте это видно как вложенные шаги теста, плюс текстовые вложения (например
снимок корзины из сценария M3) через `allure.attach`. Те же шаги уезжают в
каталог TCMS — см. [TCMS.md](TCMS.md).

### Доступные URL отчёта

Раздача отдаёт содержимое `reports` из корня, поэтому адрес отчёта — это id
прогона прямо в корне сайта, без префикса `/runs/`:

- `https://qa.tamir.rs/<run_number>/` — отчёт конкретного прогона,
  например `https://qa.tamir.rs/42/`;
- `https://qa.tamir.rs/latest/` — отчёт последнего прогона.

### Права на каталоги

Runner на staging работает под пользователем `actions-runner`, и каталог
`/opt/qa-stack/reports` (вместе с подкаталогом `json`) принадлежит `actions-runner`.
Поэтому публикация идёт обычным `cp` и обычным скриптом: **без sudo и без SSH**
(runner физически находится на том же хосте, где лежит каталог отчётов).

Отдаваемые файлы при этом должны быть доступны на чтение всем: nginx внутри
контейнера работает под своим пользователем и видит файлы только по правам.
`publish_runs_index.py` перед заменой `runs.json` явно ставит `0o644`, иначе
`tempfile.mkstemp` создаёт файл как `0600`, и индекс отдаёт `403`.

## Формат `runs.json`

```json
{
  "runs": [
    {
      "id": "42",
      "date": "2026-11-23T05:00:00Z",
      "branch": "main",
      "commit": "0a1b2c3",
      "run_url": "https://github.com/Ascaronus/Tamir-QA-Automation/actions/runs/123456",
      "run_number": 42,
      "status": "success"
    }
  ]
}
```

Записи от новых к старым, максимум 50. Повторный вызов с тем же `run_number`
заменяет запись, а не дублирует её.

## Как добавить запись о прогоне вручную

Обычно этого не требуется — запись создаёт джоб `qa-publish`. Для ручного
доноса (например, после локального прогона на runner) достаточно выполнить на
staging-хосте:

```bash
python3 qa/publish_runs_index.py \
  --reports-dir /opt/qa-stack/reports \
  --run-number 43 \
  --branch main \
  --commit 0a1b2c3 \
  --run-url https://github.com/Ascaronus/Tamir-QA-Automation/actions/runs/123457 \
  --status success
```

Скрипт идемпотентен: файл пишется атомарно, старые записи сохраняются.

## Очередь прогонов

`concurrency.group: qa-smoke-${{ github.ref_name }}` с `cancel-in-progress: false`:
прогоны сериализуются, а не отменяются. Отменять прогон стенда нельзя — каждый из
них публикует отчёт и пишет результаты в TCMS. Практика показала обратное: при
серии слияний Dependabot четыре из шести прогонов на `main` были отменены, и в
отчётности появились дырки. Раннер один (`tamir-qa`), поэтому очередь безопаснее,
чем потерянные прогоны.

## Секреты и переменные

Значений в репозитории нет — он публичный; в workflow только имена переменных.

| Имя | Где используется |
| --- | --- |
| `MEDUSA_PUBLISHABLE_API_KEY` | `qa-smoke`, `qa-e2e`: позитивные проверки store-API с publishable-ключом |
| `MEDUSA_ADMIN_EMAIL`, `MEDUSA_ADMIN_PASSWORD` | `qa-e2e`: отмена заказов админкой и уборка данных |
| `MAILPIT_URL`, `MAILPIT_USER`, `MAILPIT_PASSWORD` | `qa-e2e`: чтение тестовой почты (доступно только из локальной сети стенда) |
| `QA_WEB_EMAIL`, `QA_WEB_PASSWORD` | `qa-e2e`: учётка постоянного тестового покупателя на стенде (регистрация закрыта Turnstile, см. [FINDINGS-frontend-backend.md](FINDINGS-frontend-backend.md)) |
| `TCMS_URL`, `TCMS_USER`, `TCMS_PASSWORD` | `qa-report`: сидинг каталога и перенос результатов в Kiwi TCMS |
| `vars.WEB_URL`, `vars.API_URL` | `qa-e2e`: адреса стенда (конфигурационные переменные, не секреты) |

Без этих переменных зависящие от них проверки скипаются, а не падают: например,
без `MEDUSA_PUBLISHABLE_API_KEY` позитивная проверка с ключом скипается, а
негативная (`400 not_allowed`) работает всегда.

## Известные проблемы и как они были починены

1. **`runs.json` отдавал `403`.** `qa/publish_runs_index.py` писал индекс через
   `tempfile.mkstemp`, а тот создаёт файл с правами `0600`; nginx внутри контейнера
   работает под другим пользователем и читает файлы только по правам — публичный
   `/json/runs.json` отвечал `403`. Теперь `write_index()` пишет во временный файл,
   явно ставит `os.chmod(..., 0o644)` и только потом делает `os.replace`.
2. **Раздача отчётов закрывалась allow-списком nginx.** Статика `qa.tamir.rs` —
   `nginx:1.27-alpine` из `qa-stack`, и её `location /` закрыт парой `allow` /
   `deny all`. `cloudflared` — процесс хоста, его запрос к origin приходит с
   адресом хоста, поэтому сеть хоста должна быть в `allow`, иначе весь
   `qa.tamir.rs` отвечает `403`, хотя внутри контейнера тот же путь отдаёт `200`.
   В `/opt/qa-stack/nginx/default.conf` сейчас разрешены `127.0.0.1`, `::1`,
   `10.0.0.0/8`, `172.16.0.0/12` (docker bridge) и `192.168.0.0/16`, дальше —
   `deny all`. Порт контейнера (`192.168.1.143:8090`) опубликован на хосте, поэтому
   с рабочей машины проверка проходит, а с произвольного адреса в интернете — нет.
3. **Рефы действий в workflow — только проверенными версиями.** В
   `.github/workflows/qa-smoke.yml` сейчас `actions/checkout@v7`,
   `astral-sh/setup-uv@v7`, `actions/upload-artifact@v7`,
   `actions/download-artifact@v8`, `actions/setup-java@v6` — те, что реально
   отработали на этом раннере. У части экшенов major-тега нет (`setup-uv@v10`
   раннер не нашёл, и джоба падала ещё на `Set up job`), поэтому версии
   обновляются PR от Dependabot (`.github/dependabot.yml`) и проверяются этим же
   прогоном, а не правками в `main`.
4. **Два allure-каталога вместо одного.** После появления `qa-e2e` нельзя гонять
   все маркеры одним вызовом: `--clean-alluredir` из `addopts` стирал бы
   результаты. Поэтому `qa-smoke` пишет в `.tmp/allure-results`, `qa-e2e` —
   в `.tmp/allure-e2e`, а `qa-report` склеивает их через `pattern: allure-results*`
   и `merge-multiple: true`.

## Что пока не закрыто

- **`qa-e2e` ещё ни разу не выполнялся** — Playwright на self-hosted runner
  (`tamir-qa`) установлен вручную, но прогон в CI ещё не запускался. Первый
  прогон с пуша покажет, что браузеры видны (`~/.cache/ms-playwright`) и что
  хост-резолвер в CI не нужен.
- **Нет секретов `MAILPIT_*`** — сценарий «зарегистрированный покупатель» в CI
  пропускается (в TCMS такое исполнение помечается `BLOCKED`).
- **Cloudflare Access для `tcms.tamir.rs` не включён** — сейчас JSON-RPC доступен
  из публичного интернета, и именно поэтому сидинг и перенос результатов живут в
  GitHub-hosted `qa-report`. Как только Access включится, эти шаги нужно
  переносить на self-hosted джоб (например `qa-tcms`), иначе начнёт отвечать `403`.

Ранее здесь был пункт «результаты `qa-e2e` не попадают в TCMS» — он закрыт:
перенос читает объединённые артефакты обоих прогонов в `qa-report`.
