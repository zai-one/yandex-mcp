"""Opt-in Direct/Webmaster coverage tools (toolsets direct_extra, webmaster_extra).

Every endpoint is documented: Direct https://yandex.ru/dev/direct/doc/ru/<service>/get and
https://yandex.ru/dev/direct/doc/ru/changes/changes ; Webmaster
https://yandex.ru/dev/webmaster/doc/ru/reference/<page>. All tools are reads.
"""

from __future__ import annotations

import inspect
from typing import Any, Literal, cast

from zai_yandex.adapters.direct import YandexDirectAdapter
from zai_yandex.sanitizer import sanitize_provider_response
from zai_yandex.transport import ProviderError

DICTIONARY_NAMES = frozenset(
    {
        "Currencies", "MetroStations", "GeoRegions", "GeoRegionNames", "TimeZones", "Constants",
        "AdCategories", "OperationSystemVersions", "ProductivityAssertions", "SupplySidePlatforms",
        "Interests", "AudienceCriteriaTypes", "AudienceDemographicProfiles", "AudienceInterests",
        "FilterSchemas",
    }
)  # fmt: skip
# Compact, non-PII defaults (no phones, representatives, lead form data).
CLIENT_FIELDS = ["Login", "ClientId", "ClientInfo", "Currency", "CountryId", "Type", "Archived", "CreatedAt"]
CHANGES_FIELDS = frozenset({"CampaignIds", "AdGroupIds", "AdIds", "CampaignsStat"})
BID_MODIFIER_LEVELS = frozenset({"CAMPAIGN", "AD_GROUP"})

# name -> (service, default FieldNames, extra *FieldNames, criteria keys accepted, paged)
DIRECT_LISTS: dict[str, tuple[str, list[str], dict[str, list[str]], tuple[str, ...]]] = {
    "direct_list_ad_extensions": ("adextensions", ["Id", "Type", "Status", "Associated"],
                                  {"CalloutFieldNames": ["CalloutText"]}, ("Ids",)),
    "direct_list_audience_targets": ("audiencetargets",
                                     ["Id", "AdGroupId", "CampaignId", "RetargetingListId", "InterestId", "State"],
                                     {}, ("Ids", "AdGroupIds", "CampaignIds")),
    "direct_list_retargeting_lists": ("retargetinglists", ["Id", "Type", "Name", "IsAvailable", "Scope"], {},
                                      ("Ids",)),
    "direct_list_creatives": ("creatives", ["Id", "Type", "Name", "Width", "Height", "Associated"], {}, ("Ids",)),
    "direct_list_feeds": ("feeds", ["Id", "Name", "BusinessType", "SourceType", "Status", "NumberOfItems",
                                    "UpdatedAt"], {}, ("Ids",)),
    "direct_list_turbo_pages": ("turbopages", ["Id", "Name", "Href"], {}, ("Ids",)),
    "direct_list_negative_keyword_sets": ("negativekeywordsharedsets", ["Id", "Name", "NegativeKeywords",
                                                                        "Associated"], {}, ("Ids",)),
    "direct_list_ad_videos": ("advideos", ["Id", "Status"], {}, ("Ids",)),
    "direct_get_keyword_bids": ("keywordbids", ["KeywordId", "AdGroupId", "CampaignId", "ServingStatus"],
                                {"SearchFieldNames": ["Bid"], "NetworkFieldNames": ["Bid"]},
                                ("CampaignIds", "AdGroupIds", "KeywordIds")),
    "direct_list_leads": ("leads", ["Id", "SubmittedAt", "TurboPageId", "TurboPageName"], {}, ("TurboPageIds",)),
}  # fmt: skip
PARAM_FOR = {
    "Ids": "ids",
    "CampaignIds": "campaign_ids",
    "AdGroupIds": "ad_group_ids",
    "KeywordIds": "keyword_ids",
    "TurboPageIds": "turbo_page_ids",
}
DIRECT_EXTRA_TOOLS = (
    "direct_get_client",
    "direct_get_dictionaries",
    "direct_check_changes",
    "direct_get_bid_modifiers",
    *DIRECT_LISTS,
)
WEBMASTER_EXTRA_TOOLS = (
    "webmaster_get_owners",
    "webmaster_list_feeds",
    "webmaster_get_feed",
    "webmaster_get_feed_problems",
    "webmaster_list_regions",
    "webmaster_query_analytics",
)

