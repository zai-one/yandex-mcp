"""Webmaster API v4.1 read additions; httpx.MockTransport only, no Yandex requests."""

from __future__ import annotations

import httpx
import pytest
from test_webmaster_adapter import HOST, URL, adapter

from zai_yandex.adapters.webmaster import YandexWebmasterAdapter


async def test_sqi_history_path_period_and_local_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/user/42/hosts/https:example.test:443/sqi-history"
        assert dict(request.url.params) == {"date_from": "2026-01-01", "date_to": "2026-06-30"}
        return httpx.Response(
            200,
            json={
                "points": [
                    {"date": "2025-12-25T00:00:00,000+0300", "value": 90},
                    {"date": "2026-03-01T00:00:00,000+0300", "value": 110},
                ]
            },
        )

    result = await adapter(monkeypatch, handler).sqi_history(HOST, "2026-01-01", "2026-06-30")
    assert result["data"]["points"] == [{"date": "2026-03-01T00:00:00,000+0300", "value": 110}]
    assert result["availability"] == "available"
    assert result["truncated"] is False


async def test_sqi_history_defaults_to_last_year_and_flags_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"points": [{"value": 1}]})

    result = await adapter(monkeypatch, handler).sqi_history(HOST)
    assert set(seen) == {"date_from", "date_to"}
    assert result["availability"] == "partial"
    assert result["data"]["points"] == []


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2025-01-01", "2026-06-30"),
        ("2026-02-01", "2026-01-01"),
        ("2026-01-01", None),
        ("01.01.2026", "2026-02-01"),
    ],
)
async def test_sqi_period_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch, start: str, end: str | None
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid SQI period reached network")

    with pytest.raises(ValueError):
        await adapter(monkeypatch, handler).sqi_history(HOST, start, end)


async def test_important_urls_are_paged_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/important-urls")
        assert not request.url.params
        return httpx.Response(200, json={"urls": [{"url": f"https://example.test/{i}"} for i in range(5)]})

    result = await adapter(monkeypatch, handler).important_urls(HOST, offset=2, limit=2)
    assert [row["url"] for row in result["data"]["urls"]] == [
        "https://example.test/2",
        "https://example.test/3",
    ]
    assert result["pagination"]["next_offset"] == 4
    assert result["pagination"]["mode"] == "local_offset"


async def test_important_url_history_requires_url_of_exact_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/important-urls/history")
        assert request.url.params["url"] == URL
        return httpx.Response(200, json={"history": [{"url": URL, "change_indicators": ["TITLE"]}]})

    value = adapter(monkeypatch, handler)
    result = await value.important_url_history(HOST, URL)
    assert result["data"]["history"][0]["change_indicators"] == ["TITLE"]
    with pytest.raises(ValueError):
        await value.important_url_history(HOST, "https://other.test/page")


async def test_search_events_history_and_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search-urls/events/history"):
            assert request.url.params["date_from"] == "2026-09-01"
            return httpx.Response(
                200,
                json={
                    "indicators": {
                        "APPEARED_IN_SEARCH": [{"date": "2026-09-01T00:00:00,000+0300", "value": 3}],
                    }
                },
            )
        assert request.url.path.endswith("/search-urls/events/samples")
        assert request.url.params["limit"] == "10"
        return httpx.Response(
            200, json={"count": 1, "samples": [{"url": URL, "event": "REMOVED_FROM_SEARCH"}]}
        )

    value = adapter(monkeypatch, handler)
    history = await value.search_events_history(HOST, "2026-09-01", "2026-09-01")
    assert history["coverage"]["APPEARED_IN_SEARCH"]["available"] is True
    assert history["coverage"]["REMOVED_FROM_SEARCH"]["available"] is False
    assert history["availability"] == "partial"
    samples = await value.search_events_samples(HOST, limit=10)
    assert samples["data"]["samples"][0]["event"] == "REMOVED_FROM_SEARCH"
    assert samples["pagination"]["complete"] is True


def test_sqi_period_validator_accepts_full_year() -> None:
    assert YandexWebmasterAdapter.validate_sqi_period("2025-07-01", "2026-06-30")["date_to"] == "2026-06-30"
