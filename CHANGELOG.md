# Changelog

## Unreleased

- Opt-in toolsets `direct_extra` (14 reads: `direct_get_client`, `direct_get_dictionaries`, `direct_check_changes`, `direct_get_bid_modifiers`, `direct_get_keyword_bids`, `direct_list_audience_targets`, `direct_list_retargeting_lists`, `direct_list_feeds`, `direct_list_creatives`, `direct_list_ad_extensions`, `direct_list_turbo_pages`, `direct_list_leads`, `direct_list_ad_videos`, `direct_list_negative_keyword_sets`) and `webmaster_extra` (6 reads: `webmaster_get_owners`, `webmaster_list_feeds`, `webmaster_get_feed`, `webmaster_get_feed_problems`, `webmaster_list_regions`, `webmaster_query_analytics`). Every endpoint links its official doc page; not in `default`, so the default tool list is unchanged.

- License: LicenseRef-ZAI-ONE 2.0 (source-available). Free install and use for yourself and your organisation, including commercial work; issues and pull requests welcome; copying code into other projects, redistribution, embedding and competing hosted services need written permission. Earlier releases keep their LicenseRef-ZAI-ONE 1.0 terms (see NOTICE). README first screen: one-line scope, why this MCP, maintenance and update check.

- Metrika offline conversions: `metrika_validate_offline_conversions` (local CSV validation, confirmation_hash), `metrika_upload_offline_conversions` (multipart upload; `YANDEX_METRIKA_WRITE_ENABLED`, metrika write scope, hash + idempotency key, never auto-retried), `metrika_list_offline_conversion_uploads`, `metrika_get_offline_conversion_upload`. Toolset `metrika_offline`; upload joins `metrika_write`. Skill `metrika-offline-conversions`, guide `yandex://guide/offline-conversions`.
- Wordstat `yandex_wordstat_get_top` gains optional `devices` and validates `num_phrases` 1..2000 before reserving budget (Search API v2 GetTop).
- Metrika Logs API: `metrika_logs_evaluate`, `metrika_logs_list`, `metrika_logs_get`, `metrika_logs_download_part` (bounded TSV preview) and opt-in `metrika_logs_create` / `metrika_logs_cleanup` (`YANDEX_METRIKA_LOGS_ENABLED`, metrika write scope, idempotency ledger). Toolsets `metrika_logs`, `metrika_logs_write`.
- Direct `direct_list_agency_clients` (AgencyClients.get, compact non-PII fields) for agency accounts.

- Toolsets (`YANDEX_TOOLSETS`, `YANDEX_TOOLS`, `YANDEX_DISABLED_TOOLS`, `--toolsets`) and read-only mode (`YANDEX_READ_ONLY`, `--read-only`, driven by the reviewed annotations). Unset = previous surface.
- Router `instructions`; compact descriptions for the five tools over 300 chars; error envelopes gain `hint` and `docs`.
- Resources `yandex://toolsets`, `yandex://playbook`, `yandex://guide/{topic}`; prompts `campaign_audit`, `weekly_report`, `seo_audit`, `budget_scaling`.
- Self-contained skills (`skills/`), Claude Code plugin + marketplace (`.claude-plugin/`), `server.json`, `scripts/token_budget.py` (enforced in tests).

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
