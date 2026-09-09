from __future__ import annotations

from typing import Any

import pytest

from zai_yandex.adapters.direct import YandexDirectAdapter
from zai_yandex.transport import ProviderError


class RecordingHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"method": method, "url": url, "headers": headers, "payload": payload})
        return {"result": {"HasSearchVolumeResults": []}}


@pytest.mark.asyncio
async def test_keywords_research_allows_search_volume_forecast() -> None:
    http = RecordingHttp()
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)
    payload = {
        "method": "hasSearchVolume",
        "params": {
            "SelectionCriteria": {"Keywords": ["industrial mcp"], "RegionIds": [225]},
            "FieldNames": ["Keyword", "RegionIds", "AllDevices"],
        },
    }

    response = await adapter.read("keywordsresearch", payload)

    assert response == {"result": {"HasSearchVolumeResults": []}}
    assert http.calls[0]["url"].endswith("/keywordsresearch")
    assert http.calls[0]["payload"] == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["create", "get", "deduplicate", "add"])
async def test_keywords_research_denies_non_forecast_methods_before_network(method: str) -> None:
    http = RecordingHttp()
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)

    with pytest.raises(ProviderError, match="method is not in the v1 read allowlist"):
        await adapter.read("keywordsresearch", {"method": method, "params": {}})

    assert http.calls == []
