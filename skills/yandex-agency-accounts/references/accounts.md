# Accounts, agencies and sandbox

## Which account am I using?
- `yandex_list_accounts` (only when `YANDEX_ACCOUNTS_FILE` is set): profile names, Direct Client-Login, own tokens, sandbox.
- Pass `account=<name>` to any tool; omitted = the file's `default`, else the base credentials.
- No accounts file: one account; for an agency token set `YANDEX_DIRECT_CLIENT_LOGIN`.

## Agency workflow
1. `direct_list_agency_clients` -> client `Login`s (agency token only; 10 units + 1 per client).
2. Add each client as a profile: `{"client-a": {"direct_client_login": "<Login>"}}` (inherits the agency token).
3. Work per client: `direct_list_campaigns(account="client-a")`; never mix clients in one table without a column.
4. Writes per client go through the same prepare -> approval -> apply; name the account in the change request.

## Separate accounts
Profile with own token files: `{"shop-b": {"direct_token_file": "shop-b.token", "metrika_token_file": "m.token"}}`.
Token files are owner-private (chmod 600); paths are relative to the accounts file; tokens are never inline.

## Sandbox
`YANDEX_DIRECT_SANDBOX=true` or `"direct_sandbox": true` per profile -> `api-sandbox.direct.yandex.com` (test data;
enable once in Direct: API settings -> Sandbox). Rehearse writes there; reports contain simulated data.
Docs: https://yandex.ru/dev/direct/doc/ru/concepts/sandbox , https://yandex.ru/dev/direct/doc/ru/agencyclients/get
