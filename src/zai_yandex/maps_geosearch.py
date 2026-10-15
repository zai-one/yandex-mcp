"""Opt-in Yandex Maps organization search (toolset ``maps_geosearch``).

Official reference: https://yandex.ru/maps-api/docs/geosearch-api/index.html (request.html,
response.html). Terms: https://yandex.ru/legal/maps_api/ - results may not be stored or modified
beyond temporary caching (<=30 days) for service functionality, and their order, attributions and
notices may not be changed or hidden. This module therefore keeps nothing: no cache, no ledger,
no state file; results keep the API's relevance order and carry an attribution.

The tools exist only when an API key is configured (``YANDEX_MAPS_GEOSEARCH_API_KEY_FILE``, an
owner-private file, or ``YANDEX_MAPS_GEOSEARCH_API_KEY``). The key is never returned or logged.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from zai_yandex.config import read_secret_path

GEOSEARCH_URL = "https://search-maps.yandex.ru/v1/"
KEY_ENV = "YANDEX_MAPS_GEOSEARCH_API_KEY"
KEY_FILE_ENV = "YANDEX_MAPS_GEOSEARCH_API_KEY_FILE"
MAPS_TOOLS = ("maps_search_organizations", "maps_search_places")
# request.html: lang values; results default 10, max 50; skip max 1000 and divisible by results.
LANGS = frozenset({"ru_RU", "uk_UA", "be_BY", "en_RU", "en_US", "tr_TR"})
MAX_RESULTS = 50
MAX_SKIP = 1000
MAX_TEXT = 400  # not stated in the docs; a local bound so a prompt cannot send huge queries.
# index.html: up to 50 RPS per key (client + server). Stay well below by default.
MIN_INTERVAL_S = 0.2
ATTRIBUTION = "Data: Yandex Maps organization search API (© Yandex). Terms: https://yandex.ru/legal/maps_api/"
ORDER_NOTE = "Keep this order (API relevance); do not re-rank, store or edit these results."


def configured(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(env.get(KEY_FILE_ENV, "").strip() or env.get(KEY_ENV, "").strip())


def load_key(environ: Mapping[str, str] | None = None) -> str:
    """File first (owner-private, like every other credential here), then the plain env var."""
    env = os.environ if environ is None else environ
    path = env.get(KEY_FILE_ENV, "").strip()
    return read_secret_path(path) if path else env.get(KEY_ENV, "").strip()


def _point(value: Any, name: str, *, span: bool = False) -> str:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or any(isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) for v in value)
    ):
        raise ValueError(f"{name} must be [longitude, latitude] numbers")
    lon, lat = float(value[0]), float(value[1])
    if span:
        if not (0 < lon <= 360 and 0 < lat <= 180):
            raise ValueError(f"{name} must be positive degree spans [lon_span, lat_span]")
    elif not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"{name} must be [longitude -180..180, latitude -90..90]")
    return f"{lon:g},{lat:g}"


def build_params(
    kind: Literal["biz", "geo"],
    *,
    text: str | None = None,
    uri: str | None = None,
    lang: str = "ru_RU",
    center: list[float] | None = None,
    span: list[float] | None = None,
    bbox: list[list[float]] | None = None,
    strict_area: bool = False,
    results: int = 10,
    skip: int = 0,
) -> dict[str, str]:
    """Validated query (without apikey) per request.html."""
    params: dict[str, str] = {"type": kind}
    if uri is not None:
        if not isinstance(uri, str) or not uri.startswith("ymapsbm1://") or len(uri) > 512:
            raise ValueError("uri must be a ymapsbm1:// value returned by the API")
        params["uri"] = uri
    if text is not None:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError(f"text must contain 1-{MAX_TEXT} characters")
        params["text"] = text.strip()
    if "text" not in params and "uri" not in params:
        raise ValueError("text (or uri from a previous result) is required")
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {', '.join(sorted(LANGS))}")
    params["lang"] = lang
    if (center is None) != (span is None):
        raise ValueError("center and span go together (ll + spn)")
    if center is not None:
        params["ll"] = _point(center, "center")
        params["spn"] = _point(span, "span", span=True)
    if bbox is not None:
        if not isinstance(bbox, list | tuple) or len(bbox) != 2:
            raise ValueError("bbox must be [[lon, lat] lower-left, [lon, lat] upper-right]")
        params["bbox"] = f"{_point(bbox[0], 'bbox')}~{_point(bbox[1], 'bbox')}"
    if strict_area:
        if "ll" not in params and "bbox" not in params:
            raise ValueError("strict_area needs center+span or bbox")
        params["rspn"] = "1"
    if isinstance(results, bool) or not isinstance(results, int) or not 1 <= results <= MAX_RESULTS:
        raise ValueError(f"results must be 1..{MAX_RESULTS}")
    if isinstance(skip, bool) or not isinstance(skip, int) or not 0 <= skip <= MAX_SKIP or skip % results:
        raise ValueError(f"skip must be 0..{MAX_SKIP} and divisible by results")
    params["results"] = str(results)
    if skip:
        params["skip"] = str(skip)
    return params


def _coords(feature: Mapping[str, Any]) -> Any:
    geometry = feature.get("geometry")
    return geometry.get("coordinates") if isinstance(geometry, Mapping) else None


def _organization(feature: Mapping[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
    meta = props.get("CompanyMetaData") if isinstance(props.get("CompanyMetaData"), Mapping) else {}
    hours = meta.get("Hours") if isinstance(meta.get("Hours"), Mapping) else {}
    phones = meta.get("Phones") if isinstance(meta.get("Phones"), list) else []
    categories = meta.get("Categories") if isinstance(meta.get("Categories"), list) else []
    row = {
        "id": meta.get("id"),
        "name": meta.get("name") or props.get("name"),
        "address": meta.get("address") or props.get("description"),
        "categories": [c.get("name") for c in categories if isinstance(c, Mapping) and c.get("name")],
        "hours": hours.get("text"),
        "phones": [p.get("formatted") for p in phones if isinstance(p, Mapping) and p.get("formatted")],
        "url": meta.get("url"),
        "coordinates": _coords(feature),
        "uri": props.get("uri"),
    }
    return {key: value for key, value in row.items() if value not in (None, "", [])}


def _place(feature: Mapping[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
    meta = props.get("GeocoderMetaData") if isinstance(props.get("GeocoderMetaData"), Mapping) else {}
    row = {
        "name": props.get("name"),
        "description": props.get("description"),
        "address": meta.get("text"),
        "kind": meta.get("kind"),
        "precision": meta.get("precision"),
        "coordinates": _coords(feature),
        "uri": props.get("uri"),
    }
    return {key: value for key, value in row.items() if value not in (None, "", [])}


def shape(payload: Any, kind: str, detail: str) -> dict[str, Any]:
    """Compact rows (documented fields, values unchanged, API order kept) or the raw features."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("features"), list):
        raise ValueError("unexpected Geosearch response")
    meta = (
        ((payload.get("properties") or {}).get("ResponseMetaData") or {})
        if isinstance(payload.get("properties"), Mapping)
        else {}
    )
    response = meta.get("SearchResponse") if isinstance(meta.get("SearchResponse"), Mapping) else {}
    request = meta.get("SearchRequest") if isinstance(meta.get("SearchRequest"), Mapping) else {}
    features = [f for f in payload["features"] if isinstance(f, Mapping)]
    if detail == "full":
        items: list[Any] = features
    else:
        items = [(_organization if kind == "biz" else _place)(f) for f in features]
    return {
        "found": response.get("found"),
        "returned": len(items),
        "skip": request.get("skip", 0),
        "display": response.get("display"),
        "results": items,
        "attribution": ATTRIBUTION,
        "note": ORDER_NOTE,
        "untrusted_content": True,
    }


