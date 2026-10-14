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
    from zai_yandex.adapters.direct import APPROVAL_ONLY_WRITES

    keys = {
        "dynamictextadtargets": "Webpages",
        "smartadtargets": "SmartAdTargets",
        "dynamicfeedadtargets": "DynamicFeedAdTargets",
        "vcards": "VCards",
    }
    for legacy, key in keys.items():
        # Restored, but only through the approval-gated prepare/apply path.
        assert legacy in APPROVAL_ONLY_WRITES
        assert params(legacy, "add", [{"AdGroupId": 1}]) == {key: [{"AdGroupId": 1}]}
    assert params("smartadtargets", "setBids", [{"Id": 1}]) == {"Bids": [{"Id": 1}]}
    with pytest.raises(ProviderError):
        YandexDirectAdapter.validate_apply("vcards", "update", [{"Id": 1}])  # vCards has no update
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


async def test_webmaster_feed_and_search_url_paths_match_the_reference(tmp_path):
    # Pinned to https://yandex.ru/dev/webmaster/doc/ru/reference/ (paths and query names):
    # in-search history/samples live under /search-urls/in-search/, the feed is ``url``,
    # and feeds/add/info takes {"requestId": ...} as a JSON body of the GET.
    recorder = Calls()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    reads = [
        ("insearch_history", {"query": {"date_from": "2026-01-01"}}),
        ("insearch_samples", {"query": {"offset": 0, "limit": 10}}),
        ("feeds_problems", {"query": {"url": "https://example.test/feed.yml", "severity": "ERROR"}}),
        ("feeds_rejected_offers", {"query": {"url": "https://example.test/feed.yml"}}),
        ("feeds_status_list", {"query": {"offset": 0, "limit": 20, "status": "SUCCESS"}}),
        ("feeds_summary", {"query": {"type": "REALTY"}}),
        ("feeds_add_info", {"body": {"requestId": "c7fe80c0-36e3-11e6-8b2d-df96aa592c0a"}}),
    ]
    async with Client(server) as client:
        for name, args in reads:
            await client.call_tool(
                "webmaster_manage_read", {"operation": name, "args": {"host_id": HOST, **args}}
            )
        for name, args in (
            ("feeds_problems", {"query": {"feed_url": "https://example.test/feed.yml"}}),
            ("feeds_problems", {}),  # url is mandatory
            ("feeds_add_info", {}),  # requestId body is mandatory
        ):
            with pytest.raises(ToolError):
                await client.call_tool(
                    "webmaster_manage_read", {"operation": name, "args": {"host_id": HOST, **args}}
                )
    sent = [call for call in recorder.calls if "/hosts/" in call[1]]
    tails = [call[1].split("/hosts/", 1)[1].split("/", 1)[1] for call in sent]
    assert tails == [
        "search-urls/in-search/history",
        "search-urls/in-search/samples",
        "feeds/problems",
        "feeds/moderation/rejected-offers",
        "feeds/status/list",
        "feeds/summary",
        "feeds/add/info",
    ]
    host_params = [p for p in recorder.params if p and ("url" in p or "date_from" in p or "offset" in p)]
    assert {"url": "https://example.test/feed.yml", "severity": "ERROR"} in host_params
    assert sent[-1][0] == "GET" and sent[-1][2] == {"requestId": "c7fe80c0-36e3-11e6-8b2d-df96aa592c0a"}


def test_required_queries_and_raw_bodies_fail_closed():
    web = CATALOG["yandex_webmaster"]
    with pytest.raises(ValueError, match="verification_type"):
        validate_args("yandex_webmaster", web["verification_start"], {"host_id": HOST})
    with pytest.raises(ValueError, match="body"):  # DELETE with a raw body still needs the body
        validate_args("yandex_webmaster", web["feeds_batch_remove"], {"host_id": HOST})
    ok = validate_args(
        "yandex_webmaster",
        web["sitemap_recrawl"],
        {"host_id": HOST, "sitemap_id": "abc-1", "query": {"parent_id": "root-1"}},
    )
    assert ok["query"] == {"parent_id": "root-1"}


