# Changelog

## Unreleased

- Security: deletes and bid changes are never autonomous. `direct_write` no longer runs `delete` (campaigns, ad groups, ads, keywords) or `bids.set` / `bids.setAuto`; they go through `direct_prepare_write` -> accepted approval -> `direct_apply_write`. `metrika_apply_goal` no longer deletes goals; use `metrika_manage_prepare(operation='goal_delete')` -> approval -> `metrika_manage_apply`. An agent can pick the wrong id or be steered into a delete, so a human confirms it.
- `YANDEX_TOOLSETS=all` (and the unset default) now also exposes the new opt-in toolsets (`*_manage`, `maps_geosearch` with its key, ...). Their writes still need approval; set an explicit list if you relied on `all` meaning the previous surface.

- Optional lazy tool loading (`YANDEX_TOOL_LOADING=eager|lazy`, default `eager` = unchanged): lazy lists a small core plus `yandex_enable_toolsets` (toolsets with one-line description and token cost); enabling registers the tools at runtime and sends `notifications/tools/list_changed`. Allowlists, read-only mode and approval gates still apply. Setup wizard asks eager vs lazy. Startup tokens (tools/list + instructions, ≈chars/4): eager ≈21872, lazy ≈2574. Scope: enablement is per MCP session (FastMCP session visibility), so one client's enabled toolsets never appear for another. The HTTP transport here is stateless (every request is its own session), so `lazy` is stdio only: an HTTP server refuses to start with it; use `eager` or `YANDEX_TOOLSETS` there. README lazy section lists verified `tools/list_changed` client support with sources.
- Platform setup: default stays `all`. Prompt `yandex_setup` + tool `yandex_setup_toolsets` (platforms direct/metrika/webmaster/audience/wordstat/serp/maps/manage or presets direct-only/analytics/seo/everything) return `YANDEX_TOOLSETS` with measured token cost; `write=true` (stdio only) sets just that key in the active or per-user `mcp.local.json` (0600), restart to apply. `yandex-mcp-setup` asks the same question. No secrets involved. Router +1 line (limit 1800 chars); Unknown wizard input keeps `all`. Routing eval 45 cases. Without a key `default` = 53 tools, `all` = 127.

- Opt-in `maps_geosearch` toolset (Yandex Maps organization search, `search-maps.yandex.ru/v1`): `maps_search_organizations`, `maps_search_places`. Enabled only when `YANDEX_MAPS_GEOSEARCH_API_KEY` or `YANDEX_MAPS_GEOSEARCH_API_KEY_FILE` (owner-private file) is set; read-only, scope `yandex_search:read`, compact output in API order with attribution, no caching, client pacing. Router line appears only when the tools are exposed; guide `yandex://guide/maps-geosearch`; routing eval 43 cases. Without a key `default` and `all` are unchanged by this toolset.

