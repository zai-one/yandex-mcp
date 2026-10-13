from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from conftest import SECRET, Recorder, config, connection, token
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.adapters.audience import create_digest
from zai_yandex.server import create_server
from zai_yandex.transport import ProviderHttpError, ProviderTransportError

POINTS = [{"latitude": 55.75, "longitude": 37.61, "description": "fixture"}]
CIRCLE_PAYLOAD = {
    "name": "Fixture circle",
    "radius": 500,
    "points": POINTS,
    "geo_segment_type": "last",
}
POLYGON_POINTS = [
    {"latitude": 55.75, "longitude": 37.61},
    {"latitude": 55.76, "longitude": 37.61},
    {"latitude": 55.76, "longitude": 37.62},
    {"latitude": 55.75, "longitude": 37.61},
]


def _state_files_bytes(path) -> bytes:
    return b"".join(
        candidate.read_bytes()
        for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"))
        if candidate.exists()
    )


async def _validated(client: Client, operation: str, payload: dict) -> dict:
    return (
        await client.call_tool(
            "audience_validate_create", {"operation": operation, "payload": payload}
        )
    ).data


async def test_audience_reads_use_documented_lists_and_filter_exact_ids(tmp_path):
    def handler(method, url, payload, params):
        assert method == "GET" and payload is None and params is None
        if url.endswith("/segments"):
            return {"segments": [{"id": 41, "name": "one"}, {"id": 42, "name": "two"}]}
        if url.endswith("/pixels"):
            return {"pixels": [{"id": 7, "name": "pixel"}]}
        raise AssertionError(url)

    recorder = Recorder(handler)
    async with Client(
        create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    ) as client:
        segment = (await client.call_tool("audience_get_segment", {"segment_id": 42})).data
        pixel = (await client.call_tool("audience_get_pixel", {"pixel_id": 7})).data
        assert segment["segment"]["name"] == "two"
        assert pixel["pixel"]["name"] == "pixel"
    assert [call[1].rsplit("/", 1)[-1] for call in recorder.calls] == ["segments", "pixels"]


@pytest.mark.parametrize(
    "provider_status,ready,result_status",
    [
        ("is_processed", False, "applied"),
        ("processed", True, "applied"),
        ("processing_failed", False, "failed"),
    ],
)
async def test_circle_create_checkpoints_and_exact_readback_preserves_status(
    tmp_path, provider_status, ready, result_status
):
    segment = {"id": 42, **CIRCLE_PAYLOAD, "status": provider_status, "note": SECRET}

    def handler(method, url, payload, params):
        if method == "POST":
            assert url.endswith("/segments/create_geo")
            assert payload == {"segment": CIRCLE_PAYLOAD}
            return {"segment": segment}
        assert method == "GET" and url.endswith("/segments")
        return {"segments": [segment]}

    recorder = Recorder(handler)
    settings = config(tmp_path, yandex_audience_write_enabled=True)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        prepared = await _validated(client, "geo_circle", CIRCLE_PAYLOAD)
        assert prepared["api_request_payload"] == {"segment": CIRCLE_PAYLOAD}
        assert prepared["api_request_payload_sha256"] != prepared["confirmation_hash"]
        result = (
            await client.call_tool(
                "audience_create_geo_circle",
                {
                    **CIRCLE_PAYLOAD,
                    "confirmation_hash": prepared["confirmation_hash"],
                    "idempotency_key": f"circle-{provider_status}",
                },
            )
        ).data
        assert result["status"] == result_status and result["ready"] is ready
        assert result["provider_status"] == provider_status and result["readback_fresh"] is True
        assert SECRET not in json.dumps(result)
        cached = (
            await client.call_tool(
                "audience_create_geo_circle",
                {
                    **CIRCLE_PAYLOAD,
                    "confirmation_hash": prepared["confirmation_hash"],
                    "idempotency_key": f"circle-{provider_status}",
                },
            )
        ).data
        assert cached["readback_fresh"] is False
        with pytest.raises(ToolError):
            await client.call_tool(
                "audience_create_geo_circle",
                {
                    **CIRCLE_PAYLOAD,
                    "confirmation_hash": prepared["confirmation_hash"],
                    "idempotency_key": f"different-{provider_status}",
                },
            )
    assert [call[0] for call in recorder.calls] == ["POST", "GET"]
    assert SECRET.encode() not in _state_files_bytes(settings.state_path)
    with sqlite3.connect(settings.state_path) as db:
        stored = db.execute("SELECT result FROM provider_writes").fetchone()[0]
    assert SECRET not in stored


