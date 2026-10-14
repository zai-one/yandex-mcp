"""YANDEX_TOOL_LOADING=lazy: small start surface, runtime toolset enabling, list_changed."""

from __future__ import annotations

import json

import mcp.types
import pytest
from conftest import config
from fastmcp import Client

from zai_yandex.lazy_loading import estimate_tokens
from zai_yandex.server import create_server
from zai_yandex.toolsets import LAZY_PROFILE as PROFILE

ENABLE_SET = "metrika_logs"
OPTIN_SET = "direct_write"


def build(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return create_server(config(tmp_path), transport="stdio")


ENABLE = f"{PROFILE.service}_enable_toolsets"
ENV = PROFILE.env
LAZY_BUDGET_TOKENS = 4_000


async def startup_tokens(client: Client) -> int:
    tools = await client.list_tools()
    listed = [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
    return estimate_tokens(listed) + estimate_tokens(client.initialize_result.instructions or "")


class Notices:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def __call__(self, message) -> None:
        root = getattr(message, "root", message)
        if isinstance(root, mcp.types.ToolListChangedNotification):
            self.seen.append("tools/list_changed")


def core(eager: set[str]) -> set[str]:
    return ((set(PROFILE.toolsets.get("meta", ())) | set(PROFILE.lazy_core)) & eager) | {ENABLE}


async def eager_names(tmp_path, monkeypatch) -> set[str]:
    monkeypatch.setenv(f"{ENV}_TOOL_LOADING", "eager")
    async with Client(build(tmp_path / "eager")) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_eager_is_default_and_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv(f"{ENV}_TOOL_LOADING", raising=False)
    async with Client(build(tmp_path)) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert "Lazy tools" not in (client.initialize_result.instructions or "")
    assert ENABLE not in names
    assert names == await eager_names(tmp_path, monkeypatch)


async def test_lazy_start_surface(tmp_path, monkeypatch):
    eager = await eager_names(tmp_path, monkeypatch)
    monkeypatch.setenv(f"{ENV}_TOOL_LOADING", "lazy")
    async with Client(build(tmp_path / "lazy")) as client:
        assert client.initialize_result.capabilities.tools.listChanged is True
        names = {tool.name for tool in await client.list_tools()}
        assert f"{ENABLE}()" in client.initialize_result.instructions
        listing = (await client.call_tool(ENABLE, {})).data
    assert names == core(eager)
    assert set(PROFILE.lazy_core) & eager <= names
    assert ENABLE_SET in {row["toolset"] for row in listing["toolsets"]}
    assert all(row["description"] and row["token_cost"] > 0 for row in listing["toolsets"])
    offered = {name for row in listing["toolsets"] for name in row["tools"]}
    assert offered | names == eager | {ENABLE}


async def test_enable_registers_tools_and_notifies(tmp_path, monkeypatch):
    eager = await eager_names(tmp_path, monkeypatch)
    expected = set(PROFILE.toolsets[ENABLE_SET]) - core(eager)
    monkeypatch.setenv(f"{ENV}_TOOL_LOADING", "lazy")
    notices = Notices()
    async with Client(build(tmp_path / "lazy"), message_handler=notices) as client:
        result = (await client.call_tool(ENABLE, {"toolsets": [ENABLE_SET]})).data
        names = {tool.name for tool in await client.list_tools()}
    assert expected and set(result["added_tools"]) == expected
    assert names == core(eager) | expected
    assert notices.seen == ["tools/list_changed"]
    assert "reconnect" in result["hint"] and f"{ENV}_TOOL_LOADING=eager" in result["hint"]


async def test_enable_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv(f"{ENV}_TOOL_LOADING", "lazy")
    notices = Notices()
    async with Client(build(tmp_path), message_handler=notices) as client:
        await client.call_tool(ENABLE, {"toolsets": [ENABLE_SET]})
        again = (await client.call_tool(ENABLE, {"toolsets": [ENABLE_SET]})).data
        names = [tool.name for tool in await client.list_tools()]
        everything = (await client.call_tool(ENABLE, {"toolsets": ["all"]})).data
        twice = (await client.call_tool(ENABLE, {"toolsets": ["all"]})).data
    assert again["added_tools"] == [] and again["already_enabled"] is True
    assert len(names) == len(set(names))
    assert twice["added_tools"] == [] and not set(everything["added_tools"]) & set(names)
    assert notices.seen == ["tools/list_changed", "tools/list_changed"]


async def test_lazy_respects_read_only_and_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv(f"{ENV}_TOOL_LOADING", "lazy")
    monkeypatch.setenv(f"{ENV}_TOOLSETS", "all")
    monkeypatch.setenv(f"{ENV}_READ_ONLY", "1")
    async with Client(build(tmp_path / "ro")) as client:
        await client.call_tool(ENABLE, {"toolsets": ["all"]})
        tools = await client.list_tools()
    assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools)
    monkeypatch.delenv(f"{ENV}_READ_ONLY")
    monkeypatch.setenv(f"{ENV}_TOOLSETS", "default")
    async with Client(build(tmp_path / "allow")) as client:
        refused = (await client.call_tool(ENABLE, {"toolsets": [OPTIN_SET]})).data
    assert refused["error"] == "unknown_toolset" and OPTIN_SET not in refused["available"]


def test_invalid_mode_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(f"{ENV}_TOOL_LOADING", "sometimes")
    with pytest.raises(ValueError):
        build(tmp_path)


async def test_lazy_startup_budget(tmp_path, monkeypatch, capsys):
    costs = {}
    for mode in ("eager", "lazy"):
        monkeypatch.setenv(f"{ENV}_TOOL_LOADING", mode)
        async with Client(build(tmp_path / mode)) as client:
            costs[mode] = await startup_tokens(client)
    assert costs["lazy"] <= LAZY_BUDGET_TOKENS
    assert costs["lazy"] < costs["eager"]
    with capsys.disabled():
        print(f"\nstartup tokens {json.dumps(costs)}")


@pytest.mark.parametrize(("answer", "mode"), [("lazy", "lazy"), ("", "eager"), ("whatever", "eager")])
def test_setup_wizard_asks_eager_or_lazy(answer, mode, monkeypatch):
    from zai_yandex import setup

    asked = []

    def fake_input(prompt):
        asked.append(prompt)
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    assert setup._tool_loading() == mode
    assert "lazy" in asked[0] and "eager" in asked[0]


async def test_setup_tool_offers_and_saves_tool_loading(tmp_path, monkeypatch):
    from zai_yandex import setup_toolsets

    monkeypatch.delenv("YANDEX_TOOLSETS", raising=False)
    costs = {}
    for mode in ("eager", "lazy"):
        monkeypatch.setenv("YANDEX_TOOL_LOADING", mode)
        async with Client(build(tmp_path / mode)) as client:
            costs[mode] = await startup_tokens(client)
    for mode, measured in costs.items():
        assert abs(setup_toolsets.LOADING_COST[mode] - measured) <= 0.05 * measured, (mode, measured)
    planned = setup_toolsets.plan(None, "seo", "lazy")
    assert planned["client_env"]["YANDEX_TOOL_LOADING"] == "lazy"
    with pytest.raises(ValueError):
        setup_toolsets.plan(None, "seo", "sometimes")
    target = tmp_path / "mcp.local.json"
    setup_toolsets.write_toolsets(target, "all", "lazy")
    assert json.loads(target.read_text(encoding="utf-8"))["env"]["YANDEX_TOOL_LOADING"] == "lazy"
