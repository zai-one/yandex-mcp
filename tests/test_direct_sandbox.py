import json
import os
from dataclasses import replace
from uuid import UUID

import pytest
from conftest import Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.config import ServiceConfig, load_accounts
from zai_yandex.server import create_server
from zai_yandex.state import StateStore

CAMPAIGN = [{"Name": "Brand", "StartDate": "2026-11-01", "TextCampaign": {}}]


async def test_sandbox_flag_routes_direct_to_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("YANDEX_DIRECT_SANDBOX", "true")
    assert ServiceConfig.from_env().direct_sandbox is True
    recorder = Recorder(handler=lambda *a: {"result": {"Campaigns": []}})
    server = create_server(
        config(tmp_path, direct_sandbox=True), transport="stdio", http_factory=recorder.factory
    )
    assert "SANDBOX" in server.instructions
    async with Client(server) as client:
        await client.call_tool("direct_list_campaigns", {})
    assert recorder.calls[0][1] == "https://api-sandbox.direct.yandex.com/json/v5/campaigns"


async def test_profile_can_switch_sandbox_per_call(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": {"test": {"direct_sandbox": True}}}), encoding="utf-8")
    os.chmod(path, 0o600)
    profiles, _ = load_accounts(str(path))
    recorder = Recorder(handler=lambda *a: {"result": {"Campaigns": []}})
    server = create_server(
        config(tmp_path, accounts=profiles), transport="stdio", http_factory=recorder.factory
    )
    async with Client(server) as client:
        await client.call_tool("direct_list_campaigns", {})
        await client.call_tool("direct_list_campaigns", {"account": "test"})
    assert [call[1].split("/json")[0] for call in recorder.calls] == [
        "https://api.direct.yandex.com",
        "https://api-sandbox.direct.yandex.com",
    ]


def add_handler(writes):
    def handler(method, url, payload, params):
        writes.append(url)
        return {"result": {"AddResults": [{"Id": 111}], "Campaigns": []}}

    return handler


async def test_sandbox_write_is_never_replayed_as_a_production_write(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": {"sandbox": {"direct_sandbox": True}}}), encoding="utf-8")
    profiles, _ = load_accounts(str(path))
    writes: list[str] = []
    recorder = Recorder(handler=add_handler(writes))
    settings = config(tmp_path, accounts=profiles, yandex_direct_write_enabled=True)
    write = {"service": "campaigns", "method": "add", "items": CAMPAIGN}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        rehearsal = (await client.call_tool("direct_write", {**write, "account": "sandbox"})).data
        sandbox_calls = len(writes)
        live = (await client.call_tool("direct_write", write)).data
    assert rehearsal["ledger_scope"] == "account=sandbox;endpoint=sandbox"
    assert all(url.startswith("https://api-sandbox.") for url in writes[:sandbox_calls])
    production = writes[sandbox_calls:]
    assert production and all(url.startswith("https://api.direct.") for url in production)
    assert live["applied"] and "ledger_scope" not in live


async def test_global_sandbox_flag_partitions_ledger_and_approvals(tmp_path):
    writes: list[str] = []
    recorder = Recorder(handler=add_handler(writes))
    sandbox = config(tmp_path, direct_sandbox=True, yandex_direct_write_enabled=True)
    live = replace(sandbox, direct_sandbox=False)
    write = {"service": "campaigns", "method": "add", "items": CAMPAIGN, "idempotency_key": "launch-1"}
    items = [{"Id": 7, "Name": "Example"}]
    async with Client(create_server(sandbox, transport="stdio", http_factory=recorder.factory)) as client:
        await client.call_tool("direct_write", write)
        draft = (await client.call_tool("direct_prepare_changes", {"changes": items})).data
    assert StateStore(sandbox).accept(
        UUID(draft["approval_id"]), principal=sandbox.principal_id, request_hash=draft["request_hash"]
    )
    sandbox_calls = len(writes)
    # Restarted without the sandbox flag on the same state: the key and the approval do not carry over.
    async with Client(create_server(live, transport="stdio", http_factory=recorder.factory)) as client:
        await client.call_tool("direct_write", write)
        assert any(url.startswith("https://api.direct.") for url in writes[sandbox_calls:])
        with pytest.raises(ToolError):
            await client.call_tool(
                "direct_apply_changes",
                {
                    "service": "campaigns",
                    "method": "update",
                    "campaigns": items,
                    "approval_id": draft["approval_id"],
                    "idempotency_key": "approved-direct",
                },
            )
