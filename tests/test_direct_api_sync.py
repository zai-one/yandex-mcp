"""Direct Reports goals, Strategies (v501), ResponsiveAd reads and limit errors; offline fixtures only."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_direct_reports import FakeRawClient

from zai_yandex.adapters.direct import YandexDirectAdapter, build_statistics_request
from zai_yandex.server import create_server
from zai_yandex.transport import (
    JsonHttpClient,
    ProviderError,
    ProviderRateLimited,
    RawHttpResponse,
    request_hash,
)

BASE = "https://api.direct.yandex.com/json/v5"


class FakeJsonClient(JsonHttpClient):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__()
        self.response = response
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs.get("payload")))
        return self.response


def test_legacy_report_name_is_unchanged_without_goal_options() -> None:
    params = build_statistics_request([1], "2026-07-01", "2026-07-02", None, "CUSTOM_REPORT")
    fields = ["Date", "CampaignId", "Impressions", "Clicks", "Cost"]
    expected = request_hash([[1], "2026-07-01", "2026-07-02", fields, "CUSTOM_REPORT"])[:24]
    assert params["ReportName"] == "mcp-" + expected
    assert not {"Goals", "AttributionModels", "Page"} & params.keys()


def test_goal_options_reach_report_definition_and_change_report_name() -> None:
    fields = ["Date", "CampaignId", "Conversions", "Revenue", "GoalsRoi", "Profit"]
    legacy = build_statistics_request(None, "2026-07-01", "2026-07-02", fields, "CAMPAIGN_PERFORMANCE_REPORT")
    params = build_statistics_request(
        None,
        "2026-07-01",
        "2026-07-02",
        fields,
        "CAMPAIGN_PERFORMANCE_REPORT",
        goals=[123, "456"],
        attribution_models=["LC", "AUTO"],
        row_limit=500,
    )
    assert params["Goals"] == ["123", "456"]
    assert params["AttributionModels"] == ["LC", "AUTO"]
    assert params["Page"] == {"Limit": 500}
    assert params["ReportName"] != legacy["ReportName"]


@pytest.mark.parametrize(
    "options",
    [
        {"goals": []},
        {"goals": list(range(1, 12))},
        {"goals": [1, 1]},
        {"goals": ["abc"]},
        {"goals": [True]},
        {"goals": [0]},
        {"goals": [1], "attribution_models": ["LSC"]},
        {"goals": [1], "attribution_models": []},
        {"attribution_models": ["LC"]},
        {"row_limit": 0},
        {"row_limit": 1_000_001},
    ],
)
def test_goal_options_fail_closed(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        build_statistics_request(None, "2026-07-01", "2026-07-02", None, "CUSTOM_REPORT", **options)


@pytest.mark.parametrize(
    "report_type",
    ["CAMPAIGN_PERFORMANCE_REPORT", "CRITERIA_PERFORMANCE_REPORT", "SEARCH_QUERY_PERFORMANCE_REPORT"],
)
def test_goal_metrics_allowed_in_every_report_type(report_type: str) -> None:
    fields = ["Date", "Revenue", "Profit", "GoalsRoi", "PurchaseRevenue", "CostPerConversion"]
    assert (
        build_statistics_request(None, "2026-07-01", "2026-07-01", fields, report_type)["FieldNames"]
        == fields
    )


async def test_goal_statistics_parses_per_goal_columns() -> None:
    http = FakeRawClient(
        RawHttpResponse(200, {}, "Date\tCampaignId\tConversions_123_LC\n2026-07-01\t42\t3\n")
    )
    adapter = YandexDirectAdapter(BASE, "fixture-token", http=http)
    result = await adapter.statistics(
        None, "2026-07-01", "2026-07-01", ["Date", "CampaignId", "Conversions"], goals=[123]
    )
    assert result["rows"] == [{"Date": "2026-07-01", "CampaignId": "42", "Conversions_123_LC": "3"}]
    assert http.request_payload is not None and http.request_payload["params"]["Goals"] == ["123"]


async def test_strategies_use_documented_v501_endpoint() -> None:
    http = FakeJsonClient({"result": {"Strategies": []}})
    adapter = YandexDirectAdapter(BASE, "fixture-token", http=http)
    await adapter.read(
        "strategies", {"method": "get", "params": {"SelectionCriteria": {}, "FieldNames": ["Id"]}}
    )
    await adapter.read("ads", {"method": "get", "params": {"SelectionCriteria": {}, "FieldNames": ["Id"]}})
    assert [url for url, _ in http.calls] == [
        "https://api.direct.yandex.com/json/v501/strategies",
        "https://api.direct.yandex.com/json/v5/ads",
    ]


async def test_responsive_ad_field_names_pass_through_inventory_read() -> None:
    http = FakeJsonClient({"result": {"Ads": [{"Id": 1, "ResponsiveAd": {"Titles": []}}]}})
    adapter = YandexDirectAdapter(BASE, "fixture-token", http=http)
    params = {
        "method": "get",
        "params": {
            "SelectionCriteria": {"Types": ["RESPONSIVE_AD"]},
            "FieldNames": ["Id", "Type"],
            "ResponsiveAdFieldNames": ["Titles", "Texts", "Href"],
        },
    }
    result = await adapter.read("ads", params)
    assert http.calls[0][1] == params
    assert result["result"]["Ads"][0]["ResponsiveAd"] == {"Titles": []}


@pytest.mark.parametrize(
    ("code", "error"), [(152, ProviderRateLimited), (506, ProviderRateLimited), (8000, None)]
)
async def test_direct_limit_errors_are_rate_limited(code: int, error: type[Exception] | None) -> None:
    adapter = YandexDirectAdapter(BASE, "fixture-token", http=FakeJsonClient({"error": {"error_code": code}}))
    with pytest.raises(error or ProviderError) as caught:
        await adapter.read("campaigns", {"method": "get", "params": {}})
    assert isinstance(caught.value, ProviderRateLimited) is (error is not None)


async def test_strategy_tool_reads_bounded_page_and_validates(tmp_path) -> None:
    recorder = Recorder()
    async with Client(
        create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    ) as client:
        await client.call_tool("direct_list_strategies", {"strategy_ids": [5], "archived": "NO", "limit": 10})
        with pytest.raises(ToolError):
            await client.call_tool("direct_list_strategies", {"strategy_ids": [5, 5]})
        with pytest.raises(ToolError):
            await client.call_tool(
                "direct_get_goal_statistics",
                {"date_from": "2026-07-01", "date_to": "2026-07-01", "goals": []},
            )
    assert len(recorder.calls) == 1
    method, url, payload, _actor, _idempotent = recorder.calls[0]
    assert (method, url) == ("POST", "https://api.direct.yandex.com/json/v501/strategies")
    assert payload["params"]["SelectionCriteria"] == {"Ids": [5], "IsArchived": "NO"}
    assert payload["params"]["Page"] == {"Limit": 10, "Offset": 0}


async def test_direct_limit_error_surfaces_retryable_envelope(tmp_path) -> None:
    recorder = Recorder(lambda *args: {"error": {"error_code": 152, "error_string": "units"}})
    async with Client(
        create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    ) as client:
        with pytest.raises(ToolError, match="provider_rate_limited"):
            await client.call_tool("direct_list_campaigns", {})
