import importlib.util
import json
from pathlib import Path

import pytest
from conftest import config
from fastmcp import Client

from zai_yandex.agent_layer import GUIDE_TOPICS, guide
from zai_yandex.errors import safe_provider_error
from zai_yandex.maps_geosearch import MAPS_TOOLS  # key-gated: absent without a Geosearch key
from zai_yandex.server import create_server
from zai_yandex.tool_annotations import TOOL_ANNOTATIONS
from zai_yandex.toolsets import ALL_TOOLS, build_surface, surface_from_env
from zai_yandex.transport import ProviderRateLimited

ROOT = Path(__file__).resolve().parents[1]


async def names(tmp_path, surface):
    async with Client(create_server(config(tmp_path), transport="stdio", surface=surface)) as client:
        return {tool.name for tool in await client.list_tools()}


async def test_unset_env_keeps_every_tool(tmp_path, monkeypatch):
    for key in ("YANDEX_TOOLSETS", "YANDEX_READ_ONLY", "YANDEX_TOOLS", "YANDEX_DISABLED_TOOLS",
                "YANDEX_MAPS_GEOSEARCH_API_KEY", "YANDEX_MAPS_GEOSEARCH_API_KEY_FILE"):  # fmt: skip
        monkeypatch.delenv(key, raising=False)
    expected = set(TOOL_ANNOTATIONS) - {"yandex_list_accounts", *MAPS_TOOLS}
    assert await names(tmp_path, surface_from_env()) == expected


async def test_read_only_hides_every_non_read_tool(tmp_path):
    visible = await names(tmp_path, build_surface("all", read_only=True))
    assert visible and all(TOOL_ANNOTATIONS[name].readOnlyHint for name in visible)
    assert "direct_apply_changes" not in visible and "metrika_report" in visible


async def test_presets_and_overrides(tmp_path):
    default = await names(tmp_path, build_surface("default"))
    assert "direct_write" not in default and "yandex_serp_submit" not in default
    assert "webmaster_get_summary" in default
    only = await names(
        tmp_path,
        build_surface(
            "metrika", extra_tools=["webmaster_list_hosts"], disabled_tools=["metrika_get_statistics"]
        ),
    )
    assert only == {"metrika_list_counters", "metrika_list_goals", "metrika_report", "webmaster_list_hosts"}
    with pytest.raises(ValueError):
        build_surface("nope")
    assert surface_from_env({"YANDEX_TOOLSETS": "wordstat", "YANDEX_READ_ONLY": "1"}).tools == {
        "yandex_wordstat_get_top",
        "yandex_wordstat_dynamics",
        "yandex_wordstat_regions",
        "yandex_wordstat_regions_tree",
    }


def test_toolsets_cover_every_reviewed_tool():
    assert set(TOOL_ANNOTATIONS) == ALL_TOOLS


async def test_router_resources_and_prompts(tmp_path):
    server = create_server(config(tmp_path), transport="stdio", surface=build_surface("default"))
    assert len(server.instructions) <= 1800 and "direct-safe-change" in server.instructions
    async with Client(server) as client:
        toolsets = json.loads((await client.read_resource("yandex://toolsets"))[0].text)
        assert (
            toolsets["toolsets"]["metrika"]["enabled"] and not toolsets["toolsets"]["direct_write"]["enabled"]
        )
        assert "Units" in (await client.read_resource("yandex://guide/api-units"))[0].text
        assert "yandex://guide/errors" in (await client.read_resource("yandex://playbook"))[0].text
        prompts = {prompt.name for prompt in await client.list_prompts()}
        assert prompts == {"campaign_audit", "weekly_report", "seo_audit", "budget_scaling", "yandex_setup"}
        rendered = await client.get_prompt("budget_scaling", {"target_cpa": "900 RUB"})
        assert "Do not apply" in rendered.messages[0].content.text


def test_error_envelope_has_hint_and_docs():
    envelope = safe_provider_error("direct", ProviderRateLimited("units", retry_after_seconds=60)).envelope
    assert envelope["retryable"] and envelope["retry_after_seconds"] == 60
    assert "152" in envelope["hint"] and envelope["docs"] == "yandex://guide/errors#provider_rate_limited"


def test_token_budget_and_skills():
    spec = importlib.util.spec_from_file_location("token_budget", ROOT / "scripts" / "token_budget.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0


def test_skill_references_mirror_server_guides():
    """Each skill is self-contained; its copies must match the server guides."""
    seen = set()
    for path in ROOT.glob("skills/*/references/*.md"):
        assert path.stem in GUIDE_TOPICS, path
        assert path.read_text(encoding="utf-8") == guide(path.stem), path
        seen.add(path.stem)
    assert seen
