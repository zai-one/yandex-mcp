# Yandex MCP errors

Envelope: `{"code", "provider", "retryable", "hint", "docs", "retry_after_seconds"?, "reason_code"?}`. Retry only if `retryable`; follow `hint`.

| code | retryable | action |
|---|---|---|
| provider_rate_limited | yes | Units exhausted (Direct 152), too many connections (506) or local limit: wait `retry_after_seconds`. |
| provider_timeout, provider_transport_failed, provider_temporarily_unavailable | yes | Retry later with the same idempotency_key. |
| provider_authentication_failed | no | Token missing/expired or lacks access: ask the user to refresh the OAuth token. |
| provider_resource_not_found | no | Re-list ids (campaigns, counters, hosts). |
| provider_request_invalid, validation_failed | no | Fix arguments; see `reason_code`. |
| provider_payload_too_large | no | Narrow dates, fields or limit. |
| provider_disabled | no | Scope or write flag is off; tell the user. |
| tool_disabled | no | Toolset is off; see yandex://toolsets. |
| approval_required | no | Deletes and bid changes are never autonomous: use the prepare tool in `hint`, the user accepts, then apply. |
| provider_response_invalid, provider_http_error, operation_failed, provider_error | no | Report to the user. |