async def test_all_create_shapes_use_exact_paths_and_wrapped_payloads(tmp_path):
    cases = [
        (
            "geo_polygon",
            "audience_create_geo_polygon",
            {
                "name": "polygon",
                "polygons": [{"points": POLYGON_POINTS}],
                "geo_segment_type": "regular",
            },
            "/segments/create_geo_polygon",
            "segment",
            51,
        ),
        ("pixel", "audience_create_pixel", {"name": "pixel"}, "/pixels", "pixel", 52),
        (
            "pixel_viewers",
            "audience_create_pixel_viewers",
            {
                "name": "viewers",
                "pixel_id": 52,
                "period_length": 90,
                "times_quantity_operation": "gt",
                "times_quantity": 0,
            },
            "/segments/create_pixel",
            "segment",
            53,
        ),
    ]
    current = {}

    def handler(method, url, payload, params):
        operation, _tool, expected, path, resource, object_id = current["case"]
        if method == "POST":
            assert url.endswith(path) and payload == {resource: expected}
            item = {"id": object_id, **expected}
            if resource == "segment":
                item["status"] = "is_processed"
            current["item"] = item
            return {resource: item}
        collection = "segments" if resource == "segment" else "pixels"
        assert method == "GET" and url.endswith("/" + collection)
        return {collection: [current["item"]]}

    recorder = Recorder(handler)
    settings = config(tmp_path, yandex_audience_write_enabled=True)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        for index, case in enumerate(cases):
            current["case"] = case
            operation, tool, payload, _path, _resource, _object_id = case
            prepared = await _validated(client, operation, payload)
            result = (
                await client.call_tool(
                    tool,
                    {
                        **payload,
                        "confirmation_hash": prepared["confirmation_hash"],
                        "idempotency_key": f"create-shape-{index}",
                    },
                )
            ).data
            assert result["readback_match"] is True
            if operation == "pixel":
                assert result["ready"] is None
    assert [call[0] for call in recorder.calls] == ["POST", "GET"] * 3


async def test_disabled_and_invalid_polygon_fail_before_http_with_static_reason(tmp_path):
    recorder = Recorder()
    async with Client(
        create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    ) as client:
        prepared = await _validated(client, "geo_circle", CIRCLE_PAYLOAD)
        with pytest.raises(ToolError):
            await client.call_tool(
                "audience_create_geo_circle",
                {
                    **CIRCLE_PAYLOAD,
                    "confirmation_hash": prepared["confirmation_hash"],
                    "idempotency_key": "disabled-circle",
                },
            )
    settings = config(tmp_path / "other", yandex_audience_write_enabled=True)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        with pytest.raises(ToolError, match="audience_polygon_last_unsupported"):
            await client.call_tool(
                "audience_create_geo_polygon",
                {
                    "name": "bad",
                    "polygons": [{"points": POLYGON_POINTS}],
                    "geo_segment_type": "last",
                    "confirmation_hash": "x",
                    "idempotency_key": "invalid-polygon",
                },
            )
    assert recorder.calls == []


async def test_unknown_create_is_pending_across_restart_and_never_reposted(tmp_path):
    def fail(method, url, payload, params):
        raise ProviderTransportError(SECRET)

    recorder = Recorder(fail)
    settings = config(tmp_path, yandex_audience_write_enabled=True)
    first = None
    for _ in range(2):
        async with Client(
            create_server(settings, transport="stdio", http_factory=recorder.factory)
        ) as client:
            prepared = await _validated(client, "pixel", {"name": "unknown"})
            first = (
                await client.call_tool(
                    "audience_create_pixel",
                    {
                        "name": "unknown",
                        "confirmation_hash": prepared["confirmation_hash"],
                        "idempotency_key": "unknown-pixel",
                    },
                )
            ).data
            assert first["status"] == "pending_unknown"
            assert first["retryable"] is False and first["reconciliation_required"] is True
            assert SECRET not in json.dumps(first)
    assert len(recorder.calls) == 1
    assert SECRET.encode() not in _state_files_bytes(settings.state_path)


