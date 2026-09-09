from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from conftest import SECRET, Recorder, config, connection, token
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

from zai_yandex.config import ServiceConfig
from zai_yandex.runtime import Runtime
from zai_yandex.server import create_server
from zai_yandex.state import StateStore
from zai_yandex.transport import ProviderTransportError

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "contracts/yandex.json").read_text())["tools"]
WRITE = {
    "service": "campaigns",
    "method": "update",
    "items": [{"Id": 7, "Name": "Example"}],
    "idempotency_key": "direct-example",
}


async def test_all_44_original_contracts_and_real_read_adapters(tmp_path):
    recorder, settings = Recorder(), config(tmp_path)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        listed = {
            tool.name: {
                "inputSchema": tool.inputSchema,
                "outputSchema": tool.outputSchema,
                "description": tool.description,
            }
            for tool in await client.list_tools()
        }
        assert {name: listed[name] for name in CONTRACT} == CONTRACT
        assert set(listed) - set(CONTRACT) == {
            "yandex_wordstat_dynamics",
            "yandex_wordstat_regions",
            "yandex_wordstat_regions_tree",
        }
        direct = (await client.call_tool("direct_list_campaigns", {})).data
        assert direct["result"]["Campaigns"][0]["Id"] == 7
        assert SECRET not in json.dumps(direct)
        await client.call_tool("metrika_list_counters", {})
        await client.call_tool("webmaster_list_hosts", {})
    assert len(recorder.calls) == 4  # Webmaster user resolution + host request both charged.
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM calls WHERE outcome='success'").fetchone()[0] == 3


async def test_direct_write_is_durable_and_conflicting_payload_denied(tmp_path):
    recorder, settings = Recorder(), config(tmp_path, yandex_direct_write_enabled=True)
    first = None
    for _ in range(2):
        async with Client(
            create_server(settings, transport="stdio", http_factory=recorder.factory)
        ) as client:
            result = (await client.call_tool("direct_write", WRITE)).data
            assert result["applied"] and result["readback"]["confirmed"]
            assert first is None or first == result
            first = result
            with pytest.raises(ToolError):
                await client.call_tool("direct_write", {**WRITE, "items": [{"Id": 8}]})
    assert len(recorder.calls) == 2
    assert SECRET.encode() not in settings.state_path.read_bytes()


async def test_uncertain_direct_write_cannot_repeat_after_restart(tmp_path):
    def fail(*args):
        raise ProviderTransportError(SECRET)

    recorder, settings = Recorder(fail), config(tmp_path, yandex_direct_write_enabled=True)
    for _ in range(2):
        async with Client(
            create_server(settings, transport="stdio", http_factory=recorder.factory)
        ) as client:
            with pytest.raises(ToolError) as caught:
                await client.call_tool("direct_write", WRITE)
            assert SECRET not in str(caught.value)
    assert len(recorder.calls) == 1
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT status FROM provider_writes").fetchone()[0] == "pending"


async def test_write_flags_and_allowlists_fail_before_network(tmp_path):
    recorder = Recorder()
    async with Client(
        create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    ) as client:
        with pytest.raises(ToolError):
            await client.call_tool("direct_write", WRITE)
        with pytest.raises(ToolError):
            await client.call_tool(
                "metrika_apply_goal", {"operation": "delete_goal", "counter_id": 7, "goal_id": 9}
            )
        with pytest.raises(ToolError):
            await client.call_tool("yandex_wordstat_get_top", {"phrase": "fixture", "regions": ["225"]})
    assert not recorder.calls


async def test_metrika_write_runs_real_apply_and_readback(tmp_path):
    recorder = Recorder()
    settings = config(tmp_path, yandex_metrika_write_enabled=True)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        result = (
            await client.call_tool(
                "metrika_apply_goal",
                {
                    "operation": "create_goal",
                    "counter_id": 7,
                    "params": {
                        "name": "Example",
                        "type": "action",
                        "conditions": [{"type": "exact", "url": "ok"}],
                    },
                    "idempotency_key": "metrika-example",
                },
            )
        ).data
        assert result["applied"] and result["result"]["goal_id"] == 9
    assert [call[0] for call in recorder.calls] == ["POST", "GET"]


