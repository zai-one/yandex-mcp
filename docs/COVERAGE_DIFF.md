# Yandex MCP coverage diff (zai-one vs competitors)

Generated 2026-10-13. Competitors cloned to `/tmp/comp-yandex` (box only).
Official Direct index: https://yandex.ru/dev/direct/doc/ru/llms.txt
Official Webmaster index: https://yandex.ru/dev/webmaster/doc/ru/llms.txt

## Tool counts (claimed / counted from source)

| Server | Claimed | Counted tool names | Notes |
|---|---|---|---|
| zai-one/yandex-mcp (before this PR) | — | **101** all / **53** default | Compact toolsets; writes gated |
| SvechaPVL/yandex-mcp | 132 | 122 | Many Direct write CRUD + Metrika CRUD |
| skiddgoddamn/yandex-marketing-mcp | 153–155 | 150 | Direct+Metrika+Webmaster+Wordstat+auth |
| webkoth/yandex-mcp | 125 | 85 registered `server.tool` | Strong Webmaster Pro; multi-project |

## Direct+Webmaster method parity (what matters)

Focus: documented Direct `get` / Changes / KeywordsResearch and Webmaster reads.
Competitors inflate counts with one tool per add/update/delete; zai-one keeps writes behind `direct_write` / prepare→apply.

### Missing Direct reads (before) → action

| Method | Official doc | Competitors | Usefulness | Action |
|---|---|---|---|---|
| Bids.get | https://yandex.ru/dev/direct/doc/ru/bids/get | webkoth (as keywordbids mislabel), units table | High — auction/search prices for bid picking | **Add** `direct_get_bids` (direct_extra) |
| Sitelinks.get | https://yandex.ru/dev/direct/doc/ru/sitelinks/get | all three | High — only via inventory before | **Add** `direct_list_sitelinks` |
| AdImages.get | https://yandex.ru/dev/direct/doc/ru/adimages/get | all three | High — creative inventory | **Add** `direct_list_ad_images` |
| Businesses.get | https://yandex.ru/dev/direct/doc/ru/businesses/get | skidd, webkoth | Medium — Yandex Business profiles (no Phone by default) | **Add** `direct_list_businesses` |
| SmartAdTargets.get | units + https://yandex.ru/dev/direct/doc/ru/best-practice/smart-ads | svecha, skidd, webkoth | High — smart-banner filters | **Add** `direct_list_smart_ad_targets` |
| DynamicTextAdTargets.get | units table | webkoth | Medium — dynamic text conditions | **Add** `direct_list_dynamic_text_ad_targets` |
| VCards.get | units only; **no method page in current llms.txt** | svecha, skidd, webkoth | Medium but PII (Phone/Address) | **Skip dedicated tool** — still via `direct_get_inventory` service=vcards; avoid default PII |
| KeywordsResearch.hasSearchVolume | https://yandex.ru/dev/direct/doc/ru/keywordsresearch/hasSearchVolume | all | Already `direct_keyword_forecast` | Covered |
| KeywordsResearch.deduplicate | https://yandex.ru/dev/direct/doc/ru/keywordsresearch/deduplicate | skidd (yd_keywords_research) | Low for agents | **Skip** — niche preprocess; inventory/forecast enough |
| ExcludedSites | Campaigns.get field, not a service | skidd wrappers | Low | **Skip** — use inventory campaigns + FieldNames |
| Direct units tool | Response header Units (concepts/units) | webkoth | Low as standalone | **Skip** — surfaced in errors/hints (`yandex://guide/api-units`) |
| Per-method Direct writes (add/update/delete × N) | various | svecha/skidd inflate to 80 Direct tools | High risk | **Skip** — intentional: `direct_prepare_changes` / `direct_write` |

### Missing Webmaster reads (before) → action

| Method | Official doc | Competitors | Usefulness | Action |
|---|---|---|---|---|
| GET …/pro/limits | https://yandex.ru/dev/webmaster/doc/ru/reference/domain-limits | webkoth | High — export quota | **Add** `webmaster_get_pro_limits` |
| GET …/pro/serp/dates | https://yandex.ru/dev/webmaster/doc/ru/reference/available-dates | webkoth | High — before export | **Add** `webmaster_list_pro_dates` |
| GET …/pro/serp/queries/download/{task} | https://yandex.ru/dev/webmaster/doc/ru/reference/status-retrieval | webkoth | High — poll export | **Add** `webmaster_get_pro_export_status` |
| POST …/pro/serp/queries/download/ | https://yandex.ru/dev/webmaster/doc/ru/reference/initialization-export | webkoth | High — starts quota job | **Add** `webmaster_start_pro_export` behind `YANDEX_WEBMASTER_WRITE_ENABLED` |
| links/internal/samples|history | webkoth names “internal” | webkoth | Already our broken-links tools | Covered (`webmaster_list_broken_links`) |
| Host add/delete, verify start | write | svecha/skidd/webkoth | Destructive | **Skip** dedicated — not in prepare/apply set; out of compactness scope |
| Feeds batch add/delete/regions change | write | webkoth | Write | **Skip** — write gate pattern reserved for recrawl/sitemap; document |
| Indexing archive POST/GET | https://yandex.ru/dev/webmaster/doc/ru/reference/host-id-important-archive-all-pages | webkoth | Medium — large binary job | **Skip for now** — async binary download poorly fits chat MCP; document |
| Wordstat userInfo | v1 decommissioned (Svecha returns error; Skidd uses cloud endpoint) | svecha/skidd | Broken/legacy | **Skip** |

### Metrika / other (out of Direct+Webmaster success bar)

Competitors add Metrika counter/goal/filter/segment/grant/label/annotation/delegate CRUD and offline calls/expenses uploads. zai-one already has reports, goals write gate, offline conversions, Logs API. **Skipped** to keep default token budget and write safety; documented as intentional.

## After this PR

- Total tools: **111** (was 101); default: **53** (unchanged at 53; 52 listed without accounts file)
- `direct_extra`: 20 tools; `webmaster_extra`: 9; `webmaster_write`: 6
- Direct+Webmaster toolsets combined: 73 tools
- `all` tools/list budget raised 66→80 KB; default budget unchanged
- Documented Direct get services in llms.txt covered as dedicated tools or inventory/forecast
