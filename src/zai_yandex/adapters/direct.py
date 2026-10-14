from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.transport import JsonHttpClient, ProviderError, ProviderRateLimited, request_hash

INVENTORY_READ_SERVICES = frozenset(
    {
        "campaigns",
        "adgroups",
        "ads",
        "keywords",
        "sitelinks",
        "vcards",
        "businesses",
        "adimages",
        "strategies",
    }
)
# Services documented only under the v501 JSON endpoint
# (https://yandex.ru/dev/direct/doc/ru/strategies/strategies).
V501_SERVICES = frozenset({"strategies"})
# Documented points/connection limit errors
# (https://yandex.ru/dev/direct/doc/ru/concepts/errors-list): 152 not enough units, 506 too many connections.
RATE_LIMIT_ERROR_CODES = frozenset({"152", "506"})
# Extra documented read services (https://yandex.ru/dev/direct/doc/ru/<service>/get).
EXTRA_READ_METHODS: dict[str, frozenset[str]] = {
    "adextensions": frozenset({"get"}),
    "advideos": frozenset({"get"}),
    "audiencetargets": frozenset({"get"}),
    "bidmodifiers": frozenset({"get"}),
    "bids": frozenset({"get"}),  # https://yandex.ru/dev/direct/doc/ru/bids/get
    "keywordbids": frozenset({"get"}),
    "clients": frozenset({"get"}),
    "creatives": frozenset({"get"}),
    # getGeoRegions: https://yandex.ru/dev/direct/doc/ru/dictionaries/getGeoRegions
    "dictionaries": frozenset({"get", "getGeoRegions"}),
    "dynamictextadtargets": frozenset({"get"}),  # units + DynamicTextAdTargets service
    "dynamicfeedadtargets": frozenset({"get"}),  # see LEGACY_REFERENCE_SERVICES
    "vcards": frozenset({"get"}),
    "feeds": frozenset({"get"}),
    "leads": frozenset({"get"}),
    "negativekeywordsharedsets": frozenset({"get"}),
    "retargetinglists": frozenset({"get"}),
    "smartadtargets": frozenset({"get"}),  # units + best-practice/smart-ads
    "turbopages": frozenset({"get"}),
    # https://yandex.ru/dev/direct/doc/ru/changes/changes
    "changes": frozenset({"check", "checkCampaigns", "checkDictionaries"}),
}
READ_SERVICES = INVENTORY_READ_SERVICES | {"keywordsresearch", "agencyclients"} | set(EXTRA_READ_METHODS)

