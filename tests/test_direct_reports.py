from __future__ import annotations

from typing import Any

import pytest

from zai_yandex.adapters.direct import YandexDirectAdapter
from zai_yandex.transport import JsonHttpClient, RawHttpResponse


class FakeRawClient(JsonHttpClient):
    def __init__(self, response: RawHttpResponse) -> None:
        super().__init__()
        self.response = response
        self.request_payload: dict[str, Any] | None = None
        self.request_headers: dict[str, str] | None = None

    async def request_raw(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RawHttpResponse:
        assert method == "POST"
        assert url == "https://api.direct.yandex.com/json/v5/reports"
        self.request_payload = payload
        self.request_headers = headers
        return self.response


@pytest.mark.asyncio
async def test_direct_statistics_parses_completed_tsv() -> None:
    http = FakeRawClient(
        RawHttpResponse(
            200,
            {"requestid": "req-1"},
            "Date\tCampaignId\tImpressions\tClicks\tCost\n2026-07-01\t42\t100\t5\t12.34\n",
        )
    )
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)
    result = await adapter.statistics([42], "2026-07-01", "2026-07-02")
    assert result["status"] == "completed"
    assert result["row_count"] == 1
    assert result["rows"][0]["CampaignId"] == "42"
    assert http.request_payload is not None
    params = http.request_payload["params"]
    assert params["SelectionCriteria"]["DateFrom"] == "2026-07-01"
    assert params["SelectionCriteria"]["Filter"][0]["Values"] == ["42"]
    assert http.request_headers is not None
    assert http.request_headers["processingMode"] == "auto"


@pytest.mark.asyncio
async def test_direct_statistics_accepts_ads_read_report_contract() -> None:
    http = FakeRawClient(
        RawHttpResponse(
            200,
            {},
            "Date\tCampaignId\tCampaignName\tClickType\tClicks\n2026-07-01\t42\tBrand\tCARD\t5\n",
        )
    )
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)
    fields = ["Date", "CampaignId", "CampaignName", "ClickType", "Clicks"]

    result = await adapter.statistics(
        [42],
        "2026-07-01",
        "2026-07-02",
        fields,
        "CUSTOM_REPORT",
    )

    assert result["rows"][0]["ClickType"] == "CARD"
    assert http.request_payload is not None
    assert http.request_payload["params"]["FieldNames"] == fields
    assert http.request_payload["params"]["ReportType"] == "CUSTOM_REPORT"


@pytest.mark.asyncio
async def test_direct_statistics_supports_account_wide_report_without_filter() -> None:
    http = FakeRawClient(RawHttpResponse(200, {}, "Date\tCampaignId\n2026-07-01\t42\n"))
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)

    result = await adapter.statistics(None, "2026-07-01", "2026-07-02", ["Date", "CampaignId"])

    assert result["row_count"] == 1
    assert http.request_payload is not None
    criteria = http.request_payload["params"]["SelectionCriteria"]
    assert criteria == {"DateFrom": "2026-07-01", "DateTo": "2026-07-02"}


@pytest.mark.asyncio
async def test_direct_statistics_rejects_unapproved_fields_and_report_types() -> None:
    http = FakeRawClient(RawHttpResponse(200, {}, ""))
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)
    with pytest.raises(ValueError, match="unsupported CUSTOM_REPORT fields"):
        await adapter.statistics([42], "2026-07-01", "2026-07-02", ["Date", "SecretField"])
    with pytest.raises(ValueError, match="unsupported Direct report type"):
        await adapter.statistics([42], "2026-07-01", "2026-07-02", ["Date"], "ACCOUNT_PERFORMANCE_REPORT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([], "non-empty unique"),
        (["Date", "Date"], "non-empty unique"),
        (["Date", 1], "non-empty unique"),
    ],
)
async def test_direct_statistics_rejects_invalid_field_lists(fields: list[object], message: str) -> None:
    http = FakeRawClient(RawHttpResponse(200, {}, ""))
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)

    with pytest.raises(ValueError, match=message):
        await adapter.statistics(None, "2026-07-01", "2026-07-02", fields)  # type: ignore[arg-type]

    assert http.request_payload is None


@pytest.mark.asyncio
async def test_direct_statistics_rejects_fields_outside_report_type_matrix() -> None:
    http = FakeRawClient(RawHttpResponse(200, {}, ""))
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)

    with pytest.raises(ValueError, match="unsupported CAMPAIGN_PERFORMANCE_REPORT fields"):
        await adapter.statistics(
            None,
            "2026-07-01",
            "2026-07-02",
            ["Date", "Query"],
            "CAMPAIGN_PERFORMANCE_REPORT",
        )

    assert http.request_payload is None


@pytest.mark.asyncio
async def test_direct_statistics_exposes_offline_retry_without_polling_loop() -> None:
    http = FakeRawClient(RawHttpResponse(201, {"retryin": "12", "reportsinqueue": "2"}, ""))
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)
    result = await adapter.statistics([42], "2026-07-01", "2026-07-02")
    assert result == {
        "request_id": None,
        "report_hash": result["report_hash"],
        "live_call": True,
        "read_only": True,
        "status": "queued",
        "retry_after_seconds": 12,
        "reports_in_queue": "2",
        "result": None,
    }


@pytest.mark.asyncio
async def test_direct_statistics_rejects_reverse_date_range() -> None:
    http = FakeRawClient(RawHttpResponse(200, {}, ""))
    adapter = YandexDirectAdapter("https://api.direct.yandex.com/json/v5", "fixture-token", http=http)
    with pytest.raises(ValueError, match="must not precede"):
        await adapter.statistics([42], "2026-07-02", "2026-07-01")