@pytest.mark.parametrize(
    ("operation", "allowed", "denied"),
    [
        ("stat_bytime", {"group": "day", "timezone": "+03:00", "keys_sort": "desc"}, {"offset": 1}),
        ("stat_drilldown", {"parent_id": '["a"]', "proposed_accuracy": "true"}, {"group": "day"}),
        ("stat_comparison", {"date1_a": "2026-01-01", "filters_b": "x"}, {"date1": "2026-01-01"}),
        ("stat_comparison_drilldown", {"parent_id": "[]", "access_filter_id": 5}, {"top_keys": 3}),
    ],
)
def test_reporting_queries_follow_each_endpoint_reference(operation, allowed, denied):
    op = CATALOG["yandex_metrika"][operation]
    assert op.doc.startswith("https://yandex.ru/dev/metrika/ru/stat/openapi/")
    validate_args("yandex_metrika", op, {"query": {"ids": 9, **allowed}})
    with pytest.raises(ValueError):
        validate_args("yandex_metrika", op, {"query": {"ids": 9, **denied}})


async def test_reconcile_never_settles_a_write_that_is_still_running(tmp_path):
    import asyncio

    settings = config(tmp_path, yandex_webmaster_write_enabled=True)
    gate, seen = asyncio.Event(), asyncio.Event()

    async def handle(method, url, payload, params):
        if url.endswith("/user"):
            return {"user_id": 42}
        if method == "DELETE":
            seen.set()
            await gate.wait()
        return {"ok": True}

    recorder = Recorder(handle)
    args = {"host_id": HOST}
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool("webmaster_manage_prepare", {"operation": "host_delete", "args": args})
        ).data
        assert accept(settings, draft)
        apply = asyncio.create_task(
            client.call_tool(
                "webmaster_manage_apply",
                {
                    "operation": "host_delete",
                    "args": args,
                    "approval_id": draft["approval_id"],
                    "idempotency_key": "host-delete-slow",
                },  # fmt: skip
            )
        )
        await asyncio.wait_for(seen.wait(), 5)
        early = (
            await client.call_tool(
                "webmaster_manage_reconcile",
                {"operation": "host_delete", "args": args, "idempotency_key": "host-delete-slow"},
            )
        ).data
        assert early["status"] == "in_flight"
        gate.set()
        assert (await apply).data["applied"]
        late = (
            await client.call_tool(
                "webmaster_manage_reconcile",
                {"operation": "host_delete", "args": args, "idempotency_key": "host-delete-slow"},
            )
        ).data
        assert late["status"] == "applied"


def test_documented_value_validation_for_webmaster_and_audience():
    web, aud = CATALOG["yandex_webmaster"], CATALOG["yandex_audience"]
    host = {"host_id": HOST}
    bad = [
        (web["verification_start"], {**host, "query": {"verification_type": "WHOIS"}}),
        (web["insearch_samples"], {**host, "query": {"limit": 500}}),
        (web["feeds_status_list"], {**host, "query": {"orderDirection": "UP"}}),
        (web["feeds_summary"], {**host, "query": {"type": "SHOES"}}),
        (web["search_queries_all_history"], {**host, "query": {"date_from": "yesterday"}}),
        (aud["segment_modify_data"], {"segment_id": 1, "csv": "x", "query": {"modification_type": "ADD"}}),
        (aud["segment_modify_data"], {"segment_id": 1, "csv": "x"}),  # modification_type is required
    ]
    for op, args in bad:
        with pytest.raises(ValueError):
            validate_args("yandex_webmaster", op, args)
    ok = validate_args(
        "yandex_webmaster",
        web["search_queries_all_history"],
        {**host, "query": {"query_indicator": ["TOTAL_SHOWS", "TOTAL_CLICKS"], "date_from": "2026-01-01"}},
    )
    assert ok["query"]["query_indicator"] == ["TOTAL_SHOWS", "TOTAL_CLICKS"]
    validate_args(
        "yandex_audience",
        aud["segment_modify_data"],
        {"segment_id": 1, "csv": "x", "query": {"modification_type": "replace", "check_size": "false"}},
    )


def test_every_operation_links_its_own_reference_page():
    docs = [op.doc for ops in CATALOG.values() for op in ops.values()]
    assert not [doc for doc in docs if doc.endswith("/")], "section links instead of method pages"
    metrika = CATALOG["yandex_metrika"]
    assert metrika["goal_create"].path == "/management/v1/counter/{counter_id}/goals"
    assert metrika["goal_update"].method == "PUT" and metrika["goal_delete"].method == "DELETE"
    assert metrika["stat_pivot"].path == "/stat/v1/data/pivot"
    assert {"pivot_dimensions", "pivot_limit", "pivot_offset"} <= metrika["stat_pivot"].query


def test_feed_credentials_are_masked():
    from zai_yandex.management import mask_credentials

    value = {"feeds": [{"url": "https://yandex:WE9h4FfBml@example.com/feed.yml"}], "n": 1}
    assert mask_credentials(value) == {"feeds": [{"url": "https://***@example.com/feed.yml"}], "n": 1}
    assert mask_credentials("https://example.com/a@b") == "https://example.com/a@b"


