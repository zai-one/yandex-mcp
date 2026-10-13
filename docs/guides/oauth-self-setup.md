# OAuth self-setup

Full detail: packaged guide `yandex://guide/oauth` (`src/zai_yandex/resources/guide/oauth.md`).

Official Yandex docs:

- Device code: https://yandex.ru/dev/id/doc/ru/codes/screen-code-oauth
- Refresh: https://yandex.ru/dev/id/doc/ru/tokens/refresh-client

Tools: `yandex_auth_start` → user confirms → `yandex_auth_finish` (hot reload, 0600 files). CLI: `yandex-mcp-oauth --device --client-id ...`.
