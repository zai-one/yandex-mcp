from uuid import UUID

import pytest
from conftest import HOST, Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.coverage_tools import (
    DIRECT_EXTRA_TOOLS,
    WEBMASTER_EXTRA_TOOLS,
    build_changes_params,
    build_list_params,
)
from zai_yandex.server import create_server
from zai_yandex.state import StateStore
from zai_yandex.toolsets import DEFAULT_TOOLSETS, TOOLSETS, build_surface


def test_extra_tools_are_opt_in_only():
    default = build_surface("default").tools
    assert not set(DIRECT_EXTRA_TOOLS + WEBMASTER_EXTRA_TOOLS) & default
    assert "direct_extra" not in DEFAULT_TOOLSETS and "webmaster_extra" not in DEFAULT_TOOLSETS
    assert set(TOOLSETS["webmaster_extra"]["tools"]).isdisjoint(TOOLSETS["webmaster"]["tools"])


def test_builders_follow_documented_shapes():
    params = build_list_params("direct_get_keyword_bids", {"CampaignIds": [1]}, 10, 0)["params"]
    assert params["SelectionCriteria"] == {"CampaignIds": [1]} and params["SearchFieldNames"] == ["Bid"]
    with pytest.raises(ValueError):
        build_list_params("direct_get_keyword_bids", {}, 10, 0)
    leads = build_list_params("direct_list_leads", {"TurboPageIds": [3]}, 10, 0)["params"]
    assert "Data" not in leads["FieldNames"]
    assert build_changes_params("checkCampaigns", "2026-10-01T00:00:00Z", None, None, None, None) == {
        "method": "checkCampaigns",
        "params": {"Timestamp": "2026-10-01T00:00:00Z"},
    }
    with pytest.raises(ValueError):
        build_changes_params("check", "2026-10-01T00:00:00Z", [1], [2], None, None)


async def test_direct_extra_calls_documented_services(tmp_path):
    recorder = Recorder()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools["direct_list_feeds"].inputSchema["properties"]) == {"ids", "limit", "offset"}
        assert set(tools["direct_list_sitelinks"].inputSchema["properties"]) == {"ids", "limit", "offset"}
        assert "hashes" in tools["direct_list_ad_images"].inputSchema["properties"]
        await client.call_tool("direct_get_client", {})
        await client.call_tool("direct_get_dictionaries", {"names": ["Currencies"]})
        await client.call_tool("direct_check_changes", {"timestamp": "2026-10-01T00:00:00Z"})
        await client.call_tool("direct_get_bid_modifiers", {"campaign_ids": [5]})
        await client.call_tool("direct_list_feeds", {"limit": 5})
        await client.call_tool("direct_get_bids", {"campaign_ids": [9]})
        await client.call_tool("direct_list_sitelinks", {"limit": 3})
        await client.call_tool("direct_list_ad_images", {"associated": "YES", "limit": 2})
        await client.call_tool("direct_list_businesses", {})
        await client.call_tool("direct_list_smart_ad_targets", {"campaign_ids": [1]})
        await client.call_tool("direct_list_dynamic_text_ad_targets", {"ad_group_ids": [2]})
        with pytest.raises(ToolError):
            await client.call_tool("direct_get_dictionaries", {"names": ["Nope"]})
        with pytest.raises(ToolError):
            await client.call_tool("direct_get_bids", {})
    urls = [call[1].rsplit("/", 1)[1] for call in recorder.calls]
    assert urls == [
        "clients",
        "dictionaries",
        "changes",
        "bidmodifiers",
        "feeds",
        "bids",
        "sitelinks",
        "adimages",
        "businesses",
        "smartadtargets",
        "dynamictextadtargets",
    ]
    assert (
        recorder.calls[0][2]["params"]["FieldNames"]
        and "Phone" not in recorder.calls[0][2]["params"]["FieldNames"]
    )
    assert "AuctionBids" not in recorder.calls[5][2]["params"]["FieldNames"]
    assert "Phone" not in recorder.calls[8][2]["params"]["FieldNames"]


