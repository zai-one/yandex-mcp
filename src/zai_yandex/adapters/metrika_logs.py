"""Yandex Metrika Logs API (https://yandex.ru/dev/metrika/ru/logs/).

Endpoints (all under https://api-metrika.yandex.net/management/v1/counter/{counterId}):
GET logrequests/evaluate, GET logrequests, POST logrequests, GET logrequest/{id},
GET logrequest/{id}/part/{n}/download (TSV), POST logrequest/{id}/clean,
POST logrequest/{id}/cancel. Per counter the stored + requested data is capped at 10 GB;
clean processed requests to free the quota. The API does not filter rows.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlencode

from zai_yandex.transport import JsonHttpClient, ProviderError

SOURCES = frozenset({"hits", "visits"})
FIELD_PREFIX = {"visits": "ym:s:", "hits": "ym:pv:"}
ATTRIBUTIONS = frozenset(
    {
        "FIRST",
        "LAST",
        "LASTSIGN",
        "LAST_YANDEX_DIRECT_CLICK",
        "CROSS_DEVICE_LAST_SIGNIFICANT",
        "CROSS_DEVICE_FIRST",
        "CROSS_DEVICE_LAST_YANDEX_DIRECT_CLICK",
        "CROSS_DEVICE_LAST",
        "AUTOMATIC",
    }
)
_FIELD = re.compile(r"^ym:(s|pv):[A-Za-z0-9_<>]{1,80}$")
MAX_FIELDS = 60
MAX_PREVIEW_ROWS = 1000
MAX_CELL_CHARS = 512


def _counter(counter_id: Any) -> str:
    text = str(counter_id).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError("counter_id must be a positive integer")
    return text


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def validate_request(
    date1: str,
    date2: str,
    source: str,
    fields: list[str],
    attribution: str | None = None,
    *,
    today: date | None = None,
) -> dict[str, str]:
    if source not in SOURCES:
        raise ValueError("source must be hits or visits")
    try:
        start, end = date.fromisoformat(date1), date.fromisoformat(date2)
    except (TypeError, ValueError):
        raise ValueError("date1/date2 must be YYYY-MM-DD") from None
    if start > end:
        raise ValueError("date1 must not be after date2")
    if end >= (today or date.today()):
        raise ValueError("date2 cannot be today or later (Logs API rule)")
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
        raise ValueError(f"fields must list 1..{MAX_FIELDS} Logs API fields")
    prefix = FIELD_PREFIX[source]
    for field in fields:
        if not isinstance(field, str) or not _FIELD.match(field) or not field.startswith(prefix):
            raise ValueError(f"every field must be a {prefix}* Logs API field for source={source}")
    params = {"date1": date1, "date2": date2, "source": source, "fields": ",".join(dict.fromkeys(fields))}
    if attribution is not None:
        if attribution not in ATTRIBUTIONS:
            raise ValueError("unsupported attribution")
        params["attribution"] = attribution
    return params


def parse_tsv(body: str, max_rows: int) -> dict[str, Any]:
    lines = body.splitlines()
    if not lines:
        return {"columns": [], "rows": [], "row_count_returned": 0, "truncated": False}
    columns = lines[0].split("\t")
    rows = [
        [cell if len(cell) <= MAX_CELL_CHARS else cell[:MAX_CELL_CHARS] + "…" for cell in line.split("\t")]
        for line in lines[1 : max_rows + 1]
    ]
    return {
        "columns": columns,
        "rows": rows,
        "row_count_returned": len(rows),
        "rows_in_part": len(lines) - 1,
        "truncated": len(lines) - 1 > max_rows,
    }


class YandexMetrikaLogsAdapter:
    def __init__(self, base_url: str, token: str, http: JsonHttpClient | None = None, **_: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or JsonHttpClient(timeout=60)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ProviderError("Yandex Metrika token is not configured")
        return {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}

    def _url(self, counter_id: Any, path: str) -> str:
        return f"{self.base_url}/management/v1/counter/{_counter(counter_id)}/{path}"

    async def evaluate(self, counter_id: Any, params: dict[str, str]) -> dict[str, Any]:
        return await self.http.request(
            "GET", self._url(counter_id, "logrequests/evaluate"), headers=self._headers(), params=params
        )

    async def list(self, counter_id: Any) -> dict[str, Any]:
        return await self.http.request("GET", self._url(counter_id, "logrequests"), headers=self._headers())

    async def create(self, counter_id: Any, params: dict[str, str]) -> dict[str, Any]:
        # Query parameters on a POST, as documented; not idempotent (spends the 10 GB quota).
        url = f"{self._url(counter_id, 'logrequests')}?{urlencode(params)}"
        return await self.http.request("POST", url, headers=self._headers())

    async def info(self, counter_id: Any, request_id: int) -> dict[str, Any]:
        rid = _positive(request_id, "request_id")
        return await self.http.request(
            "GET", self._url(counter_id, f"logrequest/{rid}"), headers=self._headers()
        )

    async def action(self, counter_id: Any, request_id: int, action: str) -> dict[str, Any]:
        if action not in {"clean", "cancel"}:
            raise ValueError("action must be clean or cancel")
        rid = _positive(request_id, "request_id")
        return await self.http.request(
            "POST",
            self._url(counter_id, f"logrequest/{rid}/{action}"),
            headers=self._headers(),
            idempotent=True,
        )

    async def download_preview(
        self, counter_id: Any, request_id: int, part_number: int, max_rows: int
    ) -> dict[str, Any]:
        rid, part = _positive(request_id, "request_id"), _positive(part_number, "part_number")
        if (
            isinstance(max_rows, bool)
            or not isinstance(max_rows, int)
            or not 1 <= max_rows <= MAX_PREVIEW_ROWS
        ):
            raise ValueError(f"max_rows must be 1..{MAX_PREVIEW_ROWS}")
        response = await self.http.request_raw(
            "GET",
            self._url(counter_id, f"logrequest/{rid}/part/{part}/download"),
            headers={**self._headers(), "Accept": "text/tab-separated-values"},
            idempotent=True,
        )
        return {"request_id": rid, "part_number": part, **parse_tsv(response.body, max_rows)}
