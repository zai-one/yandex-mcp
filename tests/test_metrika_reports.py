from __future__ import annotations

import csv
import io
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import Recorder, config, connection, token
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex import metrika_reports as reports
from zai_yandex.adapters.metrika import YandexMetrikaAdapter
from zai_yandex.oauth_help import authorization_url, main
from zai_yandex.server import create_server

ARGS = dict(
    counter_id="7",
    date1="2026-08-01",
    date2="2026-08-31",
    metrics=["ym:s:visits"],
    dimensions=["ym:s:trafficSource"],
    page_size=2,
    max_pages=2,
)


def page(params, total=3, **changes):
    offset, limit = params["offset"], params["limit"]
    return {
        "data": [
            {"dimensions": [{"id": str(i), "name": "Источник " + str(i)}], "metrics": [i]}
            for i in range(offset, min(offset + limit, total + 1))
        ],
        "total_rows": total,
        "total_rows_rounded": False,
        "totals": [10],
        "sampled": False,
        "sample_share": 1.0,
        **changes,
    }


async def call(tmp_path, handler, args=None, **settings):
    recorder = Recorder(handler)
    server = create_server(config(tmp_path, **settings), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        result = (await client.call_tool("metrika_report", args or ARGS)).data
    return result, recorder


async def test_pages_totals_sources_sampling_and_per_page_admission(tmp_path):
    captured = []

    def handler(method, url, body, params):
        captured.append(params)
        return page(params, sampled=True, sample_share=0.25)

    result, recorder = await call(
        tmp_path, handler, {**ARGS, "filters": "ym:s:trafficSource=='organic'", "sort": "-ym:s:visits"}
    )
    report = result["periods"]["current"]
    assert report["rows_complete"] and report["row_count"] == 3
    assert report["totals"]["ym:s:visits"] == 10  # never sum the three row metrics
    assert report["metadata"]["sampled"] and report["metadata"]["sample_share"] == 0.25
    assert report["source"]["included_offsets"] == [1, 3]
    assert all(p["sort"] == "-ym:s:visits" and p["filters"] for p in captured)
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        assert db.execute("select count(*) from attempts").fetchone()[0] == len(recorder.calls) == 2


async def test_comparison_uses_provider_totals_and_null_for_zero_baseline(tmp_path):
    def handler(method, url, body, params):
        return page(params, totals=[0 if params["date1"] == "2026-07-01" else 10])

    result, _ = await call(
        tmp_path, handler, {**ARGS, "compare_date1": "2026-07-01", "compare_date2": "2026-07-31"}
    )
    assert result["comparison"]["ym:s:visits"] == dict(before=0, after=10, delta=10, change_percent=None)
    assert reports.delta(None, 10)["delta"] is None
    assert reports.delta(10, 5)["change_percent"] == -50


@pytest.mark.parametrize(
    "changes", [dict(total_rows_rounded=True), dict(total_rows=None), dict(data=[]), dict(total_rows=999)]
)
async def test_incomplete_metadata_never_claims_complete(tmp_path, changes):
    result, _ = await call(tmp_path, lambda m, u, b, p: page(p, **changes))
    assert not result["periods"]["current"]["rows_complete"]


async def test_changed_totals_stop_before_appending_next_page(tmp_path):
    result, _ = await call(tmp_path, lambda m, u, b, p: page(p, totals=[10 if p["offset"] == 1 else 12]))
    report = result["periods"]["current"]
    assert report["row_count"] == 2 and report["stop_reason"] == "report_changed"


async def test_page_limit_and_repeated_page_are_explicit(tmp_path):
    result, _ = await call(tmp_path, lambda m, u, b, p: page(p), {**ARGS, "max_pages": 1})
    assert result["periods"]["current"]["stop_reason"] == "page_limit"
    result, _ = await call(tmp_path, lambda m, u, b, p: page({**p, "offset": 1}))
    assert result["periods"]["current"]["stop_reason"] == "repeated_page"


async def test_csv_redacts_before_escaping_and_keeps_missing_distinct_from_zero(tmp_path):
    secret = 'synthetic"credential'

    def handler(m, u, b, p):
        rows = [
            {"dimensions": [{"id": "1", "name": secret}], "metrics": [None]},
            {"dimensions": [{"id": "2", "name": '=HYPERLINK("fixture")'}], "metrics": [0]},
        ]
        return page(p, total=2, data=rows)

    result, _ = await call(tmp_path, handler, {**ARGS, "output": "csv"}, metrika_token=secret)
    rows = list(csv.DictReader(io.StringIO(result["csv"])))
    assert rows[0]["ym:s:trafficSource:name"] == "***redacted***"
    assert rows[1]["ym:s:trafficSource:name"].startswith("'=")
    assert rows[0]["ym:s:visits"] == "" and rows[1]["ym:s:visits"] == "0"
    assert secret not in json.dumps(result)
    assert "rows" not in result["periods"]["current"]


@pytest.mark.parametrize(
    "changes",
    [
        dict(counter_id="bad"),
        dict(counter_id="7,8"),
        dict(metrics=[]),
        dict(sort="-unrequested"),
        dict(sort="--ym:s:visits"),
        dict(filters=["bad"]),
        dict(filters="x\n"),
        dict(filters="x" * 10001),
        dict(max_pages=4),
        dict(page_size=0),
        dict(compare_date1="invalid", compare_date2="2026-07-31"),
        dict(compare_date1="2026-07-01"),
        dict(date2="2027-12-31"),
        dict(output="xml"),
    ],
)
async def test_all_periods_validated_before_http(tmp_path, changes):
    recorder = Recorder(lambda *a: pytest.fail("must not reach HTTP"))
    async with Client(
        create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    ) as client:
        with pytest.raises(ToolError):
            await client.call_tool("metrika_report", {**ARGS, **changes})
    assert not recorder.calls


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": []},
        {"data": ["bad"], "totals": [1]},
        {"data": [{"dimensions": [], "metrics": [1]}], "totals": [1]},
        {"data": [], "totals": [float("nan")]},
    ],
)
async def test_malformed_response_fails_closed(tmp_path, response):
    with pytest.raises(ToolError):
        await call(tmp_path, lambda *args: response)


