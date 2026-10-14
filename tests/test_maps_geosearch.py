"""Opt-in Geosearch toolset: key gating, request validation per request.html, compact shape, no key leaks."""

from __future__ import annotations

import os
from dataclasses import replace

import httpx
import pytest
from conftest import config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex import maps_geosearch as geo
from zai_yandex.agent_layer import build_instructions
from zai_yandex.server import create_server
from zai_yandex.toolsets import build_surface, surface_from_env

KEY = "synthetic-geosearch-key-never-live"
BIZ = {
    "type": "FeatureCollection",
    "properties": {
        "ResponseMetaData": {
            "SearchRequest": {"request": "кафе", "results": 2, "skip": 0},
            "SearchResponse": {"found": 120, "display": "multiple"},
        }
    },
    "features": [
        {
            "type": "Feature",
            "properties": {
                "name": "Кафе Б",
                "description": "Москва, Таганская ул., 1",
                "uri": "ymapsbm1://org?oid=2",
                "CompanyMetaData": {
                    "id": "2",
                    "name": "Кафе Б",
                    "address": "Москва, Таганская ул., 1",
                    "url": "https://b.example",
                    "Phones": [{"type": "phone", "formatted": "+7 (495) 000-00-02"}],
                    "Categories": [{"class": "cafe", "name": "Кафе"}],
                    "Hours": {"text": "ежедневно, 10:00–22:00"},
                },
            },
            "geometry": {"type": "Point", "coordinates": [37.65, 55.74]},
        },
        {
            "type": "Feature",
            "properties": {"name": "Кафе А", "CompanyMetaData": {"id": "1", "name": "Кафе А"}},
            "geometry": {"type": "Point", "coordinates": [37.66, 55.75]},
        },
    ],
}


def test_no_key_no_tools_and_router_unchanged():
    plain = build_surface("all")
    assert not any(plain.exposes(name) for name in geo.MAPS_TOOLS)
    assert "maps_search_organizations" not in build_instructions(plain)
    # YANDEX_TOOLS cannot force them without a key either.
    surface = surface_from_env({"YANDEX_TOOLS": "maps_search_organizations"})
    assert not surface.exposes("maps_search_organizations")


def test_key_is_the_opt_in_for_any_selection():
    surface = surface_from_env({"YANDEX_TOOLSETS": "default", geo.KEY_ENV: KEY})
    assert all(surface.exposes(name) for name in geo.MAPS_TOOLS)
    assert "maps_geosearch" in surface.toolsets
    assert "maps_search_organizations" in build_instructions(surface)
    hidden = surface_from_env({geo.KEY_ENV: KEY, "YANDEX_DISABLED_TOOLS": "maps_search_places"})
    assert hidden.exposes("maps_search_organizations") and not hidden.exposes("maps_search_places")


def test_key_file_preferred_and_must_be_private(tmp_path):
    path = tmp_path / "geo.key"
    path.write_text(KEY + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o644)
        with pytest.raises(ValueError, match="owner-private"):
            geo.load_key({geo.KEY_FILE_ENV: str(path)})
        path.chmod(0o600)
    assert geo.load_key({geo.KEY_FILE_ENV: str(path), geo.KEY_ENV: "other"}) == KEY
    assert geo.load_key({geo.KEY_ENV: " plain "}) == "plain"


def test_build_params_follows_request_reference():
    params = geo.build_params("biz", text=" кафе ", center=[37.62, 55.75], span=[0.1, 0.1], strict_area=True,
                              results=25, skip=50)  # fmt: skip
    assert params == {"type": "biz", "text": "кафе", "lang": "ru_RU", "ll": "37.62,55.75", "spn": "0.1,0.1",
                      "rspn": "1", "results": "25", "skip": "50"}  # fmt: skip
    bbox = geo.build_params("geo", text="Тверь", bbox=[[35.8, 56.8], [36.0, 56.9]])
    assert bbox["bbox"] == "35.8,56.8~36,56.9"
    for bad in (
        {"text": ""},
        {},
        {"text": "x", "results": 51},
        {"text": "x", "results": 10, "skip": 15},
        {"text": "x", "results": 10, "skip": 1010},
        {"text": "x", "center": [37.6, 55.7]},
        {"text": "x", "center": [200, 55.7], "span": [1, 1]},
        {"text": "x", "strict_area": True},
        {"text": "x", "lang": "de_DE"},
        {"uri": "https://evil.example"},
    ):
        with pytest.raises(ValueError):
            geo.build_params("biz", **bad)


def test_shape_keeps_api_order_and_attribution():
    out = geo.shape(BIZ, "biz", "summary")
    assert [row["name"] for row in out["results"]] == ["Кафе Б", "Кафе А"]
    first = out["results"][0]
    assert first["phones"] == ["+7 (495) 000-00-02"] and first["categories"] == ["Кафе"]
    assert first["hours"] == "ежедневно, 10:00–22:00" and first["coordinates"] == [37.65, 55.74]
    assert out["found"] == 120 and out["attribution"].startswith("Data: Yandex Maps")
    assert geo.shape(BIZ, "biz", "full")["results"][0] == BIZ["features"][0]


@pytest.mark.asyncio
async def test_client_sends_key_but_never_returns_or_leaks_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if request.url.params.get("text") == "boom":
            raise httpx.ConnectError(f"cannot reach {request.url}")
        if request.url.params.get("text") == "denied":
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(200, json=BIZ)

    client = geo.GeosearchClient(KEY, httpx.MockTransport(handler))
    payload = await client.search(geo.build_params("biz", text="кафе"))
    assert payload == BIZ and seen[0].params["apikey"] == KEY and str(seen[0]).startswith(geo.GEOSEARCH_URL)
    with pytest.raises(ConnectionError) as failed:
        await client.search(geo.build_params("biz", text="boom"))
    assert KEY not in str(failed.value)
    with pytest.raises(PermissionError):
        await client.search(geo.build_params("biz", text="denied"))


@pytest.mark.asyncio
async def test_server_registers_tools_only_with_key_and_redacts(tmp_path, monkeypatch):
    plain = create_server(config(tmp_path), transport="stdio", surface=build_surface("all"))
    async with Client(plain) as client:
        assert not {t.name for t in await client.list_tools()} & set(geo.MAPS_TOOLS)

    async def fake_search(self, params):
        assert params["type"] == "biz"
        return BIZ

    monkeypatch.setattr(geo.GeosearchClient, "search", fake_search)
    keyed = create_server(
        replace(config(tmp_path), maps_geosearch_api_key=KEY),
        transport="stdio",
        surface=build_surface("default", maps_key=True),
    )
    async with Client(keyed) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert set(geo.MAPS_TOOLS) <= set(tools)
        assert tools["maps_search_organizations"].annotations.readOnlyHint is True
        result = await client.call_tool("maps_search_organizations", {"text": "кафе", "results": 2})
        assert KEY not in str(result.structured_content)
        assert result.structured_content["results"][0]["name"] == "Кафе Б"
        with pytest.raises(ToolError):
            await client.call_tool("maps_search_organizations", {"text": "кафе", "results": 99})


async def test_key_never_reaches_http_logs(caplog):
    caplog.set_level("DEBUG")

    def handler(request):
        assert request.url.params["apikey"] == KEY  # sent to the API ...
        return httpx.Response(200, json=BIZ)

    client = geo.GeosearchClient(KEY, transport=httpx.MockTransport(handler))
    await client.search(geo.build_params("biz", text="кафе"))
    assert "search-maps.yandex.ru" in caplog.text and "apikey=***" in caplog.text
    assert KEY not in caplog.text  # ... but never logged
