# Tool map

Every tool by toolset. Enable a toolset with YANDEX_TOOLSETS (default = marked *).
yandex_list_accounts appears only with YANDEX_ACCOUNTS_FILE.

## account*
Update check and named accounts (yandex_list_accounts when YANDEX_ACCOUNTS_FILE is set).
- `yandex_check_update` - Check GitHub for a newer zai-yandex-mcp release and suggest the update command.
- `yandex_list_accounts` - Lists configured named accounts (local).

## direct_read*
Direct campaigns, inventory (ads, groups, keywords...), strategies, forecasts.
- `direct_list_campaigns` - List Direct campaigns through the strict read allowlist.
- `direct_get_campaign` - Get one Direct campaign by numeric id.
- `direct_get_inventory` - Run a provider-native get against the explicit Direct inventory allowlist.
- `direct_list_strategies` - List Direct shared (portfolio) strategies via Strategies.get v501 with paging.
- `direct_keyword_forecast` - Forecast search-volume presence by device for phrases and regions.
- `direct_list_agency_clients` - List an agency's Direct clients (Login, ClientId, ClientInfo, Currency...) via AgencyClients.get

## direct_extra
Opt-in Direct reads: client, dictionaries, changes, bids, bid modifiers, audiences, feeds, creatives, extensions, turbo pages, leads.
- `direct_get_client` - Own Direct account info (Login, ClientId, currency, type) via Clients.get
- `direct_get_dictionaries` - Direct reference dictionaries (Currencies, GeoRegions, TimeZones, Constants, Interests, AdCategories...).
- `direct_check_changes` - What changed in Direct since timestamp (UTC ISO, Z) via Changes: checkCampaigns (which campaigns), check (objects in given ids), checkDictionaries.
- `direct_get_bid_modifiers` - Bid adjustments (mobile, demographics, retargeting, regional...) by campaign/group/id
- `direct_list_ad_extensions` - Callout extensions (Id, Type, Status, text).
- `direct_list_audience_targets` - Audience targets (retargeting/interest) by Ids, AdGroupIds or CampaignIds.
- `direct_list_retargeting_lists` - Retargeting and audience-selection lists (Id, Type, Name, IsAvailable).
- `direct_list_creatives` - Creatives (image/HTML5/video/smart): Id, Type, Name, size.
- `direct_list_feeds` - Product feeds: status, item count, update time.
- `direct_list_turbo_pages` - Turbo pages (Id, Name, Href)
- `direct_list_negative_keyword_sets` - Shared negative keyword sets by Ids (required by the API).
- `direct_list_ad_videos` - Ad video status by video Ids (required by the API).
- `direct_get_keyword_bids` - Current keyword bids and serving status by CampaignIds, AdGroupIds or KeywordIds (one required).
- `direct_list_leads` - Turbo page leads metadata by TurboPageIds

## direct_reports*
Direct Reports service: statistics and goal statistics.
- `direct_get_statistics` - Read campaign statistics
- `direct_get_goal_statistics` - Direct Reports statistics broken down by 1-10 Metrika goals and attribution models (FCCD, LC, LSCCD, AUTO).

## direct_write
Direct changes: prepare -> human approval -> apply; generic direct_write.
- `direct_prepare_changes` - Draft a Direct change set and open an unaccepted, expiring approval.
- `direct_apply_changes` - Apply an accepted Direct change set (from `direct_prepare_changes`, approved by the operator) exactly once with approval_id + idempotency_key
- `direct_write` - Generic idempotent Direct API write (service, method, items) behind the server write flag and audit log.

## metrika*
Metrika counters, goals, Reporting API and period comparison.
- `metrika_list_counters` - List Metrika counters through a server-side read-only credential.
- `metrika_list_goals` - List goals for one positive Metrika counter id.
- `metrika_get_statistics` - Run a bounded read-only Metrika reporting request.
- `metrika_report` - One-call Metrika report with optional period comparison (compare_date1/2): totals, abs/% deltas, sampling info, JSON or CSV.

## metrika_offline
Offline conversions: local CSV validation and upload status (read).
- `metrika_validate_offline_conversions` - Validate offline conversion rows locally (Target, DateTime past Unix secs, one of UserId/ClientId/Yclid/PurchaseId, Price, Currency)
- `metrika_list_offline_conversion_uploads` - List offline conversion uploads of a counter (id, status, line counts, type).
- `metrika_get_offline_conversion_upload` - Status of one offline conversion upload (processing can take up to 3 hours).

## metrika_logs
Metrika Logs API: evaluate, list, status, preview download (read).
- `metrika_logs_evaluate` - Check whether a Metrika Logs API export fits the counter quota (possible, max days).
- `metrika_logs_list` - List Logs API requests of a counter with status (created, processed, canceled...) and parts.
- `metrika_logs_get` - Status and parts of one Logs API request
- `metrika_logs_download_part` - Preview one processed Logs API part as columns + up to max_rows (<=1000) rows

## metrika_logs_write
Create Logs API exports and clean/cancel them (quota; YANDEX_METRIKA_LOGS_ENABLED).
- `metrika_logs_create` - Create a Metrika Logs API export (spends the 10 GB per-counter quota).
- `metrika_logs_cleanup` - Free Logs API quota: action=clean deletes prepared files of a processed request, cancel stops an unprocessed one.

## metrika_write
Create/update/delete Metrika goals; upload offline conversions.
- `metrika_apply_goal` - Autonomously create, update or delete a Metrika goal (idempotent, readback).
- `metrika_upload_offline_conversions` - Upload offline conversions to Metrika (affects goal stats and Direct optimisation).

