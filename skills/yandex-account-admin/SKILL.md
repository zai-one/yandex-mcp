---
name: yandex-account-admin
description: Administer Metrika, Webmaster and Audience objects safely - grants, delegates, filters, segments, labels, annotations, counters, hosts, feeds, CSV uploads (calls, expenses, user params). Use for доступы, представители, добавить сайт, загрузить звонки/затраты.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server with metrika_manage, webmaster_manage or audience_manage toolsets.
metadata:
  mcp-server: zai-yandex-mcp
---

# Yandex account admin (Metrika / Webmaster / Audience)

Toolsets: `YANDEX_TOOLSETS=default,metrika_manage,webmaster_manage,audience_manage` (opt-in).

1. Find the operation: `metrika_manage_read(operation="catalog")` (or `webmaster_manage_read` /
   `audience_manage_read`). Same table offline: `references/management.md`. Each row has HTTP verb, path ids, allowed query keys, risk and the doc link.
2. Read current state first: e.g. `metrika_manage_read("grant_list", {"counter_id": 123})`,
   `webmaster_manage_read("feeds_list", {"host_id": "https:example.com:443"})`.
3. `metrika_manage_prepare(operation, args)` returns `approval_id`, `request_hash`, risk and the
   resolved `account`. Nothing is sent yet.
4. Show the user the exact change (operation, ids, body, csv row count, risk). Risk `high` =
   deletes, grants, delegates: say what access is lost or given.
5. The user accepts the approval out of band with the approvals CLI (`references/management.md`); acceptance is bound
   to the same account and exact request, expires in 30 min.
6. `metrika_manage_apply(operation, approval_id, idempotency_key, args)` with the same args; it
   reads back the object. Timeout / unknown outcome: `metrika_manage_reconcile` with the same key;
   never resend.

Guardrails: one object change per approval; never edit args after approval (hash mismatch fails
closed); uploads are CSV text <= 8 MiB; grants/delegates only for logins the user named.
