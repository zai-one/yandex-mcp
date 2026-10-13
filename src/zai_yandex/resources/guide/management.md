# Management catalogue

Opt-in toolsets metrika_manage, webmaster_manage, audience_manage. Generated from
src/zai_yandex/management.py (python scripts/gen_tool_map.py). Each provider has
`<p>_manage_read` (GET; operation='catalog' lists all), `<p>_manage_prepare` -> user
accepts the approval (approvals CLI, same account) -> `<p>_manage_apply` with idempotency_key
-> readback; on timeout `<p>_manage_reconcile` (never resend). Writes need the provider
write flag (YANDEX_METRIKA/WEBMASTER/AUDIENCE_WRITE_ENABLED).
args: path ids (counter_id, id, label_id, host_id, sitemap_id, task_id, segment_id, pixel_id),
query{...} (only listed keys), body (documented object; wrapper key optional), csv (uploads).

Accepting an approval (operator, on the server machine; never an MCP tool):

```
python -m zai_yandex.approvals --state-path <state.sqlite> --account <YANDEX_ACCOUNT_ID> \
  --approval-id <approval_id> --principal <principal> --request-hash <request_hash> \
  [--named-account <account from the prepare result>] --accept
```

`--named-account` must be the account the change was prepared for (omit for the default).
A mismatch is refused; apply also rejects approvals from another account, endpoint or request.

## yandex_metrika (`metrika_manage_read`, `metrika_manage_prepare`, `metrika_manage_apply`, `metrika_manage_reconcile`)

