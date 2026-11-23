# Окружения и цели прогонов (M1)

В M1 тесты трогают **только стенд**. Все цели — публичные имена хостов;
прод (`tamir.rs`, `www.tamir.rs`, `api.tamir.rs`) запрещён на уровне кода:
`qa/config.py::is_allowed_target` поднимает `RuntimeError` при попытке туда
пойти, `qa/http.py::QaHttpClient` проверяет хост перед каждым запросом.

| Цель | Адрес | Как используется в M1 |
| --- | --- | --- |
| Витрина (web storefront) | `https://test.tamir.rs` | smoke: `robots.txt`, `sitemap.xml`, `/rs`, редирект `/` |
| Medusa API | `https://test-api.tamir.rs` | контракты: `/store/products`, отсутствие `/api/health` |
| UI отчётов QA (self-hosted Allure + индекс прогонов) | `https://qa.tamir.rs` | публикация результатов джобом `qa-publish` |
| Kiwi TCMS (тест-планы и тест-кейсы) | `https://tcms.tamir.rs` | учёт тестов; интеграция через XML-RPC `/xml-rpc/` — в M2 |
| Mailpit (перехватчик почты) | `192.168.1.143:8025` (LAN staging) | только из self-hosted runner на staging; из GitHub-hosted джобов **не используется** |

## Про Mailpit

Mailpit доступен только внутри локальной сети staging-хоста (`192.168.1.143`),
поэтому обращения к нему возможны исключительно из шагов, которые выполняются на
self-hosted runner (`qa-smoke`, `qa-publish`). В GitHub-hosted джобе
(`qa-report`) такие запросы физически не пройдут, и мы их там не делаем.
В M1 почтовые проверки не выполняются — адрес указан как справочный.

## Про прод

Прод-хосты не входят в белый список `qa/config.py::ALLOWED_HOSTS`. Это не
«просьба не трогать проду», а проверка в коде: конструирование клиента на
`https://tamir.rs` и любой запрос на прод-хост падают с `RuntimeError`.
