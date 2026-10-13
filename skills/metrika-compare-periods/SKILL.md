---
name: metrika-compare-periods
description: Compare Yandex Metrika traffic and goals between two periods with deltas by source, device or page and explain what changed. Use for сравнить периоды, почему упал трафик, weekly/monthly Metrika report, period comparison.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server.
metadata:
  mcp-server: zai-yandex-mcp
---

# Metrika period comparison

1. `metrika_list_counters` -> counter_id (ask if several).
2. `metrika_list_goals(counter_id)` when conversions matter.
3. Pick equal periods (week vs previous week, month vs same month last year); say which.
4. One `metrika_report` with `compare_date1/compare_date2`, metrics and one dimension at a time (trafficSource, then deviceCategory/startURL) - see `references/metrics-dimensions.md`.
5. Read `comparison` (abs/pct deltas) and `sampled`/`sample_share`; drill into the biggest movers only.
6. Fill `templates/comparison-report.md`.

Guardrails: read-only; <= 12 metrics, 5 dimensions, 93 days per period; never sum partial rows; flag sampling.
