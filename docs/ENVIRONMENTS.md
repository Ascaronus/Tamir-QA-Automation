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
| Kiwi TCMS (тест-планы и тест-кейсы) | `https://tcms.tamir.rs` | учёт тестов; интеграция через JSON-RPC `/json-rpc/` — см. [TCMS.md](TCMS.md) |
| Mailpit (перехватчик почты) | `192.168.1.143:8025` (LAN staging) | только из self-hosted runner на staging; из GitHub-hosted джобов **не используется** |

## Маркеры и куда они ходят

| Маркер | Откуда | Куда ходят | Что проверяют |
| --- | --- | --- | --- |
| `unit` | `tests/unit/` | никуда, офлайн | guard по хостам, парсеры `qa/seo.py`, клиент TCMS |
| `smoke` | `tests/smoke/` | витрина | `robots.txt`, `sitemap.xml`, `/rs`, редирект корня |
| `api` | `tests/api/` | Medusa API | контракты `/store/products`, `/health` |
| `seo` (M2) | `tests/seo/` | витрина | non-prod закрыт от индексации, редиректы локалей, `canonical`/`hreflang` — см. [TEST-PLAN.md](TEST-PLAN.md) |
| `catalog` (M2) | `tests/catalog/` | витрина | `sitemap.xml` и контракт листинга каталога |
| `tcms` | `tests/api/` | Kiwi TCMS | читающие проверки TCMS, запускаются вручную |

Ручной прогон того же набора, что в CI (`qa-smoke`):

```bash
pytest -m "unit or smoke or api or seo or catalog"
```

SEO-проверки учитывают, куда смотреть: `QaConfig.site_origin` берёт публичный origin из
таблицы `STAGING_SITE_ORIGINS` (`test.tamir.rs` → `https://tamir.rs`), потому что продуктовый
`frontend/src/lib/seo.ts` ставит `canonical`/`hreflang` по публичному домену. При этом сам
публичный домен остаётся запрещённым для запросов: в `ALLOWED_HOSTS` есть `qa.tamir.rs` и
`tcms.tamir.rs` (сервисы QA-стека на staging), а `tamir.rs`, `www.tamir.rs`, `api.tamir.rs` — нет.
`QaConfig.expects_noindex` говорит, должен ли окружение быть закрыто от поисковых систем:
`False` бывает только если `QA_IS_PRODUCTION` выставлен в `1`/`true`/`yes`, и тогда
проверка `test_html_pages_closed_for_indexing` снимается как неприменимая.

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
