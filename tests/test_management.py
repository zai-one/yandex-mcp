"""Governed management catalogue (Metrika/Webmaster/Audience) and Direct prepare->apply writes."""

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from conftest import HOST, Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_accounts import accept_as, two_accounts

from zai_yandex.defaults import apply_defaults, default_config_file, user_dir
from zai_yandex.management import CATALOG, MANAGEMENT_TOOLS, validate_args
from zai_yandex.server import create_server
from zai_yandex.state import StateStore
from zai_yandex.toolsets import DEFAULT_TOOLSETS, build_surface
from zai_yandex.transport import ProviderError

OFFICIAL = (
    "https://yandex.ru/dev/metrika/",
    "https://yandex.ru/dev/webmaster/",
    "https://yandex.ru/dev/audience/",
)


def accept(settings, draft):
    return StateStore(settings).accept(
        UUID(draft["approval_id"]), principal=settings.principal_id, request_hash=draft["request_hash"]
    )


class Calls(Recorder):
    multipart = True

    def __init__(self):
        super().__init__(self.handle)
        self.params = []

    def handle(self, method, url, payload, params):
        self.params.append(params)
        if url.endswith("/user"):
            return {"user_id": 42}
        return {"ok": True, "grants": [], "id": 5}


def test_catalogue_is_documented_opt_in_and_fail_closed():
    for ops in CATALOG.values():
        for name, op in ops.items():
            assert op.doc.startswith(OFFICIAL), name
            assert op.method in {"GET", "POST", "PUT", "DELETE"}, name
            assert (op.kind == "read") == (op.method == "GET"), name
            assert op.readback is None or ops[op.readback].kind == "read", name
    default = build_surface("default").tools
    assert not {tool for tools in MANAGEMENT_TOOLS.values() for tool in tools} & default
    assert not {"direct_prepare_write", "direct_apply_write"} & default
    assert {"metrika_manage", "webmaster_manage", "audience_manage"}.isdisjoint(DEFAULT_TOOLSETS)
    grant = CATALOG["yandex_metrika"]["grant_delete"]
    with pytest.raises(ValueError):
        validate_args("yandex_metrika", grant, {"counter_id": 1})  # user_login is mandatory
    with pytest.raises(ValueError):
        validate_args("yandex_metrika", grant, {"counter_id": 1, "query": {"user_login": "a/../b"}})
    with pytest.raises(ValueError):
        validate_args("yandex_metrika", grant, {"counter_id": 1, "query": {"user_login": "x", "oauth": "t"}})
    with pytest.raises(ValueError):
        validate_args(
            "yandex_metrika", CATALOG["yandex_metrika"]["filter_update"], {"counter_id": 1, "id": True}
        )
    with pytest.raises(ValueError):  # raw-body writes need a body too
        validate_args("yandex_webmaster", CATALOG["yandex_webmaster"]["host_add"], {})
    wrapped = validate_args(
        "yandex_metrika", CATALOG["yandex_metrika"]["label_create"], {"body": {"name": "client-a"}}
    )
    assert wrapped["body"] == {"label": {"name": "client-a"}}


async def test_reads_catalog_and_documented_urls(tmp_path):
    recorder = Calls()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        listed = (await client.call_tool("metrika_manage_read", {"operation": "catalog"})).data
        assert "grant_create" in listed["operations"] and not recorder.calls
        await client.call_tool(
            "metrika_manage_read",
            {"operation": "grant_get", "args": {"counter_id": 9, "query": {"user_login": "ivan"}}},
        )
        await client.call_tool(
            "metrika_manage_read", {"operation": "stat_bytime", "args": {"query": {"ids": 9}}}
        )
        await client.call_tool(
            "webmaster_manage_read", {"operation": "sitemap_recrawl_quota", "args": {"host_id": HOST}}
        )
        await client.call_tool(
            "audience_manage_read", {"operation": "segment_grants", "args": {"segment_id": 3}}
        )
        with pytest.raises(ToolError):
            await client.call_tool(
                "metrika_manage_read", {"operation": "grant_delete", "args": {"counter_id": 9}}
            )
    urls = [call[1] for call in recorder.calls]
    assert urls[0] == "https://api-metrika.yandex.net/management/v1/counter/9/grant"
    assert recorder.params[0] == {"user_login": "ivan"}
    assert urls[1] == "https://api-metrika.yandex.net/stat/v1/data/bytime"
    assert urls[3].startswith("https://api.webmaster.yandex.net/v4.1/user/42/hosts/") and urls[3].endswith(
        "/sitemaps/recrawl"
    )
    assert urls[4] == "https://api-audience.yandex.ru/v1/management/segment/3/grants"