- Broader documented API coverage with the default `tools/list` kept compact (now 53 tools, 33 KB incl. the setup tool).
- Opt-in `metrika_manage`, `webmaster_manage`, `audience_manage`: 126 documented operations (counters, filters, operations, segments, grants, labels, annotations, access filters, delegates; calls/expenses/user params/CRM/chat uploads; bytime/drilldown/comparison reports; hosts, verification, feeds, sitemap recrawl, indexing archive; Audience uploads, lookalikes, grants, pixels) via `*_manage_read|prepare|apply|reconcile`.
- Direct: `direct_prepare_write` -> `direct_apply_write` for every allowlisted service/method; new writes for keyword bids, bid modifiers, audience targets, retargeting lists, sitelinks, images, extensions, videos, creatives, feeds, negative keyword sets, strategies (v501), agency clients and clients.update (approval-only), unified performance campaigns/groups add/update on v501 (approval-only); readback for every new service with a documented get; `direct_deduplicate_keywords`, `direct_list_dynamic_feed_ad_targets`.
- Direct: VCards/DynamicTextAdTargets/SmartAdTargets/DynamicFeedAdTargets writes as approval-only with readback (absent from the current reference navigation but not announced as removed); `agencyclients.addPassportOrganizationMember` (approval-only, high risk); `dictionaries.getGeoRegions` via `direct_get_inventory`; Yandex Business/Maps/Turbo API availability documented.
- Metrika goals list/create/update/delete, `stat_pivot`, offline conversions upload (BASIC/CALLS/CHATS, `client_id_type`) + uploadings; Audience `pixel_create`, `segment_create_geo`/`_geo_polygon`, `segment_upload_file`, required `modification_type` (+`check_size`); Webmaster documented enum/range/date validation and per-method doc links everywhere; credentials in URLs (`user:pass@`) masked in previews, results and the durable ledger. Wordstat: phrase/regions validated per the v2 proto, `to_date` optional for dynamics.
- Management fixes vs official references: Webmaster in-search history/samples use `/search-urls/in-search/*`; feed problems/rejected offers take `url` (required, + `severity`); `feeds_add_info` sends `{"requestId"}` as the GET body; feeds status/summary filters; `sitemap_recrawl` `parent_id`; `verification_start` requires `verification_type`; raw-body ops (incl. DELETE `feeds_batch_remove`) require a body; Metrika `stat_*` query keys per endpoint (adds `timezone`, `proposed_accuracy`, annotations, `keys_sort`, `only_expandable_undefined`, `access_filter_id`) with stat/openapi doc links; `*_manage_reconcile` reports `in_flight` instead of settling a write that is still running.
- Security: approvals are verified against the named account/endpoint on accept and consume; `approvals --named-account`; `direct_apply_changes` executes Campaigns.update only. The autonomous `direct_write` keeps its 0.6.0 allowlist (campaigns, adgroups, ads, keywords, bids); every Direct service/method added here (bid and bid-modifier changes, deletes, ads unarchive, ...) runs only through `direct_prepare_write` -> accepted approval -> `direct_apply_write`. The Geosearch key is redacted from httpx request logs; `yandex_setup_toolsets` never reuses a stale temp file.
- Zero-config start: `--config` optional (`YANDEX_MCP_CONFIG` or the per-user `mcp.local.json`), per-user state directory, OAuth device-flow tokens auto-loaded after restart.
- Router: management route, `yandex-account-admin` skill, `management` guide; routing eval 28 -> 37 cases; token budget for `all` 92 KB.

## 0.6.0

### Added
- Coverage parity (opt-in): Direct `Bids.get`, Sitelinks/AdImages/Businesses/SmartAdTargets/DynamicTextAdTargets reads in `direct_extra`; Webmaster Pro export limits/dates/status in `webmaster_extra` and `webmaster_start_pro_export` (write gate; the paid `use_pro_tariff=true` also needs an accepted approval) in `webmaster_write`. Default toolset unchanged.