async def test_account_and_scope_boundary_before_network(tmp_path, pair):
    recorder = Recorder(lambda *a: pytest.fail("no network"))
    server = create_server(config(tmp_path, public_key=pair.public_key), http_factory=recorder.factory)
    for bearer in [token(pair, account="foreign"), token(pair, scopes=["yandex_direct:read"])]:
        async with connection(server, bearer) as client:
            with pytest.raises(ToolError):
                await client.call_tool("metrika_report", ARGS)
    assert not recorder.calls


def test_oauth_url_uses_only_official_origin_and_public_client_id(capsys):
    identifier = "a" * 32
    url = urlsplit(authorization_url(identifier))
    assert url.scheme == "https" and url.netloc == "oauth.yandex.ru"
    assert parse_qs(url.query) == {"response_type": ["token"], "client_id": [identifier]}
    main(["--client-id", identifier])
    assert "yandex-mcp-setup" in capsys.readouterr().out
    for value in ["https://evil.example", "x&redirect_uri=bad", "", "a" * 33]:
        with pytest.raises(ValueError):
            authorization_url(value)


def test_existing_statistics_accepts_filters_without_changing_payload():
    params = {
        "ids": "7",
        "date1": "2026-08-01",
        "date2": "2026-08-02",
        "metrics": "ym:s:visits",
        "filters": "ym:s:trafficSource=='organic'",
        "sort": "-ym:s:visits",
    }
    assert YandexMetrikaAdapter.validate_statistics(params) == params


async def test_partial_page_overlap_never_claims_complete(tmp_path):
    def handler(m, u, b, p):
        data = page(p, total=4)
        if p["offset"] == 3:
            data["data"][0]["dimensions"][0] = {"id": "2", "name": "renamed"}
        return data

    result, _ = await call(tmp_path, handler)
    report = result["periods"]["current"]
    assert not report["rows_complete"]
    assert report["row_count"] == 2 and report["next_offset"] == 3
    assert report["stop_reason"] == "overlapping_dimension_groups"


@pytest.mark.parametrize("before,after", [(1e-308, 10.0), (-1e308, 1e308), (-(10**308), 10**308)])
def test_numeric_overflow_returns_explicit_null_instead_of_breaking_report(before, after):
    result = reports.delta(before, after)
    assert result["calculation_status"] == "numeric_overflow"
    assert result["change_percent"] is None or result["change_percent"] == 200
    json.dumps(result, allow_nan=False)