## webmaster*
Webmaster hosts, summary, SQI, diagnostics, queries, indexing, links, sitemaps.
- `webmaster_get_user` - Read the OAuth account user identifier
- `webmaster_list_hosts` - Read one bounded page of sites accessible to the server account.
- `webmaster_get_host` - Read metadata for an exact Webmaster host identifier.
- `webmaster_get_verification` - Read ownership verification state without starting verification.
- `webmaster_get_summary` - Read indexing and site summary
- `webmaster_get_diagnostics` - Read problems with native severity, presence state and state-change time.
- `webmaster_get_query_history` - Read at most 31 days of aggregate or selected-query indicators.
- `webmaster_list_popular_queries` - Read one page of popular queries
- `webmaster_get_indexing_history` - Read at most 31 days of indexing history.
- `webmaster_list_indexing_samples` - Read one page of indexing samples with continuation metadata.
- `webmaster_list_search_urls` - Read one page of URLs in search
- `webmaster_get_search_urls_history` - Read at most 31 days of search URL count history.
- `webmaster_get_search_events_history` - Read at most 31 days of APPEARED_IN_SEARCH / REMOVED_FROM_SEARCH page counts.
- `webmaster_list_search_events` - Read one page (limit 1-100) of pages that appeared in or were removed from search.
- `webmaster_get_sqi_history` - Read site quality index (SQI/ИКС) history
- `webmaster_list_important_urls` - Read one page of monitored important URLs with indexing and search status.
- `webmaster_get_important_url_history` - Read change history of one monitored URL
- `webmaster_list_sitemaps` - Read one sitemap page using the upstream sitemap identifier cursor.
- `webmaster_get_sitemap` - Read detected sitemap details or registration of a submitted sitemap.
- `webmaster_list_user_sitemaps` - Read one page of submitted sitemaps with an exclusive identifier cursor.
- `webmaster_list_external_links` - Read one page of external link samples.
- `webmaster_get_external_links_history` - Read at most 31 days of external link history.
- `webmaster_list_broken_links` - Read one page of broken internal link samples.
- `webmaster_get_broken_links_history` - Read at most 31 days of broken internal link history.
- `webmaster_list_recrawl_queue` - Read one page of asynchronous recrawl requests.
- `webmaster_get_recrawl_quota` - Read the daily quota and remaining submissions.
- `webmaster_get_recrawl_task` - Read IN_PROGRESS/DONE/FAILED

## webmaster_extra
Opt-in Webmaster reads: owners, feeds, regions, query analytics with filters.
- `webmaster_get_owners` - Verified owners of a Webmaster host (login, verification type/date).
- `webmaster_list_feeds` - Product/service feeds added to a Webmaster host.
- `webmaster_get_feed` - One Webmaster feed's status and errors by feed URL.
- `webmaster_get_feed_problems` - Host-level feed problems in Webmaster (blocking offers).
- `webmaster_list_regions` - Webmaster region directory (ids for `webmaster_query_analytics` region_ids), filter by name.
- `webmaster_query_analytics` - Webmaster query/URL analytics with region and text/statistic filters (up to 500 rows).

## webmaster_write
Recrawl and sitemap submission: prepare -> apply -> reconcile.
- `webmaster_prepare_recrawl` - Prepare exactly one URL for human approval after ownership, quota and duplicate checks.
- `webmaster_apply_recrawl` - Submit one accepted URL exactly once.
- `webmaster_prepare_sitemap` - Prepare one sitemap registration for expiring human approval.
- `webmaster_apply_sitemap` - Submit one accepted sitemap with a durable key and registration readback.
- `webmaster_reconcile_action` - Read and reconcile this principal's existing action.

## wordstat*
Wordstat top requests, dynamics, regions (paid per call).
- `yandex_wordstat_get_top` - Wordstat top requests + associations
- `yandex_wordstat_dynamics` - Get Wordstat demand over time using UTC dates YYYY-MM-DD and the server budget gate.
- `yandex_wordstat_regions` - Get Wordstat demand by region for the last 30 days through the server budget gate.
- `yandex_wordstat_regions_tree` - Get the Wordstat region tree through Cloud credentials and the server budget gate.

## search
Yandex Search API SERP jobs: prepare -> submit (paid) -> status -> result.
- `yandex_serp_prepare` - Prepare an asynchronous Yandex SERP request without a live provider call.
- `yandex_serp_submit` - Queue an approved asynchronous Yandex SERP request.
- `yandex_serp_status` - Return durable status for an asynchronous Yandex SERP job.
- `yandex_serp_get_result` - Return raw provenance metadata and normalized Yandex SERP output.

## audience*
Audience segments and pixels (read) and offline create validation.
- `audience_list_segments` - List Audience segments visible to the server-side Audience credential.
- `audience_get_segment` - Read one segment by filtering the documented authoritative segment list.
- `audience_list_pixels` - List Audience tracking pixels visible to the server-side credential.
- `audience_get_pixel` - Read one pixel by filtering the documented authoritative pixel list.
- `audience_validate_create` - Validate a closed Audience create payload offline and return its immutable confirmation hash.

## audience_write
Create Audience geo/pixel segments with confirmation_hash; reconcile.
- `audience_create_geo_circle` - Run one guarded geo-circle create
- `audience_create_geo_polygon` - Run one guarded geo-polygon create
- `audience_create_pixel` - Run one guarded tracking-pixel create
- `audience_create_pixel_viewers` - Run one guarded pixel-viewer segment create
- `audience_reconcile_create` - Reconcile one owned pending create by saved object id without sending another POST.
