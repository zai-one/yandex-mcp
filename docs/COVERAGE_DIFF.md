# Coverage diff: zai-one/yandex-mcp vs competitors

Updated 2026-10-13 for the functional-leadership change. Competitors were read from source
(SvechaPVL/yandex-mcp, skiddgoddamn/yandex-marketing-mcp, webkoth/yandex-mcp,
axisrow/yandex-direct-mcp-plugin); every endpoint below was checked against the official
references (Direct `https://yandex.ru/dev/direct/doc/ru/`, Metrika
`https://yandex.ru/dev/metrika/ru/llms.txt`, Webmaster `https://yandex.ru/dev/webmaster/doc/ru/llms.txt`,
Audience `https://yandex.ru/dev/audience/ru/llms.txt`, Wordstat/Search API).

Competitors expose one MCP tool per CRUD verb (122–150 tools). We keep the default
`tools/list` flat (52 tools, 32 KB) and reach the same methods through opt-in toolsets:
allowlisted generic tools with a declarative catalogue (`yandex://guide/management`) and
the prepare -> approval -> apply -> reconcile flow for every write.

| | zai-one (this change) | SvechaPVL | skiddgoddamn | webkoth | axisrow |
|---|---|---|---|---|---|
| MCP tools (all / default) | 127 / 53 | ~122 | ~150 | ~85 registered | ~110 |
| Callable documented methods | 127 tools + 114 catalogue operations + 75 Direct write methods | ≈ tools | ≈ tools | ≈ tools | ≈ tools |
| Human approval bound to account + exact request | yes (all writes) | no | no | no | no |
| Idempotency ledger, readback, reconcile | yes | no | no | no | no |
| Zero-config start + OAuth device flow | yes | no | partial (token env) | no | yes (plugin OAuth) |

Legend: ✅ covered · ➕ covered only by us · — not offered · ⛔ intentionally not covered (reason).

## Direct (API v5 / v501)

| Capability | Competitors | zai-one |
|---|---|---|
| Campaigns/ad groups/ads/keywords get | all | ✅ `direct_list_campaigns`, `direct_get_inventory` |
| Campaigns add/update/suspend/resume/archive/unarchive/delete | svecha, skidd, axisrow | ✅ `direct_prepare_write` -> `direct_apply_write` (and `direct_write`) |
| Ads add/update/delete/moderate/suspend/resume/archive/unarchive | svecha, skidd, axisrow | ✅ same |
| Keywords add/update/delete/suspend/resume | all | ✅ same |
| Bids get / set / setAuto | all | ✅ `direct_get_bids` + write |
| KeywordBids get / set / setAuto | skidd, webkoth, axisrow | ✅ `direct_get_keyword_bids` + write |
| BidModifiers get / add / set / delete | svecha, skidd, axisrow | ✅ `direct_get_bid_modifiers` + write |
| AudienceTargets get / add / delete / suspend / resume / setBids | svecha, axisrow | ✅ `direct_list_audience_targets` + write |
| RetargetingLists get / add / update / delete | svecha, skidd, axisrow | ✅ list + write |
| Sitelinks, AdImages, AdExtensions (callouts) get/add/delete | all | ✅ lists + write (AdImages by hash) |
| VCards get / add / delete | all | ✅ read + approval-only write (`direct_prepare_write` -> `direct_apply_write`); see *Legacy-reference services* below |
| AdVideos, Creatives get / add | skidd, axisrow | ✅ lists + write |
| Feeds get / add / update / delete | svecha, skidd, axisrow | ✅ `direct_list_feeds` + write |
| NegativeKeywordSharedSets get / add / update / delete | skidd, axisrow | ✅ list + write |
| DynamicTextAdTargets (webpages) get / add / delete / suspend / resume / setBids | webkoth, axisrow | ✅ list + approval-only write (`direct_prepare_write` -> `direct_apply_write`); see *Legacy-reference services* below |
| DynamicFeedAdTargets get / add / delete / suspend / resume / setBids | axisrow | ✅ `direct_list_dynamic_feed_ad_targets` + approval-only write (`direct_prepare_write` -> `direct_apply_write`); see *Legacy-reference services* below |
| SmartAdTargets get / add / update / delete / suspend / resume / setBids | svecha, skidd, axisrow | ✅ list + approval-only write (`direct_prepare_write` -> `direct_apply_write`); see *Legacy-reference services* below |
| Clients update; unified performance campaigns / groups add / update (v501) | — | ✅ `direct_prepare_write` -> `direct_apply_write` (approval-only, v501 routed) |
| Strategies (v501) get / add / update / archive / unarchive | axisrow | ✅ `direct_list_strategies` + approval-only write |
| AgencyClients get / update | skidd, axisrow | ✅ `direct_list_agency_clients` + approval-only update |
| AgencyClients addPassportOrganizationMember | — | ✅ approval-only write, high risk (one invite: login, ADMIN/USER, Phone xor Email; 10/day) |
| AgencyClients add / addPassportOrganization | axisrow | ⛔ `add` is no longer supported (changelog 16.06.2025); `addPassportOrganization` needs a second user's token in `Member-Authorization`, which this server never holds; do it in the Direct UI |
| Dictionaries getGeoRegions | — | ✅ `direct_get_inventory(service="dictionaries", params={SelectionCriteria, FieldNames})` |
| Clients get | all | ✅ `direct_get_client` (no phones by default) |
| Dictionaries, Changes, TurboPages, Leads, Businesses | all | ✅ `direct_extra` |
| Reports (custom, goals, attribution) | all | ✅ `direct_get_statistics`, `direct_get_goal_statistics` |
| KeywordsResearch hasSearchVolume / deduplicate | all / skidd, axisrow | ✅ `direct_keyword_forecast`, `direct_deduplicate_keywords` |
| Sandbox | — | ➕ `YANDEX_DIRECT_SANDBOX`, approvals bound to endpoint |
| Balance (Live v4 AccountManagement) | axisrow | ⛔ v4 Live only, token in request body; campaign Funds via `direct_get_campaign` |
| "Masters" (Мастер кампаний) via browser automation | axisrow | ⛔ not a public API (Playwright scraping of the UI) |
| Per-change history | axisrow | ⛔ no public v5 method; use Changes (`direct_check_changes`) |

