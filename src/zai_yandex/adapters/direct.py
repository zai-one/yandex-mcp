from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.transport import JsonHttpClient, ProviderError, request_hash

INVENTORY_READ_SERVICES = frozenset(
    {"campaigns", "adgroups", "ads", "keywords", "sitelinks", "vcards", "businesses", "adimages"}
)
READ_SERVICES = INVENTORY_READ_SERVICES | {"keywordsresearch"}

# Guarded write allowlist per service. A new writable service/method is inert
# until it is added here on review. campaigns:update keeps the approval-gated
# path (direct_apply_changes); the full set is exposed autonomously via
# direct_write with idempotency + readback.
WRITE_METHODS: dict[str, frozenset[str]] = {
    "campaigns": frozenset({"update", "add", "suspend", "resume", "archive", "unarchive", "delete"}),
    "adgroups": frozenset({"add", "update", "delete"}),
    "ads": frozenset({"add", "update", "delete", "moderate", "suspend", "resume", "archive"}),
    "keywords": frozenset({"add", "update", "delete", "suspend", "resume"}),
    "bids": frozenset({"set", "setAuto"}),
}
# Upstream top-level payload key for object-based writes (add/update/set).
SERVICE_PAYLOAD_KEY: dict[str, str] = {
    "campaigns": "Campaigns",
    "adgroups": "AdGroups",
    "ads": "Ads",
    "keywords": "Keywords",
    "bids": "Bids",
}
# Methods that target existing objects by id via SelectionCriteria.Ids instead
# of a full object payload.
ID_BASED_METHODS: frozenset[str] = frozenset(
    {"suspend", "resume", "archive", "unarchive", "delete", "moderate"}
)
MAX_APPLY_ITEMS = 100

READ_METHODS = {
    "campaigns": frozenset({"get"}),
    "adgroups": frozenset({"get"}),
    "ads": frozenset({"get"}),
    "keywords": frozenset({"get"}),
    "sitelinks": frozenset({"get"}),
    "vcards": frozenset({"get"}),
    "businesses": frozenset({"get"}),
    "adimages": frozenset({"get"}),
    "keywordsresearch": frozenset({"hasSearchVolume"}),
}
DIRECT_REPORT_TYPES = frozenset(
    {
        "CAMPAIGN_PERFORMANCE_REPORT",
        "CRITERIA_PERFORMANCE_REPORT",
        "CUSTOM_REPORT",
        "SEARCH_QUERY_PERFORMANCE_REPORT",
    }
)
DIRECT_REPORT_FIELDS = frozenset(
    {
        "AdGroupId",
        "AdGroupName",
        "AdId",
        "AdNetworkType",
        "Age",
        "AvgCpc",
        "CampaignId",
        "CampaignName",
        "ClickType",
        "Clicks",
        "ConversionRate",
        "Conversions",
        "Cost",
        "CostPerConversion",
        "Criterion",
        "CriterionId",
        "CriterionType",
        "Ctr",
        "Date",
        "Device",
        "ExternalNetworkName",
        "Gender",
        "Impressions",
        "LocationOfPresenceId",
        "LocationOfPresenceName",
        "MobilePlatform",
        "Placement",
        "Query",
        "TargetingLocationId",
        "TargetingLocationName",
    }
)
CAMPAIGN_REPORT_FIELDS = frozenset(
    {
        "Date",
        "CampaignId",
        "CampaignName",
        "AdNetworkType",
        "Impressions",
        "Clicks",
        "Cost",
        "Conversions",
    }
)
SEARCH_QUERY_REPORT_FIELDS = frozenset(
    {
        "Date",
        "CampaignId",
        "CampaignName",
        "AdGroupId",
        "AdGroupName",
        "Criterion",
        "Query",
        "Impressions",
        "Clicks",
        "Cost",
        "Conversions",
    }
)
CRITERIA_REPORT_FIELDS = frozenset(
    {
        "Date",
        "CampaignId",
        "CampaignName",
        "AdGroupId",
        "AdGroupName",
        "CriterionId",
        "Criterion",
        "CriterionType",
        "AdNetworkType",
        "Placement",
        "ExternalNetworkName",
        "Device",
        "MobilePlatform",
        "LocationOfPresenceId",
        "LocationOfPresenceName",
        "TargetingLocationId",
        "TargetingLocationName",
        "Age",
        "Gender",
        "Impressions",
        "Clicks",
        "Ctr",
        "Cost",
        "AvgCpc",
        "Conversions",
        "ConversionRate",
        "CostPerConversion",
    }
)
DIRECT_REPORT_FIELDS_BY_TYPE = {
    "CAMPAIGN_PERFORMANCE_REPORT": CAMPAIGN_REPORT_FIELDS,
    "CRITERIA_PERFORMANCE_REPORT": CRITERIA_REPORT_FIELDS,
    "CUSTOM_REPORT": DIRECT_REPORT_FIELDS,
    "SEARCH_QUERY_PERFORMANCE_REPORT": SEARCH_QUERY_REPORT_FIELDS,
}
DEFAULT_DIRECT_REPORT_FIELDS = ("Date", "CampaignId", "Impressions", "Clicks", "Cost")


