from __future__ import annotations

import math
from typing import Any

from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.errors import BoundedValidationError
from zai_yandex.transport import JsonHttpClient, ProviderError, ProviderResponseError, request_hash

PROVIDER = "yandex_audience"
BASE_URL = "https://api-audience.yandex.ru/v1/management"
CREATE_PATHS = {
    "geo_circle": "/segments/create_geo",
    "geo_polygon": "/segments/create_geo_polygon",
    "pixel": "/pixels",
    "pixel_viewers": "/segments/create_pixel",
}
SEGMENT_OPERATIONS = frozenset({"geo_circle", "geo_polygon", "pixel_viewers"})
GEO_TYPES = frozenset({"last", "regular", "home", "work", "condition"})
POLYGON_GEO_TYPES = GEO_TYPES - {"last"}
VIEWER_OPERATIONS = frozenset({"eq", "lt", "gt"})


def _positive_int(value: Any, field: str, *, maximum: int = 2_147_483_647) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be a positive integer no greater than {maximum}")
    return value


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _text(value: Any, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} must be a bounded printable string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _point(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not {"latitude", "longitude"} <= set(value):
        raise ValueError("each point requires latitude and longitude")
    if set(value) - {"latitude", "longitude", "description"}:
        raise ValueError("point contains unsupported fields")
    result: dict[str, Any] = {}
    for field, minimum, maximum in (("latitude", -90, 90), ("longitude", -180, 180)):
        coordinate = value[field]
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise ValueError(f"point {field} must be numeric")
        coordinate = float(coordinate)
        if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
            raise ValueError(f"point {field} is outside its valid range")
        result[field] = coordinate
    if "description" in value:
        result["description"] = _text(
            value["description"], "point description", maximum=200, allow_empty=True
        )
    return result


def _condition_fields(payload: dict[str, Any], geo_type: str) -> dict[str, int]:
    present = {name for name in ("period_length", "times_quantity") if name in payload}
    if geo_type == "condition":
        if present != {"period_length", "times_quantity"}:
            raise ValueError("condition requires period_length and times_quantity")
        period = _bounded_int(payload["period_length"], "period_length", minimum=1, maximum=90)
        times = _positive_int(payload["times_quantity"], "times_quantity", maximum=period)
        return {"period_length": period, "times_quantity": times}
    if present:
        raise ValueError("period_length and times_quantity are allowed only for condition")
    return {}


def validate_create(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(operation, str) or operation not in CREATE_PATHS:
        raise ValueError("unsupported Audience create operation")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    name = _text(payload.get("name"), "name", maximum=255)
    if operation == "pixel":
        if set(payload) != {"name"}:
            raise ValueError("pixel payload supports only name")
        return {"name": name}
    if operation == "pixel_viewers":
        allowed = {"name", "pixel_id", "period_length", "times_quantity_operation", "times_quantity"}
        if set(payload) != allowed:
            raise ValueError("pixel_viewers payload has missing or unsupported fields")
        comparison = str(payload["times_quantity_operation"]).strip()
        if comparison not in VIEWER_OPERATIONS:
            raise ValueError("times_quantity_operation must be eq, lt or gt")
        return {
            "name": name,
            "pixel_id": _positive_int(payload["pixel_id"], "pixel_id"),
            "period_length": _bounded_int(payload["period_length"], "period_length", minimum=1, maximum=90),
            "times_quantity_operation": comparison,
            "times_quantity": _bounded_int(
                payload["times_quantity"], "times_quantity", minimum=0, maximum=1_000_000
            ),
        }
    common = {"name", "geo_segment_type", "period_length", "times_quantity"}
    geometry = "points" if operation == "geo_circle" else "polygons"
    allowed = common | {geometry} | ({"radius"} if operation == "geo_circle" else set())
    if set(payload) - allowed or not {"name", "geo_segment_type", geometry} <= set(payload):
        raise ValueError(f"{operation} payload has missing or unsupported fields")
    geo_type = str(payload["geo_segment_type"]).strip()
    supported = GEO_TYPES if operation == "geo_circle" else POLYGON_GEO_TYPES
    if geo_type not in supported:
        if operation == "geo_polygon" and geo_type == "last":
            raise BoundedValidationError("audience_polygon_last_unsupported")
        raise ValueError(f"geo_segment_type is unsupported for {operation}")
    result: dict[str, Any] = {"name": name, "geo_segment_type": geo_type}
    result.update(_condition_fields(payload, geo_type))
    if operation == "geo_circle":
        if "radius" not in payload:
            raise ValueError("geo_circle requires radius")
        points = payload["points"]
        maximum_points = 100 if geo_type == "condition" else 1_000
        if not isinstance(points, list) or not 1 <= len(points) <= maximum_points:
            raise ValueError(f"points must contain between 1 and {maximum_points} items")
        result.update(radius=_bounded_int(payload["radius"], "radius", minimum=500, maximum=10_000))
        result["points"] = [_point(point) for point in points]
        return result
    polygons = payload["polygons"]
    if not isinstance(polygons, list) or not 1 <= len(polygons) <= 10:
        raise ValueError("polygons must contain between 1 and 10 items")
    normalized_polygons = []
    for polygon in polygons:
        if (
            not isinstance(polygon, dict)
            or "points" not in polygon
            or set(polygon) - {"points", "description"}
        ):
            raise ValueError("each polygon requires points and supports an optional description")
        points = polygon["points"]
        if not isinstance(points, list) or not 4 <= len(points) <= 10_000:
            raise ValueError("each polygon must contain between 4 and 10000 points")
        normalized_points = [_point(point) for point in points]
        distinct = {(point["latitude"], point["longitude"]) for point in normalized_points}
        if len(distinct) < 3:
            raise ValueError("each polygon must contain at least three distinct vertices")
        normalized: dict[str, Any] = {"points": normalized_points}
        if "description" in polygon:
            normalized["description"] = _text(
                polygon["description"], "polygon description", maximum=200, allow_empty=True
            )
        normalized_polygons.append(normalized)
    result["polygons"] = normalized_polygons
    return result


def create_digest(operation: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    normalized = validate_create(operation, payload)
    return request_hash({"provider": PROVIDER, "operation": operation, "payload": normalized}), normalized


def api_request(operation: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    normalized = validate_create(operation, payload)
    resource = "pixel" if operation == "pixel" else "segment"
    return CREATE_PATHS[operation], {resource: normalized}


class YandexAudienceAdapter:
    def __init__(
        self,
        base_url: str,
        token: str,
        http: JsonHttpClient | None = None,
        coalescer: AsyncSingleFlight | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http or JsonHttpClient(timeout=60)
        self.coalescer = coalescer or AsyncSingleFlight()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ProviderError("Yandex Audience token is not configured")
        return {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}

    async def _get(self, path: str) -> dict[str, Any]:
        return await self.coalescer.run(
            request_hash({"provider": PROVIDER, "path": path}),
            lambda: self.http.request("GET", f"{self.base_url}{path}", headers=self._headers()),
        )

    async def list_segments(self) -> dict[str, Any]:
        return await self._get("/segments")

    async def get_segment(self, segment_id: int) -> dict[str, Any]:
        identifier = _positive_int(segment_id, "segment_id")
        response = await self.list_segments()
        matches = [
            item
            for item in response.get("segments", [])
            if isinstance(item, dict)
            and not isinstance(item.get("id"), bool)
            and item.get("id") == identifier
        ]
        if len(matches) != 1:
            raise ProviderResponseError("Audience segment list did not contain exactly one requested id")
        return {"segment": matches[0]}

    async def list_pixels(self) -> dict[str, Any]:
        return await self._get("/pixels")

    async def get_pixel(self, pixel_id: int) -> dict[str, Any]:
        identifier = _positive_int(pixel_id, "pixel_id")
        response = await self.list_pixels()
        matches = [
            item
            for item in response.get("pixels", [])
            if isinstance(item, dict)
            and not isinstance(item.get("id"), bool)
            and item.get("id") == identifier
        ]
        if len(matches) != 1:
            raise ProviderResponseError("Audience pixel list did not contain exactly one requested id")
        return {"pixel": matches[0]}

    async def create(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        path, body = api_request(operation, payload)
        resource = "pixel" if operation == "pixel" else "segment"
        response = await self.http.request(
            "POST",
            f"{self.base_url}{path}",
            headers=self._headers(),
            payload=body,
        )
        if isinstance(response.get("errors"), list) and response["errors"]:
            raise ProviderResponseError("Yandex Audience returned an error envelope")
        item = response.get(resource)
        if (
            not isinstance(item, dict)
            or isinstance(item.get("id"), bool)
            or not isinstance(item.get("id"), int)
            or item["id"] <= 0
        ):
            raise ProviderResponseError("Yandex Audience create receipt has no positive object id")
        return response
