# Changelog

## Unreleased

- Direct: `direct_get_goal_statistics` (Reports `Goals`, `AttributionModels`, row limit), goal revenue/profit/ROI fields in all supported report types, `direct_list_strategies` (Strategies.get, v501) and `strategies` in the inventory allowlist.
- Direct error codes 152 and 506 are reported as `provider_rate_limited` and start the provider cooldown.
- Webmaster v4.1 reads: SQI history, important URLs and their history, search appearance/removal events (history and samples).
- `yandex_check_update`: suggests a newer GitHub release with its notes link and update command; public API,
  3 s timeout, 24 h cache, graceful offline, no Yandex quota, never auto-updates. A cached "update available"
  hint appears in the server instructions. Opt out with `YANDEX_DISABLE_UPDATE_CHECK=1`.
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