def build_statistics_request(
    campaign_ids: list[int] | None,
    date_from: str,
    date_to: str,
    field_names: list[str] | None,
    report_type: str,
) -> dict[str, Any]:
    if campaign_ids is not None and (
        not campaign_ids
        or len(campaign_ids) > 10_000
        or len(set(campaign_ids)) != len(campaign_ids)
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in campaign_ids)
    ):
        raise ValueError("campaign_ids must contain between 1 and 10000 unique positive ids")
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start:
        raise ValueError("date_to must not precede date_from")
    fields = list(DEFAULT_DIRECT_REPORT_FIELDS if field_names is None else field_names)
    if (
        not fields
        or len(fields) > len(DIRECT_REPORT_FIELDS)
        or any(not isinstance(field, str) or not field for field in fields)
        or len(set(fields)) != len(fields)
    ):
        raise ValueError("field_names must be a non-empty unique Direct report field list")
    normalized_report_type = report_type.strip().upper()
    if normalized_report_type not in DIRECT_REPORT_TYPES:
        raise ValueError("unsupported Direct report type")
    unsupported_fields = sorted(set(fields) - DIRECT_REPORT_FIELDS_BY_TYPE[normalized_report_type])
    if unsupported_fields:
        raise ValueError(f"unsupported {normalized_report_type} fields: " + ", ".join(unsupported_fields))
    selection_criteria: dict[str, Any] = {"DateFrom": date_from, "DateTo": date_to}
    if campaign_ids is not None:
        selection_criteria["Filter"] = [
            {
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": [str(value) for value in campaign_ids],
            }
        ]
    return {
        "SelectionCriteria": selection_criteria,
        "FieldNames": fields,
        "ReportName": "mcp-"
        + request_hash([campaign_ids, date_from, date_to, fields, normalized_report_type])[:24],
        "ReportType": normalized_report_type,
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES",
        "IncludeDiscount": "NO",
    }


