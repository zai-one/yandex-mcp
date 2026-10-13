---
name: webmaster-seo-health
description: Check a site's Yandex Webmaster health - SQI, indexing, excluded pages, diagnostics, broken links, top queries - and produce a prioritized fix list. Use for SEO health, индексация, ИКС, Webmaster audit, почему выпали страницы.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server.
metadata:
  mcp-server: zai-yandex-mcp
---

# Webmaster SEO health

1. `webmaster_list_hosts` -> host_id (verified hosts only).
2. `webmaster_get_summary(host_id)` - SQI, searchable/excluded, problem counts.
3. `webmaster_get_diagnostics` - FATAL/CRITICAL first (`references/diagnostics-codes.md`).
4. `webmaster_get_indexing_history` + `webmaster_get_search_urls_history` (last 90 days) - trends; `webmaster_list_search_events` for removed pages and reasons.
5. `webmaster_list_popular_queries` (TOTAL_SHOWS, TOTAL_CLICKS, positions) - top 20.
6. `webmaster_list_broken_links` when diagnostics hint at 404s.
7. Fill `templates/health-report.md`; offer recrawl/sitemap only via prepare -> approval -> apply.

Guardrails: read-only until the user approves a recrawl/sitemap; check `webmaster_get_recrawl_quota` before proposing recrawls.
