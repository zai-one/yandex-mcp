# Yandex Webmaster provenance

Reviewed 2026-09-07. This provider is a native Python adaptation inside ZAI MCP;
it does not execute either upstream server or import their OAuth storage.

| Source | Pinned commit | License | Adapted or consulted parts |
| --- | --- | --- | --- |
| [weselow/Yandex-webmaster-mcp-server](https://github.com/weselow/Yandex-webmaster-mcp-server) | `ee9a780151233e48551d915a26ec913ad3957fa8` | MIT; [preserved notice](weselow-LICENSE) | `src/client/yandex-webmaster-client.ts`: user resolution, closed method-to-endpoint mapping, query/device vocabulary and JSON bodies. `src/client/types.ts`: host, summary, history, sitemap and recrawl field names. `tests/client/client.test.ts`, `tests/tools/actions.test.ts`: scenarios for user caching, authentication, URL construction, query parameters, recrawl queue/task and POST bodies. Ported to `src/zai_yandex/adapters/webmaster.py` and `tests/test_webmaster_adapter.py`. |
| [webkoth/yandex-mcp](https://github.com/webkoth/yandex-mcp) | `310369b32df2489035ef2603d71b92a678d70bbe` | MIT; [preserved notice](webkoth-LICENSE) | `src/webmaster/recrawl.ts` consulted to cross-check queue, quota, task status and request bodies. No code imported or separate server dependency. |

The Python HTTP transport, OAuth server injection, response sanitation, bounded
pagination, validation, data availability metadata and write governance are ZAI
platform adaptations. Upstream permissive mutation tools (host removal, owners,
verification, sitemap removal, feeds and original texts) are not imported.

Official documentation overrides donor code. Corrections found during adaptation:

- Verification reads use `/verification`, whereas the primary donor used
  `/owner-verification`.
- Discovered sitemaps paginate with `from=<sitemap-id>`; user-added sitemaps use
  `offset=<sitemap-id>`, both exclusive cursors, not numeric row offsets.
- External link history requires `indicator=LINKS_TOTAL_COUNT` and does not
  document server date parameters. The requested window is filtered locally.
- Query history sends repeated `query_indicator` parameters when several metrics
  are requested. Undefined metrics stay absent.

Official API reference: https://yandex.com/dev/webmaster/doc/en/ .
The preserved tool schemas are in contracts/yandex.json.
