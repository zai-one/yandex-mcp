import json
import os

import pytest
from conftest import SECRET, Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.config import ServiceConfig, load_accounts
from zai_yandex.runtime import AdmittedHttpClient
from zai_yandex.server import create_server


def write_accounts(tmp_path, accounts, default=None):
    token = tmp_path / "client-b.token"
    token.write_text("client-b-secret-token", encoding="utf-8")
    os.chmod(token, 0o600)
    data = {"accounts": accounts}
    if default:
        data["default"] = default
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class HeaderRecorder(Recorder):
    def factory(self, policy, execution_id, actor):
        recorder = self

        class Http(AdmittedHttpClient):
            async def request(
                self, method, url, *, headers=None, payload=None, params=None, idempotent=False
            ):
                await self._admit_attempt(1)
                recorder.calls.append((url, dict(headers or {})))
                return recorder.handler(method, url, payload, params)

        return Http(policy, execution_id, actor)


def test_load_accounts_validates_names_and_keys(tmp_path):
    path = write_accounts(tmp_path, {"client-a": {"direct_client_login": "login-a"}})
    profiles, default = load_accounts(str(path))
    assert profiles[0].name == "client-a" and default is None
    for bad in ({"Bad Name": {}}, {"ok": {"direct_token": "inline-secret"}}):
        with pytest.raises(ValueError):
            load_accounts(str(write_accounts(tmp_path, bad)))


async def test_unconfigured_server_has_no_account_parameter(tmp_path):
    async with Client(create_server(config(tmp_path), transport="stdio")) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert "yandex_list_accounts" not in tools
    assert "account" not in tools["direct_list_campaigns"].inputSchema["properties"]


async def test_per_call_account_selects_client_login_and_token(tmp_path):
    path = write_accounts(
        tmp_path,
        {
            "client-a": {"direct_client_login": "login-a", "description": "Agency client A"},
            "client-b": {"direct_token_file": "client-b.token"},
        },
    )
    profiles, default = load_accounts(str(path))
    settings = config(tmp_path, accounts=profiles, default_account=default)
    recorder = HeaderRecorder()
    server = create_server(settings, transport="stdio", http_factory=recorder.factory)
    assert "yandex_list_accounts" in server.instructions
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "account" in tools["direct_list_campaigns"].inputSchema["properties"]
        listed = (await client.call_tool("yandex_list_accounts", {})).data
        assert [item["name"] for item in listed["accounts"]] == ["client-a", "client-b"]
        assert "secret" not in json.dumps(listed)
        await client.call_tool("direct_list_campaigns", {})
        await client.call_tool("direct_list_campaigns", {"account": "client-a"})
        await client.call_tool("direct_list_campaigns", {"account": "client-b"})
        with pytest.raises(ToolError):
            await client.call_tool("direct_list_campaigns", {"account": "nope"})
    headers = [call[1] for call in recorder.calls]
    assert "Client-Login" not in headers[0] and headers[0]["Authorization"] == f"Bearer {SECRET}"
    assert headers[1]["Client-Login"] == "login-a" and headers[1]["Authorization"] == f"Bearer {SECRET}"
    assert headers[2]["Authorization"] == "Bearer client-b-secret-token"


def test_profile_secrets_are_redacted(tmp_path):
    profiles, _ = load_accounts(str(write_accounts(tmp_path, {"b": {"direct_token_file": "client-b.token"}})))
    settings = ServiceConfig(state_path=tmp_path / "s.sqlite", accounts=profiles)
    assert "client-b-secret-token" in settings.secrets
    assert "client-b-secret-token" not in repr(settings)
