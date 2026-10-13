---
name: yandex-direct-audit
description: Audit a Yandex Direct account - campaign states, spend vs conversions, goals, strategies, search queries, negatives - and produce prioritized recommendations. Use for аудит Директа, why CPA grew, campaign review, Direct audit.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server.
metadata:
  mcp-server: zai-yandex-mcp
---

# Yandex Direct audit

1. `direct_list_campaigns` -> ids, states, types. Never guess ids.
2. `direct_get_statistics` (CAMPAIGN_PERFORMANCE_REPORT, last 30 days and previous 30; Cost, Clicks, Impressions, Conversions, CostPerConversion).
3. Goals: `metrika_list_goals` + `direct_get_goal_statistics` for priority goals (state the attribution model).
4. Strategies: `direct_get_campaign` / `direct_list_strategies`; compare with `references/strategies.md`.
5. Search queries: `direct_get_statistics` with SEARCH_QUERY_PERFORMANCE_REPORT for top-spend campaigns -> negative candidates.
6. Ads/keywords as needed: `direct_get_inventory` (ads, keywords) with minimal FieldNames.
7. Walk `references/audit-checklist.md`; fill `templates/audit-report.md`.

Guardrails
- Read-only skill: changes go through skill direct-safe-change.
- Watch units (`references/api-units.md`): few get calls, narrow FieldNames.
- Numbers come from the API; mark sampled or partial data.
