# API coverage notes

Tools added in 0.5.0. All are read-only and use the existing scopes, rate
limits, sanitizer and error envelopes. Each maps to one documented endpoint.

## Direct

| Tool | Upstream | Notes |
|---|---|---|
| `direct_get_goal_statistics` | [Reports: `Goals`, `AttributionModels`, `Page`](https://yandex.ru/dev/direct/doc/ru/spec) | 1–10 Metrika goal ids; models `FCCD`, `LC`, `LSCCD`, `AUTO`; `row_limit` 1–1 000 000. Goal columns come back as `<Field>_<GoalId>_<Model>`. |
| `direct_list_strategies` | [Strategies.get](https://yandex.ru/dev/direct/doc/ru/strategies/get) | Uses the documented `json/v501/strategies` endpoint; bounded `Page`. Type-specific fields: `direct_get_inventory` with `service="strategies"`. |
| `direct_get_statistics` (allowlist) | [Report fields](https://yandex.ru/dev/direct/doc/ru/fields-list) | `Revenue`, `Profit`, `GoalsRoi`, `PurchaseRevenue`, `PurchaseProfit`, `PurchaseGoalsRoi`, `ConversionRate`, `CostPerConversion` are allowed for every supported report type. |

Combinatorial ads ([`ResponsiveAd`, 20.03.2026](https://yandex.ru/dev/direct/doc/ru/changelog)) are readable with
`direct_get_inventory` (`service="ads"`, `ResponsiveAdFieldNames`, `SelectionCriteria.Types=["RESPONSIVE_AD"]`).
Direct error codes 152 (not enough units) and 506 (too many connections),
[documented here](https://yandex.ru/dev/direct/doc/ru/concepts/errors-list), return `provider_rate_limited`.

## Webmaster (API v4.1)

| Tool | Upstream |
|---|---|
| `webmaster_get_sqi_history` | [GET …/sqi-history](https://yandex.ru/dev/webmaster/doc/ru/reference/sqi-history) — up to 366 days, default last year |
| `webmaster_list_important_urls` | [GET …/important-urls](https://yandex.ru/dev/webmaster/doc/ru/reference/host-id-important-urls) — local offset paging |
| `webmaster_get_important_url_history` | [GET …/important-urls/history](https://yandex.ru/dev/webmaster/doc/ru/reference/host-id-important-urls-history) — URL must match the host |
| `webmaster_get_search_events_history` | [GET …/search-urls/events/history](https://yandex.ru/dev/webmaster/doc/ru/reference/hosts-search-events-history) — at most 31 days |
| `webmaster_list_search_events` | [GET …/search-urls/events/samples](https://yandex.ru/dev/webmaster/doc/ru/reference/hosts-search-events-samples) — limit 1–100 |

## Metrika Logs API

| Tool | Upstream |
|---|---|
| `metrika_logs_evaluate` | [GET …/logrequests/evaluate](https://yandex.ru/dev/metrika/ru/logs/openapi/evaluate) |
| `metrika_logs_list` | [GET …/logrequests](https://yandex.ru/dev/metrika/ru/logs/openapi/getLogRequests_1) |
| `metrika_logs_create` | [POST …/logrequests](https://yandex.ru/dev/metrika/ru/logs/openapi/createLogRequest_1) — opt-in, quota |
| `metrika_logs_get` | [GET …/logrequest/{id}](https://yandex.ru/dev/metrika/ru/logs/openapi/getLogRequest) |
| `metrika_logs_download_part` | [GET …/logrequest/{id}/part/{n}/download](https://yandex.ru/dev/metrika/ru/logs/openapi/download) — preview ≤1000 rows |
| `metrika_logs_cleanup` | [POST …/clean](https://yandex.ru/dev/metrika/ru/logs/openapi/clean), [POST …/cancel](https://yandex.ru/dev/metrika/ru/logs/openapi/cancel) — opt-in |

Per counter, requested plus stored logs are capped at 10 GB ([Logs API intro](https://yandex.ru/dev/metrika/ru/logs/)); `date2` cannot be today; the API has no row filters.

## Direct AgencyClients

`direct_list_agency_clients` → [AgencyClients.get](https://yandex.ru/dev/direct/doc/ru/agencyclients/get) (10 units + 1 per client, [units](https://yandex.ru/dev/direct/doc/ru/concepts/units)). Returns Login, ClientId, ClientInfo, Archived, Currency, CreatedAt, Type; no phones or representatives. Set `YANDEX_DIRECT_CLIENT_LOGIN` to a returned Login to work on that client.

## Tool annotations

Every tool publishes MCP `readOnlyHint`, `destructiveHint`, `idempotentHint` and
`openWorldHint` from `src/zai_yandex/tool_annotations.py`. These are hints for
clients. Scopes, write flags, approvals and idempotency remain the server-side gates.

## По-русски

Добавлены только инструменты чтения: статистика Директа по целям Метрики
(`Goals`/`AttributionModels`), пакетные стратегии (Strategies.get, v501), ИКС,
важные страницы и события появления/исключения страниц в поиске Вебмастера.
Ссылки на документацию Яндекса приведены в таблицах выше. Все инструменты
публикуют MCP-аннотации; реальные ограничения по-прежнему задаёт сервер.
