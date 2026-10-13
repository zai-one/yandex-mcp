"""Metrika Logs API and Direct AgencyClients tools (API expansion, docs-linked).

Logs: https://yandex.ru/dev/metrika/ru/logs/ ; AgencyClients:
https://yandex.ru/dev/direct/doc/ru/agencyclients/get
"""

from __future__ import annotations

from typing import Any

from zai_yandex.adapters.direct import YandexDirectAdapter
from zai_yandex.adapters.metrika_logs import _counter, _positive, validate_request

# Compact, non-PII field set (no phones, representatives or contracts).
AGENCY_CLIENT_FIELDS = ["Login", "ClientId", "ClientInfo", "Archived", "Currency", "CreatedAt", "Type"]


def _validate_cleanup(counter_id: Any, request_id: Any, action: str) -> None:
    _counter(counter_id)
    _positive(request_id, "request_id")
    if action not in {"clean", "cancel"}:
        raise ValueError("action must be clean or cancel")


def register_expansion_tools(server: Any, runtime: Any) -> None:
    registry, settings, require_scopes = runtime.registry, runtime.settings, runtime.require_scopes
    read, write = runtime.read, runtime.write

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_logs_evaluate(
        counter_id: str, date1: str, date2: str, source: str, fields: list[str]
    ) -> dict[str, Any]:
        """Check whether a Metrika Logs API export fits the counter quota (possible, max days). Use before `metrika_logs_create`. source=visits (ym:s:*) or hits (ym:pv:*); date2 before today. Read-only."""
        params: dict[str, str] = {}

        def check() -> None:
            _counter(counter_id)
            params.update(validate_request(date1, date2, source, fields))

        return await read(
            "yandex_metrika",
            "logs_evaluate",
            {"counter_id": counter_id, "date1": date1, "date2": date2, "source": source, "fields": fields},
            lambda: registry.yandex_metrika_logs().evaluate(counter_id, params),
            validate=check,
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_logs_list(counter_id: str) -> dict[str, Any]:
        """List Logs API requests of a counter with status (created, processed, canceled...) and parts. Use to find request_id. Read-only."""
        return await read(
            "yandex_metrika",
            "logs_list",
            {"counter_id": counter_id},
            lambda: registry.yandex_metrika_logs().list(counter_id),
            validate=lambda: _counter(counter_id),
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_logs_get(counter_id: str, request_id: int) -> dict[str, Any]:
        """Status and parts of one Logs API request; poll until status=processed, then `metrika_logs_download_part`. Read-only."""
        return await read(
            "yandex_metrika",
            "logs_get",
            {"counter_id": counter_id, "request_id": request_id},
            lambda: registry.yandex_metrika_logs().info(counter_id, request_id),
            validate=lambda: (_counter(counter_id), _positive(request_id, "request_id")),
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_logs_download_part(
        counter_id: str, request_id: int, part_number: int = 0, max_rows: int = 200
    ) -> dict[str, Any]:
        """Preview one processed Logs API part as columns + up to max_rows (<=1000) rows; truncated=true if more. For full exports download outside chat. Read-only."""
        return await read(
            "yandex_metrika",
            "logs_download",
            {"counter_id": counter_id, "request_id": request_id, "part": part_number},
            lambda: registry.yandex_metrika_logs().download_preview(
                counter_id, request_id, part_number, max_rows
            ),
            validate=lambda: (_counter(counter_id), _positive(request_id, "request_id")),
        )

    @server.tool(auth=require_scopes("yandex_metrika:write"))
    async def metrika_logs_create(
        counter_id: str,
        date1: str,
        date2: str,
        source: str,
        fields: list[str],
        attribution: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a Metrika Logs API export (spends the 10 GB per-counter quota). Run `metrika_logs_evaluate` and confirm with the user first; needs YANDEX_METRIKA_LOGS_ENABLED. Idempotent by key; then poll `metrika_logs_get`."""
        params: dict[str, str] = {}

        def check() -> None:
            _counter(counter_id)
            params.update(validate_request(date1, date2, source, fields, attribution))

        return await write(
            "yandex_metrika",
            "metrika_logs_create",
            {
                "counter_id": counter_id,
                "date1": date1,
                "date2": date2,
                "source": source,
                "fields": fields,
                "attribution": attribution,
            },
            idempotency_key,
            enabled=settings.yandex_metrika_logs_enabled,
            validate=check,
            apply_fn=lambda: registry.yandex_metrika_logs().create(counter_id, params),
        )

    @server.tool(auth=require_scopes("yandex_metrika:write"))
    async def metrika_logs_cleanup(
        counter_id: str, request_id: int, action: str = "clean", idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Free Logs API quota: action=clean deletes prepared files of a processed request, cancel stops an unprocessed one. Irreversible; confirm first; needs YANDEX_METRIKA_LOGS_ENABLED."""
        return await write(
            "yandex_metrika",
            "metrika_logs_cleanup",
            {"counter_id": counter_id, "request_id": request_id, "action": action},
            idempotency_key,
            enabled=settings.yandex_metrika_logs_enabled,
            validate=lambda: _validate_cleanup(counter_id, request_id, action),
            apply_fn=lambda: registry.yandex_metrika_logs().action(counter_id, request_id, action),
        )

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_list_agency_clients(
        archived: str = "NO", logins: list[str] | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """List an agency's Direct clients (Login, ClientId, ClientInfo, Currency...) via AgencyClients.get; agency tokens only. Use the Login as YANDEX_DIRECT_CLIENT_LOGIN for that client. 10 units + 1/client. Read-only."""
        if archived not in {"YES", "NO"}:
            raise runtime.safe_error("yandex_direct", ValueError("archived must be YES or NO"))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise runtime.safe_error("yandex_direct", ValueError("limit must be 1..10000"))
        criteria: dict[str, Any] = {"Archived": archived}
        if logins:
            criteria["Logins"] = [str(login) for login in logins][:100]
        params = {
            "method": "get",
            "params": {
                "SelectionCriteria": criteria,
                "FieldNames": AGENCY_CLIENT_FIELDS,
                "Page": {"Limit": limit, "Offset": max(0, int(offset))},
            },
        }
        adapter = registry.yandex_direct()
        return await read(
            "yandex_direct",
            "read:agencyclients",
            {"service": "agencyclients", "params": params},
            lambda: adapter.read("agencyclients", params),
            validate=lambda: YandexDirectAdapter.validate_read("agencyclients", params),
        )