| operation | kind/risk | HTTP | doc |
|---|---|---|---|
| access_filter_create | write/medium | POST /management/v1/counter/{counter_id}/access_filters | https://yandex.ru/dev/metrika/ru/management/openapi/access_filter/ |
| access_filter_delete | write/high | DELETE /management/v1/counter/{counter_id}/access_filter/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/access_filter/ |
| access_filter_get | read | GET /management/v1/counter/{counter_id}/access_filter/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/access_filter/ |
| access_filter_list | read | GET /management/v1/counter/{counter_id}/access_filters | https://yandex.ru/dev/metrika/ru/management/openapi/access_filter/ |
| access_filter_update | write/medium | PUT /management/v1/counter/{counter_id}/access_filter/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/access_filter/ |
| account_list | read | GET /management/v1/accounts | https://yandex.ru/dev/metrika/ru/management/openapi/account/accounts |
| annotation_create | write/medium | POST /management/v1/counter/{counter_id}/chart_annotations | https://yandex.ru/dev/metrika/ru/management/openapi/chart_annotation/ |
| annotation_delete | write/high | DELETE /management/v1/counter/{counter_id}/chart_annotation/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/chart_annotation/ |
| annotation_get | read | GET /management/v1/counter/{counter_id}/chart_annotation/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/chart_annotation/ |
| annotation_list | read | GET /management/v1/counter/{counter_id}/chart_annotations | https://yandex.ru/dev/metrika/ru/management/openapi/chart_annotation/ |
| annotation_update | write/medium | PUT /management/v1/counter/{counter_id}/chart_annotation/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/chart_annotation/ |
| calls_upload | upload/medium | POST /management/v1/counter/{counter_id}/offline_conversions/upload_calls | https://yandex.ru/dev/metrika/ru/management/openapi/call/uploadCalls |
| calls_uploading | read | GET /management/v1/counter/{counter_id}/offline_conversions/calls_uploading/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/call/findCallUploadingById |
| calls_uploadings | read | GET /management/v1/counter/{counter_id}/offline_conversions/calls_uploadings | https://yandex.ru/dev/metrika/ru/management/openapi/call/findAllCallUploadings |
| chats_upload | upload/medium | POST /management/v1/counter/{counter_id}/offline_conversions/upload | https://yandex.ru/dev/metrika/ru/management/openapi/chats/upload_chats |
| client_list | read | GET /management/v1/clients | https://yandex.ru/dev/metrika/ru/management/openapi/clients/getClients |
| counter_create | write/medium | POST /management/v1/counters | https://yandex.ru/dev/metrika/ru/management/openapi/counter/addCounter |
| counter_delete | write/high | DELETE /management/v1/counter/{counter_id} | https://yandex.ru/dev/metrika/ru/management/openapi/counter/deleteCounter |
| counter_get | read | GET /management/v1/counter/{counter_id} | https://yandex.ru/dev/metrika/ru/management/openapi/counter/counter |
| counter_label_set | write/low | POST /management/v1/counter/{counter_id}/label/{label_id} | https://yandex.ru/dev/metrika/ru/management/openapi/counter-label/setCounterLabel |
| counter_label_unset | write/medium | DELETE /management/v1/counter/{counter_id}/label/{label_id} | https://yandex.ru/dev/metrika/ru/management/openapi/counter-label/unsetCounterLabel |
| counter_undelete | write/medium | POST /management/v1/counter/{counter_id}/undelete | https://yandex.ru/dev/metrika/ru/management/openapi/counter/undeleteCounter |
| counter_update | write/medium | PUT /management/v1/counter/{counter_id} | https://yandex.ru/dev/metrika/ru/management/openapi/counter/editCounter |
| crm_last_uploadings | read | GET /cdp/api/v1/counter/{counter_id}/last_uploadings | https://yandex.ru/dev/metrika/ru/management/openapi/schema/getLastUploadings |
| crm_simple_orders_upload | upload/medium | POST /cdp/api/v1/counter/{counter_id}/data/simple_orders | https://yandex.ru/dev/metrika/ru/management/openapi/schema/uploadSimpleOrders |
| delegate_add | write/high | POST /management/v1/delegates | https://yandex.ru/dev/metrika/ru/management/openapi/delegates/addDelegate |
| delegate_delete | write/high | DELETE /management/v1/delegate | https://yandex.ru/dev/metrika/ru/management/openapi/delegates/deleteDelegate |
| delegate_list | read | GET /management/v1/delegates | https://yandex.ru/dev/metrika/ru/management/openapi/delegates/delegates |
| expense_delete | upload/high | POST /management/v1/counter/{counter_id}/expense/delete | https://yandex.ru/dev/metrika/ru/management/openapi/expense/uploadRemoveBody_1 |
| expense_upload | upload/medium | POST /management/v1/counter/{counter_id}/expense/upload | https://yandex.ru/dev/metrika/ru/management/openapi/expense/uploadMultipart |
| expense_uploading | read | GET /management/v1/counter/{counter_id}/expense/uploading/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/expense/findById_2 |
| expense_uploadings | read | GET /management/v1/counter/{counter_id}/expense/uploadings | https://yandex.ru/dev/metrika/ru/management/openapi/expense/findAll_2 |
| filter_create | write/medium | POST /management/v1/counter/{counter_id}/filters | https://yandex.ru/dev/metrika/ru/management/openapi/filter/ |
| filter_delete | write/high | DELETE /management/v1/counter/{counter_id}/filter/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/filter/ |
| filter_get | read | GET /management/v1/counter/{counter_id}/filter/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/filter/ |
| filter_list | read | GET /management/v1/counter/{counter_id}/filters | https://yandex.ru/dev/metrika/ru/management/openapi/filter/ |
| filter_update | write/medium | PUT /management/v1/counter/{counter_id}/filter/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/filter/ |
| goal_get | read | GET /management/v1/counter/{counter_id}/goal/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/goal/goal |
| grant_create | write/high | POST /management/v1/counter/{counter_id}/grants | https://yandex.ru/dev/metrika/ru/management/openapi/grant/addGrant |
| grant_delete | write/high | DELETE /management/v1/counter/{counter_id}/grant | https://yandex.ru/dev/metrika/ru/management/openapi/grant/deleteGrant |
| grant_get | read | GET /management/v1/counter/{counter_id}/grant | https://yandex.ru/dev/metrika/ru/management/openapi/grant/grant |
| grant_list | read | GET /management/v1/counter/{counter_id}/grants | https://yandex.ru/dev/metrika/ru/management/openapi/grant/grants |
| grant_update | write/high | PUT /management/v1/counter/{counter_id}/grant | https://yandex.ru/dev/metrika/ru/management/openapi/grant/editGrant |
| label_create | write/medium | POST /management/v1/labels | https://yandex.ru/dev/metrika/ru/management/openapi/label/ |
| label_delete | write/high | DELETE /management/v1/label/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/label/ |
| label_get | read | GET /management/v1/label/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/label/ |
| label_list | read | GET /management/v1/labels | https://yandex.ru/dev/metrika/ru/management/openapi/label/ |
| label_update | write/medium | PUT /management/v1/label/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/label/ |
| messengers | read | GET /management/v1/messengers | https://yandex.ru/dev/metrika/ru/management/openapi/goal/getMessengers |
| my_grant | read | GET /management/v1/counter/{counter_id}/my_grant | https://yandex.ru/dev/metrika/ru/management/openapi/grant/myGrant |
| operation_create | write/medium | POST /management/v1/counter/{counter_id}/operations | https://yandex.ru/dev/metrika/ru/management/openapi/operation/ |
| operation_delete | write/high | DELETE /management/v1/counter/{counter_id}/operation/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/operation/ |
| operation_get | read | GET /management/v1/counter/{counter_id}/operation/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/operation/ |
| operation_list | read | GET /management/v1/counter/{counter_id}/operations | https://yandex.ru/dev/metrika/ru/management/openapi/operation/ |
| operation_update | write/medium | PUT /management/v1/counter/{counter_id}/operation/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/operation/ |
| public_grant_create | write/high | POST /management/v1/counter/{counter_id}/public_grant | https://yandex.ru/dev/metrika/ru/management/openapi/grant/addPublicGrant |
| public_grant_delete | write/medium | DELETE /management/v1/counter/{counter_id}/public_grant | https://yandex.ru/dev/metrika/ru/management/openapi/grant/deletePublicGrant |
| segment_create | write/medium | POST /management/v1/counter/{counter_id}/apisegment/segments | https://yandex.ru/dev/metrika/ru/management/openapi/segment/ |
| segment_delete | write/high | DELETE /management/v1/counter/{counter_id}/apisegment/segment/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/segment/ |
| segment_get | read | GET /management/v1/counter/{counter_id}/apisegment/segment/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/segment/ |
| segment_list | read | GET /management/v1/counter/{counter_id}/apisegment/segments | https://yandex.ru/dev/metrika/ru/management/openapi/segment/ |
| segment_update | write/medium | PUT /management/v1/counter/{counter_id}/apisegment/segment/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/segment/ |
| social_networks | read | GET /management/v1/social_networks | https://yandex.ru/dev/metrika/ru/management/openapi/goal/getSocialNetworks |
| stat_bytime | read | GET /stat/v1/data/bytime | https://yandex.ru/dev/metrika/ru/stat/bytime |
| stat_comparison | read | GET /stat/v1/data/comparison | https://yandex.ru/dev/metrika/ru/stat/comparison |
| stat_comparison_drilldown | read | GET /stat/v1/data/comparison/drilldown | https://yandex.ru/dev/metrika/ru/stat/comparison-drilldown |
| stat_drilldown | read | GET /stat/v1/data/drilldown | https://yandex.ru/dev/metrika/ru/stat/drilldown |
| user_params_confirm | write/medium | POST /management/v1/counter/{counter_id}/user_params/uploading/{id}/confirm | https://yandex.ru/dev/metrika/ru/management/openapi/user_params/confirm |
| user_params_upload | upload/medium | POST /management/v1/counter/{counter_id}/user_params/uploadings/upload | https://yandex.ru/dev/metrika/ru/management/openapi/user_params/upload |
| user_params_uploading | read | GET /management/v1/counter/{counter_id}/user_params/uploading/{id} | https://yandex.ru/dev/metrika/ru/management/openapi/user_params/findById |
| user_params_uploadings | read | GET /management/v1/counter/{counter_id}/user_params/uploadings | https://yandex.ru/dev/metrika/ru/management/openapi/user_params/findAll |

