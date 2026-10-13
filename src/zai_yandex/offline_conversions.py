"""Metrika offline conversions (Management API, docs-linked).

https://yandex.ru/dev/metrika/ru/management/offline-conv
POST /management/v1/counter/{counterId}/offline_conversions/upload (multipart CSV `file`;
query `type`=BASIC|CALLS|CHATS, `comment`), GET .../offline_conversions/uploadings and
GET .../offline_conversions/uploading/{id}. CSV columns: Target + DateTime (Unix, past only)
are required, plus at least one of UserId, ClientId, Yclid, PurchaseId; Price and Currency
(ISO 4217) are optional. Processing takes up to 3 hours.
"""

from __future__ import annotations

import csv
import io
import re
import time
from typing import Any

from zai_yandex.adapters.metrika_logs import _counter, _positive
from zai_yandex.transport import JsonHttpClient, ProviderError, request_hash

TYPES = frozenset({"BASIC", "CALLS", "CHATS"})
ID_COLUMNS = ("UserId", "ClientId", "Yclid", "PurchaseId")
COLUMNS = (*ID_COLUMNS, "Target", "DateTime", "Price", "Currency")
MAX_ROWS = 10000
MAX_CELL = 255
_COMMENT = re.compile(r"^[0-9A-Za-zА-Яа-яЁё ]{0,255}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _cell(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text or len(text) > MAX_CELL or any(ch in text for ch in '\r\n,"\t'):
        raise ValueError(f"{name} must be 1..{MAX_CELL} chars without commas, quotes or newlines")
    return text


def normalize(
    counter_id: Any,
    conversions: Any,
    conversion_type: str = "BASIC",
    comment: str | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    counter = _counter(counter_id)
    if conversion_type not in TYPES:
        raise ValueError("conversion_type must be BASIC, CALLS or CHATS")
    if comment is not None and not _COMMENT.match(comment):
        raise ValueError("comment: up to 255 digits, Latin or Cyrillic letters and spaces")
    if not isinstance(conversions, list) or not 1 <= len(conversions) <= MAX_ROWS:
        raise ValueError(f"conversions must list 1..{MAX_ROWS} rows")
    limit = int(now if now is not None else time.time())
    rows: list[dict[str, str]] = []
    for index, row in enumerate(conversions):
        if not isinstance(row, dict) or set(row) - set(COLUMNS):
            raise ValueError(f"row {index}: only columns {', '.join(COLUMNS)} are allowed")
        out: dict[str, str] = {}
        for name in ID_COLUMNS:
            if row.get(name) not in (None, ""):
                out[name] = _cell(row[name], name)
        if not out:
            raise ValueError(f"row {index}: one of UserId, ClientId, Yclid, PurchaseId is required")
        out["Target"] = _cell(row.get("Target", ""), "Target")
        stamp = row.get("DateTime")
        if isinstance(stamp, bool) or not isinstance(stamp, int) or not 0 < stamp <= limit:
            raise ValueError(f"row {index}: DateTime must be a past Unix timestamp (seconds)")
        out["DateTime"] = str(stamp)
        if row.get("Price") is not None:
            price = row["Price"]
            if isinstance(price, bool) or not isinstance(price, int | float) or price < 0:
                raise ValueError(f"row {index}: Price must be a non-negative number")
            out["Price"] = repr(float(price)) if isinstance(price, float) else str(price)
        if row.get("Currency") is not None:
            if not isinstance(row["Currency"], str) or not _CURRENCY.match(row["Currency"]):
                raise ValueError(f"row {index}: Currency must be an ISO 4217 code like RUB")
            out["Currency"] = row["Currency"]
        rows.append(out)
    columns = [name for name in COLUMNS if any(name in row for row in rows)]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows([[row.get(name, "") for name in columns] for row in rows])
    csv_text = buffer.getvalue()
    digest = request_hash(
        {"counter_id": counter, "type": conversion_type, "comment": comment, "csv": csv_text}
    )
    return {
        "counter_id": counter,
        "type": conversion_type,
        "comment": comment,
        "columns": columns,
        "row_count": len(rows),
        "csv": csv_text,
        "confirmation_hash": digest,
    }


class YandexMetrikaOfflineAdapter:
    def __init__(self, base_url: str, token: str, http: JsonHttpClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or JsonHttpClient(timeout=60)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ProviderError("Yandex Metrika token is not configured")
        return {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}

    def _url(self, counter_id: Any, path: str) -> str:
        return f"{self.base_url}/management/v1/counter/{_counter(counter_id)}/offline_conversions/{path}"

    async def upload(self, prepared: dict[str, Any]) -> dict[str, Any]:
        params = {"type": prepared["type"]}
        if prepared["comment"]:
            params["comment"] = prepared["comment"]
        return await self.http.request_multipart(
            "POST",
            self._url(prepared["counter_id"], "upload"),
            headers=self._headers(),
            params=params,
            files={"file": ("conversions.csv", prepared["csv"].encode("utf-8"), "text/csv")},
        )

    async def list(self, counter_id: Any, conversion_type: str, limit: int, offset: int) -> dict[str, Any]:
        return await self.http.request(
            "GET",
            self._url(counter_id, "uploadings"),
            headers=self._headers(),
            params={"type": conversion_type, "limit": limit, "offset": offset},
        )

    async def get(self, counter_id: Any, uploading_id: int) -> dict[str, Any]:
        uid = _positive(uploading_id, "uploading_id")
        return await self.http.request(
            "GET", self._url(counter_id, f"uploading/{uid}"), headers=self._headers()
        )


def _list_args(conversion_type: str, limit: int, offset: int) -> None:
    if conversion_type not in TYPES:
        raise ValueError("conversion_type must be BASIC, CALLS or CHATS")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit must be 1..10000")
    _positive(offset, "offset")


def register_offline_conversion_tools(server: Any, runtime: Any) -> None:
    registry, settings, require_scopes = runtime.registry, runtime.settings, runtime.require_scopes
    read, write = runtime.read, runtime.write

    def adapter() -> YandexMetrikaOfflineAdapter:
        logs = registry.yandex_metrika_logs()  # provider check + server credential
        return YandexMetrikaOfflineAdapter(logs.base_url, logs.token, http=logs.http)

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_validate_offline_conversions(
        counter_id: str,
        conversions: list[dict[str, Any]],
        conversion_type: str = "BASIC",
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Validate offline conversion rows locally (Target, DateTime past Unix secs, one of UserId/ClientId/Yclid/PurchaseId, Price, Currency); returns CSV preview + confirmation_hash. No API call."""
        try:
            prepared = normalize(counter_id, conversions, conversion_type, comment)
        except ValueError as exc:
            raise runtime.safe_error("yandex_metrika", exc) from None
        lines = prepared["csv"].splitlines()
        return {
            "valid": True,
            "counter_id": prepared["counter_id"],
            "type": prepared["type"],
            "columns": prepared["columns"],
            "row_count": prepared["row_count"],
            "csv_preview": lines[:6],
            "confirmation_hash": prepared["confirmation_hash"],
            "next": "Show the preview to the user; after explicit OK call metrika_upload_offline_conversions.",
        }

    @server.tool(auth=require_scopes("yandex_metrika:write"))
    async def metrika_upload_offline_conversions(
        counter_id: str,
        conversions: list[dict[str, Any]],
        confirmation_hash: str,
        idempotency_key: str,
        conversion_type: str = "BASIC",
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Upload offline conversions to Metrika (affects goal stats and Direct optimisation). Needs the hash from metrika_validate_offline_conversions, user confirmation and YANDEX_METRIKA_WRITE_ENABLED."""
        prepared: dict[str, Any] = {}

        def check() -> None:
            prepared.update(normalize(counter_id, conversions, conversion_type, comment))
            if confirmation_hash != prepared["confirmation_hash"]:
                raise PermissionError("confirmation_hash must match the validated conversions")

        return await write(
            "yandex_metrika",
            "metrika_upload_offline_conversions",
            {
                "counter_id": counter_id,
                "conversions": conversions,
                "type": conversion_type,
                "comment": comment,
            },
            idempotency_key,
            enabled=settings.yandex_metrika_write_enabled,
            validate=check,
            apply_fn=lambda: adapter().upload(prepared),
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_list_offline_conversion_uploads(
        counter_id: str, conversion_type: str = "BASIC", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List offline conversion uploads of a counter (id, status, line counts, type). Read-only."""
        return await read(
            "yandex_metrika",
            "offline_conversions_list",
            {"counter_id": counter_id, "type": conversion_type, "limit": limit, "offset": offset},
            lambda: adapter().list(counter_id, conversion_type, limit, offset),
            validate=lambda: (_counter(counter_id), _list_args(conversion_type, limit, offset)),
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_get_offline_conversion_upload(counter_id: str, uploading_id: int) -> dict[str, Any]:
        """Status of one offline conversion upload (processing can take up to 3 hours). Read-only."""
        return await read(
            "yandex_metrika",
            "offline_conversions_get",
            {"counter_id": counter_id, "uploading_id": uploading_id},
            lambda: adapter().get(counter_id, uploading_id),
            validate=lambda: (_counter(counter_id), _positive(uploading_id, "uploading_id")),
        )
