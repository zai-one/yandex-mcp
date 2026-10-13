---
name: yandex-agency-accounts
description: Work across several Yandex accounts or agency clients - list clients, set up named profiles, pick account per call, rehearse in Direct Sandbox. Use for агентство, клиенты агентства, несколько аккаунтов, Client-Login, песочница, multi-account.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server.
metadata:
  mcp-server: zai-yandex-mcp
---

# Agency and multi-account work

1. `yandex_list_accounts` -> configured profiles (absent = single account; see `references/accounts.md`).
2. Agency without profiles: `direct_list_agency_clients` -> Logins; propose `templates/accounts.json` for `YANDEX_ACCOUNTS_FILE` (user restarts the server).
3. Every call for a client: add `account="<name>"`. Label every table/row with the account.
4. Cross-client report: loop accounts, same period and fields; sum only in one currency.
5. Changes: rehearse with a sandbox profile first, then the `direct-safe-change` flow with the account named.

Guardrails: never ask for tokens in chat; token files stay on disk (chmod 600); do not mix client data in one write.