## yandex_webmaster (`webmaster_manage_read`, `webmaster_manage_prepare`, `webmaster_manage_apply`, `webmaster_manage_reconcile`)

| operation | kind/risk | HTTP | doc |
|---|---|---|---|
| feeds_add_info | read | GET {host}/feeds/add/info | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-add-info |
| feeds_add_start | write/medium | POST {host}/feeds/add/start | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-add-start |
| feeds_batch_add | write/medium | POST {host}/feeds/batch/add | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-batch-add |
| feeds_batch_remove | write/high | DELETE {host}/feeds/batch/remove | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-batch-delete |
| feeds_change_regions | write/medium | POST {host}/feeds/change | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-region-change |
| feeds_list | read | GET {host}/feeds/list | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-list |
| feeds_moderation_recheck | write/low | POST {host}/feeds/moderation/recheck | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-moderation-recheck |
| feeds_problems | read | GET {host}/feeds/problems | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-problems |
| feeds_rejected_offers | read | GET {host}/feeds/moderation/rejected-offers | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-moderation-rejected-offers |
| feeds_status_list | read | GET {host}/feeds/status/list | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-status-list |
| feeds_summary | read | GET {host}/feeds/summary | https://yandex.ru/dev/webmaster/doc/ru/reference/feeds-summary |
| host_add | write/medium | POST {user}/hosts | https://yandex.ru/dev/webmaster/doc/ru/reference/hosts-add-site |
| host_delete | write/high | DELETE {host} | https://yandex.ru/dev/webmaster/doc/ru/reference/hosts-delete |
| indexing_archive_create | write/low | POST {host}/indexing/archive/ | https://yandex.ru/dev/webmaster/doc/ru/reference/host-id-important-archive-all-pages |
| indexing_archive_status | read | GET {host}/indexing/archive/{task_id} | https://yandex.ru/dev/webmaster/doc/ru/reference/host-id-important-archive-all-pages |
| insearch_history | read | GET {host}/indexing/insearch/history | https://yandex.ru/dev/webmaster/doc/ru/reference/hosts-indexing-insearch-history |
| insearch_samples | read | GET {host}/indexing/insearch/samples | https://yandex.ru/dev/webmaster/doc/ru/reference/hosts-indexing-insearch-samples |
| search_queries_all_history | read | GET {host}/search-queries/all/history | https://yandex.ru/dev/webmaster/doc/ru/reference/host-search-queries-history-all |
| sitemap_delete | write/medium | DELETE {host}/user-added-sitemaps/{sitemap_id} | https://yandex.ru/dev/webmaster/doc/ru/reference/host-user-added-sitemaps-sitemap-id-delete |
| sitemap_recrawl | write/low | POST {host}/sitemaps/{sitemap_id}/recrawl | https://yandex.ru/dev/webmaster/doc/ru/reference/host-priority-re-crawl-initiation-sitemap |
| sitemap_recrawl_quota | read | GET {host}/sitemaps/recrawl | https://yandex.ru/dev/webmaster/doc/ru/reference/host-priority-limit-sitemap-get |
| verification_start | write/low | POST {host}/verification | https://yandex.ru/dev/webmaster/doc/ru/reference/host-verification-post |

