from dataclasses import replace
from datetime import date

import pytest
from conftest import Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.adapters.metrika_logs import YandexMetrikaLogsAdapter, parse_tsv, validate_request
from zai_yandex.server import create_server
from zai_yandex.transport import RawHttpResponse

FIELDS = ["ym:s:visitID", "ym:s:dateTime", "ym:s:lastTrafficSource"]
TODAY = date(2026, 10, 13)


def test_validate_request_follows_logs_api_rules():
    params = validate_request("2026-09-01", "2026-09-30", "visits", FIELDS, "LASTSIGN", today=TODAY)
    assert params == {
        "date1": "2026-09-01",
        "date2": "2026-09-30",
        "source": "visits",
        "fields": ",".join(FIELDS),
        "attribution": "LASTSIGN",
    }
    for args in (
        ("2026-09-01", "2026-10-13", "visits", FIELDS),  # date2 cannot be today
        ("2026-09-02", "2026-09-01", "visits", FIELDS),
        ("2026-09-01", "2026-09-30", "clicks", FIELDS),
        ("2026-09-01", "2026-09-30", "hits", FIELDS),  # visits fields for hits source
        ("2026-09-01", "2026-09-30", "visits", []),
    ):
        with pytest.raises(ValueError):
            validate_request(*args, today=TODAY)
    with pytest.raises(ValueError):
        validate_request("2026-09-01", "2026-09-30", "visits", FIELDS, "NOPE", today=TODAY)


def test_parse_tsv_bounds_rows_and_cells():
    body = "ym:s:visitID\tym:s:startURL\n1\thttps://a.test\n2\t" + "x" * 600 + "\n3\tc\n"
    preview = parse_tsv(body, 2)
    assert preview["columns"] == ["ym:s:visitID", "ym:s:startURL"]
    assert preview["row_count_returned"] == 2 and preview["rows_in_part"] == 3 and preview["truncated"]
    assert len(preview["rows"][1][1]) == 513


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, *, headers=None, payload=None, params=None, idempotent=False):
        self.calls.append((method, url, params, idempotent))
        return {"log_request": {"request_id": 5, "status": "created"}}

    async def request_raw(self, method, url, *, headers=None, payload=None, idempotent=False):
        self.calls.append((method, url, headers["Accept"], idempotent))
        return RawHttpResponse(200, {}, "ym:s:visitID\n1\n2\n")


async def test_adapter_uses_documented_endpoints():
    http = FakeHttp()
    adapter = YandexMetrikaLogsAdapter("https://api-metrika.yandex.net", "synthetic", http=http)
    base = "https://api-metrika.yandex.net/management/v1/counter/123"
    params = {"date1": "2026-09-01", "date2": "2026-09-02", "source": "visits", "fields": "ym:s:visitID"}
    await adapter.evaluate("123", params)
    await adapter.list("123")
    await adapter.create("123", params)
    await adapter.info("123", 5)
    await adapter.action("123", 5, "clean")
    await adapter.action("123", 5, "cancel")
    preview = await adapter.download_preview("123", 5, 0, 10)
    urls = [(call[0], call[1].split("?")[0]) for call in http.calls]
    assert urls == [
        ("GET", f"{base}/logrequests/evaluate"),
        ("GET", f"{base}/logrequests"),
        ("POST", f"{base}/logrequests"),
        ("GET", f"{base}/logrequest/5"),
        ("POST", f"{base}/logrequest/5/clean"),
        ("POST", f"{base}/logrequest/5/cancel"),
        ("GET", f"{base}/logrequest/5/part/0/download"),
    ]
    assert "source=visits" in http.calls[2][1] and preview["rows"] == [["1"], ["2"]]
    with pytest.raises(ValueError):
        await adapter.action("123", 5, "delete")


async def test_logs_tools_read_and_gate_writes(tmp_path):
    recorder = Recorder()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    args = {
        "counter_id": "123",
        "date1": "2020-01-01",
        "date2": "2020-01-31",
        "source": "visits",
        "fields": FIELDS,
    }
    async with Client(server) as client:
        await client.call_tool("metrika_logs_evaluate", args)
        await client.call_tool("metrika_logs_get", {"counter_id": "123", "request_id": 5})
        with pytest.raises(ToolError, match="validation_failed"):
            await client.call_tool("metrika_logs_evaluate", {**args, "source": "clicks"})
        with pytest.raises(ToolError):
            await client.call_tool("metrika_logs_create", {**args, "idempotency_key": "logs-1"})
    assert [call[1].rsplit("/", 2)[-2:] for call in recorder.calls] == [
        ["logrequests", "evaluate"],
        ["logrequest", "5"],
    ]


async def test_logs_create_when_enabled_is_idempotent(tmp_path):
    recorder = Recorder()
    settings = replace(config(tmp_path), yandex_metrika_logs_enabled=True)
    server = create_server(settings, transport="stdio", http_factory=recorder.factory)
    args = {
        "counter_id": "123",
        "date1": "2020-01-01",
        "date2": "2020-01-31",
        "source": "visits",
        "fields": FIELDS,
    }
    async with Client(server) as client:
        await client.call_tool("metrika_logs_create", {**args, "idempotency_key": "logs-1"})
        await client.call_tool("metrika_logs_create", {**args, "idempotency_key": "logs-1"})
    posts = [call for call in recorder.calls if call[0] == "POST"]
    assert (
        len(posts) == 1 and posts[0][1].endswith("source=visits") is False and "logrequests?" in posts[0][1]
    )


async def test_agency_clients_uses_compact_fields(tmp_path):
    recorder = Recorder()
    server = create_server(config(tmp_path), transport="stdio", http_factory=recorder.factory)
    async with Client(server) as client:
        await client.call_tool("direct_list_agency_clients", {"limit": 50})
        with pytest.raises(ToolError, match="validation_failed"):
            await client.call_tool("direct_list_agency_clients", {"archived": "MAYBE"})
    method, url, payload, _actor, _idem = recorder.calls[0]
    assert method == "POST" and url.endswith("/json/v5/agencyclients")
    assert payload["method"] == "get" and payload["params"]["Page"] == {"Limit": 50, "Offset": 0}
    assert "Phone" not in payload["params"]["FieldNames"]
