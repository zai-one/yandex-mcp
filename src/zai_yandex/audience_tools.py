from __future__ import annotations

from typing import Any, cast

from zai_yandex.adapters.audience import PROVIDER, api_request, create_digest
from zai_yandex.transport import ProviderError, request_hash


def register_audience_tools(server: Any, runtime: Any) -> None:
    registry = runtime.registry
    require_scopes = runtime.require_scopes
    read = runtime.read
    safe_provider_error = runtime.safe_error
    actions = runtime.audience_actions

    async def _read(operation: str, arguments: dict[str, Any], factory: Any) -> dict[str, Any]:
        return cast(dict[str, Any], await read(PROVIDER, operation, arguments, factory))

    @server.tool(auth=require_scopes("yandex_audience:read"))
    async def audience_list_segments() -> dict[str, Any]:
        """List Audience segments visible to the server-side Audience credential."""
        return await _read("list_segments", {}, registry.yandex_audience().list_segments)

    @server.tool(auth=require_scopes("yandex_audience:read"))
    async def audience_get_segment(segment_id: int) -> dict[str, Any]:
        """Read one segment by filtering the documented authoritative segment list."""
        adapter = registry.yandex_audience()
        return await _read(
            "get_segment",
            {"segment_id": segment_id},
            lambda: adapter.get_segment(segment_id),
        )

    @server.tool(auth=require_scopes("yandex_audience:read"))
    async def audience_list_pixels() -> dict[str, Any]:
        """List Audience tracking pixels visible to the server-side credential."""
        return await _read("list_pixels", {}, registry.yandex_audience().list_pixels)

    @server.tool(auth=require_scopes("yandex_audience:read"))
    async def audience_get_pixel(pixel_id: int) -> dict[str, Any]:
        """Read one pixel by filtering the documented authoritative pixel list."""
        adapter = registry.yandex_audience()
        return await _read("get_pixel", {"pixel_id": pixel_id}, lambda: adapter.get_pixel(pixel_id))

    @server.tool(auth=require_scopes("yandex_audience:write"))
    async def audience_validate_create(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a closed Audience create payload offline and return its immutable confirmation hash."""
        try:
            digest, normalized = create_digest(operation, payload)
            path, api_payload = api_request(operation, normalized)
            return {
                "valid": True,
                "provider": PROVIDER,
                "operation": operation,
                "normalized_payload": normalized,
                "confirmation_hash": digest,
                "api_path": path,
                "api_request_payload": api_payload,
                "api_request_payload_sha256": request_hash(api_payload),
                "live_call": False,
            }
        except (ProviderError, PermissionError, ValueError) as exc:
            raise safe_provider_error(PROVIDER, exc) from None

    async def _apply(
        operation: str,
        payload: dict[str, Any],
        confirmation_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await actions.apply(
            runtime.current_access().principal_id,
            operation,
            payload,
            confirmation_hash,
            idempotency_key,
        )

    @server.tool(auth=require_scopes("yandex_audience:write"))
    async def audience_create_geo_circle(
        name: str,
        radius: int,
        points: list[dict[str, Any]],
        geo_segment_type: str,
        confirmation_hash: str,
        idempotency_key: str,
        period_length: int | None = None,
        times_quantity: int | None = None,
    ) -> dict[str, Any]:
        """Run one guarded geo-circle create; uncertain outcomes are not resent."""
        payload: dict[str, Any] = {
            "name": name,
            "radius": radius,
            "points": points,
            "geo_segment_type": geo_segment_type,
        }
        if period_length is not None:
            payload["period_length"] = period_length
        if times_quantity is not None:
            payload["times_quantity"] = times_quantity
        return await _apply("geo_circle", payload, confirmation_hash, idempotency_key)

    @server.tool(auth=require_scopes("yandex_audience:write"))
    async def audience_create_geo_polygon(
        name: str,
        polygons: list[dict[str, Any]],
        geo_segment_type: str,
        confirmation_hash: str,
        idempotency_key: str,
        period_length: int | None = None,
        times_quantity: int | None = None,
    ) -> dict[str, Any]:
        """Run one guarded geo-polygon create; `last` is refused and unknowns are not resent."""
        payload: dict[str, Any] = {
            "name": name,
            "polygons": polygons,
            "geo_segment_type": geo_segment_type,
        }
        if period_length is not None:
            payload["period_length"] = period_length
        if times_quantity is not None:
            payload["times_quantity"] = times_quantity
        return await _apply("geo_polygon", payload, confirmation_hash, idempotency_key)

    @server.tool(auth=require_scopes("yandex_audience:write"))
    async def audience_create_pixel(
        name: str, confirmation_hash: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Run one guarded tracking-pixel create; uncertain outcomes are not resent."""
        return await _apply("pixel", {"name": name}, confirmation_hash, idempotency_key)

    @server.tool(auth=require_scopes("yandex_audience:write"))
    async def audience_create_pixel_viewers(
        name: str,
        pixel_id: int,
        period_length: int,
        times_quantity_operation: str,
        times_quantity: int,
        confirmation_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Run one guarded pixel-viewer segment create; uncertain outcomes are not resent."""
        return await _apply(
            "pixel_viewers",
            {
                "name": name,
                "pixel_id": pixel_id,
                "period_length": period_length,
                "times_quantity_operation": times_quantity_operation,
                "times_quantity": times_quantity,
            },
            confirmation_hash,
            idempotency_key,
        )

    @server.tool(auth=require_scopes("yandex_audience:write"))
    async def audience_reconcile_create(
        operation: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        """Reconcile one owned pending create by saved object id without sending another POST."""
        return await actions.reconcile(
            runtime.current_access().principal_id, operation, payload, idempotency_key
        )
