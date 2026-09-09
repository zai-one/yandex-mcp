from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from conftest import HOST, URL, Recorder, config, connection, token
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.server import create_server
from zai_yandex.state import StateStore
from zai_yandex.transport import ProviderTimeoutError


class WebmasterRecorder(Recorder):
    def __init__(self, action="recrawl", uncertain=False, verified=True):
        self.action, self.uncertain, self.verified = action, uncertain, verified
        self.present = False
        super().__init__(self.respond)

    def respond(self, method, url, payload, params):
        if method == "POST":
            if self.uncertain:
                raise ProviderTimeoutError("synthetic interrupted response")
            self.present = True
            return {"task_id": "task-new"} if self.action == "recrawl" else {"sitemap_id": "sitemap-new"}
        row = (
            {"task_id": "task-new", "state": "IN_PROGRESS", "url": URL}
            if self.action == "recrawl"
            else {"sitemap_id": "sitemap-new", "url": URL}
        )
        if url.endswith(("/task-new", "/sitemap-new")):
            return row
        if url.endswith("/recrawl/queue"):
            return {"tasks": [row] if self.present else [], "count": int(self.present)}
        if url.endswith("/user-added-sitemaps"):
            return {"sitemaps": [row] if self.present else [], "count": int(self.present)}
        result = self.default(method, url, payload, params)
        if "verified" in result:
            result["verified"] = self.verified
        return result


@pytest.mark.parametrize("action", ["recrawl", "sitemap"])
async def test_exact_approval_real_webmaster_write_readback_and_replay(tmp_path, action):
    settings = config(tmp_path, yandex_webmaster_write_enabled=True)
    recorder = WebmasterRecorder(action)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (await client.call_tool("webmaster_prepare_" + action, {"host_id": HOST, "url": URL})).data
        assert draft["status"] == "prepared"
        args = {
            "host_id": HOST,
            "url": URL,
            "approval_id": draft["approval_id"],
            "idempotency_key": "webmaster-example",
        }
        with pytest.raises(ToolError):
            await client.call_tool("webmaster_apply_" + action, args)
        assert not [call for call in recorder.calls if call[0] == "POST"]
        assert StateStore(settings).accept(
            UUID(draft["approval_id"]), principal=settings.principal_id, request_hash=draft["request_hash"]
        )
        result = (await client.call_tool("webmaster_apply_" + action, args)).data
        assert result["status"] == ("accepted" if action == "recrawl" else "registered")
        assert result["post_retried"] is False and result["readback"]["url"] == URL
        assert result["indexed"] is None
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        replay = (await client.call_tool("webmaster_apply_" + action, args)).data
        assert replay["replayed"] and replay["status"] == result["status"]
    assert len([call for call in recorder.calls if call[0] == "POST"]) == 1


async def test_uncertain_webmaster_can_reconcile_after_restart_with_write_flag_off(tmp_path, pair):
    settings = config(tmp_path, yandex_webmaster_write_enabled=True, public_key=pair.public_key)
    recorder = WebmasterRecorder(uncertain=True)
    server = create_server(settings, http_factory=recorder.factory)
    async with connection(server, token(pair)) as client:
        draft = (await client.call_tool("webmaster_prepare_recrawl", {"host_id": HOST, "url": URL})).data
        assert StateStore(settings).accept(
            UUID(draft["approval_id"]), principal="alice", request_hash=draft["request_hash"]
        )
        args = {
            "host_id": HOST,
            "url": URL,
            "approval_id": draft["approval_id"],
            "idempotency_key": "webmaster-uncertain",
        }
        pending = (await client.call_tool("webmaster_apply_recrawl", args)).data
        assert pending["status"] == "pending_reconciliation"
    recorder.present = True  # The upstream applied despite the lost POST response.
    server = create_server(
        replace(settings, yandex_webmaster_write_enabled=False), http_factory=recorder.factory
    )
    reconcile = {"action": "recrawl", "host_id": HOST, "url": URL, "idempotency_key": args["idempotency_key"]}
    async with connection(server, token(pair, actor="bob", scopes=["yandex_webmaster:read"])) as client:
        with pytest.raises(ToolError):
            await client.call_tool("webmaster_reconcile_action", reconcile)
    async with connection(server, token(pair, scopes=["yandex_webmaster:read"])) as client:
        confirmed = (await client.call_tool("webmaster_reconcile_action", reconcile)).data
        assert confirmed["status"] == "accepted" and confirmed["post_retried"] is False
    assert len([call for call in recorder.calls if call[0] == "POST"]) == 1


async def test_unverified_host_cannot_produce_approval_or_post(tmp_path):
    settings, recorder = config(tmp_path), WebmasterRecorder(verified=False)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("webmaster_prepare_recrawl", {"host_id": HOST, "url": URL})
    with StateStore(settings).connect() as db:
        assert db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert not [call for call in recorder.calls if call[0] == "POST"]
