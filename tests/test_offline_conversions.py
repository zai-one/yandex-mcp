from dataclasses import replace

import httpx
import pytest
from conftest import Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.offline_conversions import YandexMetrikaOfflineAdapter, normalize
from zai_yandex.server import create_server
from zai_yandex.transport import JsonHttpClient
from zai_yandex.wordstat import validate_devices, validate_num_phrases

NOW = 1_790_000_000
ROWS = [
    {
        "ClientId": "1700000000123",
        "Target": "crm_sale",
        "DateTime": 1_780_000_000,
        "Price": 1500.5,
        "Currency": "RUB",
    },
    {"Yclid": "8123", "Target": "crm_sale", "DateTime": 1_780_000_100},
]


def test_normalize_builds_documented_csv():
    prepared = normalize("123", ROWS, "BASIC", "crm sync", now=NOW)
    assert prepared["columns"] == ["ClientId", "Yclid", "Target", "DateTime", "Price", "Currency"]
    assert prepared["csv"].splitlines() == [
        "ClientId,Yclid,Target,DateTime,Price,Currency",
        "1700000000123,,crm_sale,1780000000,1500.5,RUB",
        ",8123,crm_sale,1780000100,,",
    ]
    assert (
        normalize("123", ROWS, "BASIC", "crm sync", now=NOW)["confirmation_hash"]
        == prepared["confirmation_hash"]
    )


@pytest.mark.parametrize(
    "rows,kwargs",
    [
        ([{"Target": "t", "DateTime": 1}], {}),  # no visit identifier
        ([{"ClientId": "1", "Target": "t", "DateTime": NOW + 10}], {}),  # future time
        ([{"ClientId": "1", "Target": "a,b", "DateTime": 1}], {}),
        ([{"ClientId": "1", "Target": "t", "DateTime": 1, "Phone": "+7"}], {}),
        ([{"ClientId": "1", "Target": "t", "DateTime": 1, "Currency": "rub"}], {}),
        ([], {}),
        (ROWS, {"conversion_type": "EMAIL"}),
        (ROWS, {"comment": "bad;comment"}),
    ],
)
def test_normalize_rejects_invalid(rows, kwargs):
    with pytest.raises(ValueError):
        normalize("123", rows, now=NOW, **kwargs)


def test_wordstat_top_validation():
    assert validate_devices(None) == ["DEVICE_ALL"]
    assert validate_devices(["DEVICE_PHONE", "DEVICE_TABLET"]) == ["DEVICE_PHONE", "DEVICE_TABLET"]
    assert validate_num_phrases(2000) == 2000
    for bad in (["DEVICE_ALL", "DEVICE_PHONE"], ["DEVICE_TV"], []):
        with pytest.raises(ValueError):
            validate_devices(bad)
    for bad in (0, 2001, True):
        with pytest.raises(ValueError):
            validate_num_phrases(bad)


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, *, headers=None, payload=None, params=None, idempotent=False):
        self.calls.append((method, url, params))
        return {"uploadings": []}

    async def request_multipart(self, method, url, *, files, headers=None, params=None):
        self.calls.append((method, url, params, files["file"][1].decode()))
        return {"uploading": {"id": 77, "status": "UPLOADED"}}


async def test_adapter_uses_documented_endpoints():
    http = FakeHttp()
    adapter = YandexMetrikaOfflineAdapter("https://api-metrika.yandex.net", "synthetic", http=http)
    base = "https://api-metrika.yandex.net/management/v1/counter/123/offline_conversions"
    await adapter.upload(normalize("123", ROWS, "CALLS", now=NOW))
    await adapter.list("123", "BASIC", 10, 0)
    await adapter.get("123", 77)
    assert [(call[0], call[1]) for call in http.calls] == [
        ("POST", f"{base}/upload"),
        ("GET", f"{base}/uploadings"),
        ("GET", f"{base}/uploading/77"),
    ]
    assert http.calls[0][2] == {"type": "CALLS"} and http.calls[0][3].startswith("ClientId,Yclid,Target")


async def test_multipart_transport_sends_file_once():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"uploading": {"id": 1}})

    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(httpx.AsyncClient, "__init__", patched)
    try:
        result = await JsonHttpClient().request_multipart(
            "POST", "https://api-metrika.yandex.test/upload", files={"file": ("c.csv", b"a,b\n", "text/csv")}
        )
    finally:
        mp.undo()
    assert result == {"uploading": {"id": 1}} and len(seen) == 1
    assert seen[0].headers["content-type"].startswith("multipart/form-data")


async def _upload(settings, monkeypatch, args):
    sent = []

    async def fake(self, method, url, *, files, headers=None, params=None):
        sent.append((url, params))
        return {"uploading": {"id": 77, "status": "UPLOADED"}}

    monkeypatch.setattr(JsonHttpClient, "request_multipart", fake)
    recorder = Recorder()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        validated = (await client.call_tool("metrika_validate_offline_conversions", args)).data
        upload = {**args, "confirmation_hash": validated["confirmation_hash"], "idempotency_key": "oc-1"}
        results = []
        for payload in (upload, upload, {**upload, "confirmation_hash": "0" * 64, "idempotency_key": "oc-2"}):
            try:
                results.append((await client.call_tool("metrika_upload_offline_conversions", payload)).data)
            except ToolError as exc:
                results.append(exc)
        await client.call_tool(
            "metrika_get_offline_conversion_upload", {"counter_id": "123", "uploading_id": 77}
        )
    return validated, results, sent, recorder


async def test_upload_gated_by_write_flag(tmp_path, monkeypatch):
    args = {"counter_id": "123", "conversions": ROWS}
    validated, results, sent, _ = await _upload(config(tmp_path), monkeypatch, args)
    assert validated["row_count"] == 2 and not sent
    assert all(isinstance(result, ToolError) for result in results)


async def test_upload_when_enabled_is_hash_confirmed_and_idempotent(tmp_path, monkeypatch):
    settings = replace(config(tmp_path), yandex_metrika_write_enabled=True)
    args = {"counter_id": "123", "conversions": ROWS, "comment": "crm"}
    _, results, sent, recorder = await _upload(settings, monkeypatch, args)
    assert len(sent) == 1 and sent[0][0].endswith("/counter/123/offline_conversions/upload")
    assert sent[0][1] == {"type": "BASIC", "comment": "crm"}
    assert results[0] == results[1] and results[0]["applied"]
    assert isinstance(results[2], ToolError)
    assert recorder.calls[-1][1].endswith("/offline_conversions/uploading/77")
