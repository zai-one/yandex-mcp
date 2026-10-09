from __future__ import annotations

import pytest
from conftest import config
from fastmcp import Client

from zai_yandex.server import create_server
from zai_yandex.tool_annotations import TOOL_ANNOTATIONS, annotations_for

WRITE_TOOLS = {
    "direct_write",
    "direct_apply_changes",
    "metrika_apply_goal",
    "webmaster_apply_recrawl",
    "webmaster_apply_sitemap",
    "yandex_serp_submit",
    "audience_create_geo_circle",
    "audience_create_geo_polygon",
    "audience_create_pixel",
    "audience_create_pixel_viewers",
}


async def test_every_tool_exposes_reviewed_annotations(tmp_path) -> None:
    async with Client(create_server(config(tmp_path), transport="stdio")) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert set(tools) == set(TOOL_ANNOTATIONS)
    for name, tool in tools.items():
        hints = tool.annotations
        assert hints is not None, name
        assert hints.readOnlyHint is not None and hints.destructiveHint is not None, name
        if name in WRITE_TOOLS:
            assert hints.readOnlyHint is False, name
        if hints.destructiveHint:
            assert name in WRITE_TOOLS, name
    for name in ("direct_write", "direct_apply_changes", "metrika_apply_goal"):
        assert tools[name].annotations.destructiveHint is True
    for name in ("webmaster_get_sqi_history", "direct_list_strategies", "direct_get_goal_statistics"):
        assert tools[name].annotations.readOnlyHint is True


def test_unclassified_tool_fails_closed() -> None:
    with pytest.raises(ValueError):
        annotations_for("direct_unreviewed_tool")
