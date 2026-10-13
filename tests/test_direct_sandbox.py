import json
import os

from conftest import Recorder, config
from fastmcp import Client

from zai_yandex.config import ServiceConfig, load_accounts
from zai_yandex.server import create_server


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