### Legacy-reference services (evidence, checked 2026-10-13)

VCards, DynamicTextAdTargets, SmartAdTargets and DynamicFeedAdTargets have no method pages in
the current reference navigation (`/dev/direct/doc/ru/<service>/add` -> 404; the old
`ref-v5/<service>/add.html` URLs 301 there). They are **not** removed:

- the changelog (https://yandex.ru/dev/direct/doc/ru/changelog) announces their introduction
  (DynamicFeedAdTargets 23.06.2021, SmartAdTargets 16.03.2020) and no withdrawal, while it does
  record other withdrawals (e.g. AgencyClients.add, 16.06.2025);
- current guides still use them: best-practice/smart-ads ("метод SmartAdTargets.add"),
  concepts/contact-info (business cards -> vCards service), ads/get (`VCardIds`,
  `VCardModerationStatuses`), concepts/errors-list ("удалить визитку, которая используется");
- a 2025-09-17 crawl of the official English reference still lists VCards add/get/delete with
  the v5/v501 WSDL and JSON endpoints.

A live probe was not possible (api.direct.yandex.com answers 204 to any unauthenticated request).
So the writes are restored, but approval-only, with readback by Id.

## Not available through an official API (checked 2026-10-13)

| Area | Status | Source |
|---|---|---|
| Yandex Business organization cards (edit profile, hours, prices, photos) | ⛔ no public API; chains update via an XML/CSV file URL set in the cabinet | https://yandex.ru/support/business-priority/ru/branches/branches-xml |
| Yandex Business reviews and replies | ⛔ replies only in the cabinet ("Отвечать на отзывы можно только в Личном кабинете"); third-party clients use session cookies, which we never do | https://yandex.ru/support/business-priority/ru/reviews/respond |
| Promotion in Maps (former Geoadv / Business subscription) | ⚠️ through Direct only: unified campaigns (`Maps`, `SearchOrganizationList` fields, v501) and `BusinessId` in ads; profiles via `direct_list_businesses` | Direct changelog, https://yandex.ru/support/direct/ru/company-ads/create-upc |
| Maps Places (Geosearch) API | ⚠️ official but a separate paid key for public search with display/storage restrictions; not account management, not added | https://yandex.ru/maps-api/docs/geosearch-api/ |
| Yandex Business booking API | ⛔ an API Yandex calls on the partner, not one we call | https://yandex.ru/support/business-priority/ru/manage/booking-api-partners |
| Turbo pages | ⛔ no longer shown in search since 2 April; only Direct `turbopages.get` / leads stay | https://yandex.ru/dev/turbo/doc/ru/ |
| Webmaster original texts | ⛔ tool discontinued | https://yandex.ru/dev/webmaster/doc/ru/reference/host-original-texts-post |
| Webmaster site quality / reviews | ✅ SQI history, summary, diagnostics already covered; there is no reviews resource in Webmaster v4 | https://yandex.ru/dev/webmaster/doc/ru/llms.txt |

## Metrika (Management, Reporting, Logs)

| Capability | Competitors | zai-one |
|---|---|---|
| Counters list / get | all | ✅ `metrika_list_counters`, `metrika_manage_read counter_get` |
| Counter create / update / delete / undelete | svecha, skidd | ✅ `metrika_manage_*` (undelete ➕) |
| Goals CRUD | all | ✅ `metrika_list_goals`, `metrika_apply_goal`, `goal_list/get/create/update/delete` |
| Pivot report; offline conversions (BASIC/CALLS/CHATS) | — | ✅ `stat_pivot`, `offline_conversions_upload` + uploadings |
| Audience pixel create, geo circle/polygon segments, data-file upload | — | ✅ `pixel_create`, `segment_create_geo(_polygon)`, `segment_upload_file` |
| Filters, Operations, Segments CRUD | svecha, skidd | ✅ catalogue |
| Grants list/get/create/update/delete, public grants, my_grant | svecha, skidd | ✅ catalogue (login as `user_login` query per docs; competitors put it in the path) |
| Labels CRUD, link/unlink counter | svecha, skidd | ✅ catalogue |
| Chart annotations CRUD | svecha, skidd | ✅ catalogue |
| Delegates list/add/delete, accounts, clients | svecha, skidd | ✅ catalogue |
| Access filters CRUD | — | ➕ catalogue |
| Reports data / bytime / drilldown / comparison / comparison drilldown | all | ✅ `metrika_get_statistics`, `metrika_report`, `stat_*` operations |
| Logs API evaluate/create/list/get/download/clean | skidd, webkoth | ✅ `metrika_logs_*` |
| Offline conversions upload/list/get | all | ✅ `metrika_*_offline_conversion*` (local CSV validation ➕) |
| Calls upload + uploadings | svecha, skidd | ✅ `calls_upload`, `calls_uploadings`, `calls_uploading` |
| Expenses upload / delete + uploadings | svecha, skidd | ✅ `expense_upload`, `expense_delete`, `expense_uploading(s)` |
| User params upload / confirm / uploadings | svecha, skidd | ✅ catalogue |
| CRM simple orders upload, last uploadings | skidd | ✅ `crm_simple_orders_upload`, `crm_last_uploadings` |
| Chats upload | — | ➕ `chats_upload` |
| CRM contacts/orders JSON, products, attributes, order statuses | — | ⛔ CDP schema management; low agent value, large PII payloads |
| Measurement Protocol tokens, Cloud export | — | ⛔ credential issuance / infrastructure; not an agent task |

## Webmaster (v4 / v4.1)

| Capability | Competitors | zai-one |
|---|---|---|
| User, hosts list/get, summary, SQI, diagnostics | all | ✅ default `webmaster` |
| Host add / delete | all | ✅ `webmaster_manage host_add`, `host_delete` |
| Verification status / start, owners | all | ✅ `webmaster_get_verification`, `verification_start`, `webmaster_get_owners` |
| Popular queries, query history, all-queries history, query analytics | all | ✅ (`search_queries_all_history` in catalogue) |
| Indexing history/samples, in-search history/samples, search events | all | ✅ |
| Important URLs + history | webkoth | ✅ |
| External / broken internal links + history | all | ✅ |
| Sitemaps list/get, user sitemaps add/delete | all | ✅ `webmaster_prepare_sitemap`, `sitemap_delete` |
| Sitemap priority recrawl (v4.1) + quota | webkoth | ✅ `sitemap_recrawl`, `sitemap_recrawl_quota` |
| Recrawl submit / queue / task / quota | all | ✅ prepare/apply/reconcile |
| Feeds list/info/problems/add/batch add/remove/change regions/moderation | webkoth | ✅ `webmaster_extra` + catalogue |
| Indexing archive create / status | webkoth | ✅ catalogue |
| Pro: limits, dates, regions, export start/status | webkoth | ✅ `webmaster_extra`, `webmaster_start_pro_export` |

## Wordstat / Search API

| Capability | Competitors | zai-one |
|---|---|---|
| Top requests, dynamics, regions, regions tree | all | ✅ `yandex_wordstat_*` |
| userInfo (quota) | svecha, skidd | ⛔ v1 decommissioned; quota errors are surfaced in error hints |
| SERP (Search API) async jobs | — | ➕ `yandex_serp_*` with paid-cost approval |

## Audience

| Capability | Competitors | zai-one |
|---|---|---|
| Segments/pixels list | — | ➕ default `audience` |
| Geo circle/polygon, pixel segments | — | ➕ `audience_create_*` |
| CSV/file upload + confirm, modify data, lookalike/Metrika/AppMetrica segments, update/delete/reprocess, grants, delegates, pixel update/delete/undelete, accounts | — | ➕ `audience_manage_*` |

## Account binding of approvals (verified)

Before this change an approval row already carried the named account / Direct endpoint and
`get_approval` hid foreign rows, but two gaps existed: (1) the approvals CLI could not name the
account, and `accept` / `_consume` did not check the scope, so an operator could accept a
change prepared for another named account without seeing it; (2) `direct_apply_changes`
hashed only the items, so an approval for `update` could execute another method on the same ids.
Now `accept` and `_consume` verify the scope, the CLI takes `--named-account` and reports it,
`direct_apply_changes` runs only Campaigns.update, and `direct_prepare_write` / every
`*_manage_prepare` hash service/method/args together with the resolved account and endpoint.