## yandex_audience (`audience_manage_read`, `audience_manage_prepare`, `audience_manage_apply`, `audience_manage_reconcile`)

| operation | kind/risk | HTTP | doc |
|---|---|---|---|
| account_list | read | GET /accounts | https://yandex.ru/dev/audience/ru/ref/openapi/accounts/accounts |
| delegate_add | write/high | PUT /delegate | https://yandex.ru/dev/audience/ru/ref/openapi/delegates/addDelegate |
| delegate_delete | write/high | DELETE /delegate | https://yandex.ru/dev/audience/ru/ref/openapi/delegates/deleteDelegate |
| delegate_list | read | GET /delegates | https://yandex.ru/dev/audience/ru/ref/openapi/delegates/delegates |
| pixel_delete | write/high | DELETE /pixel/{pixel_id} | https://yandex.ru/dev/audience/ru/ref/openapi/pixels/deletePixel |
| pixel_undelete | write/low | POST /pixel/{pixel_id}/undelete | https://yandex.ru/dev/audience/ru/ref/openapi/pixels/undelete |
| pixel_update | write/medium | PUT /pixel/{pixel_id} | https://yandex.ru/dev/audience/ru/ref/openapi/pixels/editPixel |
| segment_confirm | write/medium | POST /segment/{segment_id}/confirm | https://yandex.ru/dev/audience/ru/ref/openapi/segments/confirm |
| segment_confirm_client_id | write/medium | POST /segment/client_id/{segment_id}/confirm | https://yandex.ru/dev/audience/ru/ref/openapi/segments/confirmClientId |
| segment_create_appmetrica | write/medium | POST /segments/create_appmetrica | https://yandex.ru/dev/audience/ru/ref/openapi/segments/createAppMetrica |
| segment_create_lookalike | write/medium | POST /segments/create_lookalike | https://yandex.ru/dev/audience/ru/ref/openapi/segments/createLookalike |
| segment_create_metrika | write/medium | POST /segments/create_metrika | https://yandex.ru/dev/audience/ru/ref/openapi/segments/createMetrika |
| segment_delete | write/high | DELETE /segment/{segment_id} | https://yandex.ru/dev/audience/ru/ref/openapi/segments/delete |
| segment_grant_add | write/high | PUT /segment/{segment_id}/grant | https://yandex.ru/dev/audience/ru/ref/openapi/grants/addGrant |
| segment_grant_delete | write/high | DELETE /segment/{segment_id}/grant | https://yandex.ru/dev/audience/ru/ref/openapi/grants/deleteGrant |
| segment_grants | read | GET /segment/{segment_id}/grants | https://yandex.ru/dev/audience/ru/ref/openapi/grants/grants |
| segment_modify_data | upload/high | POST /segment/{segment_id}/modify_data | https://yandex.ru/dev/audience/ru/ref/openapi/segments/modifyUploadingData |
| segment_reprocess | write/low | PUT /segment/{segment_id}/reprocess | https://yandex.ru/dev/audience/ru/ref/openapi/segments/reprocessSegment |
| segment_update | write/medium | PUT /segment/{segment_id} | https://yandex.ru/dev/audience/ru/ref/openapi/segments/edit |
| segment_update_geo_points | write/medium | POST /segment/{segment_id}/update_geo_points | https://yandex.ru/dev/audience/ru/ref/openapi/segments/updateGeoPoints |
| segment_upload_csv | upload/medium | POST /segments/upload_csv_file | https://yandex.ru/dev/audience/ru/ref/openapi/segments/uploadCsvFile |