APIKEY_IN_URL = re.compile(r"(?i)(apikey=)[^&\s\"'#]+")


class _RedactApiKey(logging.Filter):
    """httpx logs every request URL at INFO; Geosearch takes its key only as a query parameter."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging over a malformed record
            return True
        if "apikey=" in message.lower():
            record.msg, record.args = APIKEY_IN_URL.sub(r"\1***", message), ()
        return True


def _redact_http_logs() -> None:
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _RedactApiKey) for item in logger.filters):
            logger.addFilter(_RedactApiKey())


class GeosearchClient:
    """Stateless caller: one request per tool call, paced, nothing cached."""

    def __init__(self, key: str, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 15.0):
        self._key, self._transport, self._timeout = key, transport, timeout
        _redact_http_logs()
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def search(self, params: dict[str, str]) -> Any:
        if not self._key:
            raise PermissionError(f"Geosearch API key is not configured ({KEY_FILE_ENV} or {KEY_ENV})")
        async with self._lock:
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                response = await client.get(GEOSEARCH_URL, params={**params, "apikey": self._key})
        except httpx.HTTPError as exc:
            # httpx messages can carry the request URL (with apikey): report the class only.
            raise ConnectionError(f"Geosearch request failed ({type(exc).__name__})") from None
        if response.status_code in (401, 403):
            raise PermissionError("Geosearch rejected the API key (invalid, inactive or not for this API)")
        if response.status_code == 429:
            raise RuntimeError("Geosearch rate or daily license limit reached; retry later")
        if response.status_code != 200:
            raise RuntimeError(f"Geosearch HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Geosearch returned non-JSON") from None


def register_maps_tools(
    server: Any,
    runtime: Any,
    *,
    key: str,
    surface: Any = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GeosearchClient:
    """Register on the raw FastMCP server: Geosearch has its own key and pacing, so it stays outside
    the per-provider admission policies and their execution rows; nothing is written to disk."""
    from zai_yandex.tool_annotations import annotations_for

    client = GeosearchClient(key, transport)
    require_scopes = runtime.require_scopes

    def tool(name: str) -> Any:
        if surface is not None and not surface.exposes(name):
            return lambda function: function
        return server.tool(auth=require_scopes("yandex_search:read"), annotations=annotations_for(name))

    async def run(kind: Literal["biz", "geo"], detail: str, **arguments: Any) -> dict[str, Any]:
        try:
            # Same account boundary as the provider tools (scope alone is not enough): a token minted for
            # another account must not spend this server's Geosearch license quota.
            runtime.identity()
            params = build_params(kind, **arguments)
            if detail not in {"summary", "full"}:
                raise ValueError("detail must be summary or full")
            return shape(await client.search(params), kind, detail)
        except (ValueError, PermissionError, ConnectionError, RuntimeError) as exc:
            raise runtime.safe_error("yandex_maps_geosearch", exc) from None

    @tool("maps_search_organizations")
    async def maps_search_organizations(
        text: str | None = None,
        center: list[float] | None = None,
        span: list[float] | None = None,
        bbox: list[list[float]] | None = None,
        strict_area: bool = False,
        results: int = 10,
        skip: int = 0,
        lang: str = "ru_RU",
        uri: str | None = None,
        detail: Literal["summary", "full"] = "summary",
    ) -> dict[str, Any]:
        """Find organizations on Yandex Maps by name, category, phone or address (Geosearch type=biz).
        Area: center+span [lon,lat] or bbox; strict_area stays inside. results<=50. Returns name, address,
        hours, phones, url, coordinates in API order + attribution. Read-only; uses license quota."""
        return await run(
            "biz", detail, text=text, uri=uri, lang=lang, center=center, span=span, bbox=bbox,
            strict_area=strict_area, results=results, skip=skip,
        )  # fmt: skip

    @tool("maps_search_places")
    async def maps_search_places(
        text: str,
        center: list[float] | None = None,
        span: list[float] | None = None,
        bbox: list[list[float]] | None = None,
        strict_area: bool = False,
        results: int = 10,
        skip: int = 0,
        lang: str = "ru_RU",
        detail: Literal["summary", "full"] = "summary",
    ) -> dict[str, Any]:
        """Find places/addresses (toponyms) on Yandex Maps via Geosearch type=geo: name, full address, kind,
        precision, coordinates [lon,lat]. Use to resolve an area before maps_search_organizations.
        Read-only; counts against the Geosearch license quota."""
        return await run(
            "geo", detail, text=text, lang=lang, center=center, span=span, bbox=bbox,
            strict_area=strict_area, results=results, skip=skip,
        )  # fmt: skip

    return client