async def test_webmaster_extra_calls_documented_paths(tmp_path):
    recorder = Recorder()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        await client.call_tool("webmaster_get_owners", {"host_id": HOST})
        await client.call_tool("webmaster_list_feeds", {"host_id": HOST})
        await client.call_tool("webmaster_list_regions", {"host_id": HOST, "filter_text": "Моск"})
        await client.call_tool("webmaster_query_analytics", {"host_id": HOST, "limit": 50})
        await client.call_tool("webmaster_get_pro_limits", {"host_id": HOST})
        await client.call_tool("webmaster_list_pro_dates", {"host_id": HOST})
        await client.call_tool(
            "webmaster_get_pro_export_status",
            {"host_id": HOST, "task_id": "11111111-2222-3333-4444-555555555555"},
        )
    paths = [(call[0], call[1].split("/hosts/")[-1]) for call in recorder.calls if "/hosts/" in call[1]]
    suffixes = [(method, path.split("/", 1)[1]) for method, path in paths]
    assert suffixes == [
        ("GET", "owners"),
        ("GET", "feeds/list"),
        ("GET", "pro/regions"),
        ("POST", "query-analytics/list"),
        ("GET", "pro/limits"),
        ("GET", "pro/serp/dates"),
        ("GET", "pro/serp/queries/download/11111111-2222-3333-4444-555555555555"),
    ]
    qa = next(call[2] for call in recorder.calls if call[0] == "POST")
    assert qa["limit"] == 50


async def test_webmaster_start_pro_export_requires_write_flag(tmp_path):
    recorder = Recorder()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "webmaster_start_pro_export",
                {"host_id": HOST, "dates": ["2026-09-10"], "idempotency_key": "pro-1"},
            )
    assert recorder.calls == []

    enabled = config(tmp_path, yandex_webmaster_write_enabled=True)
    recorder2 = Recorder()
    server2 = create_server(enabled, transport="stdio", http_factory=recorder2.factory)
    async with Client(server2) as client:
        await client.call_tool(
            "webmaster_start_pro_export",
            {
                "host_id": HOST,
                "dates": ["2026-09-10", "2026-09-11"],
                "paths": ["/blog"],
                "idempotency_key": "pro-2",
            },
        )
    posts = [c for c in recorder2.calls if c[0] == "POST"]
    assert posts and posts[0][1].endswith("/pro/serp/queries/download/")
    body = posts[0][2]
    assert body["dates"] == ["2026-09-10", "2026-09-11"] and body["use_pro_tariff"] == "false"


async def test_paid_pro_tariff_export_needs_an_accepted_approval(tmp_path):
    settings = config(tmp_path, yandex_webmaster_write_enabled=True)
    recorder = Recorder()
    paid = {"host_id": HOST, "dates": ["2026-09-10"], "use_pro_tariff": True, "idempotency_key": "pro-paid-1"}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        gate = (await client.call_tool("webmaster_start_pro_export", paid)).data
        assert gate["status"] == "approval_required"
        assert not [call for call in recorder.calls if call[0] == "POST"]
        approved = {**paid, "approval_id": gate["approval_id"]}
        with pytest.raises(ToolError):  # Prepared, not yet accepted by a human.
            await client.call_tool("webmaster_start_pro_export", approved)
        assert StateStore(settings).accept(
            UUID(gate["approval_id"]), principal=settings.principal_id, request_hash=gate["request_hash"]
        )
        with pytest.raises(ToolError):  # The approval covers these exact dates only.
            await client.call_tool("webmaster_start_pro_export", {**approved, "dates": ["2026-09-11"]})
        first = (await client.call_tool("webmaster_start_pro_export", approved)).data
        assert first["applied"]
        replay = (await client.call_tool("webmaster_start_pro_export", approved)).data
        assert replay == first
        with pytest.raises(ToolError):  # Consumed: no second paid export under a new key.
            await client.call_tool(
                "webmaster_start_pro_export", {**approved, "idempotency_key": "pro-paid-2"}
            )
    posts = [call for call in recorder.calls if call[0] == "POST"]
    assert len(posts) == 1 and posts[0][2]["use_pro_tariff"] == "true"
