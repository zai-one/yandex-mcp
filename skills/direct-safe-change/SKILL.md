---
name: direct-safe-change
description: Make a Yandex Direct change safely - prepare a diff, show cost and impact, get explicit approval, apply once with an idempotency key, verify. Use for изменить ставки/бюджет, pause campaign, add negatives, Direct write.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server.
metadata:
  mcp-server: zai-yandex-mcp
---

# Direct safe change

1. Read the current state (`direct_get_campaign` / `direct_get_inventory`) for every object you will touch.
2. Campaigns: `direct_prepare_changes(changes)`; any other service/method (ads, keywords, bids, modifiers, sitelinks...): `direct_prepare_write(service, method, items)` -> diff, hash, `approval_id`, `account`.
3. Show `templates/change-request.md`: before/after, objects, units cost (`references/api-units.md`), risk, rollback.
4. Wait for explicit approval; the operator accepts it with the approvals CLI (same `--named-account`), not an MCP tool (`references/prepare-apply.md`). Approvals expire after 30 minutes.
5. `direct_apply_changes` / `direct_apply_write(..., approval_id, idempotency_key)`; on timeout retry with the SAME key.
6. Verify by re-reading and report.

Guardrails: one change set per approval; never widen the change after approval; budgets/bids changes always quoted in rubles and percent.
