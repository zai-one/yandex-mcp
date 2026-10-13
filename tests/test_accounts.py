import json
import os
import sqlite3
from uuid import UUID

import pytest
from conftest import HOST, SECRET, URL, Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_webmaster_runtime import WebmasterRecorder

from zai_yandex.config import ACTIVE_ACCOUNT, ServiceConfig, load_accounts
from zai_yandex.runtime import AdmittedHttpClient
from zai_yandex.server import create_server
from zai_yandex.state import StateStore


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


def two_accounts(tmp_path, **kwargs):
    path = write_accounts(
        tmp_path,
        {"client-a": {"direct_client_login": "login-a"}, "client-b": {"direct_token_file": "client-b.token"}},
    )
    profiles, default = load_accounts(str(path))
    return config(tmp_path, accounts=profiles, default_account=default, **kwargs)


def direct_posts(recorder):
    return [call for call in recorder.calls if call[0] == "POST" and call[1].endswith("/campaigns")]


async def test_write_ledger_never_replays_across_accounts(tmp_path):
    """The same Direct write on another account is dispatched, never replayed as applied."""
    settings = two_accounts(tmp_path, yandex_direct_write_enabled=True)
    recorder = Recorder()
    write = {"service": "campaigns", "method": "update", "items": [{"Id": 7, "Name": "Example"}]}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        on_a = (await client.call_tool("direct_write", {**write, "account": "client-a"})).data
        assert len(direct_posts(recorder)) == 2  # apply + readback
        base = (await client.call_tool("direct_write", write)).data
        assert len(direct_posts(recorder)) == 4, "base-account write was replayed from client-a"
        assert on_a["ledger_scope"] == "account=client-a" and "ledger_scope" not in base
        # An explicit key is per account too, and still replays within one account.
        keyed = {**write, "idempotency_key": "shared-key-1"}
        await client.call_tool("direct_write", {**keyed, "account": "client-a"})
        await client.call_tool("direct_write", {**keyed, "account": "client-b"})
        assert len(direct_posts(recorder)) == 8
        replay = (await client.call_tool("direct_write", {**keyed, "account": "client-b"})).data
        assert replay["applied"] and len(direct_posts(recorder)) == 8
    with sqlite3.connect(settings.state_path) as db:
        scopes = sorted(row[0] for row in db.execute("SELECT DISTINCT provider FROM provider_writes"))
    # The base account keeps the bare provider name, so pre-existing ledger rows stay valid.
    assert scopes == ["yandex_direct", "yandex_direct|account=client-a", "yandex_direct|account=client-b"]


def accept_as(settings, draft, account):
    """Operator acceptance under an explicit named account (as `approvals --named-account`)."""
    token = ACTIVE_ACCOUNT.set(account)
    try:
        return StateStore(settings).accept(
            UUID(draft["approval_id"]), principal=settings.principal_id, request_hash=draft["request_hash"]
        )
    finally:
        ACTIVE_ACCOUNT.reset(token)


async def test_direct_approval_is_bound_to_its_account(tmp_path):
    settings = two_accounts(tmp_path, yandex_direct_write_enabled=True)
    recorder = Recorder()
    items = [{"Id": 7, "Name": "Example"}]
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool("direct_prepare_changes", {"changes": items, "account": "client-a"})
        ).data
        assert draft["account"] == "client-a"
        assert not accept_as(settings, draft, None)
        assert not accept_as(settings, draft, "client-b")
        assert accept_as(settings, draft, "client-a")
        args = {
            "service": "campaigns",
            "method": "update",
            "campaigns": items,
            "approval_id": draft["approval_id"],
            "idempotency_key": "approved-direct",
        }
        for other in ({}, {"account": "client-b"}):
            with pytest.raises(ToolError):
                await client.call_tool("direct_apply_changes", {**args, **other})
        assert not direct_posts(recorder)
        result = (await client.call_tool("direct_apply_changes", {**args, "account": "client-a"})).data
        assert result["applied"]
    assert recorder.calls[0][1].endswith("/campaigns")


async def test_webmaster_approval_and_key_are_bound_to_their_account(tmp_path):
    settings = two_accounts(tmp_path, yandex_webmaster_write_enabled=True)
    recorder = WebmasterRecorder()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool(
                "webmaster_prepare_recrawl", {"host_id": HOST, "url": URL, "account": "client-a"}
            )
        ).data
        assert draft["account"] == "client-a"
        assert not accept_as(settings, draft, None)
        assert not accept_as(settings, draft, "client-b")
        assert accept_as(settings, draft, "client-a")
        args = {
            "host_id": HOST,
            "url": URL,
            "approval_id": draft["approval_id"],
            "idempotency_key": "wm-key-1",
        }
        with pytest.raises(ToolError):
            await client.call_tool("webmaster_apply_recrawl", args)
        assert not [call for call in recorder.calls if call[0] == "POST"]
        applied = (await client.call_tool("webmaster_apply_recrawl", {**args, "account": "client-a"})).data
        assert applied["status"] == "accepted"
        # The applied key of client-a is not a receipt for the base account.
        with pytest.raises(ToolError):
            await client.call_tool(
                "webmaster_reconcile_action",
                {"action": "recrawl", "host_id": HOST, "url": URL, "idempotency_key": "wm-key-1"},
            )
    assert len([call for call in recorder.calls if call[0] == "POST"]) == 1