# Guarded write allowlist per service. A new writable service/method is inert
# until it is added here on review. campaigns:update keeps the approval-gated
# path (direct_apply_changes); the full set is exposed autonomously via
# direct_write with idempotency + readback.
WRITE_METHODS: dict[str, frozenset[str]] = {
    "campaigns": frozenset({"update", "add", "suspend", "resume", "archive", "unarchive", "delete"}),
    "adgroups": frozenset({"add", "update", "delete"}),
    "ads": frozenset({"add", "update", "delete", "moderate", "suspend", "resume", "archive", "unarchive"}),
    "keywords": frozenset({"add", "update", "delete", "suspend", "resume"}),
    "bids": frozenset({"set", "setAuto"}),
    # Parity writes, each a documented method (https://yandex.ru/dev/direct/doc/ru/<service>/<method>).
    "keywordbids": frozenset({"set", "setAuto"}),
    "bidmodifiers": frozenset({"add", "set", "delete"}),
    "audiencetargets": frozenset({"add", "delete", "suspend", "resume", "setBids"}),
    "retargetinglists": frozenset({"add", "update", "delete"}),
    "sitelinks": frozenset({"add", "delete"}),
    "adimages": frozenset({"add", "delete"}),
    "adextensions": frozenset({"add", "delete"}),
    "advideos": frozenset({"add"}),
    "negativekeywordsharedsets": frozenset({"add", "update", "delete"}),
    "feeds": frozenset({"add", "update", "delete"}),
    # Services whose method pages were dropped from the new reference navigation (2026) but which
    # are neither announced as removed in the changelog (https://yandex.ru/dev/direct/doc/ru/changelog)
    # nor unused by the current guides (best-practice/smart-ads still calls SmartAdTargets.add;
    # concepts/contact-info maps business cards to the vCards service). Approval-only.
    "vcards": frozenset({"add", "delete"}),
    "dynamictextadtargets": frozenset({"add", "delete", "suspend", "resume", "setBids"}),
    "smartadtargets": frozenset({"add", "update", "delete", "suspend", "resume", "setBids"}),
    "dynamicfeedadtargets": frozenset({"add", "delete", "suspend", "resume", "setBids"}),
    "creatives": frozenset({"add"}),
    # v501 endpoint (service_url); https://yandex.ru/dev/direct/doc/ru/strategies/strategies
    "strategies": frozenset({"add", "update", "archive", "unarchive"}),
    # https://yandex.ru/dev/direct/doc/ru/agencyclients/update (agency accounts only; approval-gated)
    # addPassportOrganizationMember invites one person into a Yandex ID organization
    # (https://yandex.ru/dev/direct/doc/ru/agencyclients/addPassportOrganizationMember; 10 invites/day).
    "agencyclients": frozenset({"update", "addPassportOrganizationMember"}),
    # https://yandex.ru/dev/direct/doc/ru/clients/update (the advertiser itself; approval-gated)
    "clients": frozenset({"update"}),
}
# Unified performance campaigns/groups are served only by the v501 endpoint
# (https://yandex.ru/dev/direct/doc/ru/campaigns/add-unified-campaign, adgroups/add).
UNIFIED_KEYS: dict[str, str] = {"campaigns": "UnifiedCampaign", "adgroups": "UnifiedAdGroup"}
UNIFIED_METHODS = frozenset({"add", "update"})
# update items addressed by another key than ``Id`` (None: the authorized client itself).
UPDATE_ID_FIELD: dict[str, str | None] = {"agencyclients": "ClientId", "clients": None}
# Writes that only the approval-gated path (direct_prepare_write -> direct_apply_write) may run.
LEGACY_REFERENCE_SERVICES = frozenset(
    {"vcards", "dynamictextadtargets", "smartadtargets", "dynamicfeedadtargets"}
)
APPROVAL_ONLY_WRITES: frozenset[str] = (
    frozenset({"agencyclients", "clients", "strategies"}) | LEGACY_REFERENCE_SERVICES
)
# Upstream top-level payload key for object-based writes (add/update/set).
SERVICE_PAYLOAD_KEY: dict[str, str] = {
    "campaigns": "Campaigns",
    "adgroups": "AdGroups",
    "ads": "Ads",
    "keywords": "Keywords",
    "bids": "Bids",
    "keywordbids": "KeywordBids",
    "bidmodifiers": "BidModifiers",
    "audiencetargets": "AudienceTargets",
    "retargetinglists": "RetargetingLists",
    "sitelinks": "SitelinksSets",
    "adimages": "AdImages",
    "adextensions": "AdExtensions",
    "advideos": "AdVideos",
    "negativekeywordsharedsets": "NegativeKeywordSharedSets",
    "feeds": "Feeds",
    "vcards": "VCards",
    "dynamictextadtargets": "Webpages",
    "smartadtargets": "SmartAdTargets",
    "dynamicfeedadtargets": "DynamicFeedAdTargets",
    "creatives": "Creatives",
    "strategies": "Strategies",
    "agencyclients": "Clients",
    "clients": "Clients",
}
# Per-method payload key overrides: setBids takes "Bids"; set on bidmodifiers keeps BidModifiers.
METHOD_PAYLOAD_KEY: dict[str, str] = {"setBids": "Bids"}
# Methods that target existing objects by id via SelectionCriteria.Ids instead
# of a full object payload.
ID_BASED_METHODS: frozenset[str] = frozenset(
    {"suspend", "resume", "archive", "unarchive", "delete", "moderate"}
)
# AdImages are addressed by hash, not by integer id (adimages/delete).
HASH_BASED: frozenset[tuple[str, str]] = frozenset({("adimages", "delete")})
MAX_APPLY_ITEMS = 100

