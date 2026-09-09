# Metrika reports and period comparison

Ask the assistant: “Compare organic visits for August and July, keep the sampling
information, and export the traffic-source breakdown as CSV.”

`metrika_report` makes this a single MCP call. It uses your configured Metrika
credential and the existing `yandex_metrika:read` permission. Counters remain limited
by the provider permissions of that credential; this release does not introduce a
separate per-counter server allowlist.

```json
{
  "counter_id": "12345",
  "date1": "2026-08-01",
  "date2": "2026-08-31",
  "compare_date1": "2026-07-01",
  "compare_date2": "2026-07-31",
  "metrics": ["ym:s:visits", "ym:s:users"],
  "dimensions": ["ym:s:trafficSource"],
  "filters": "ym:s:trafficSource=='organic'",
  "sort": "-ym:s:visits",
  "page_size": 100,
  "max_pages": 2,
  "output": "csv"
}
```

Use your own counter ID. Omit both comparison dates for a single report. Choose
equal-length periods when that matches your analysis; the server does not silently
rescale unequal periods. Metric availability and filter expressions are ultimately
validated by Metrika. Local validation rejects unsupported types, control characters,
oversized expressions, invalid dates and unrequested sort fields before HTTP.

The response contains:

- `periods`: request parameters, retrieval time, provider totals, sampling metadata,
  included offsets and completeness for each period.
- `comparison`: before/after totals, absolute difference and percentage change per
  metric. A zero baseline or missing value produces a null percentage, never infinity
  or a fabricated zero. Numeric overflow is marked as `numeric_overflow`, with an unrepresentable result kept null. Totals come from Metrika; partial row values are never summed.
- JSON rows or a CSV string with a `period` column, dimension IDs/names and metrics.
  Empty cells preserve missing values; numeric zero remains zero. Formula-like text
  is prefixed with an apostrophe. Literal configured credentials are masked before
  CSV serialization. The CSV can still contain the business data you requested.

Each period is limited to 93 days, 12 metrics, five dimensions and three pages of
at most 500 rows. A call can make up to six provider requests; each attempt uses the
existing request limits. The complete response is limited to 256 KiB.

`rows_complete` means all rows identified by an exact provider row count were
included. It does **not** mean unsampled data. Inspect `sampled`, `sample_share` and
`total_rows_rounded`. Missing metadata, changing totals, repeated pages or overlapping dimension groups or a local
limit produce an explicit partial result. Requests are not an atomic snapshot.
`next_offset` is a continuation hint for `metrika_get_statistics`, whose `params`
accept `offset`; the convenience report always starts at offset 1. Narrow the report
or increase its page budget before using partial rows to make a decision.

The existing `metrika_get_statistics` tool also accepts `filters` and `sort` in its
`params`. Its original MCP schema and response remain unchanged.

## Local OAuth assistance

Create your own application in [Yandex OAuth](https://oauth.yandex.ru/), select the
API permissions you need, and set its Redirect URI to
`https://oauth.yandex.ru/verification_code`. Then run:

```sh
yandex-mcp-oauth --client-id YOUR_32_CHARACTER_APPLICATION_CLIENT_ID
```

From a source checkout, prefix the command with `uv run --frozen --extra standalone`.
The helper prints an official authorization URL. The Client ID is public; never
substitute a client secret or token. Open the URL locally and paste the issued token
only into the hidden `yandex-mcp-setup` prompt. An existing configuration should be
updated through its private token file; the wizard intentionally refuses overwrites.

This helper does not run an OAuth callback server, capture browser tokens or configure
Cloud Search/Wordstat credentials. Their Cloud API setup remains separate.

Sources: [Metrika Reporting API](https://yandex.com/dev/metrika/en/stat/openapi/data),
[manual Yandex OAuth token issuance](https://yandex.ru/dev/id/doc/en/tokens/debug-token).

## По-русски

`metrika_report` собирает отчёт Метрики или сравнивает два периода одним вызовом.
Передайте свой счётчик, даты, метрики и при необходимости фильтр и сортировку.
Пример выше сравнивает органический трафик августа и июля и возвращает CSV.

Итоги берутся из ответа Метрики, а не из суммы строк выборки. Отсутствующие значения
остаются пустыми; при нулевой базе процент изменения не вычисляется. В каждом периоде
сохраняются параметры запроса, время, полнота строк и сведения о сэмплировании.
Полный набор строк ещё не означает отсутствие сэмплирования.

Лимиты одного периода: 93 дня, 12 метрик, пять группировок, до трёх страниц по 500 строк.
Для больших отчётов уточните фильтр. CSV может содержать запрошенные бизнес-данные;
его размер ограничен 256 КиБ вместе с остальным ответом.

Для помощи с доступом выполните `yandex-mcp-oauth --client-id CLIENT_ID` со своим
32-символьным ID приложения. Помощник выведет ссылку Яндекса. Откройте её локально,
а токен вставьте в скрытое поле настройщика. В чат токен передавать не нужно.