async def test_feed_credentials_never_reach_preview_or_ledger(tmp_path):
    settings = config(tmp_path, yandex_webmaster_write_enabled=True)
    secret = "WE9h4FfBml"

    def handle(method, url, payload, params):
        if url.endswith("/user"):
            return {"user_id": 42}
        return {"feeds": [{"url": f"https://yandex:{secret}@example.com/feed.yml"}]}

    recorder = Recorder(handle)
    args = {"host_id": HOST, "body": {"feeds": [{"url": f"https://yandex:{secret}@example.com/f.yml",
                                                 "type": "REALTY", "regionIds": [225]}]}}  # fmt: skip
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft = (
            await client.call_tool("webmaster_manage_prepare", {"operation": "feeds_batch_add", "args": args})
        ).data
        assert secret not in json.dumps(draft) and accept(settings, draft)
        done = (
            await client.call_tool(
                "webmaster_manage_apply",
                {
                    "operation": "feeds_batch_add",
                    "args": args,
                    "approval_id": draft["approval_id"],
                    "idempotency_key": "feeds-add-1",
                },  # fmt: skip
            )
        ).data
        assert secret not in json.dumps(done, default=str)
    sent = [call for call in recorder.calls if call[1].endswith("/feeds/batch/add")]
    assert secret in json.dumps(sent[0][2])  # the provider still receives the real URL
    with StateStore(settings).connect() as db:
        rows = [tuple(row) for row in db.execute("SELECT * FROM provider_writes")]
    assert rows and secret not in json.dumps(rows, default=str)


async def test_offline_conversions_and_audience_uploads(tmp_path):
    settings = config(tmp_path, yandex_metrika_write_enabled=True, yandex_audience_write_enabled=True)
    files = []

    class Multi(Calls):
        def handle(self, method, url, payload, params):
            if isinstance(payload, dict) and "files" in payload:
                files.append((url, payload["files"]["file"][0], payload["files"]["file"][2], params))
            return super().handle(method, url, payload, params)

    recorder = Multi()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        for provider, operation, args in (
            ("metrika", "offline_conversions_upload",
             {"counter_id": 9, "csv": "ClientId,Target,DateTime\n1,t,1\n",
              "query": {"type": "CALLS", "client_id_type": "CLIENT_ID"}}),
            ("audience", "segment_upload_file", {"csv": "a@b.c\nd@e.f\n"}),
            ("audience", "pixel_create", {"body": {"name": "p"}}),
            ("audience", "segment_create_geo",
             {"body": {"name": "g", "radius": 500, "points": [{"latitude": 55.7, "longitude": 37.6}],
                       "geo_segment_type": "REGULAR"}}),
        ):  # fmt: skip
            draft = (
                await client.call_tool(f"{provider}_manage_prepare", {"operation": operation, "args": args})
            ).data
            assert accept(settings, draft)
            await client.call_tool(
                f"{provider}_manage_apply",
                {
                    "operation": operation,
                    "args": args,
                    "approval_id": draft["approval_id"],
                    "idempotency_key": f"{operation}-1",
                },  # fmt: skip
            )
        with pytest.raises(ToolError):
            await client.call_tool(
                "metrika_manage_prepare",
                {"operation": "offline_conversions_upload",
                 "args": {"counter_id": 9, "csv": "x", "query": {"type": "SALES"}}},
            )  # fmt: skip
    posts = [call for call in recorder.calls if call[0] == "POST"]
    urls = [call[1] for call in posts]
    assert urls[0].endswith("/counter/9/offline_conversions/upload")
    assert urls[1].endswith("/v1/management/segments/upload_file")
    assert urls[2].endswith("/v1/management/pixels") and posts[2][2] == {"pixel": {"name": "p"}}
    assert urls[3].endswith("/segments/create_geo") and "segment" in posts[3][2]
    assert files[0][3] == {"type": "CALLS", "client_id_type": "CLIENT_ID"}
    assert files[1][1:3] == ("data.txt", "text/plain")  # a data file, not CSV


async def _governed(client, settings, service, method, items, key):
    draft = (
        await client.call_tool("direct_prepare_write", {"service": service, "method": method, "items": items})
    ).data
    assert accept(settings, draft)
    return draft, (
        await client.call_tool(
            "direct_apply_write",
            {
                "service": service,
                "method": method,
                "items": items,
                "approval_id": draft["approval_id"],
                "idempotency_key": key,
            },
        )  # fmt: skip
    ).data


