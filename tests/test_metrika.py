from __future__ import annotations

from typing import Any

import pytest

from zai_yandex.adapters.metrika import YandexMetrikaAdapter
from zai_yandex.transport import ProviderError


class RecordingHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert payload is None
        self.calls.append((method, url, headers or {}, params))
        return {"ok": True}


@pytest.mark.asyncio
async def test_metrika_read_contract_uses_server_credential() -> None:
    http = RecordingHttp()
    adapter = YandexMetrikaAdapter("https://api-metrika.yandex.net", "secret", http=http)  # type: ignore[arg-type]

    await adapter.counters()
    await adapter.goals("7")
    await adapter.statistics(
        {
            "ids": "7",
            "date1": "2026-07-01",
            "date2": "2026-07-17",
            "metrics": "ym:s:visits",
            "dimensions": "ym:s:date",
            "accuracy": "full",
        }
    )

    assert [call[0] for call in http.calls] == ["GET", "GET", "GET"]
    assert http.calls[0][1].endswith("/management/v1/counters")
    assert http.calls[1][1].endswith("/management/v1/counter/7/goals")
    assert http.calls[2][1].endswith("/stat/v1/data")
    assert all(call[2]["Authorization"] == "OAuth secret" for call in http.calls)


@pytest.mark.asyncio
async def test_metrika_rejects_unbounded_or_invalid_input() -> None:
    adapter = YandexMetrikaAdapter("https://api-metrika.yandex.net", "secret", http=RecordingHttp())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="positive integer"):
        await adapter.goals("not-an-id")
    with pytest.raises(ValueError, match="unsupported Metrika parameters"):
        await adapter.statistics(
            {
                "ids": "7",
                "date1": "2026-07-01",
                "date2": "2026-07-17",
                "metrics": "ym:s:visits",
                "unrecognized_parameter": "unsafe",
            }
        )
    with pytest.raises(ValueError, match="date2"):
        await adapter.statistics(
            {
                "ids": "7",
                "date1": "2026-07-18",
                "date2": "2026-07-17",
                "metrics": "ym:s:visits",
            }
        )


@pytest.mark.asyncio
async def test_metrika_fails_closed_without_token() -> None:
    adapter = YandexMetrikaAdapter("https://api-metrika.yandex.net", "", http=RecordingHttp())  # type: ignore[arg-type]
    with pytest.raises(ProviderError, match="not configured"):
        await adapter.counters()
