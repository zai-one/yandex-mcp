import asyncio
import json
import os

import pytest
from fastmcp import Client

from zai_yandex import setup_toolsets
from zai_yandex.server import create_server
from zai_yandex.setup_toolsets import PRESET_COST, PRESETS, resolve_toolsets, write_toolsets
from zai_yandex.toolsets import build_surface, surface_from_env


def _budget():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "token_budget", Path(__file__).resolve().parents[1] / "scripts/token_budget.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_presets_and_platforms_resolve():
    assert resolve_toolsets(preset="everything") == "all"
    assert resolve_toolsets(preset="direct-only").split(",")[:2] == ["account", "direct_read"]
    assert "maps_geosearch" in resolve_toolsets(["metrika", "maps"])
    assert build_surface(resolve_toolsets(preset="seo")).exposes("webmaster_list_hosts")
    with pytest.raises(ValueError):
        resolve_toolsets(["bogus"])
    with pytest.raises(ValueError):
        resolve_toolsets()


def test_default_is_still_all():
    assert surface_from_env({}).tools == build_surface("all").tools


def test_write_keeps_other_keys_and_is_private(tmp_path):
    path = tmp_path / "mcp.local.json"
    path.write_text(json.dumps({"env": {"YANDEX_STATE_PATH": "s.sqlite"}}), encoding="utf-8")
    write_toolsets(path, "account,metrika")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"env": {"YANDEX_STATE_PATH": "s.sqlite", "YANDEX_TOOLSETS": "account,metrika"}}
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_preset_cost_table_matches_measurement(tmp_path):
    module = _budget()
    measured = asyncio.run(module.preset_costs(module.synthetic_config(str(tmp_path))))
    assert set(measured) == set(PRESETS)
    for preset, cost in measured.items():
        assert cost["tools"] == PRESET_COST[preset]["tools"], preset
        assert abs(cost["tokens"] - PRESET_COST[preset]["tokens"]) <= 0.03 * cost["tokens"], preset


async def test_tool_writes_active_config_and_prompt_exists(tmp_path, monkeypatch):
    target = tmp_path / "mcp.local.json"
    monkeypatch.setattr(setup_toolsets, "ACTIVE_CONFIG", str(target))
    server = create_server(
        _budget().synthetic_config(str(tmp_path)), transport="stdio", surface=build_surface("all")
    )
    async with Client(server) as client:
        result = await client.call_tool("yandex_setup_toolsets", {"preset": "analytics", "write": True})
        prompts = {p.name for p in await client.list_prompts()}
    assert result.data["restart_required"] and "secret" not in json.dumps(result.data).lower()
    assert json.loads(target.read_text())["env"]["YANDEX_TOOLSETS"] == resolve_toolsets(preset="analytics")
    assert "yandex_setup" in prompts