async def test_write_needs_flag_acceptance_and_runs_once(tmp_path):
    settings = config(tmp_path, yandex_metrika_write_enabled=True)
    recorder = Calls()
    args = {"counter_id": 9, "query": {"user_login": "ivan"}}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool("metrika_manage_prepare", {"operation": "grant_delete", "args": args})
        ).data
        assert draft["risk"] == "high" and not recorder.calls
        apply = {"operation": "grant_delete", "args": args, "approval_id": draft["approval_id"],
                 "idempotency_key": "grant-delete-1"}  # fmt: skip
        with pytest.raises(ToolError):
            await client.call_tool("metrika_manage_apply", apply)  # not accepted yet
        assert accept(settings, draft)
        with pytest.raises(ToolError):  # args changed after approval -> hash mismatch
            await client.call_tool(
                "metrika_manage_apply", {**apply, "args": {**args, "query": {"user_login": "other"}}}
            )
        done = (await client.call_tool("metrika_manage_apply", apply)).data
        assert done["applied"] and done["readback"] == {"ok": True, "grants": [], "id": 5}
        calls = len(recorder.calls)
        again = (await client.call_tool("metrika_manage_apply", apply)).data
        assert again["request_hash"] == done["request_hash"] and len(recorder.calls) == calls
        with pytest.raises(ToolError):  # consumed approval cannot authorise a second dispatch
            await client.call_tool("metrika_manage_apply", {**apply, "idempotency_key": "grant-delete-2"})
        settled = (
            await client.call_tool(
                "metrika_manage_reconcile",
                {"operation": "grant_delete", "args": args, "idempotency_key": "grant-delete-1"},
            )
        ).data
        assert settled["status"] == "applied"
    deletes = [call for call in recorder.calls if call[0] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][1].endswith("/counter/9/grant")


async def test_write_flag_off_fails_closed(tmp_path):
    settings = config(tmp_path)
    recorder = Calls()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        args = {"host_id": HOST}
        draft = (
            await client.call_tool("webmaster_manage_prepare", {"operation": "host_delete", "args": args})
        ).data
        assert draft["write_enabled"] is False and accept(settings, draft)
        with pytest.raises(ToolError):
            await client.call_tool(
                "webmaster_manage_apply",
                {
                    "operation": "host_delete",
                    "args": args,
                    "approval_id": draft["approval_id"],
                    "idempotency_key": "host-delete-1",
                },
            )
    assert not [call for call in recorder.calls if call[0] == "DELETE"]


async def test_upload_and_webmaster_user_level_write(tmp_path):
    settings = config(tmp_path, yandex_metrika_write_enabled=True, yandex_webmaster_write_enabled=True)
    recorder = Calls()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        upload = {"counter_id": 9, "csv": "UserId,DateTime,Phone\n1,1696000000,+70000000000\n"}
        draft = (
            await client.call_tool("metrika_manage_prepare", {"operation": "calls_upload", "args": upload})
        ).data
        assert "csv" not in draft["args"] and draft["args"]["csv_rows"] == 3
        assert accept(settings, draft)
        await client.call_tool(
            "metrika_manage_apply",
            {
                "operation": "calls_upload",
                "args": upload,
                "approval_id": draft["approval_id"],
                "idempotency_key": "calls-upload-1",
            },
        )
        host = {"body": {"host_url": "https://example.test"}}
        draft = (
            await client.call_tool("webmaster_manage_prepare", {"operation": "host_add", "args": host})
        ).data
        assert accept(settings, draft)
        await client.call_tool(
            "webmaster_manage_apply",
            {
                "operation": "host_add",
                "args": host,
                "approval_id": draft["approval_id"],
                "idempotency_key": "host-add-1",
            },
        )
    multipart = next(call for call in recorder.calls if "files" in (call[2] or {}))
    assert multipart[1].endswith("/counter/9/offline_conversions/upload_calls")
    add = next(call for call in recorder.calls if call[0] == "POST" and call[1].endswith("/user/42/hosts"))
    assert add[2] == {"host_url": "https://example.test"}


async def test_management_approval_is_bound_to_its_account(tmp_path):
    settings = two_accounts(tmp_path, yandex_audience_write_enabled=True)
    recorder = Calls()
    args = {"segment_id": 3}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool(
                "audience_manage_prepare",
                {"operation": "segment_delete", "args": args, "account": "client-a"},
            )
        ).data
        assert draft["account"] == "client-a"
        assert not accept_as(settings, draft, None)
        assert not accept_as(settings, draft, "client-b")
        assert accept_as(settings, draft, "client-a")
        apply = {"operation": "segment_delete", "args": args, "approval_id": draft["approval_id"],
                 "idempotency_key": "segment-delete-1"}  # fmt: skip
        for other in ({}, {"account": "client-b"}):
            with pytest.raises(ToolError):
                await client.call_tool("audience_manage_apply", {**apply, **other})
        assert not [call for call in recorder.calls if call[0] == "DELETE"]
        done = (await client.call_tool("audience_manage_apply", {**apply, "account": "client-a"})).data
        assert done["ledger_scope"] == "account=client-a"


