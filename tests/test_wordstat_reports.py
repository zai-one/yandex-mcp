import json
import sqlite3

import pytest
from conftest import Recorder, config, connection, token
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.server import create_server
from zai_yandex.wordstat import request_body

DYNAMICS = dict(
    phrase="купить велосипед",
    from_date="2026-01-01",
    to_date="2026-02-01",
    period="PERIOD_MONTHLY",
    regions=["213"],
)


@pytest.mark.parametrize(
    "operation,args,field",
    [
        ("dynamics", DYNAMICS, "results"),
        (
            "regions",
            {"phrase": "велосипед", "region": "REGION_CITIES", "devices": ["DEVICE_PHONE"]},
            "results",
        ),
        ("getRegionsTree", {}, "regions"),
    ],
)
async def test_reports_execute_cloud_request_with_provenance_and_admission(tmp_path, operation, args, field):
    recorder = Recorder(lambda *_: {field: [{"count": "123", "label": "fixture"}]})
    settings = config(
        tmp_path,
        yandex_wordstat_live_enabled=True,
        yandex_wordstat_cost_per_call=1,
        yandex_wordstat_max_cost_per_call=1,
        principal_monthly_cost_limit=10,
        account_monthly_cost_limit=10,
    )
    names = {
        "dynamics": "yandex_wordstat_dynamics",
        "regions": "yandex_wordstat_regions",
        "getRegionsTree": "yandex_wordstat_regions_tree",
    }
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        result = (await client.call_tool(names[operation], args)).data
    assert len(recorder.calls) == 1
    method, url, body, _, _ = recorder.calls[0]
    assert method == "POST" and url == "https://searchapi.api.cloud.yandex.net/v2/wordstat/" + operation
    assert body == {**request_body(operation, args), "folderId": "synthetic-folder"}
    assert result["normalized"][field][0]["count"] == "123"
    assert result["provenance"]["operation"] == operation
    assert "synthetic-yandex-credential" not in json.dumps(result)
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"from_date": "2026-02-30"},
        {"to_date": "2025-01-01"},
        {"period": "invalid"},
        {"phrase": " "},
        {"devices": ["DEVICE_ALL", "DEVICE_PHONE"]},
        {"regions": ["../"]},
    ],
)
async def test_invalid_input_never_dispatches(tmp_path, change):
    recorder = Recorder()
    settings = config(
        tmp_path,
        yandex_wordstat_live_enabled=True,
        yandex_wordstat_cost_per_call=1,
        yandex_wordstat_max_cost_per_call=1,
        principal_monthly_cost_limit=10,
        account_monthly_cost_limit=10,
    )
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("yandex_wordstat_dynamics", {**DYNAMICS, **change})
    assert not recorder.calls


async def test_new_reports_preserve_budget_and_scope_denials(tmp_path, pair):
    recorder = Recorder()
    settings = config(tmp_path, public_key=pair.public_key)
    server = create_server(settings, transport="http", http_factory=recorder.factory)
    async with connection(server, token(pair, scopes=["yandex_search:read"])) as client:
        with pytest.raises(ToolError):
            await client.call_tool("yandex_wordstat_dynamics", DYNAMICS)
    async with connection(server, token(pair)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("yandex_wordstat_dynamics", DYNAMICS)
    assert not recorder.calls