async def test_unknown_create_digest_is_account_wide_across_principals_and_restart(tmp_path, pair):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def fail(method, url, payload, params):
        calls.append(method)
        entered.set()
        await release.wait()
        raise ProviderTransportError("synthetic unknown")

    settings = config(
        tmp_path,
        public_key=pair.public_key,
        yandex_audience_write_enabled=True,
    )
    server = create_server(settings, http_factory=Recorder(fail).factory)
    confirmation, _ = create_digest("pixel", {"name": "shared account payload"})

    async def create(actor, key):
        async with connection(server, token(pair, actor=actor)) as client:
            return (
                await client.call_tool(
                    "audience_create_pixel",
                    {
                        "name": "shared account payload",
                        "confirmation_hash": confirmation,
                        "idempotency_key": key,
                    },
                )
            ).data

    async def reject(actor, key):
        async with connection(server, token(pair, actor=actor)) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "audience_create_pixel",
                    {
                        "name": "shared account payload",
                        "confirmation_hash": confirmation,
                        "idempotency_key": key,
                    },
                )

    first = asyncio.create_task(create("alice", "shared-alice"))
    await entered.wait()
    await reject("bob", "shared-bob")
    release.set()
    assert (await first)["status"] == "pending_unknown"
    restarted = create_server(settings, http_factory=Recorder(fail).factory)
    async with connection(restarted, token(pair, actor="bob")) as client:
        with pytest.raises(ToolError):
            await client.call_tool(
                "audience_create_pixel",
                {
                    "name": "shared account payload",
                    "confirmation_hash": confirmation,
                    "idempotency_key": "shared-bob-restart",
                },
            )
    assert calls == ["POST"]


async def test_readback_mismatch_stays_pending_and_reconcile_only_reads(tmp_path):
    receipt = {"id": 61, "name": "wanted"}

    def handler(method, url, payload, params):
        if method == "POST":
            return {"pixel": receipt}
        return {"pixels": [{"id": 61, "name": "different"}]}

    recorder = Recorder(handler)
    settings = config(tmp_path, yandex_audience_write_enabled=True)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        prepared = await _validated(client, "pixel", {"name": "wanted"})
        args = {
            "name": "wanted",
            "confirmation_hash": prepared["confirmation_hash"],
            "idempotency_key": "mismatch-pixel",
        }
        result = (await client.call_tool("audience_create_pixel", args)).data
        assert result["status"] == "pending_reconciliation"
        assert result["readback_match"] is False
        again = (await client.call_tool("audience_create_pixel", args)).data
        assert again["status"] == "pending_reconciliation"
    assert [call[0] for call in recorder.calls] == ["POST", "GET", "GET"]
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT status FROM provider_writes").fetchone()[0] == "pending"


async def test_http_scope_account_and_explicit_400_are_fail_closed(tmp_path, pair):
    calls = []

    def fail(method, url, payload, params):
        calls.append(method)
        raise ProviderHttpError(400, body_preview=SECRET)

    settings = config(
        tmp_path,
        public_key=pair.public_key,
        yandex_audience_write_enabled=True,
    )
    server = create_server(settings, http_factory=Recorder(fail).factory)
    async with connection(server, token(pair, scopes=["yandex_audience:read"])) as client:
        assert "audience_create_pixel" not in {tool.name for tool in await client.list_tools()}
        with pytest.raises(ToolError):
            await client.call_tool(
                "audience_create_pixel",
                {"name": "x", "confirmation_hash": "x", "idempotency_key": "blocked-scope"},
            )
    async with connection(server, token(pair, account="other")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("audience_list_pixels", {})
    async with connection(server, token(pair)) as client:
        prepared = await _validated(client, "pixel", {"name": "bad request"})
        with pytest.raises(ToolError) as caught:
            await client.call_tool(
                "audience_create_pixel",
                {
                    "name": "bad request",
                    "confirmation_hash": prepared["confirmation_hash"],
                    "idempotency_key": "explicit-400",
                },
            )
        assert SECRET not in str(caught.value)
    assert calls == ["POST"]
    assert SECRET.encode() not in _state_files_bytes(settings.state_path)
    with sqlite3.connect(settings.state_path) as db:
        stored = db.execute("SELECT result FROM provider_writes").fetchone()[0]
    assert SECRET not in stored
