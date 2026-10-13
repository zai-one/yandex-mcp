# Safe writes: prepare -> approve -> apply -> verify

- Direct: `direct_prepare_changes(changes)` returns a diff, its hash and `approval_id` (TTL 30 min); the local operator accepts it outside MCP with `uv run python -m zai_yandex.approvals --state-path ... --account ... --principal ... --approval-id UUID --request-hash SHA256 --accept` (see docs/RUNTIME.md); then `direct_apply_changes(..., approval_id, idempotency_key)`; the server re-reads campaigns to confirm. `direct_write` is the generic idempotent path for other services and still needs the write flag/scope.
- Webmaster: `webmaster_prepare_recrawl|sitemap` -> `webmaster_apply_*` with approval_id + idempotency_key; uncertain results -> `webmaster_reconcile_action`.
- Audience: `audience_validate_create` returns `confirmation_hash`; pass it to `audience_create_*`; uncertain -> `audience_reconcile_create`.
- Search API: `yandex_serp_prepare` -> show the cost note -> `yandex_serp_submit` (paid) -> `yandex_serp_status` -> `yandex_serp_get_result`.
Rules: show the exact diff and its cost (units, money, quota) before asking; one approval per change set; reuse the idempotency_key on retry; never "fix" a failed apply by re-preparing a different change silently.
