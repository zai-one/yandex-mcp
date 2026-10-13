---
name: metrika-offline-conversions
description: Upload CRM or call-center offline conversions to Yandex Metrika goals safely (validate, preview, confirm, upload, check status). Use for офлайн-конверсии, загрузить продажи из CRM в Метрику, offline conversions upload, звонки в Метрику.
license: LicenseRef-ZAI-ONE
compatibility: Requires the zai-yandex-mcp server with YANDEX_METRIKA_WRITE_ENABLED for the upload step.
metadata:
  mcp-server: zai-yandex-mcp
---

# Metrika offline conversions

1. `metrika_list_counters` -> counter_id; `metrika_list_goals(counter_id)` -> the JavaScript-event goal used as `Target` (ask if unclear).
2. Map the user's rows to columns - see `references/offline-conversions.md`. Convert dates to Unix seconds; drop rows without any visit identifier and say how many.
3. `metrika_validate_offline_conversions` -> row_count, csv_preview, confirmation_hash. No API call.
4. Fill `templates/upload-confirmation.md` and wait for an explicit "yes".
5. `metrika_upload_offline_conversions` with the same rows, the confirmation_hash and an idempotency_key; reuse the key if you retry.
6. `metrika_get_offline_conversion_upload` for status; tell the user data appears within about 3 hours.

Guardrails: never change rows after confirmation (the hash will not match); no phones or e-mails as identifiers; one upload per confirmation.
