"""Bounded Yandex Webmaster v4 transport; see third_party/webmaster/UPSTREAM.md.

MIT API mappings adapted from weselow/Yandex-webmaster-mcp-server. Governance
belongs to the platform: the two submit methods must only follow its write gate.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.sanitizer import sanitize_provider_response
from zai_yandex.transport import (
    JsonHttpClient,
    ProviderAdmissionDenied,
    ProviderError,
    ProviderHttpError,
    ProviderResponseError,
    request_hash,
    retry_attempt_admission,
)

QUERY_INDICATORS = ("TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION", "AVG_CLICK_POSITION")
DEVICE_INDICATORS = ("ALL", "DESKTOP", "MOBILE_AND_TABLET", "MOBILE", "TABLET")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,199}\Z")
_HOST = re.compile(r"(https?):([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?):([0-9]{1,5})\Z")


class YandexWebmasterAdapter:
    """No generic method/path proxy, client OAuth argument, or implicit POST retry."""

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
        self._user_id: str | None = None

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ProviderError("Yandex Webmaster token is not configured")
        return {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}

    @staticmethod
    def validate_user_id(value: Any) -> str:
        if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,18}", str(value)):
            raise ValueError("user_id must be a positive int64")
        if int(value) > 9223372036854775807:
            raise ValueError("user_id must be a positive int64")
        return str(value)

    @staticmethod
    def validate_host_id(host_id: str) -> str:
        match = _HOST.fullmatch(host_id)
        if not match or not 1 <= int(match[3]) <= 65535:
            raise ValueError("host_id must be a Webmaster ASCII scheme:hostname:port identifier")
        labels = match[2].split(".")
        if any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels
        ):
            raise ValueError("host_id contains an invalid hostname")
        return host_id

    @staticmethod
    def validate_resource_id(value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("invalid Webmaster resource identifier")
        return value

    @classmethod
    def validate_url_for_host(cls, url: str, host_id: str) -> str:
        cls.validate_host_id(host_id)
        if not url or len(url) > 2048 or any(ord(char) <= 32 for char in url) or "\\" in url:
            raise ValueError("URL must be a non-empty HTTP(S) URL without whitespace")
        parsed = urlsplit(url)
        scheme, hostname, port = host_id.split(":")
        try:
            actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            actual_host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError) as exc:
            raise ValueError("URL has an invalid authority") from exc
        if parsed.username is not None or parsed.password is not None or parsed.fragment or "#" in url:
            raise ValueError("URL must not contain credentials or a fragment")
        sensitive = ("token", "oauth", "password", "passwd", "secret", "apikey", "authorization", "session")
        if any(
            any(marker in re.sub(r"[^a-z0-9]", "", key.lower()) for marker in sensitive)
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        ):
            raise ValueError("URL query must not contain credential parameters")
        if parsed.scheme != scheme or actual_host != hostname or actual_port != int(port):
            raise ValueError("URL must match the exact verified host scheme, hostname and port")
        return url

    @staticmethod
    def validate_period(date_from: str, date_to: str) -> dict[str, str]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", date_to
        ):
            raise ValueError("dates must use YYYY-MM-DD")
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        if not 0 <= (end - start).days <= 30:
            raise ValueError("history period must cover 1 to 31 inclusive calendar days")
        return {"date_from": date_from, "date_to": date_to}

    @staticmethod
    def _page_args(offset: int, limit: int) -> dict[str, int]:
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2147483647:
            raise ValueError("offset must be a non-negative int32")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return {"offset": offset, "limit": limit}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = await self.coalescer.run(
            request_hash({"provider": "yandex_webmaster", "path": path, "params": params}),
            lambda: self._request("GET", f"{self.base_url}{path}", headers=self._headers(), params=params),
        )
        if "error_code" in raw:
            raise ProviderResponseError("Webmaster returned an error object without HTTP error status")
        return raw

    def _hide_token(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self.token, "***redacted***") if self.token else value
        if isinstance(value, dict):
            return {self._hide_token(key): self._hide_token(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._hide_token(item) for item in value]
        return value

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            raw = await self.http.request(method, url, **kwargs)
        except ProviderHttpError as exc:
            if exc.body_preview:
                exc.body_preview = self._hide_token(exc.body_preview)
            raise
        sanitized: dict[str, Any] = self._hide_token(sanitize_provider_response(raw))
        return sanitized

    async def _uid(self) -> str:
        if self._user_id is None:
            raw = await self._get("/user")
            try:
                self._user_id = self.validate_user_id(raw.get("user_id"))
            except ValueError as exc:
                raise ProviderResponseError("Webmaster returned an invalid user_id") from exc
            # The logical call's first permit covered /user. Charge its second
            # resource request through the same durable governor admission seam.
            admission = retry_attempt_admission.get()
            if admission is not None and not await admission():
                raise ProviderAdmissionDenied(
                    "provider governor denied the host request after user resolution",
                    retry_after_seconds=60,
                )
        return self._user_id

    async def _host_path(self, host_id: str) -> str:
        normalized = self.validate_host_id(host_id)
        return f"/user/{await self._uid()}/hosts/{quote(normalized, safe=':')}"

    @staticmethod
    def _envelope(
        data: dict[str, Any],
        *,
        period: dict[str, str] | None = None,
        availability: str | None = None,
        pagination: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "data": sanitize_provider_response(data),
            "collected_at": datetime.now(UTC).isoformat(),
            "period": period,
            "availability": availability or ("available" if data else "unavailable"),
            "pagination": pagination,
        }

    def _page(
        self,
        raw: dict[str, Any],
        key: str,
        offset: int,
        limit: int,
        *,
        cursor: str | None = None,
        cursor_mode: bool = False,
        local: bool = False,
    ) -> dict[str, Any]:
        values = raw.get(key)
        valid = isinstance(values, list) and all(isinstance(item, dict) for item in values)
        rows: list[Any] = values if isinstance(values, list) else []
        total_value = len(rows) if local and valid else raw.get("count")
        total = (
            int(str(total_value))
            if not isinstance(total_value, bool) and re.fullmatch(r"[0-9]+", str(total_value))
            else None
        )
        page = rows[offset : offset + limit] if local else rows[:limit]
        returned = len(page)
        complete = valid and (
            (offset + returned >= total if total is not None else returned < limit)
            if not cursor_mode
            else (
                (returned >= total if total is not None else returned < limit)
                if cursor is None
                else returned < limit
            )
        )
        next_cursor = None
        if cursor_mode and valid and not complete and page:
            candidate = page[-1].get("sitemap_id")
            if isinstance(candidate, str) and _ID.fullmatch(candidate) and candidate != cursor:
                next_cursor = candidate
        inconsistent = (
            not valid
            or (not complete and returned == 0)
            or len(rows) > limit
            and not local
            or total is not None
            and len(rows) > total
        )
        if cursor_mode and not complete and next_cursor is None:
            inconsistent = True
        data = {**raw, key: page} if isinstance(values, list) else dict(raw)
        return self._envelope(
            data,
            availability="partial" if inconsistent else ("available" if returned else "empty"),
            pagination={
                "offset": None if cursor_mode else offset,
                "cursor": cursor,
                "limit": limit,
                "returned": returned,
                "total": total,
                "next_offset": offset + returned if not cursor_mode and not complete and returned else None,
                "next_cursor": next_cursor,
                "complete": complete and not inconsistent,
                "mode": "cursor" if cursor_mode else ("local_offset" if local else "offset"),
            },
        )

    async def user(self) -> dict[str, Any]:
        raw = await self._get("/user")
        try:
            self._user_id = self.validate_user_id(raw.get("user_id"))
        except ValueError as exc:
            raise ProviderResponseError("Webmaster returned an invalid user_id") from exc
        return self._envelope(raw)

    async def hosts(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        self._page_args(offset, limit)
        raw = await self._get(f"/user/{await self._uid()}/hosts")
        return self._page(raw, "hosts", offset, limit, local=True)

    async def host(self, host_id: str) -> dict[str, Any]:
        return self._envelope(await self._get(await self._host_path(host_id)))

    async def verified_host(self, host_id: str) -> dict[str, Any]:
        """Fresh server ownership evidence, not a client-supplied verified flag."""
        raw = await self._get(await self._host_path(host_id))
        if raw.get("host_id") != host_id or raw.get("verified") is not True:
            raise ProviderError("Webmaster host is not verified for the token owner")
        host_url = raw.get("ascii_host_url")
        if not isinstance(host_url, str):
            raise ProviderResponseError("Webmaster host URL is unavailable")
        self.validate_url_for_host(host_url, host_id)
        return self._envelope(raw)

    async def _detail(self, host_id: str, suffix: str) -> dict[str, Any]:
        return self._envelope(await self._get(f"{await self._host_path(host_id)}/{suffix}"))

    async def verification(self, host_id: str) -> dict[str, Any]:
        return await self._detail(host_id, "verification")

    async def summary(self, host_id: str) -> dict[str, Any]:
        result = await self._detail(host_id, "summary")
        if (
            not {"sqi", "excluded_pages_count", "searchable_pages_count", "site_problems"}
            <= result["data"].keys()
        ):
            result["availability"] = "partial"
        return result

    async def diagnostics(self, host_id: str) -> dict[str, Any]:
        result = await self._detail(host_id, "diagnostics")
        if not isinstance(result["data"].get("problems"), dict):
            result["availability"] = "unavailable"
        return result

    async def _list(
        self,
        host_id: str,
        suffix: str,
        key: str,
        offset: int,
        limit: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = {**self._page_args(offset, limit), **(extra or {})}
        raw = await self._get(f"{await self._host_path(host_id)}/{suffix}", params)
        return self._page(raw, key, offset, limit)

    @staticmethod
    def _history_data(
        raw: dict[str, Any], period: dict[str, str], expected: tuple[str, ...] | None
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        # Even endpoints lacking server-side dates are clipped locally. Never fill gaps.
        source = raw.get("indicators")
        series = source if isinstance(source, dict) else {"history": raw.get("history")}
        required = expected or tuple(series)
        filtered: dict[str, Any] = {}
        coverage: dict[str, Any] = {}
        partial = False
        start = date.fromisoformat(period["date_from"])
        requested_dates = {
            (start + timedelta(days=index)).isoformat()
            for index in range((date.fromisoformat(period["date_to"]) - start).days + 1)
        }
        for key in required:
            rows = series.get(key)
            if not isinstance(rows, list):
                coverage[key] = {
                    "available": False,
                    "dates": [],
                    "missing_dates": sorted(requested_dates),
                    "complete_daily_coverage": False,
                }
                partial = True
                continue
            kept = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("date"), str):
                    partial = True
                    continue
                day = row["date"][:10]
                try:
                    date.fromisoformat(day)
                except ValueError:
                    partial = True
                    continue
                if period["date_from"] <= day <= period["date_to"]:
                    kept.append(row)
                    partial = partial or row.get("value") is None
            filtered[key] = kept
            dates = sorted({row["date"][:10] for row in kept if row.get("value") is not None})
            missing = sorted(requested_dates - set(dates))
            coverage[key] = {
                "available": bool(dates),
                "dates": dates,
                "missing_dates": missing,
                "complete_daily_coverage": not missing,
            }
            partial = partial or not dates
        data = dict(raw)
        if isinstance(source, dict):
            data["indicators"] = filtered
        elif "history" in filtered:
            data["history"] = filtered["history"]
        any_data = any(item["available"] for item in coverage.values())
        status = ("partial" if partial else "available") if any_data else "unavailable"
        return data, status, coverage

    async def _history(
        self,
        host_id: str,
        suffix: str,
        date_from: str,
        date_to: str,
        *,
        extra: dict[str, Any] | None = None,
        expected: tuple[str, ...] | None = None,
        server_dates: bool = True,
    ) -> dict[str, Any]:
        period = self.validate_period(date_from, date_to)
        params = {**(period if server_dates else {}), **(extra or {})}
        raw = await self._get(f"{await self._host_path(host_id)}/{suffix}", params)
        data, status, coverage = self._history_data(raw, period, expected)
        if (
            suffix.startswith("search-queries/")
            and status == "available"
            and any(not item["complete_daily_coverage"] for item in coverage.values())
        ):
            status = "partial"
        result = self._envelope(data, period=period, availability=status)
        result["coverage"] = coverage
        result["date_filter"] = "server_and_local" if server_dates else "local"
        return result

    @staticmethod
    def _query_args(query_indicator: list[str] | tuple[str, ...] | str, device: str) -> dict[str, Any]:
        indicators = [query_indicator] if isinstance(query_indicator, str) else list(query_indicator)
        if not indicators or len(indicators) > 4 or not set(indicators) <= set(QUERY_INDICATORS):
            raise ValueError("unsupported query_indicator")
        if device not in DEVICE_INDICATORS:
            raise ValueError("unsupported device_type_indicator")
        return {"query_indicator": list(dict.fromkeys(indicators)), "device_type_indicator": device}

    async def query_history(
        self,
        host_id: str,
        date_from: str,
        date_to: str,
        query_indicator: list[str] | tuple[str, ...] | str = QUERY_INDICATORS,
        device_type_indicator: str = "ALL",
        query_id: str | None = None,
    ) -> dict[str, Any]:
        params = self._query_args(query_indicator, device_type_indicator)
        query = self.validate_resource_id(query_id) if query_id is not None else "all"
        return await self._history(
            host_id,
            f"search-queries/{query}/history",
            date_from,
            date_to,
            extra=params,
            expected=tuple(params["query_indicator"]),
        )

    async def popular_queries(
        self,
        host_id: str,
        query_indicator: list[str] | tuple[str, ...] | str = QUERY_INDICATORS,
        device_type_indicator: str = "ALL",
        order_by: str = "TOTAL_SHOWS",
        offset: int = 0,
        limit: int = 100,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        params = self._query_args(query_indicator, device_type_indicator)
        if order_by not in {"TOTAL_SHOWS", "TOTAL_CLICKS"}:
            raise ValueError("order_by must be TOTAL_SHOWS or TOTAL_CLICKS")
        if (date_from is None) != (date_to is None):
            raise ValueError("provide both date_from and date_to")
        end = datetime.now(UTC).date() - timedelta(days=1)
        period = self.validate_period(
            date_from or (end - timedelta(days=6)).isoformat(), date_to or end.isoformat()
        )
        result = await self._list(
            host_id,
            "search-queries/popular",
            "queries",
            offset,
            limit,
            {**params, **period, "order_by": order_by},
        )
        result["period"] = period
        result["available_period"] = {key: result["data"].get(key) for key in ("date_from", "date_to")}
        for row in result["data"].get("queries", []):
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("indicators"), dict)
                or any(row["indicators"].get(key) is None for key in params["query_indicator"])
            ):
                result["availability"] = "partial"
        return result

    async def indexing_history(self, host_id: str, date_from: str, date_to: str) -> dict[str, Any]:
        return await self._history(host_id, "indexing/history", date_from, date_to)

    async def indexing_samples(self, host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return await self._list(host_id, "indexing/samples", "samples", offset, limit)

    async def search_urls(self, host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return await self._list(host_id, "search-urls/in-search/samples", "samples", offset, limit)

    async def search_urls_history(self, host_id: str, date_from: str, date_to: str) -> dict[str, Any]:
        return await self._history(host_id, "search-urls/in-search/history", date_from, date_to)

    async def sitemaps(
        self,
        host_id: str,
        cursor: str | None = None,
        limit: int = 100,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        self._page_args(0, limit)
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["from"] = self.validate_resource_id(cursor)
        if parent_id is not None:
            params["parent_id"] = self.validate_resource_id(parent_id)
        raw = await self._get(f"{await self._host_path(host_id)}/sitemaps", params)
        return self._page(raw, "sitemaps", 0, limit, cursor=cursor, cursor_mode=True)

    async def user_sitemaps(
        self, host_id: str, cursor: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        self._page_args(0, limit)
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["offset"] = self.validate_resource_id(cursor)
        raw = await self._get(f"{await self._host_path(host_id)}/user-added-sitemaps", params)
        return self._page(raw, "sitemaps", 0, limit, cursor=cursor, cursor_mode=True)

    async def sitemap(self, host_id: str, sitemap_id: str, user_added: bool = False) -> dict[str, Any]:
        resource = self.validate_resource_id(sitemap_id)
        return await self._detail(
            host_id, f"{'user-added-sitemaps' if user_added else 'sitemaps'}/{resource}"
        )

    async def external_links(self, host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return await self._list(host_id, "links/external/samples", "links", offset, limit)

    async def external_links_history(self, host_id: str, date_from: str, date_to: str) -> dict[str, Any]:
        return await self._history(
            host_id,
            "links/external/history",
            date_from,
            date_to,
            extra={"indicator": "LINKS_TOTAL_COUNT"},
            expected=("LINKS_TOTAL_COUNT",),
            server_dates=False,
        )

    async def broken_links(self, host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return await self._list(host_id, "links/internal/broken/samples", "links", offset, limit)

    async def broken_links_history(self, host_id: str, date_from: str, date_to: str) -> dict[str, Any]:
        return await self._history(host_id, "links/internal/broken/history", date_from, date_to)

    async def recrawl_queue(self, host_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return await self._list(host_id, "recrawl/queue", "tasks", offset, limit)

    async def recrawl_quota(self, host_id: str) -> dict[str, Any]:
        result = await self._detail(host_id, "recrawl/quota")
        if not {"daily_quota", "quota_remainder"} <= result["data"].keys():
            result["availability"] = "partial"
        return result

    async def recrawl_task(self, host_id: str, task_id: str) -> dict[str, Any]:
        return await self._detail(host_id, f"recrawl/queue/{self.validate_resource_id(task_id)}")

    async def submit_recrawl(self, host_id: str, url: str) -> dict[str, Any]:
        self.validate_url_for_host(url, host_id)
        return await self._request(
            "POST",
            f"{self.base_url}{await self._host_path(host_id)}/recrawl/queue",
            headers=self._headers(),
            payload={"url": url},
            idempotent=False,
        )

    async def submit_sitemap(self, host_id: str, url: str) -> dict[str, Any]:
        self.validate_url_for_host(url, host_id)
        return await self._request(
            "POST",
            f"{self.base_url}{await self._host_path(host_id)}/user-added-sitemaps",
            headers=self._headers(),
            payload={"url": url},
            idempotent=False,
        )

    @classmethod
    def duplicate_details(cls, exc: ProviderHttpError) -> dict[str, str]:
        """Extract only documented safe duplicate fields, never upstream error text."""
        if exc.status_code != 409 or not exc.body_preview:
            return {}
        try:
            raw = json.loads(exc.body_preview)
        except (TypeError, ValueError):
            return {}
        if not isinstance(raw, dict) or raw.get("error_code") not in {
            "URL_ALREADY_ADDED",
            "SITEMAP_ALREADY_ADDED",
        }:
            return {}
        result = {"error_code": str(raw["error_code"])}
        sitemap_id = raw.get("sitemap_id")
        if isinstance(sitemap_id, str) and _ID.fullmatch(sitemap_id):
            result["sitemap_id"] = sitemap_id
        return result