- OAuth self-setup: `yandex_auth_start` / `yandex_auth_finish` / `yandex_auth_status` (device-code flow per https://yandex.ru/dev/id/doc/ru/codes/screen-code-oauth), owner-private token files (0600), hot reload without restart, optional MCP URL elicitation, per-account when profiles are configured. CLI: `yandex-mcp-oauth --device`. Guide `yandex://guide/oauth`. Tokens never returned in tool output. `yandex_auth_start` / `yandex_auth_finish` exist only on the local stdio transport (an HTTP server is shared, so its credentials are never replaceable by a caller; over HTTP only `yandex_auth_status` remains, behind a Yandex scope). Results name token locations relative to the state directory, not absolute paths. A configured token file always wins; a new per-account file is linked into `YANDEX_ACCOUNTS_FILE` so it survives a restart; a replaced token is kept as `<file>.bak`; only directories the tool creates are set to 0700.

- Router: server `instructions` are now a table of intents (RU/EN words) -> first tool; skill; `yandex://guide/<topic>`, with a fallback to `yandex://playbook` / `yandex://guide/tool-map`. New guides `tool-map` (every tool by toolset, generated by `scripts/gen_tool_map.py`) and `accounts`; new skill `yandex-agency-accounts`. Routing eval (`tests/evals/routing_cases.json`, 23 colloquial requests) and a startup-token budget (instructions + tools/list + skill frontmatters). README 1-minute start.

- Direct Sandbox: `YANDEX_DIRECT_SANDBOX=true` (or per profile `direct_sandbox`) routes Direct JSON v5 calls to `https://api-sandbox.direct.yandex.com/json/v5`; router notes sandbox mode. The sandbox endpoint is part of the write ledger and approval scope, so a sandbox write or approval never replays or applies in production.

- Multi-account / agency profiles: `YANDEX_ACCOUNTS_FILE` (named profiles with own token files and/or Direct Client-Login, optional default). Every tool gains an optional `account` argument only when profiles are configured; `yandex_list_accounts` (local, no secrets). Backward compatible: without the file the surface is unchanged. The write ledger, idempotency keys and approvals are partitioned by the resolved account, so a write or approval never replays or applies across accounts; writes with no resolved account keep their previous ledger keys.

- Opt-in toolsets `direct_extra` (14 reads: `direct_get_client`, `direct_get_dictionaries`, `direct_check_changes`, `direct_get_bid_modifiers`, `direct_get_keyword_bids`, `direct_list_audience_targets`, `direct_list_retargeting_lists`, `direct_list_feeds`, `direct_list_creatives`, `direct_list_ad_extensions`, `direct_list_turbo_pages`, `direct_list_leads`, `direct_list_ad_videos`, `direct_list_negative_keyword_sets`) and `webmaster_extra` (6 reads: `webmaster_get_owners`, `webmaster_list_feeds`, `webmaster_get_feed`, `webmaster_get_feed_problems`, `webmaster_list_regions`, `webmaster_query_analytics`). Every endpoint links its official doc page; not in `default`, so the default tool list is unchanged.

- Metrika offline conversions: `metrika_validate_offline_conversions` (local CSV validation, confirmation_hash), `metrika_upload_offline_conversions` (multipart upload; `YANDEX_METRIKA_WRITE_ENABLED`, metrika write scope, hash + idempotency key, never auto-retried), `metrika_list_offline_conversion_uploads`, `metrika_get_offline_conversion_upload`. Toolset `metrika_offline`; upload joins `metrika_write`. Skill `metrika-offline-conversions`, guide `yandex://guide/offline-conversions`.
- Wordstat `yandex_wordstat_get_top` gains optional `devices` and validates `num_phrases` 1..2000 before reserving budget (Search API v2 GetTop).
- Metrika Logs API: `metrika_logs_evaluate`, `metrika_logs_list`, `metrika_logs_get`, `metrika_logs_download_part` (bounded TSV preview) and opt-in `metrika_logs_create` / `metrika_logs_cleanup` (`YANDEX_METRIKA_LOGS_ENABLED`, metrika write scope, idempotency ledger). Toolsets `metrika_logs`, `metrika_logs_write`.
- Direct `direct_list_agency_clients` (AgencyClients.get, compact non-PII fields) for agency accounts.

- Toolsets (`YANDEX_TOOLSETS`, `YANDEX_TOOLS`, `YANDEX_DISABLED_TOOLS`, `--toolsets`) and read-only mode (`YANDEX_READ_ONLY`, `--read-only`, driven by the reviewed annotations). Unset = every tool, as before.
- Router `instructions`; compact descriptions for the five tools over 300 chars; error envelopes gain `hint` and `docs`.
- Resources `yandex://toolsets`, `yandex://playbook`, `yandex://guide/{topic}`; prompts `campaign_audit`, `weekly_report`, `seo_audit`, `budget_scaling`.
- Self-contained skills (`skills/`), Claude Code plugin + marketplace (`.claude-plugin/`), `server.json`, `scripts/token_budget.py` (enforced in tests).

### Changed
- Toolsets: with `YANDEX_TOOLSETS` unset (or `all`) every tool stays visible, which now includes all the new toolsets
  above. `YANDEX_TOOLSETS=default` keeps the read toolsets plus `account`; `direct_extra`, `webmaster_extra`,
  `metrika_logs`, `metrika_offline`, `search` and every write toolset are opt-in, e.g.
  `YANDEX_TOOLSETS=default,direct_extra`. `default` is not strictly read-only: `account` includes the stdio OAuth
  self-setup tools; add `YANDEX_READ_ONLY=1` to hide them.
- The Claude Code plugin starts the server with `YANDEX_TOOLSETS=default`. Plugin, README and Codex snippets install
  `zai-yandex-mcp[standalone] @ git+https://github.com/zai-one/yandex-mcp@v0.6.0`.
- License: LicenseRef-ZAI-ONE 2.0 (source-available). Free install and use for yourself and your organisation, including commercial work; issues and pull requests welcome; copying code into other projects, redistribution, embedding and competing hosted services need written permission. Earlier releases keep their LicenseRef-ZAI-ONE 1.0 terms (see NOTICE). README first screen: one-line scope, why this MCP, maintenance and update check.

### Security
Safeguards in the new features of this release:
- Write ledger, idempotency keys and approvals are partitioned by the resolved account profile: a write or approval
  for one account never replays or applies on another account or on the base credentials.
- Direct sandbox writes and approvals are kept apart from production: toggling `YANDEX_DIRECT_SANDBOX` never answers a
  production write from a sandbox rehearsal or applies a sandbox approval live.
- `yandex_auth_start` / `yandex_auth_finish` exist only on the local stdio transport, so an authenticated HTTP caller
  cannot replace the server's token files; results no longer show absolute token paths.
- `webmaster_start_pro_export` with the paid `use_pro_tariff=true` first returns `approval_required`; it starts only
  after a human accepts the approval bound to that exact export, and the approval is consumed once.

## 0.5.1

- Update check: a cached `latest` or `release_notes_url` is validated on read like a fresh GitHub answer; only plain
  version tags and this repository's release links reach the tool result or the server instructions.
- Update check: every attempt is recorded; after a failed fetch no new request is made for 30 minutes (the last good
  result is returned with `status: unknown`), and `force=true` is honoured at most once a minute. The startup refresh
  follows the same rules. Future timestamps in the cache file are ignored.
- The clean-install gate turns the update check off for the installed server and fails if it starts or makes an
  external DNS lookup.
- `docs/RUNTIME.md` and `.env.example` document `YANDEX_DISABLE_UPDATE_CHECK` and the startup request to
  `api.github.com`.

## 0.5.0

- Direct: `direct_get_goal_statistics` (Reports `Goals`, `AttributionModels`, row limit), goal revenue/profit/ROI fields in all supported report types, `direct_list_strategies` (Strategies.get, v501) and `strategies` in the inventory allowlist.
- Direct error codes 152 and 506 are reported as `provider_rate_limited` and start the provider cooldown.
- Webmaster v4.1 reads: SQI history, important URLs and their history, search appearance/removal events (history and samples).
- `yandex_check_update`: suggests a newer GitHub release with its notes link and update command; public API,
  3 s timeout, 24 h cache, graceful offline, no Yandex quota, never auto-updates. A cached "update available"
  hint appears in the server instructions. Opt out with `YANDEX_DISABLE_UPDATE_CHECK=1`.
- `direct_list_strategies` accepts at most 1000 ids (the Strategies.get limit); update tags must be plain versions.
- Every MCP tool publishes reviewed `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` annotations.

## 0.4.0

- Add a separate Audience credential, read scope and default-off write scope.
- Add bounded list/filter reads plus guarded circle, polygon, pixel and pixel-viewer creates.
- Preserve unknown create outcomes without automatic POST retries; checkpoint successful receipts before exact list readback.

## 0.3.0

- Metrika filters/sort and `metrika_report`: period comparison, bounded pages, provider totals, sampling and CSV.
- Local `yandex-mcp-oauth` help prints the official authorization route for your own application.

- Installable setup wizard and JSON/TOML client snippets that work outside the checkout.
- English and Russian agency information, integration contact and package installation guide.

## 0.2.0

- Explicit config-file startup and offline configuration doctor.
- Install/client guide, local setup wizard and clean-wheel validation.
- Wordstat dynamics, regions and region tree use Yandex Cloud Search API credentials and the existing cost gate. OAuth onboarding and Metrika Logs export are future work.
- LicenseRef-ZAI-ONE and Issues-based feedback policy.