READ_METHODS = {
    # https://yandex.ru/dev/direct/doc/ru/agencyclients/get (agency accounts only)
    "agencyclients": frozenset({"get"}),
    "campaigns": frozenset({"get"}),
    "adgroups": frozenset({"get"}),
    "ads": frozenset({"get"}),
    "keywords": frozenset({"get"}),
    "sitelinks": frozenset({"get"}),
    "vcards": frozenset({"get"}),
    "businesses": frozenset({"get"}),
    "adimages": frozenset({"get"}),
    "strategies": frozenset({"get"}),
    # https://yandex.ru/dev/direct/doc/ru/keywordsresearch/deduplicate (local-effect utility, no writes)
    "keywordsresearch": frozenset({"hasSearchVolume", "deduplicate"}),
    **EXTRA_READ_METHODS,
}
DIRECT_REPORT_TYPES = frozenset(
    {
        "CAMPAIGN_PERFORMANCE_REPORT",
        "CRITERIA_PERFORMANCE_REPORT",
        "CUSTOM_REPORT",
        "SEARCH_QUERY_PERFORMANCE_REPORT",
    }
)
# Metrika goal metrics valid in every supported report type
# (https://yandex.ru/dev/direct/doc/ru/fields-list). With Goals set, the aggregate goal columns are
# returned per goal and attribution model as <Field>_<GoalId>_<Model>.
GOAL_REPORT_FIELDS = frozenset(
    {
        "ConversionRate",
        "Conversions",
        "CostPerConversion",
        "GoalsRoi",
        "Profit",
        "PurchaseGoalsRoi",
        "PurchaseProfit",
        "PurchaseRevenue",
        "Revenue",
    }
)
ATTRIBUTION_MODELS = frozenset({"FCCD", "LC", "LSCCD", "AUTO"})
MAX_REPORT_GOALS = 10
MAX_REPORT_ROWS = 1_000_000
DIRECT_REPORT_FIELDS = GOAL_REPORT_FIELDS | frozenset(
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
CAMPAIGN_REPORT_FIELDS = GOAL_REPORT_FIELDS | frozenset(
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
SEARCH_QUERY_REPORT_FIELDS = GOAL_REPORT_FIELDS | frozenset(
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
CRITERIA_REPORT_FIELDS = GOAL_REPORT_FIELDS | frozenset(
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


def _goal_ids(goals: list[int | str] | None) -> list[str] | None:
    if goals is None:
        return None
    if not isinstance(goals, list) or not 1 <= len(goals) <= MAX_REPORT_GOALS:
        raise ValueError(f"goals must contain between 1 and {MAX_REPORT_GOALS} Metrika goal ids")
    normalized = []
    for goal in goals:
        value = str(goal).strip() if isinstance(goal, (int, str)) and not isinstance(goal, bool) else ""
        if not value.isascii() or not value.isdigit() or len(value) > 19 or int(value) <= 0:
            raise ValueError("goals must be positive numeric Metrika goal ids")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("goals must be unique")
    return normalized


def _attribution_models(models: list[str] | None, goals: list[str] | None) -> list[str] | None:
    if models is None:
        return None
    if goals is None:
        raise ValueError("attribution_models require goals")
    if (
        not isinstance(models, list)
        or not models
        or len(set(models)) != len(models)
        or not set(models) <= ATTRIBUTION_MODELS
    ):
        allowed = ", ".join(sorted(ATTRIBUTION_MODELS))
        raise ValueError(f"attribution_models must be unique values of {allowed}")
    return list(models)


def build_statistics_request(
    campaign_ids: list[int] | None,
    date_from: str,
    date_to: str,
    field_names: list[str] | None,
    report_type: str,
    *,
    goals: list[int | str] | None = None,
    attribution_models: list[str] | None = None,
    row_limit: int | None = None,
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
    goal_ids = _goal_ids(goals)
    models = _attribution_models(attribution_models, goal_ids)
    if row_limit is not None and (
        isinstance(row_limit, bool) or not isinstance(row_limit, int) or not 1 <= row_limit <= MAX_REPORT_ROWS
    ):
        raise ValueError(f"row_limit must be between 1 and {MAX_REPORT_ROWS}")
    selection_criteria: dict[str, Any] = {"DateFrom": date_from, "DateTo": date_to}
    if campaign_ids is not None:
        selection_criteria["Filter"] = [
            {
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": [str(value) for value in campaign_ids],
            }
        ]
    name_parts: list[Any] = [campaign_ids, date_from, date_to, fields, normalized_report_type]
    extra: dict[str, Any] = {}
    if goal_ids is not None:
        extra["Goals"] = goal_ids
    if models is not None:
        extra["AttributionModels"] = models
    if row_limit is not None:
        extra["Page"] = {"Limit": row_limit}
    if extra:
        # Offline report names must be unique per definition; legacy names stay unchanged.
        name_parts.append(extra)
    return {
        "SelectionCriteria": selection_criteria,
        **{key: extra[key] for key in ("Goals", "AttributionModels") if key in extra},
        "FieldNames": fields,
        **({"Page": extra["Page"]} if "Page" in extra else {}),
        "ReportName": "mcp-" + request_hash(name_parts)[:24],
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

    def service_url(self, service: str) -> str:
        if service in V501_SERVICES and self.base_url.endswith("/v5"):
            return f"{self.base_url[: -len('/v5')]}/v501/{service}"
        return f"{self.base_url}/{service}"

    @staticmethod
    def raise_for_error(response: dict[str, Any]) -> None:
        error = response.get("error")
        if not isinstance(error, dict):
            return
        code = str(error.get("error_code", "?"))
        if code in RATE_LIMIT_ERROR_CODES:
            raise ProviderRateLimited(f"Yandex Direct limit error {code}")
        raise ProviderError(f"Yandex Direct error {code}")

    async def read(self, service: str, params: dict[str, Any]) -> dict[str, Any]:
        normalized, values = self.validate_read(service, params)
        if not self.token:
            raise ProviderError("Yandex Direct token is not configured")
        headers = {"Authorization": f"Bearer {self.token}", "Accept-Language": "ru"}
        if self.client_login:
            headers["Client-Login"] = self.client_login
        response = await self.coalescer.run(
            request_hash({"provider": "yandex_direct", "service": normalized, "params": values}),
            lambda: self.http.request("POST", self.service_url(normalized), headers=headers, payload=values),
        )
        self.raise_for_error(response)
        return response

    @staticmethod
    def risk_tier(service: str, method: str) -> str:
        normalized_method = str(method).strip()
        if normalized_method in {"delete", "addPassportOrganizationMember"}:
            return "high"  # irreversible / sends an invitation to a person
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
        id_field = UPDATE_ID_FIELD.get(normalized, "Id") if normalized_method == "update" else "Id"
        if normalized_method == "addPassportOrganizationMember":
            YandexDirectAdapter._validate_member_invite(items)
            return normalized, normalized_method, [dict(items[0])]
        if normalized == "clients" and len(items) != 1:
            raise ProviderError("clients update takes exactly one item (the authorized client)")
        YandexDirectAdapter.is_unified(normalized, normalized_method, items)
        for item in items:
            if not isinstance(item, dict) or not item:
                raise ProviderError("each Direct apply item must be a non-empty object")
            if (normalized, normalized_method) in HASH_BASED:
                if not isinstance(item.get("AdImageHash"), str) or not item["AdImageHash"]:
                    raise ProviderError("adimages delete requires an AdImageHash per item")
            elif needs_id and id_field is not None:
                item_id = item.get(id_field)
                if isinstance(item_id, bool) or not isinstance(item_id, int):
                    raise ProviderError(f"this Direct method requires an integer {id_field} per item")
        return normalized, normalized_method, [dict(item) for item in items]

    @staticmethod
    def _validate_member_invite(items: list[Any]) -> None:
        if len(items) != 1 or not isinstance(items[0], dict):
            raise ProviderError("addPassportOrganizationMember takes exactly one object")
        item = items[0]
        if set(item) - {"PassportOrganizationLogin", "Role", "SendInviteTo"}:
            raise ProviderError(
                "addPassportOrganizationMember accepts PassportOrganizationLogin, Role, SendInviteTo"
            )
        login = item.get("PassportOrganizationLogin")
        if not isinstance(login, str) or not login.strip():
            raise ProviderError("PassportOrganizationLogin is required")
        if item.get("Role") not in {"ADMIN", "USER"}:
            raise ProviderError("Role must be ADMIN or USER")
        target = item.get("SendInviteTo")
        if not isinstance(target, dict) or len(target) != 1 or not set(target) <= {"Phone", "Email"}:
            raise ProviderError("SendInviteTo needs exactly one of Phone or Email")
        if "Phone" in target and not (isinstance(target["Phone"], str) and target["Phone"].isdigit()):
            raise ProviderError("SendInviteTo.Phone must be digits only")
        if "Email" in target and not (isinstance(target["Email"], str) and "@" in target["Email"]):
            raise ProviderError("SendInviteTo.Email must be an email address")

    @staticmethod
    def is_unified(service: str, method: str, items: list[dict[str, Any]]) -> bool:
        """True when every item is a UnifiedCampaign/UnifiedAdGroup (v501); mixed batches fail."""
        key = UNIFIED_KEYS.get(service)
        if key is None or method not in UNIFIED_METHODS:
            return False
        flags = {isinstance(item, dict) and key in item for item in items}
        if len(flags) > 1:
            raise ProviderError(f"do not mix {key} items with other types in one call")
        return flags == {True}

    def endpoint(self, service: str, *, v501: bool = False) -> str:
        if v501 and self.base_url.endswith("/v5"):
            return f"{self.base_url[: -len('/v5')]}/v501/{service}"
        return self.service_url(service)

    @staticmethod
    def _apply_params(service: str, method: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        if method == "addPassportOrganizationMember":
            return dict(items[0])  # params is the single invite object, not a list
        if (service, method) in HASH_BASED:
            return {"SelectionCriteria": {"AdImageHashes": [item["AdImageHash"] for item in items]}}
        if method in ID_BASED_METHODS:
            return {"SelectionCriteria": {"Ids": [item["Id"] for item in items]}}
        return {METHOD_PAYLOAD_KEY.get(method, SERVICE_PAYLOAD_KEY[service]): items}

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
            self.endpoint(normalized, v501=self.is_unified(normalized, normalized_method, validated)),
            headers=headers,
            payload={
                "method": normalized_method,
                "params": self._apply_params(normalized, normalized_method, validated),
            },
        )
        self.raise_for_error(response)
        return response

    async def readback(self, service: str, ids: list[int], *, v501: bool = False) -> dict[str, Any]:
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
            # FieldNames from each service's documented get (https://yandex.ru/dev/direct/doc/ru/<s>/get)
            "retargetinglists": ["Id", "Name", "Type", "IsAvailable"],
            "sitelinks": ["Id", "Sitelinks"],
            "negativekeywordsharedsets": ["Id", "Name", "NegativeKeywords"],
            "feeds": ["Id", "Name", "SourceType", "Status"],
            "audiencetargets": ["Id", "AdGroupId", "CampaignId", "RetargetingListId", "InterestId", "State"],
            "bidmodifiers": ["Id", "CampaignId", "AdGroupId", "Level", "Type"],
            "adextensions": ["Id", "Type", "Status"],
            "strategies": ["Id", "Name", "Type", "StatusArchived"],
            "creatives": ["Id", "Type", "Name"],
            "vcards": ["Id", "CampaignId", "CompanyName"],
            "dynamictextadtargets": ["Id", "AdGroupId", "CampaignId", "Name", "State"],
            "smartadtargets": ["Id", "AdGroupId", "CampaignId", "Name", "State"],
            "dynamicfeedadtargets": ["Id", "AdGroupId", "CampaignId", "Name", "State"],
        }
        if normalized == "clients":
            # clients.get has no SelectionCriteria: it returns the authorized client.
            result = await self.read(
                "clients",
                {"method": "get", "params": {"FieldNames": ["ClientId", "Login", "ClientInfo", "Phone"]}},
            )
            return {"service": normalized, "confirmed": True, "ids": [], "result": result}
        if not ids or normalized not in field_map:
            return {"service": normalized, "confirmed": False, "ids": ids, "result": None}
        criteria: dict[str, Any] = {"Ids": ids}
        if normalized == "bidmodifiers":
            criteria["Levels"] = ["CAMPAIGN", "AD_GROUP"]  # required by bidmodifiers/get
        params = {"SelectionCriteria": criteria, "FieldNames": field_map[normalized]}
        if v501:
            result = await self._read_v501(normalized, params)
        else:
            result = await self.read(normalized, {"method": "get", "params": params})
        return {"service": normalized, "confirmed": True, "ids": ids, "result": result}

    async def _read_v501(self, service: str, params: dict[str, Any]) -> dict[str, Any]:
        """get on the v501 endpoint (unified campaigns/groups readback)."""
        self.validate_read(service, {"method": "get", "params": params})
        headers = {"Authorization": f"Bearer {self.token}", "Accept-Language": "ru"}
        if self.client_login:
            headers["Client-Login"] = self.client_login
        response = await self.http.request(
            "POST",
            self.endpoint(service, v501=True),
            headers=headers,
            payload={"method": "get", "params": params},
        )
        self.raise_for_error(response)
        return response

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
        *,
        goals: list[int | str] | None = None,
        attribution_models: list[str] | None = None,
        row_limit: int | None = None,
    ) -> dict[str, Any]:
        params = build_statistics_request(
            campaign_ids,
            date_from,
            date_to,
            field_names,
            report_type,
            goals=goals,
            attribution_models=attribution_models,
            row_limit=row_limit,
        )
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