async def test_direct_unified_clients_and_readbacks(tmp_path):
    from zai_yandex.adapters.direct import YandexDirectAdapter

    settings = config(tmp_path, yandex_direct_write_enabled=True)

    def handle(method, url, payload, params):
        if payload.get("method") == "add":
            return {"result": {"AddResults": [{"Id": 31}]}}
        return {"result": {"ok": True}}

    recorder = Recorder(handle)
    unified = [{"Name": "UC", "StartDate": "2026-11-01", "UnifiedCampaign": {"BiddingStrategy": {}}}]
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        draft, done = await _governed(client, settings, "campaigns", "add", unified, "uc-add-1")
        assert draft["endpoint"] == "v501" and done["readback"]["confirmed"]
        await _governed(client, settings, "clients", "update", [{"Phone": "+70000000000"}], "client-upd-1")
        await _governed(
            client, settings, "bidmodifiers", "add", [{"CampaignId": 5, "MobileAdjustment": {}}], "bm-add-1"
        )
        with pytest.raises(ToolError):  # unified writes are approval-only
            await client.call_tool(
                "direct_write",
                {"service": "campaigns", "method": "add", "items": unified, "idempotency_key": "auto-uc-1"},
            )
    posts = [(call[1], call[2]) for call in recorder.calls if call[0] == "POST"]
    assert posts[0][0].endswith("/json/v501/campaigns") and posts[0][1]["method"] == "add"
    assert posts[1][0].endswith("/json/v501/campaigns") and posts[1][1]["method"] == "get"
    assert posts[2][0].endswith("/json/v5/clients") and posts[2][1]["params"] == {
        "Clients": [{"Phone": "+70000000000"}]
    }
    assert posts[3][0].endswith("/json/v5/clients") and posts[3][1]["method"] == "get"
    bm_get = posts[5][1]["params"]
    assert bm_get["SelectionCriteria"] == {"Ids": [31], "Levels": ["CAMPAIGN", "AD_GROUP"]}
    with pytest.raises(ProviderError):  # mixed unified / text items
        YandexDirectAdapter.validate_apply("adgroups", "add", [{"UnifiedAdGroup": {}}, {"TextAdGroup": {}}])
    with pytest.raises(ProviderError):  # agency clients are addressed by ClientId
        YandexDirectAdapter.validate_apply("agencyclients", "update", [{"Id": 1}])
    YandexDirectAdapter.validate_apply("agencyclients", "update", [{"ClientId": 1, "Phone": "+7"}])
    with pytest.raises(ProviderError):
        YandexDirectAdapter.validate_apply("clients", "update", [{"Phone": "1"}, {"Phone": "2"}])


def test_wordstat_matches_the_v2_proto():
    # yandex-cloud/cloudapi yandex/cloud/searchapi/v2/wordstat_service.proto
    from zai_yandex.wordstat import request_body, validate_phrase, validate_regions

    body = request_body("dynamics", {"phrase": "x", "from_date": "2026-01-01", "period": "PERIOD_WEEKLY"})
    assert "toDate" not in body and body["fromDate"] == "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        validate_phrase("x" * 401)
    with pytest.raises(ValueError):
        validate_regions([str(i) for i in range(101)])
    with pytest.raises(ValueError):
        validate_regions(["moscow"])


def test_direct_member_invite_and_geo_regions_follow_the_docs():
    from zai_yandex.adapters.direct import APPROVAL_ONLY_WRITES, YandexDirectAdapter

    invite = {"PassportOrganizationLogin": "org", "Role": "USER", "SendInviteTo": {"Email": "a@b.ru"}}
    service, method, items = YandexDirectAdapter.validate_apply(
        "agencyclients", "addPassportOrganizationMember", [invite]
    )
    assert YandexDirectAdapter._apply_params(service, method, items) == invite
    assert "agencyclients" in APPROVAL_ONLY_WRITES
    assert YandexDirectAdapter.risk_tier(service, method) == "high"
    for bad in (
        [invite, invite],
        [{**invite, "Role": "OWNER"}],
        [{**invite, "SendInviteTo": {"Email": "a@b.ru", "Phone": "7999"}}],
        [{**invite, "SendInviteTo": {"Phone": "+7 999"}}],
        [{**invite, "Extra": 1}],
    ):
        with pytest.raises(ProviderError):
            YandexDirectAdapter.validate_apply("agencyclients", "addPassportOrganizationMember", bad)
    assert (
        YandexDirectAdapter.validate_read("dictionaries", {"method": "getGeoRegions", "params": {}})[0]
        == "dictionaries"
    )