async def test_direct_prepare_write_binds_service_and_method(tmp_path):
    settings = config(tmp_path, yandex_direct_write_enabled=True)
    recorder = Recorder()
    items = [{"Id": 7, "Name": "Example"}]
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool(
                "direct_prepare_write", {"service": "keywords", "method": "update", "items": items}
            )
        ).data
        assert accept(settings, draft) and not recorder.calls
        base = {"items": items, "approval_id": draft["approval_id"], "idempotency_key": "kw-update-1"}
        with pytest.raises(ToolError):
            await client.call_tool("direct_apply_write", {**base, "service": "keywords", "method": "delete"})
        with pytest.raises(ToolError):
            await client.call_tool("direct_apply_write", {**base, "service": "ads", "method": "update"})
        done = (
            await client.call_tool("direct_apply_write", {**base, "service": "keywords", "method": "update"})
        ).data
        assert done["applied"]
        # The legacy campaign path cannot turn an approved update into another method.
        legacy = (await client.call_tool("direct_prepare_changes", {"changes": items})).data
        assert accept(settings, legacy)
        with pytest.raises(ToolError):
            await client.call_tool(
                "direct_apply_changes",
                {
                    "service": "campaigns",
                    "method": "delete",
                    "campaigns": items,
                    "approval_id": legacy["approval_id"],
                    "idempotency_key": "legacy-delete",
                },
            )
    posts = [call for call in recorder.calls if call[0] == "POST"]
    assert [call[2]["method"] for call in posts] == ["update", "get"]
    assert posts[0][1].endswith("/keywords") and posts[0][2]["params"] == {"Keywords": items}


def test_direct_parity_writes_use_documented_payload_keys():
    from zai_yandex.adapters.direct import YandexDirectAdapter

    params = YandexDirectAdapter._apply_params
    assert params("audiencetargets", "setBids", [{"Id": 1}]) == {"Bids": [{"Id": 1}]}
    assert params("sitelinks", "add", [{"Sitelinks": []}]) == {"SitelinksSets": [{"Sitelinks": []}]}
    assert params("dynamictextadtargets", "add", [{"AdGroupId": 1}]) == {"Webpages": [{"AdGroupId": 1}]}
    assert params("adimages", "delete", [{"AdImageHash": "h"}]) == {
        "SelectionCriteria": {"AdImageHashes": ["h"]}
    }
    assert params("bidmodifiers", "delete", [{"Id": 4}]) == {"SelectionCriteria": {"Ids": [4]}}
    with pytest.raises(ProviderError):
        YandexDirectAdapter.validate_apply("adimages", "delete", [{"Id": 1}])
    with pytest.raises(ProviderError):
        YandexDirectAdapter.validate_apply("agencyclients", "add", [{"Login": "x"}])


def test_zero_config_defaults(tmp_path, monkeypatch):
    env = {"APPDATA": str(tmp_path / "roaming")}
    assert user_dir(env, platform="win32") == tmp_path / "roaming" / "zai-yandex-mcp"
    assert user_dir({"XDG_CONFIG_HOME": str(tmp_path)}, platform="linux") == tmp_path / "zai-yandex-mcp"
    home = {"YANDEX_MCP_HOME": str(tmp_path / "home")}
    assert default_config_file(dict(home)) is None
    applied = apply_defaults(home)
    assert Path(applied["YANDEX_STATE_PATH"]) == tmp_path / "home" / "state" / "yandex.sqlite"
    assert "YANDEX_METRIKA_TOKEN_FILE" not in applied
    token = tmp_path / "home" / "state" / "secrets" / "metrika.token"
    token.parent.mkdir(parents=True)
    token.write_text("synthetic", encoding="utf-8")
    again = {"YANDEX_MCP_HOME": str(tmp_path / "home")}
    assert apply_defaults(again)["YANDEX_METRIKA_TOKEN_FILE"] == str(token)
    explicit = {**again, "YANDEX_METRIKA_TOKEN_FILE": "/explicit"}
    assert "YANDEX_METRIKA_TOKEN_FILE" not in apply_defaults(explicit)
    (tmp_path / "home" / "mcp.local.json").write_text(json.dumps({"env": {}}), encoding="utf-8")
    assert default_config_file(dict(home)) == str(tmp_path / "home" / "mcp.local.json")


