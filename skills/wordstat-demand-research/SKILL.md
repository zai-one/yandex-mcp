---
name: wordstat-demand-research
description: Research search demand in Yandex Wordstat - top phrases, seasonality, regions - and cluster phrases by intent. Use for сбор семантики, спрос, сезонность, Wordstat research, keyword demand.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server.
metadata:
  mcp-server: zai-yandex-mcp
---

# Wordstat demand research

1. Agree seeds (3-10 phrases), regions and period with the user; Wordstat calls are paid/limited.
2. `yandex_wordstat_get_top(phrase, regions)` per seed -> related phrases with counts.
3. `yandex_wordstat_dynamics` for 2-3 key seeds -> seasonality (monthly).
4. `yandex_wordstat_regions` for the main seed -> where demand concentrates (`yandex_wordstat_regions_tree` for ids).
5. Refine with operators (`references/wordstat-operators.md`) only where counts look inflated.
6. Cluster by intent/product; fill `templates/demand-report.md`.

Guardrails: confirm before more than ~20 Wordstat calls; never invent counts; state region and period.