async def test_direct_approval_is_exact_actor_bound_and_consumed_once(tmp_path, pair):
    recorder = Recorder()
    settings = config(tmp_path, public_key=pair.public_key, yandex_direct_write_enabled=True)
    server = create_server(settings, http_factory=recorder.factory)
    async with connection(server, token(pair)) as client:
        draft = (await client.call_tool("direct_prepare_changes", {"changes": WRITE["items"]})).data
        args = {
            "service": "campaigns",
            "method": "update",
            "campaigns": WRITE["items"],
            "approval_id": draft["approval_id"],
            "idempotency_key": "approved-direct",
        }
        with pytest.raises(ToolError):
            await client.call_tool("direct_apply_changes", args)
    store = StateStore(settings)
    assert store.accept(UUID(draft["approval_id"]), principal="alice", request_hash=draft["request_hash"])
    async with connection(server, token(pair, actor="bob")) as other:
        with pytest.raises(ToolError):
            await other.call_tool("direct_apply_changes", args)
    async with connection(server, token(pair)) as client:
        result = (await client.call_tool("direct_apply_changes", args)).data
        assert result["applied"]
        with pytest.raises(ToolError):
            await client.call_tool("direct_apply_changes", args)
    assert len(recorder.calls) == 2


async def test_http_scopes_account_binding_and_untrusted_budget_claims(tmp_path, pair):
    recorder = Recorder()
    settings = config(tmp_path, public_key=pair.public_key, yandex_direct_write_enabled=True)
    server = create_server(settings, http_factory=recorder.factory)
    async with connection(server, token(pair, scopes=["yandex_direct:read"])) as client:
        assert "direct_write" not in {t.name for t in await client.list_tools()}
        with pytest.raises(ToolError):
            await client.call_tool("direct_write", WRITE)
    async with connection(server, token(pair, account="other")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("direct_list_campaigns", {})
    async with connection(
        server, token(pair, monthly_cost_limit=999999, monthly_cost_unlimited=True)
    ) as client:
        draft = (await client.call_tool("yandex_serp_prepare", {"query": "fixture"})).data
        assert draft["can_accept"] is False and draft["budget_limit"] == 0
    assert not recorder.calls


async def test_rate_limits_persist_restart_and_separate_provider_buckets(tmp_path):
    recorder = Recorder()
    settings = config(tmp_path, request_rate_limit=1, principal_rate_limit=1)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        await client.call_tool("direct_list_campaigns", {})
        await client.call_tool("metrika_list_counters", {})
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        with pytest.raises(ToolError, match="provider_rate_limited"):
            await client.call_tool("direct_list_campaigns", {})
        # User resolution spends the Webmaster bucket. The host request is denied.
        with pytest.raises(ToolError, match="provider_rate_limited"):
            await client.call_tool("webmaster_list_hosts", {})
    assert len(recorder.calls) == 3


@pytest.mark.parametrize("deadline", [False, True])
async def test_cancel_and_deadline_leave_no_detached_upstream(tmp_path, monkeypatch, deadline):
    entered, released = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    recorder = Recorder(slow)
    runtime = Runtime(config(tmp_path, max_concurrency=1), "stdio", recorder.factory)
    if deadline:
        monkeypatch.setattr("zai_yandex.runtime.OPERATION_TIMEOUT_SECONDS", 0.04)

    async def run():
        return await runtime.execute(
            "direct_list_campaigns",
            {},
            lambda: runtime.registry.yandex_direct().read(
                "campaigns", {"method": "get", "params": {"SelectionCriteria": {}, "FieldNames": ["Id"]}}
            ),
        )

    task = asyncio.create_task(run())
    await entered.wait()
    if not deadline:
        task.cancel()
    with pytest.raises(ToolError if deadline else asyncio.CancelledError):
        await task
    assert released.is_set()
    with sqlite3.connect(runtime.config.state_path) as db:
        assert db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
        assert db.execute("SELECT outcome FROM calls").fetchone()[0] in {"cancelled", "error"}
    assert len(recorder.calls) == 1


async def test_real_stdio_process_discovers_without_platform_or_credentials(tmp_path):
    env = {"YANDEX_STATE_PATH": str(tmp_path / "stdio.sqlite")}
    async with Client(StdioTransport(sys.executable, ["-m", "zai_yandex"], env=env)) as client:
        assert {tool.name for tool in await client.list_tools()} == set(CONTRACT) | {
            "yandex_wordstat_dynamics",
            "yandex_wordstat_regions",
            "yandex_wordstat_regions_tree",
        }
        draft = (await client.call_tool("yandex_serp_prepare", {"query": "fixture"})).data
        assert draft["live_call"] is False and draft["can_accept"] is False


def test_http_requires_key_and_costs_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        create_server(ServiceConfig(state_path=tmp_path / "none.sqlite"))
    for value in (float("nan"), float("inf"), -1, True):
        with pytest.raises(ValueError):
            replace(config(tmp_path), account_monthly_cost_limit=value)
