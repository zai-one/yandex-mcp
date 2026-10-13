# Webmaster diagnostics and site health

Docs: https://yandex.ru/dev/webmaster/doc/ru/
- `webmaster_get_summary`: SQI (ИКС), searchable pages, excluded pages, problem counts by severity (FATAL, CRITICAL, POSSIBLE_PROBLEM, RECOMMENDATION).
- `webmaster_get_diagnostics`: problem types with state (PRESENT/ABSENT/UNDEFINED); fix FATAL then CRITICAL first. Common: `NO_ROBOTS_TXT`, `NO_SITEMAPS`, `DOCUMENTS_MISSING_TITLE`, `DOCUMENTS_MISSING_DESCRIPTION`, `NO_METRIKA_COUNTER_BINDING`, `SLOW_AVG_RESPONSE_TIME`, `SSL_CERTIFICATE_ERROR`, `MAIN_MIRROR_IS_NOT_HTTPS`, `DUPLICATE_PAGES`, `DISALLOWED_IN_ROBOTS`, `NOT_MOBILE_FRIENDLY`, `THREATS` (security, fix immediately).
- Indexing: `webmaster_get_indexing_history` (HTTP status groups 2xx/3xx/4xx/5xx), `webmaster_list_indexing_samples`; spikes of 4xx/5xx = broken pages or outages.
- In search: `webmaster_get_search_urls_history`, `webmaster_list_search_events` (APPEARED/REMOVED with exclusion reasons).
- Queries: `webmaster_list_popular_queries` (TOTAL_SHOWS, TOTAL_CLICKS, AVG_SHOW_POSITION, AVG_CLICK_POSITION).
- Links: `webmaster_list_broken_links`, `webmaster_list_external_links`.
- Fixes that need Yandex: recrawl (`webmaster_prepare_recrawl` -> apply, daily quota from `webmaster_get_recrawl_quota`), sitemap add (`webmaster_prepare_sitemap` -> apply).
The exact enum list evolves; treat unknown codes as-is and quote them.