LIST_DOCS = {
    "direct_list_ad_extensions": "Callout extensions (Id, Type, Status, text). For sitelinks use `direct_get_inventory` service=sitelinks.",
    "direct_list_audience_targets": "Audience targets (retargeting/interest) by Ids, AdGroupIds or CampaignIds.",
    "direct_list_retargeting_lists": "Retargeting and audience-selection lists (Id, Type, Name, IsAvailable).",
    "direct_list_creatives": "Creatives (image/HTML5/video/smart): Id, Type, Name, size.",
    "direct_list_feeds": "Product feeds: status, item count, update time.",
    "direct_list_turbo_pages": "Turbo pages (Id, Name, Href); use Id for `direct_list_leads`.",
    "direct_list_negative_keyword_sets": "Shared negative keyword sets by Ids (required by the API).",
    "direct_list_ad_videos": "Ad video status by video Ids (required by the API).",
    "direct_get_keyword_bids": "Current keyword bids and serving status by CampaignIds, AdGroupIds or KeywordIds (one required). Change bids via `direct_write` service=bids.",
    "direct_list_leads": "Turbo page leads metadata by TurboPageIds; form data (PII) only with include_data=true.",
}  # fmt: skip


def _ids(values: list[int] | None, name: str) -> list[int] | None:
    if values is None:
        return None
    if not isinstance(values, list) or not 1 <= len(values) <= 10_000:
        raise ValueError(f"{name} must contain 1..10000 ids")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in values):
        raise ValueError(f"{name} must be positive integers")
    return values


