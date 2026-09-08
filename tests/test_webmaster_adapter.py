"""MIT client/action contract scenarios ported; additional platform boundaries.

All transport is httpx.MockTransport; no Yandex or paid API requests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from zai_yandex.adapters.webmaster import YandexWebmasterAdapter
from zai_yandex.transport import (
    JsonHttpClient,
    ProviderAdmissionDenied,
    ProviderAuthenticationError,
    ProviderError,
    ProviderHttpError,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeoutError,
    RetryPolicy,
    retry_attempt_admission,
)

HOST = "https:example.test:443"
URL = "https://example.test/page?a=b"
BASE = "https://api.webmaster.yandex.net/v4"
_REAL_CLIENT = httpx.AsyncClient


def adapter(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    resolved: bool = True,
) -> YandexWebmasterAdapter:
    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("zai_yandex.transport.httpx.AsyncClient", factory)
    value = YandexWebmasterAdapter(
        BASE, "fixture-token", JsonHttpClient(retry=RetryPolicy(attempts=3, base_delay_seconds=0))
    )
    if resolved:
        value._user_id = "42"
    return value


async def test_resolves_and_caches_token_user_and_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["Authorization"] == "OAuth fixture-token"
        return httpx.Response(
            200, json={"user_id": 42} if request.url.path.endswith("/user") else {"hosts": []}
        )

    value = adapter(monkeypatch, handler, resolved=False)
    await value.hosts()
    await value.hosts()
    assert paths == ["/v4/user", "/v4/user/42/hosts", "/v4/user/42/hosts"]


@pytest.mark.parametrize("user_id", [True, -2, "1/hosts", "01", None, 9223372036854775808])
async def test_invalid_resolved_user_never_reaches_host(
    monkeypatch: pytest.MonkeyPatch, user_id: Any
) -> None:
    value = adapter(monkeypatch, lambda _: httpx.Response(200, json={"user_id": user_id}), resolved=False)
    with pytest.raises(ProviderResponseError):
        await value.hosts()


@pytest.mark.parametrize(
    "host_id", ["https:example.test:443/../owners", "https:example.test:0", "http:a..b:80", "http:a:99999"]
)
def test_path_injection_and_invalid_hosts_rejected(host_id: str) -> None:
    with pytest.raises(ValueError):
        YandexWebmasterAdapter.validate_host_id(host_id)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/page",
        "https://example.test:444/page",
        "https://other.test/page",
        "https://example.test.evil.test/",
        "https://user:password@example.test/",
        "https://example.test/#part",
        "https://example.test/\npath",
        "https://example.test/?access_token=value",
        "https://example.test\\@evil.test/",
    ],
)
def test_write_url_exact_origin_and_secret_boundaries(url: str) -> None:
    with pytest.raises(ValueError):
        YandexWebmasterAdapter.validate_url_for_host(url, HOST)
    assert YandexWebmasterAdapter.validate_url_for_host(URL, HOST) == URL


@pytest.mark.parametrize("verified", [False, None, "true"])
async def test_verified_host_requires_real_server_boolean(
    monkeypatch: pytest.MonkeyPatch, verified: Any
) -> None:
    value = adapter(monkeypatch, lambda _: httpx.Response(200, json={"host_id": HOST, "verified": verified}))
    with pytest.raises(ProviderError, match="not verified"):
        await value.verified_host(HOST)


async def test_verified_host_fresh_owner_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200, json={"host_id": HOST, "verified": True, "ascii_host_url": "https://example.test/"}
        )

    value = adapter(monkeypatch, handler)
    assert (await value.verified_host(HOST))["data"]["verified"] is True
    await value.verified_host(HOST)
    assert len(calls) == 2


async def test_local_host_pagination_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    value = adapter(
        monkeypatch, lambda _: httpx.Response(200, json={"hosts": [{"host_id": str(i)} for i in range(205)]})
    )
    result = await value.hosts(offset=100)
    assert len(result["data"]["hosts"]) == 100
    assert result["pagination"]["next_offset"] == 200
    assert result["pagination"]["complete"] is False
    assert (await value.hosts(offset=200))["pagination"]["complete"] is True


async def test_short_page_with_reported_more_does_not_claim_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    value = adapter(monkeypatch, lambda _: httpx.Response(200, json={"samples": [{"url": URL}], "count": 9}))
    result = await value.indexing_samples(HOST, offset=2, limit=3)
    assert result["pagination"]["next_offset"] == 3
    assert result["pagination"]["complete"] is False


async def test_missing_list_and_oversized_page_are_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    value = adapter(monkeypatch, lambda _: httpx.Response(200, json={"count": 12}))
    result = await value.external_links(HOST)
    assert result["availability"] == "partial"
    assert result["pagination"]["complete"] is False
    value = adapter(
        monkeypatch, lambda _: httpx.Response(200, json={"tasks": [{"task_id": "one"}, {"task_id": "two"}]})
    )
    result = await value.recrawl_queue(HOST, limit=1)
    assert len(result["data"]["tasks"]) == 1
    assert result["availability"] == "partial"


@pytest.mark.parametrize(("method", "parameter"), [("sitemaps", "from"), ("user_sitemaps", "offset")])
async def test_sitemap_opaque_cursor_not_numeric_offset(
    monkeypatch: pytest.MonkeyPatch, method: str, parameter: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params[parameter] == "c7-fe:80-c0"
        return httpx.Response(200, json={"sitemaps": [{"sitemap_id": "next-id"}], "count": 5})

    result = await getattr(adapter(monkeypatch, handler), method)(HOST, cursor="c7-fe:80-c0", limit=1)
    assert result["pagination"]["next_cursor"] == "next-id"
    assert result["pagination"]["next_offset"] is None
    assert result["pagination"]["complete"] is False


async def test_query_history_repeated_indicators_missing_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get_list("query_indicator") == ["TOTAL_SHOWS", "TOTAL_CLICKS"]
        return httpx.Response(200, json={"indicators": {"TOTAL_SHOWS": [{"date": "2026-09-01", "value": 4}]}})

    value = adapter(monkeypatch, handler)
    result = await value.query_history(
        HOST, "2026-09-01", "2026-09-02", query_indicator=["TOTAL_SHOWS", "TOTAL_CLICKS"]
    )
    assert "TOTAL_CLICKS" not in result["data"]["indicators"]
    assert result["coverage"]["TOTAL_CLICKS"]["available"] is False
    assert result["availability"] == "partial"
    assert result["period"] == {"date_from": "2026-09-01", "date_to": "2026-09-02"}
    assert result["collected_at"].endswith("+00:00")


async def test_external_history_uses_required_indicator_and_local_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"indicator": "LINKS_TOTAL_COUNT"}
        return httpx.Response(
            200,
            json={
                "indicators": {
                    "LINKS_TOTAL_COUNT": [
                        {"date": "2026-08-01", "value": 1},
                        {"date": "2026-09-01", "value": 2},
                    ]
                }
            },
        )

    result = await adapter(monkeypatch, handler).external_links_history(HOST, "2026-09-01", "2026-09-02")
    assert result["data"]["indicators"]["LINKS_TOTAL_COUNT"] == [{"date": "2026-09-01", "value": 2}]
    assert result["date_filter"] == "local"


@pytest.mark.parametrize(
    ("start", "end"), [("2026-08-01", "2026-09-01"), ("2026-09-02", "2026-09-01"), ("20260901", "2026-09-02")]
)
async def test_history_period_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch, start: str, end: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid period reached network")

    with pytest.raises(ValueError):
        await adapter(monkeypatch, handler).query_history(HOST, start, end)
    assert YandexWebmasterAdapter.validate_period("2026-08-01", "2026-08-31")["date_to"] == "2026-08-31"


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors_are_not_retried_and_secret_echo_redacted(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    calls = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            status, json={"access_token": "fixture-hidden", "error_message": "token=fixture-hidden"}
        )

    with pytest.raises(ProviderAuthenticationError) as exc:
        await adapter(monkeypatch, handler).summary(HOST)
    assert len(calls) == 1
    assert "fixture-hidden" not in str(exc.value.body_preview)


@pytest.mark.parametrize("operation", ["submit_recrawl", "submit_sitemap"])
@pytest.mark.parametrize("failure", ["timeout", "quota", "server"])
async def test_uncertain_and_quota_posts_never_retried(
    monkeypatch: pytest.MonkeyPatch, operation: str, failure: str
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "POST"
        assert json.loads(request.content) == {"url": URL}
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture transport timeout", request=request)
        return httpx.Response(429 if failure == "quota" else 503, json={"error_code": "QUOTA_EXCEEDED"})

    expected = {"timeout": ProviderTimeoutError, "quota": ProviderRateLimited, "server": ProviderHttpError}[
        failure
    ]
    with pytest.raises(expected):
        await getattr(adapter(monkeypatch, handler), operation)(HOST, URL)
    assert len(calls) == 1


async def test_recrawl_acceptance_and_readback_keep_distinct_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path.endswith("/recrawl/queue")
            return httpx.Response(202, json={"task_id": "task-123", "quota_remainder": 4})
        assert request.url.path.endswith("/recrawl/queue/task-123")
        return httpx.Response(200, json={"task_id": "task-123", "url": URL, "state": "IN_PROGRESS"})

    value = adapter(monkeypatch, handler)
    receipt = await value.submit_recrawl(HOST, URL)
    assert "state" not in receipt
    assert (await value.recrawl_task(HOST, receipt["task_id"]))["data"]["state"] == "IN_PROGRESS"


def test_duplicate_error_exposes_only_safe_ids() -> None:
    error = ProviderHttpError(
        409,
        body_preview=json.dumps(
            {"error_code": "SITEMAP_ALREADY_ADDED", "sitemap_id": "c7-fe:80-c0", "error_message": "untrusted"}
        ),
    )
    assert YandexWebmasterAdapter.duplicate_details(error) == {
        "error_code": "SITEMAP_ALREADY_ADDED",
        "sitemap_id": "c7-fe:80-c0",
    }


async def test_oauth_echo_hidden_in_success_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    value = adapter(monkeypatch, lambda _: httpx.Response(200, json={"message": "OAuth fixture-token"}))
    assert "fixture-token" not in json.dumps(await value.host(HOST))
    value = adapter(monkeypatch, lambda _: httpx.Response(403, json={"message": "OAuth fixture-token"}))
    with pytest.raises(ProviderAuthenticationError) as exc:
        await value.host(HOST)
    assert "fixture-token" not in str(exc.value.body_preview)


async def test_verification_uses_official_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v4/user/42/hosts/{HOST}/verification"
        return httpx.Response(200, json={"verification_state": "VERIFIED"})

    assert (await adapter(monkeypatch, handler).verification(HOST))["data"][
        "verification_state"
    ] == "VERIFIED"


@pytest.mark.parametrize("admit", [True, False])
async def test_user_resolution_charges_second_request_before_host(
    monkeypatch: pytest.MonkeyPatch, admit: bool
) -> None:
    paths = []
    admissions = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200, json={"user_id": 42} if request.url.path.endswith("/user") else {"hosts": []}
        )

    async def admission() -> bool:
        admissions.append(1)
        return admit

    value = adapter(monkeypatch, handler, resolved=False)
    token = retry_attempt_admission.set(admission)
    try:
        if admit:
            await value.hosts()
            await value.hosts()
            assert len(paths) == 3
        else:
            with pytest.raises(ProviderAdmissionDenied):
                await value.hosts()
            assert paths == ["/v4/user"]
        assert len(admissions) == 1
    finally:
        retry_attempt_admission.reset(token)


async def test_daily_query_gap_is_partial_even_when_metric_present(monkeypatch: pytest.MonkeyPatch) -> None:
    value = adapter(
        monkeypatch,
        lambda _: httpx.Response(
            200, json={"indicators": {"TOTAL_SHOWS": [{"date": "2026-09-01", "value": 4}]}}
        ),
    )
    result = await value.query_history(HOST, "2026-09-01", "2026-09-02", query_indicator="TOTAL_SHOWS")
    assert result["availability"] == "partial"
    assert result["coverage"]["TOTAL_SHOWS"]["missing_dates"] == ["2026-09-02"]


async def test_inconsistent_count_and_malformed_rows_cannot_escape_page_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = adapter(
        monkeypatch, lambda _: httpx.Response(200, json={"count": 0, "sitemaps": [{"sitemap_id": "x"}]})
    )
    result = await value.user_sitemaps(HOST)
    assert result["availability"] == "partial"
    assert result["pagination"]["complete"] is False
    value = adapter(monkeypatch, lambda _: httpx.Response(200, json={"queries": ["bad"] * 200}))
    result = await value.popular_queries(HOST)
    assert len(result["data"]["queries"]) == 100
    assert result["availability"] == "partial"


@pytest.mark.parametrize("method", ["sitemaps", "user_sitemaps"])
@pytest.mark.parametrize("malformed_row", ["bad", None])
@pytest.mark.parametrize("row_count", [1, 200])
async def test_malformed_sitemap_rows_are_partial_and_bounded_without_cursor_crash(
    monkeypatch: pytest.MonkeyPatch, method: str, malformed_row: Any, row_count: int
) -> None:
    value = adapter(
        monkeypatch,
        lambda _: httpx.Response(200, json={"sitemaps": [malformed_row] * row_count, "count": row_count}),
    )
    result = await getattr(value, method)(HOST)
    assert len(result["data"]["sitemaps"]) == min(row_count, 100)
    assert result["availability"] == "partial"
    assert result["pagination"]["complete"] is False
    assert result["pagination"]["next_cursor"] is None
