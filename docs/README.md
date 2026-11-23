# Документация

| Документ | О чём |
| --- | --- |
| [ENVIRONMENTS.md](ENVIRONMENTS.md) | куда имеют право ходить прогоны и как устроен guard по хостам |
| [REPORTING.md](REPORTING.md) | как прогон попадает на `https://qa.tamir.rs` (Allure, `runs.json`, права) |
| [TCMS.md](TCMS.md) | Kiwi TCMS на `https://tcms.tamir.rs`: JSON-RPC, клиент, сидинг каталога, перенос результатов прогонов, маркер `tcms` |

Секретов здесь нет: значения существуют только под именами переменных окружения
(`MEDUSA_PUBLISHABLE_API_KEY`, `TCMS_USER`, `TCMS_PASSWORD`) и приходят из
GitHub Actions secrets или локального `.env`, который лежит в `.gitignore`.