class YandexDirectAdapter:
    def __init__(
        self,
        base_url: str,
        token: str,
        client_login: str = "",
        http: JsonHttpClient | None = None,
        coalescer: AsyncSingleFlight | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client_login = client_login
        self.http = http or JsonHttpClient(timeout=120)
        self.coalescer = coalescer or AsyncSingleFlight()

    @staticmethod
    def validate_read(service: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        normalized = service.strip("/").lower()
        if normalized not in READ_SERVICES:
            raise ProviderError("Direct service is not in the v1 read allowlist")
        method = params.get("method")
        if method not in READ_METHODS[normalized]:
            raise ProviderError("Direct method is not in the v1 read allowlist")
        return normalized, dict(params)

    async def read(self, service: str, params: dict[str, Any]) -> dict[str, Any]:
        normalized, values = self.validate_read(service, params)
        if not self.token:
            raise ProviderError("Yandex Direct token is not configured")
        headers = {"Authorization": f"Bearer {self.token}", "Accept-Language": "ru"}
        if self.client_login:
            headers["Client-Login"] = self.client_login
        response = await self.coalescer.run(
            request_hash({"provider": "yandex_direct", "service": normalized, "params": values}),
            lambda: self.http.request(
                "POST", f"{self.base_url}/{normalized}", headers=headers, payload=values
            ),
        )
        if isinstance(response.get("error"), dict):
            raise ProviderError(f"Yandex Direct error {response['error'].get('error_code', '?')}")
        return response

    @staticmethod
    def risk_tier(service: str, method: str) -> str:
        normalized_method = str(method).strip()
        if normalized_method == "delete":
            return "high"
        if normalized_method in {"add"} or normalized_method in {"set", "setAuto"}:
            return "high" if str(service).strip("/").lower() in {"bids", "campaigns"} else "medium"
        return "medium"

    @staticmethod
    def validate_apply(
        service: str, method: str, items: list[dict[str, Any]]
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """Fail-closed validation for a Direct write before any admission.

        Only an allowlisted service/method and a bounded item count pass. Id-based
        methods (suspend/resume/archive/delete/moderate) require an integer Id per
        item; ``update`` requires an integer Id; ``add``/``set``/``setAuto`` require
        a non-empty object. Runs as the governor pre-admission validator so a bad
        write never opens the provider circuit.
        """
        normalized = service.strip("/").lower()
        normalized_method = str(method).strip()
        if normalized not in WRITE_METHODS or normalized_method not in WRITE_METHODS[normalized]:
            raise ProviderError("Direct service/method is not in the write allowlist")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_APPLY_ITEMS:
            raise ProviderError(f"Direct apply requires 1..{MAX_APPLY_ITEMS} items")
        needs_id = normalized_method in ID_BASED_METHODS or normalized_method == "update"
        for item in items:
            if not isinstance(item, dict) or not item:
                raise ProviderError("each Direct apply item must be a non-empty object")
            if needs_id:
                item_id = item.get("Id")
                if isinstance(item_id, bool) or not isinstance(item_id, int):
                    raise ProviderError("this Direct method requires an integer Id per item")
        return normalized, normalized_method, [dict(item) for item in items]

    @staticmethod
    def _apply_params(service: str, method: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        if method in ID_BASED_METHODS:
            return {"SelectionCriteria": {"Ids": [item["Id"] for item in items]}}
        return {SERVICE_PAYLOAD_KEY[service]: items}

    async def apply_changes(self, service: str, method: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute one allowlisted Direct write for any writable service."""
        normalized, normalized_method, validated = self.validate_apply(service, method, items)
        if not self.token:
            raise ProviderError("Yandex Direct token is not configured")
        headers = {"Authorization": f"Bearer {self.token}", "Accept-Language": "ru"}
        if self.client_login:
            headers["Client-Login"] = self.client_login
        # A production mutation is never coalesced or retried blindly.
        response = await self.http.request(
            "POST",
            f"{self.base_url}/{normalized}",
            headers=headers,
            payload={
                "method": normalized_method,
                "params": self._apply_params(normalized, normalized_method, validated),
            },
        )
        if isinstance(response.get("error"), dict):
            raise ProviderError(f"Yandex Direct error {response['error'].get('error_code', '?')}")
        return response

    async def readback(self, service: str, ids: list[int]) -> dict[str, Any]:
        """Re-read written objects of one service to confirm the applied state.

        Services without a get endpoint (bids) return an unconfirmed ack rather
        than a fabricated confirmation.
        """
        normalized = str(service).strip("/").lower()
        field_map = {
            "campaigns": ["Id", "Name", "State", "Status", "StatusPayment"],
            "adgroups": ["Id", "Name", "CampaignId", "Status"],
            "ads": ["Id", "AdGroupId", "State", "Status"],
            "keywords": ["Id", "Keyword", "AdGroupId", "State", "Status", "Bid"],
        }
        if not ids or normalized not in field_map:
            return {"service": normalized, "confirmed": False, "ids": ids, "result": None}
        field_names = field_map[normalized]
        result = await self.read(
            normalized,
            {
                "method": "get",
                "params": {"SelectionCriteria": {"Ids": ids}, "FieldNames": field_names},
            },
        )
        return {"service": normalized, "confirmed": True, "ids": ids, "result": result}

    async def readback_campaigns(self, campaign_ids: list[int]) -> dict[str, Any]:
        """Backward-compatible campaign readback for the approval-gated apply path."""
        return await self.read(
            "campaigns",
            {
                "method": "get",
                "params": {
                    "SelectionCriteria": {"Ids": campaign_ids},
                    "FieldNames": ["Id", "Name", "State", "Status", "StatusPayment"],
                },
            },
        )

    async def statistics(
        self,
        campaign_ids: list[int] | None,
        date_from: str,
        date_to: str,
        field_names: list[str] | None = None,
        report_type: str = "CUSTOM_REPORT",
    ) -> dict[str, Any]:
        params = build_statistics_request(campaign_ids, date_from, date_to, field_names, report_type)
        if not self.token:
            raise ProviderError("Yandex Direct token is not configured")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": "ru",
            "processingMode": "auto",
            "returnMoneyInMicros": "false",
            "skipReportHeader": "true",
            "skipReportSummary": "true",
        }
        if self.client_login:
            headers["Client-Login"] = self.client_login
        response = await self.coalescer.run(
            request_hash({"provider": "yandex_direct", "operation": "reports", "params": params}),
            lambda: self.http.request_raw(
                "POST",
                f"{self.base_url}/reports",
                headers=headers,
                payload={"params": params},
            ),
        )
        common = {
            "request_id": response.headers.get("requestid"),
            "report_hash": request_hash(params),
            "live_call": True,
            "read_only": True,
        }
        if response.status_code in {201, 202}:
            retry_value = response.headers.get("retryin", "0")
            return {
                **common,
                "status": "queued" if response.status_code == 201 else "waiting",
                "retry_after_seconds": int(retry_value) if retry_value.isdigit() else 0,
                "reports_in_queue": response.headers.get("reportsinqueue"),
                "result": None,
            }
        if response.status_code != 200:
            raise ProviderError(f"Direct Reports API returned HTTP {response.status_code}")
        rows = list(csv.DictReader(io.StringIO(response.body), delimiter="\t"))
        return {**common, "status": "completed", "rows": rows, "row_count": len(rows)}


def prepare_changes(changes: list[dict[str, Any]]) -> dict[str, Any]:
    forbidden = {"execute", "apply", "send", "production_write"}
    for change in changes:
        if forbidden & {key.lower() for key in change}:
            raise ValueError("Direct write fields are forbidden in v1 drafts")
    return {
        "draft_id": request_hash(changes),
        "changes": changes,
        "executable": False,
        "write_tool_available": False,
        "next_gate": "separate Direct write objective with sandbox and approval workflow",
    }