def test_cli_starts_without_config_and_approvals_cli_checks_account(tmp_path):
    env = {
        "YANDEX_MCP_HOME": str(tmp_path / "home"),
        "YANDEX_DISABLE_UPDATE_CHECK": "1",
        "PATH": "",
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    result = subprocess.run(
        [sys.executable, "-m", "zai_yandex", "--check-config"],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert "configuration error" not in result.stderr, result.stderr
    assert json.loads(result.stdout)["checks"]["configuration"] is True
    assert (tmp_path / "home" / "state").is_dir() or result.returncode in (0, 2)


async def test_uncertain_write_is_reconciled_never_resent(tmp_path):
    from zai_yandex.transport import ProviderTimeoutError

    settings = config(tmp_path, yandex_metrika_write_enabled=True)

    def handle(method, url, payload, params):
        if method == "DELETE":
            raise ProviderTimeoutError("synthetic timeout")
        return {"filters": []}

    recorder = Recorder(handle)
    args = {"counter_id": 9, "id": 4}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool("metrika_manage_prepare", {"operation": "filter_delete", "args": args})
        ).data
        assert accept(settings, draft)
        apply = {"operation": "filter_delete", "args": args, "approval_id": draft["approval_id"],
                 "idempotency_key": "filter-delete-1"}  # fmt: skip
        with pytest.raises(ToolError):
            await client.call_tool("metrika_manage_apply", apply)
        with pytest.raises(ToolError):  # pending write: no second dispatch under the same key
            await client.call_tool("metrika_manage_apply", apply)
        rec = {"operation": "filter_delete", "args": args, "idempotency_key": "filter-delete-1"}
        settled = (await client.call_tool("metrika_manage_reconcile", rec)).data
        assert settled["status"] == "settled_unconfirmed" and settled["observed"]["result"] == {"filters": []}
        assert (await client.call_tool("metrika_manage_reconcile", rec)).data[
            "status"
        ] == "settled_unconfirmed"
    assert len([call for call in recorder.calls if call[0] == "DELETE"]) == 1


def test_approvals_cli_requires_the_prepared_account(tmp_path):
    import asyncio
    import os

    from zai_yandex.config import ACTIVE_ACCOUNT
    from zai_yandex.management import open_approval

    settings = two_accounts(tmp_path)
    store = StateStore(settings)

    class Runtime:
        def __init__(self):
            self.store, self.settings = store, settings

        def current_access(self):
            return type("A", (), {"principal_id": settings.principal_id})()

    token = ACTIVE_ACCOUNT.set("client-a")
    try:
        draft = asyncio.run(open_approval(Runtime(), "yandex_metrika", "manage:label_delete", "h" * 64))
    finally:
        ACTIVE_ACCOUNT.reset(token)
    accounts = next(tmp_path.glob("*.json"))

    def run(*extra):
        command = [
            *(sys.executable, "-m", "zai_yandex.approvals", "--state-path", str(settings.state_path)),
            *("--account", settings.account_id, "--approval-id", draft["approval_id"]),
            *("--principal", settings.principal_id, "--request-hash", "h" * 64, "--accept", *extra),
        ]
        env = {**os.environ, "YANDEX_ACCOUNTS_FILE": str(accounts)}
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, check=False)

    assert run().returncode == 1
    assert run("--named-account", "client-b").returncode == 1
    done = run("--named-account", "client-a")
    assert done.returncode == 0 and json.loads(done.stdout)["named_account"] == "client-a"


async def test_axisrow_parity_direct_services(tmp_path):
    settings = config(tmp_path, yandex_direct_write_enabled=True)
    recorder = Recorder()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        await client.call_tool("direct_deduplicate_keywords", {"keywords": ["купить слона", "слон купить"]})
        await client.call_tool("direct_list_dynamic_feed_ad_targets", {"campaign_ids": [1]})
        with pytest.raises(ToolError):  # strategies/agency clients are approval-only
            await client.call_tool(
                "direct_write", {"service": "strategies", "method": "archive", "items": [{"Id": 3}]}
            )
        draft = (
            await client.call_tool(
                "direct_prepare_write", {"service": "strategies", "method": "archive", "items": [{"Id": 3}]}
            )
        ).data
        assert accept(settings, draft)
        await client.call_tool(
            "direct_apply_write",
            {
                "service": "strategies",
                "method": "archive",
                "items": [{"Id": 3}],
                "approval_id": draft["approval_id"],
                "idempotency_key": "strategy-archive-1",
            },
        )
    urls = [call[1] for call in recorder.calls]
    assert urls[0].endswith("/json/v5/keywordsresearch") and recorder.calls[0][2]["method"] == "deduplicate"
    assert urls[1].endswith("/dynamicfeedadtargets")
    assert urls[2].endswith("/json/v501/strategies") and recorder.calls[2][2]["method"] == "archive"
