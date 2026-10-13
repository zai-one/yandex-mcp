# OAuth self-setup

Connect a Yandex ID to this MCP without restarting the server.

## Agent tools (toolset `account`, in `default`)

1. `yandex_auth_start` — begin the [device-code flow](https://yandex.ru/dev/id/doc/ru/codes/screen-code-oauth). Returns `verification_url` / `verification_link` and `user_code`. Never returns a token. Optional MCP URL elicitation when the client declares it.
2. User opens the link, enters the code, allows access.
3. `yandex_auth_finish` — polls `POST https://oauth.yandex.ru/token` (`grant_type=device_code`). On success writes owner-private (0600) token files and hot-reloads credentials.
4. `yandex_auth_status` — which services have tokens (booleans only).

Pass `client_id` (32 hex chars from [oauth.yandex.ru](https://oauth.yandex.ru/)) or set `YANDEX_OAUTH_CLIENT_ID`. Optional `YANDEX_OAUTH_CLIENT_SECRET` for confidential apps (public clients usually need no secret for device code; some apps require it on `/token`).

`services`: subset of `direct`, `metrika`, `webmaster`, `audience` (default: all four). Same access token is written to each selected file. With named accounts (`YANDEX_ACCOUNTS_FILE`), pass `account=<name>`.

## CLI

```sh
yandex-mcp-oauth --device --client-id YOUR_32_CHAR_CLIENT_ID
# optional: --client-secret ... --services metrika,direct --directory .
yandex-mcp-oauth --client-id YOUR_32_CHAR_CLIENT_ID   # implicit-flow URL only (manual paste into setup)
```

## Refresh

When the token response includes `refresh_token`, it is saved under `secrets/oauth.refresh` (or `secrets/<account>.oauth.refresh`). Use it with `grant_type=refresh_token` against the same client (CLI/library helpers can call the refresh endpoint; the running server hot-reloads after a successful device finish).

## Safety

- Access and refresh tokens never appear in tool output or logs (redacted).
- `device_code` stays server-side only.
- Token files are created with mode 0600.
