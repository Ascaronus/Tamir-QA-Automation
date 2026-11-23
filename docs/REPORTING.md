# Как прогон попадает на https://qa.tamir.rs

Три джоба в `.github/workflows/qa-smoke.yml` образуют конвейер:
`qa-smoke` → `qa-report` → `qa-publish`.

## 1. `qa-smoke` — self-hosted runner на staging

Runner с лейблами `[self-hosted, linux, tamir-qa]` живёт на том же хосте, где
развернут стек отчётов (`/opt/qa-stack`). Он:

1. ставит зависимости через `uv` (`uv venv --python 3.12`, `uv pip install -r requirements.txt`);
2. запускает **одним** вызовом `pytest -m "unit or smoke or api" --junitxml=.tmp/junit.xml`
   (`unit` — офлайн-проверки самого репозитория, они идут первыми). Один вызов важен:
   `--clean-alluredir` из `addopts` стирал бы результаты предыдущего запуска;
3. пишет в `.tmp/allure-results` файлы `environment.properties` и `executor.json` —
   описание окружения и ссылка на прогон GitHub Actions прямо внутри Allure-отчёта;
4. складывает сырые Allure-результаты в `.tmp/allure-results` (это делает
   `--alluredir` из `pytest.ini`) и загружает их артефактом `allure-results`
   с `if: always()`, чтобы отчёт был даже при упавших тестах.

Сам Allure-отчёт на staging **не** собирается: на staging-хосте нет Java,
поэтому сборка уезжает на GitHub-hosted джоб.

## 2. `qa-report` — GitHub-hosted (ubuntu-latest)

Ставит Java (`actions/setup-java@v4`, Temurin 17) и CLI Allure
(`npm i -g allure-commandline`), скачивает артефакт `allure-results` и выполняет:

```bash
allure generate .tmp/allure-results -o .tmp/allure-report --clean
```

Результат — артефакт `allure-report`.

Версии действий зафиксированы теми, что уже отработали на этом раннере: у части
экшенов нет major-тега (`astral-sh/setup-uv@v10` раннер не нашёл, и джоба упала
ещё на `Set up job`), поэтому обновления приходят отдельными PR от Dependabot
(`.github/dependabot.yml`), а не ручными правками в `main`.

## 3. `qa-publish` — self-hosted runner, только `main`

Скачивает `allure-report` и обычными файловыми операциями раскладывает отчёт по
фиксированным путям:

- `/opt/qa-stack/reports/${GITHUB_RUN_NUMBER}` — каталог конкретного прогона
  (подкаталога `runs/` нет, id прогона лежит прямо в `reports`);
- `/opt/qa-stack/reports/latest` — копия отчёта последнего прогона
  (перед копированием каталог очищается `rm -rf`).

Шаг публикации дословно делает:

```bash
mkdir -p /opt/qa-stack/reports/${GITHUB_RUN_NUMBER}
rm -rf "/opt/qa-stack/reports/${GITHUB_RUN_NUMBER:?}/"*
cp -a .tmp/allure-report/. /opt/qa-stack/reports/${GITHUB_RUN_NUMBER}/
rm -rf /opt/qa-stack/reports/latest
mkdir -p /opt/qa-stack/reports/latest
cp -a .tmp/allure-report/. /opt/qa-stack/reports/latest/
```

После этого шаг вызывает `qa/publish_runs_index.py`, который обновляет
`/opt/qa-stack/reports/json/runs.json` — индекс прогонов для фронтенда
`https://qa.tamir.rs` (он же отдаётся как `https://qa.tamir.rs/json/runs.json`).

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

## Секреты

Единственный используемый секрет — `MEDUSA_PUBLISHABLE_API_KEY` (только имя;
значение живёт в GitHub Actions secrets). Без него позитивная проверка с
publishable-ключом корректно скипается, а негативная (`400 not_allowed`)
работает всегда.
