# Metrika offline conversions

Source: https://yandex.ru/dev/metrika/ru/management/offline-conv

- Goal: a Metrika goal of type JavaScript event (condition "matches"); `Target` is its identifier. List goals with `metrika_list_goals`.
- Columns: `Target` and `DateTime` (Unix seconds, past only) are required; at least one of `UserId`, `ClientId`, `Yclid`, `PurchaseId` links the conversion to a visit. Optional: `Price` (dot decimal), `Currency` (ISO 4217, e.g. RUB).
- Types: `BASIC` (default), `CALLS`, `CHATS`.
- Flow: `metrika_validate_offline_conversions` (local, no API call) -> show row count and CSV preview -> explicit user OK -> `metrika_upload_offline_conversions` with the returned `confirmation_hash` and an `idempotency_key` (reuse it on retry; uploads are never auto-retried).
- Uploads need `YANDEX_METRIKA_WRITE_ENABLED=true` and the `yandex_metrika:write` scope. At most 10000 rows per call here.
- Status: `metrika_list_offline_conversion_uploads` / `metrika_get_offline_conversion_upload`. Processing takes up to 3 hours; data then appears in goal reports.
- Never upload personal data (phones, e-mails) in identifier columns.
