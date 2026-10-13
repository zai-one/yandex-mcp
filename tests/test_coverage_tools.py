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
        await client.call_tool("direct_get_client", {})
        await client.call_tool("direct_get_dictionaries", {"names": ["Currencies"]})
        await client.call_tool("direct_check_changes", {"timestamp": "2026-10-01T00:00:00Z"})
        await client.call_tool("direct_get_bid_modifiers", {"campaign_ids": [5]})
        await client.call_tool("direct_list_feeds", {"limit": 5})
        with pytest.raises(ToolError):
            await client.call_tool("direct_get_dictionaries", {"names": ["Nope"]})
    urls = [call[1].rsplit("/", 1)[1] for call in recorder.calls]
    assert urls == ["clients", "dictionaries", "changes", "bidmodifiers", "feeds"]
    assert (
        recorder.calls[0][2]["params"]["FieldNames"]
        and "Phone" not in recorder.calls[0][2]["params"]["FieldNames"]
    )


async def test_webmaster_extra_calls_documented_paths(tmp_path):
    recorder = Recorder()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        await client.call_tool("webmaster_get_owners", {"host_id": HOST})
        await client.call_tool("webmaster_list_feeds", {"host_id": HOST})
        await client.call_tool("webmaster_list_regions", {"host_id": HOST, "filter_text": "Моск"})
        await client.call_tool("webmaster_query_analytics", {"host_id": HOST, "limit": 50})
    paths = [(call[0], call[1].split("/hosts/")[-1]) for call in recorder.calls if "/hosts/" in call[1]]
    suffixes = [(method, path.split("/", 1)[1]) for method, path in paths]
    assert suffixes == [
        ("GET", "owners"),
        ("GET", "feeds/list"),
        ("GET", "pro/regions"),
        ("POST", "query-analytics/list"),
    ]
    assert recorder.calls[-1][2]["limit"] == 50