def build_list_params(
    tool: str, criteria: dict[str, list[int] | None], limit: int, offset: int, include_data: bool = False
) -> dict[str, Any]:
    service, fields, extra, keys = DIRECT_LISTS[tool]
    selection = {k: v for k, v in ((k, _ids(criteria.get(k), k)) for k in keys) if v}
    if service in {"keywordbids", "leads", "advideos", "negativekeywordsharedsets"} and not selection:
        raise ValueError(f"{service}.get requires one of: {', '.join(keys)}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise ValueError("limit must be 1..10000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be >= 0")
    names = [*fields, "Data"] if include_data and service == "leads" else fields
    params: dict[str, Any] = {"SelectionCriteria": selection, "FieldNames": names, **extra}
    params["Page"] = {"Limit": limit, "Offset": offset}
    return {"method": "get", "params": params}


def build_changes_params(
    method: str,
    timestamp: str | None,
    campaign_ids: list[int] | None,
    ad_group_ids: list[int] | None,
    ad_ids: list[int] | None,
    field_names: list[str] | None,
) -> dict[str, Any]:
    if method == "checkDictionaries":
        return {"method": method, "params": {"Timestamp": timestamp} if timestamp else {}}
    if not timestamp:
        raise ValueError("timestamp (UTC, YYYY-MM-DDThh:mm:ssZ) is required")
    if method == "checkCampaigns":
        return {"method": method, "params": {"Timestamp": timestamp}}
    if method != "check":
        raise ValueError("method must be check, checkCampaigns or checkDictionaries")
    ids = {
        "CampaignIds": _ids(campaign_ids, "campaign_ids"),
        "AdGroupIds": _ids(ad_group_ids, "ad_group_ids"),
        "AdIds": _ids(ad_ids, "ad_ids"),
    }
    present = {k: v for k, v in ids.items() if v}
    if len(present) != 1:
        raise ValueError("check needs exactly one of campaign_ids, ad_group_ids, ad_ids")
    fields = field_names or ["CampaignIds", "AdGroupIds", "AdIds"]
    if not set(fields) <= CHANGES_FIELDS:
        raise ValueError("field_names must be from CampaignIds, AdGroupIds, AdIds, CampaignsStat")
    return {"method": method, "params": {**present, "Timestamp": timestamp, "FieldNames": fields}}


def register_coverage_tools(server: Any, runtime: Any) -> None:
    registry, require_scopes, read = runtime.registry, runtime.require_scopes, runtime.read

    async def direct(service: str, build: Any) -> dict[str, Any]:
        holder: dict[str, Any] = {}

        def check() -> None:
            holder["params"] = build()
            YandexDirectAdapter.validate_read(service, holder["params"])

        try:
            check()
        except ValueError as exc:
            raise runtime.safe_error("yandex_direct", exc) from None
        params = holder["params"]
        return await read(
            "yandex_direct",
            f"read:{service}",
            {"service": service, "params": params},
            lambda: registry.yandex_direct().read(service, params),
            validate=check,
        )

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_get_client() -> dict[str, Any]:
        """Own Direct account info (Login, ClientId, currency, type) via Clients.get; no phones/representatives. Agencies: `direct_list_agency_clients`. Read-only."""
        return await direct("clients", lambda: {"method": "get", "params": {"FieldNames": CLIENT_FIELDS}})

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_get_dictionaries(names: list[str]) -> dict[str, Any]:
        """Direct reference dictionaries (Currencies, GeoRegions, TimeZones, Constants, Interests, AdCategories...). Use for region ids/min bids before forecasts or writes. GeoRegions is large. Read-only."""

        def build() -> dict[str, Any]:
            if not names or not set(names) <= DICTIONARY_NAMES:
                raise ValueError("names must be from: " + ", ".join(sorted(DICTIONARY_NAMES)))
            return {"method": "get", "params": {"DictionaryNames": list(dict.fromkeys(names))}}

        return await direct("dictionaries", build)

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_check_changes(
        timestamp: str | None = None,
        method: Literal["checkCampaigns", "check", "checkDictionaries"] = "checkCampaigns",
        campaign_ids: list[int] | None = None,
        ad_group_ids: list[int] | None = None,
        ad_ids: list[int] | None = None,
        field_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """What changed in Direct since timestamp (UTC ISO, Z) via Changes: checkCampaigns (which campaigns), check (objects in given ids), checkDictionaries. Use for incremental sync instead of re-reading everything. Read-only."""
        return await direct(
            "changes",
            lambda: build_changes_params(method, timestamp, campaign_ids, ad_group_ids, ad_ids, field_names),
        )

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_get_bid_modifiers(
        campaign_ids: list[int] | None = None,
        ad_group_ids: list[int] | None = None,
        ids: list[int] | None = None,
        levels: list[str] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Bid adjustments (mobile, demographics, retargeting, regional...) by campaign/group/id; levels CAMPAIGN, AD_GROUP. Read-only."""

        def build() -> dict[str, Any]:
            selection = {
                k: v
                for k, v in (
                    ("CampaignIds", _ids(campaign_ids, "campaign_ids")),
                    ("AdGroupIds", _ids(ad_group_ids, "ad_group_ids")),
                    ("Ids", _ids(ids, "ids")),
                )
                if v
            }
            if not selection:
                raise ValueError("provide campaign_ids, ad_group_ids or ids")
            chosen = levels or ["CAMPAIGN", "AD_GROUP"]
            if not set(chosen) <= BID_MODIFIER_LEVELS:
                raise ValueError("levels must be CAMPAIGN and/or AD_GROUP")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
                raise ValueError("limit must be 1..10000")
            return {
                "method": "get",
                "params": {
                    "SelectionCriteria": {**selection, "Levels": chosen},
                    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Level", "Type"],
                    "MobileAdjustmentFieldNames": ["BidModifier"],
                    "DemographicsAdjustmentFieldNames": ["Gender", "Age", "BidModifier"],
                    "RetargetingAdjustmentFieldNames": ["RetargetingConditionId", "BidModifier"],
                    "RegionalAdjustmentFieldNames": ["RegionId", "BidModifier"],
                    "Page": {"Limit": limit, "Offset": max(0, int(offset))},
                },
            }

        return await direct("bidmodifiers", build)

    def make_list(tool: str) -> None:
        service, _, _, keys = DIRECT_LISTS[tool]

        async def list_tool(
            ids: list[int] | None = None,
            campaign_ids: list[int] | None = None,
            ad_group_ids: list[int] | None = None,
            keyword_ids: list[int] | None = None,
            turbo_page_ids: list[int] | None = None,
            limit: int = 500,
            offset: int = 0,
            include_data: bool = False,
        ) -> dict[str, Any]:
            given = {
                "Ids": ids,
                "CampaignIds": campaign_ids,
                "AdGroupIds": ad_group_ids,
                "KeywordIds": keyword_ids,
                "TurboPageIds": turbo_page_ids,
            }
            extra = sorted(k for k, v in given.items() if v and k not in keys)
            if extra:
                raise runtime.safe_error(
                    "yandex_direct", ValueError(f"{tool} accepts only: {', '.join(keys)}")
                )
            return await direct(service, lambda: build_list_params(tool, given, limit, offset, include_data))

        list_tool.__name__ = list_tool.__qualname__ = tool
        # Advertise only the criteria this service accepts (keeps tools/list small).
        signature = inspect.signature(list_tool)
        keep = (
            {PARAM_FOR[key] for key in keys}
            | {"limit", "offset"}
            | ({"include_data"} if service == "leads" else set())
        )
        list_tool.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=[p for p in signature.parameters.values() if p.name in keep]
        )
        list_tool.__doc__ = LIST_DOCS[tool] + " Read-only."
        server.tool(auth=require_scopes("yandex_direct:read"))(list_tool)

    for tool in DIRECT_LISTS:
        make_list(tool)

    async def webmaster(method: str, **arguments: Any) -> dict[str, Any]:
        access = runtime.current_access()
        try:
            await registry.ensure_enabled("yandex_webmaster")
            adapter = registry.yandex_webmaster()
            result = await registry.read(
                access.principal_id,
                "yandex_webmaster",
                method,
                arguments,
                lambda: getattr(adapter, method)(**arguments),
            )
            return cast(dict[str, Any], sanitize_provider_response(result))
        except (ProviderError, PermissionError, ValueError) as exc:
            raise runtime.safe_error("yandex_webmaster", exc) from None

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_owners(host_id: str) -> dict[str, Any]:
        """Verified owners of a Webmaster host (login, verification type/date). Read-only."""
        return await webmaster("owners", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_feeds(host_id: str) -> dict[str, Any]:
        """Product/service feeds added to a Webmaster host. Details via `webmaster_get_feed`. Read-only."""
        return await webmaster("feeds", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_feed(host_id: str, url: str) -> dict[str, Any]:
        """One Webmaster feed's status and errors by feed URL. Read-only."""
        return await webmaster("feed_info", host_id=host_id, url=url)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_feed_problems(host_id: str) -> dict[str, Any]:
        """Host-level feed problems in Webmaster (blocking offers). Read-only."""
        return await webmaster("feeds_host_problems", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_regions(
        host_id: str, filter_text: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Webmaster region directory (ids for `webmaster_query_analytics` region_ids), filter by name. Read-only."""
        return await webmaster("regions", host_id=host_id, filter_text=filter_text, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_query_analytics(
        host_id: str,
        text_indicator: Literal["QUERY", "URL"] = "QUERY",
        device_type_indicator: Literal["ALL", "DESKTOP", "MOBILE_AND_TABLET", "MOBILE", "TABLET"] = "ALL",
        region_ids: list[int] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Webmaster query/URL analytics with region and text/statistic filters (up to 500 rows). Deeper than `webmaster_list_popular_queries`. Read-only."""
        return await webmaster(
            "query_analytics",
            host_id=host_id,
            offset=offset,
            limit=limit,
            device_type_indicator=device_type_indicator,
            text_indicator=text_indicator,
            region_ids=region_ids,
            filters=filters,
        )
