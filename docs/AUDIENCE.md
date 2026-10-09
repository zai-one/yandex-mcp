# Yandex Audience

Audience is a separate provider in this server. Its OAuth credential is read only
from `YANDEX_AUDIENCE_TOKEN_FILE`; a token, login or provider URL is never accepted
as a tool argument. Audience reads require `yandex_audience:read`. Creates require
`yandex_audience:write` and `YANDEX_AUDIENCE_WRITE_ENABLED=true`, which is false by
default.

This package does not enable Audience in a central `zai-mcp` deployment. A central
gateway remains the owner of its own principals, accounts, scopes, approvals,
quotas, jobs and audit binding.

## Bounded surface

The read surface lists segments and pixels. Single-object tools filter the
authoritative documented list and require exactly one matching numeric ID; they do
not invent a provider GET endpoint. No arbitrary path, query parameter or account
selector is exposed.

The create surface deliberately excludes CRM/device-ID uploads, Metrika imports,
lookalikes, edits, deletes, delegate access and sharing. It supports only:

| Operation | Provider path | Closed body | Local restrictions |
|---|---|---|---|
| `geo_circle` | `/v1/management/segments/create_geo` | `segment{name,radius,points,geo_segment_type}` | radius 500-10000 m; `last` is supported; `condition` requires 1-90 days and visits no greater than days |
| `geo_polygon` | `/v1/management/segments/create_geo_polygon` | `segment{name,polygons,geo_segment_type}` | 1-10 polygons, at least four points and three distinct vertices each; `last` is refused before HTTP |
| `pixel` | `/v1/management/pixels` | `pixel{name}` | no extra fields |
| `pixel_viewers` | `/v1/management/segments/create_pixel` | `segment{name,pixel_id,period_length,times_quantity_operation,times_quantity}` | period 1-90 days; operation is exactly `eq`, `lt` or `gt`; at least one is `gt` with quantity `0` |

Latitude/longitude, sizes, strings, list counts and unknown keys are checked before
provider admission. These checks do not claim to reproduce every provider geometry
rule, such as polygon self-intersection and final service acceptance. Provider
rejection remains possible. In particular, the supported local matrix follows the
known service behavior that current-location `last` works for circles but the same
mode is rejected for polygons.

## Integrity, dispatch and reconciliation

Call `audience_validate_create` before a create. It performs no provider request and
returns normalized data plus two deliberately different hashes:

- `confirmation_hash` binds provider, operation and normalized payload for this
  server's create tool. It is an integrity confirmation, not a server-verified human
  approval and not an ADS approval receipt.
- `api_request_payload_sha256` hashes the exact wrapped JSON body shown in
  `api_request_payload`. ADS orchestration can use this value for its own immutable
  evidence without confusing the two contracts.

The matching `confirmation_hash` and a required 8-128 character idempotency key are
needed for a create. The ledger is bound to server account, principal, provider,
operation, key and payload hash. A second key cannot dispatch the same owned payload.
The payload anti-join spans every principal sharing the same server account and
Audience credential; denial never reveals the other principal's key or result.
POST is never retried automatically.

After a successful POST, the sanitized object ID and receipt are checkpointed before
a list-based readback. The write is settled as applied only when every requested
field matches. A mismatch remains `pending_reconciliation`; it is never reported as
applied. `audience_reconcile_create` may only repeat the readback. It cannot send a
POST.

If a timeout, transport error or ambiguous response occurs without a saved object
ID, the result is `pending_unknown`, `retryable=false`,
`reconciliation_required=true`. Repeating the call after restart does not resend the
POST. This is at-most-one dispatch for a key and payload, not a guarantee that an
unknown provider mutation did or did not happen.

## Processing status

Provider status is preserved verbatim:

- `is_processed`, `is_updated` and `uploaded` are transitional and are not ready;
- only a segment status of `processed` sets `ready=true`;
- `processing_failed` and `few_data` are terminal failures for use.

For a tracking-pixel object, `ready` is `null` because segment processing status
does not apply. Creating a pixel does not create a viewer segment or bind anything
to a Direct campaign.

An applied result proves the create payload matched one readback snapshot. A cached
idempotent result has `readback_fresh=false`. Always call `audience_get_segment`
again immediately before using its ID in a Direct launch; an older `processed`
snapshot is not current launch-readiness evidence.

All tests use synthetic fixtures. They do not prove OAuth scope, provider
connectivity, account ownership, polygon acceptance or production readiness.
