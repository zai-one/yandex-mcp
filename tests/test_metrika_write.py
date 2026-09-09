from __future__ import annotations

from typing import Any

import pytest

from zai_yandex.adapters.metrika import (
    METRIKA_WRITE_ALLOWLIST,
    YandexMetrikaAdapter,
)
from zai_yandex.transport import ProviderError


class WriteRecordingHttp:
    """Records write/read calls and answers Metrika responses locally.

    No network is touched: POST/PUT/DELETE mutations and readback GETs are
    served from canned bodies so the guarded write path runs deterministically.
    """

    def __init__(self, *, force_response: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.force_response = force_response

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "payload": payload})
        if self.force_response is not None:
            return self.force_response
        if method == "DELETE":
            return {"success": True}
        if method == "GET" and url.endswith("/goals"):
            return {"goals": [{"id": 555, "name": "Order"}]}
        if method == "GET" and "/goal/" in url:
            return {"goal": {"id": 555, "name": "Order"}}
        goal = (payload or {}).get("goal", {})
        return {"goal": {"id": 555, **goal}}


def _adapter(http: WriteRecordingHttp) -> YandexMetrikaAdapter:
    return YandexMetrikaAdapter("https://api-metrika.yandex.net", "secret", http=http)  # type: ignore[arg-type]


def _goal() -> dict[str, Any]:
    return {
        "name": "Order",
        "type": "action",
        "conditions": [{"type": "exact", "url": "thankyou"}],
    }


def test_risk_tier_marks_delete_high() -> None:
    assert YandexMetrikaAdapter.risk_tier("create_goal") == "medium"
    assert YandexMetrikaAdapter.risk_tier("update_goal") == "medium"
    assert YandexMetrikaAdapter.risk_tier("delete_goal") == "high"
    assert YandexMetrikaAdapter.risk_tier("drop_counter") == "unknown"
    assert METRIKA_WRITE_ALLOWLIST == {
        "create_goal": "medium",
        "update_goal": "medium",
        "delete_goal": "high",
    }


def test_validate_write_is_fail_closed() -> None:
    # Only allowlisted operations pass.
    with pytest.raises(ProviderError, match="allowlist"):
        YandexMetrikaAdapter.validate_write("purge_counter", 7, _goal())
    # counter_id must be a positive integer (bool is rejected explicitly).
    with pytest.raises(ProviderError, match="counter_id"):
        YandexMetrikaAdapter.validate_write("create_goal", 0, _goal())
    with pytest.raises(ProviderError, match="counter_id"):
        YandexMetrikaAdapter.validate_write("create_goal", True, _goal())
    with pytest.raises(ProviderError, match="counter_id"):
        YandexMetrikaAdapter.validate_write("create_goal", "7", _goal())
    # A goal write requires name/type/conditions.
    with pytest.raises(ProviderError, match="fields"):
        YandexMetrikaAdapter.validate_write("create_goal", 7, {"name": "Order"})
    with pytest.raises(ProviderError, match="conditions"):
        YandexMetrikaAdapter.validate_write(
            "create_goal", 7, {"name": "Order", "type": "action", "conditions": []}
        )
    with pytest.raises(ProviderError, match="name"):
        YandexMetrikaAdapter.validate_write(
            "update_goal", 7, {"name": "   ", "type": "action", "conditions": [{"x": 1}]}
        )


def test_validate_write_normalizes_and_accepts_wrapped_goal() -> None:
    operation, counter_id, goal = YandexMetrikaAdapter.validate_write("create_goal", 7, _goal())
    assert (operation, counter_id) == ("create_goal", 7)
    assert goal == _goal()

    # Params may arrive wrapped as {"goal": {...}}; the goal body is unwrapped.
    _, _, wrapped = YandexMetrikaAdapter.validate_write("update_goal", 7, {"goal": _goal()})
    assert wrapped == _goal()

    # Delete carries no body and validates clean without params.
    assert YandexMetrikaAdapter.validate_write("delete_goal", 7, None) == ("delete_goal", 7, {})


@pytest.mark.asyncio
async def test_apply_create_goal_posts_and_returns_goal_id() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    result = await adapter.apply_write("create_goal", 7, _goal())

    assert result["applied"] is True
    assert result["operation"] == "create_goal"
    assert result["counter_id"] == 7
    assert result["risk_tier"] == "medium"
    # goal id is surfaced from the response for idempotency / readback.
    assert result["goal_id"] == 555
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/management/v1/counter/7/goals")
    assert call["payload"] == {"goal": _goal()}
    assert call["headers"]["Authorization"] == "OAuth secret"


@pytest.mark.asyncio
async def test_apply_update_goal_puts_to_goal_path() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    result = await adapter.apply_write("update_goal", 7, _goal(), goal_id=321)

    assert result["goal_id"] == 321
    assert result["risk_tier"] == "medium"
    call = http.calls[0]
    assert call["method"] == "PUT"
    assert call["url"].endswith("/management/v1/counter/7/goal/321")
    assert call["payload"] == {"goal": _goal()}


@pytest.mark.asyncio
async def test_apply_delete_goal_deletes_without_body() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    result = await adapter.apply_write("delete_goal", 7, goal_id=321)

    assert result["goal_id"] == 321
    assert result["risk_tier"] == "high"
    call = http.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/management/v1/counter/7/goal/321")
    assert call["payload"] is None


@pytest.mark.asyncio
async def test_apply_enforces_goal_id_contract() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    with pytest.raises(ProviderError, match="goal_id"):
        await adapter.apply_write("update_goal", 7, _goal())
    with pytest.raises(ProviderError, match="goal_id"):
        await adapter.apply_write("delete_goal", 7)
    with pytest.raises(ProviderError, match="must not carry a goal_id"):
        await adapter.apply_write("create_goal", 7, _goal(), goal_id=321)
    # A rejected write never reaches the transport.
    assert http.calls == []


@pytest.mark.asyncio
async def test_apply_fails_closed_without_token() -> None:
    http = WriteRecordingHttp()
    adapter = YandexMetrikaAdapter("https://api-metrika.yandex.net", "", http=http)  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="not configured"):
        await adapter.apply_write("create_goal", 7, _goal())
    assert http.calls == []


@pytest.mark.asyncio
async def test_apply_surfaces_provider_error_envelope() -> None:
    http = WriteRecordingHttp(force_response={"errors": [{"error_type": "invalid_parameter"}]})
    adapter = _adapter(http)

    with pytest.raises(ProviderError, match="error envelope"):
        await adapter.apply_write("create_goal", 7, _goal())


@pytest.mark.asyncio
async def test_readback_reads_single_goal_and_list() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    single = await adapter.readback(7, goal_id=555)
    assert single["goal"]["id"] == 555
    assert http.calls[-1]["method"] == "GET"
    assert http.calls[-1]["url"].endswith("/management/v1/counter/7/goal/555")

    listed = await adapter.readback(7)
    assert listed["goals"][0]["id"] == 555
    assert http.calls[-1]["method"] == "GET"
    assert http.calls[-1]["url"].endswith("/management/v1/counter/7/goals")


@pytest.mark.asyncio
async def test_readback_validates_ids() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    with pytest.raises(ProviderError, match="counter_id"):
        await adapter.readback(0)
    with pytest.raises(ProviderError, match="goal_id"):
        await adapter.readback(7, goal_id=0)


@pytest.mark.asyncio
async def test_create_then_readback_confirms_goal() -> None:
    http = WriteRecordingHttp()
    adapter = _adapter(http)

    applied = await adapter.apply_write("create_goal", 7, _goal())
    confirmed = await adapter.readback(7, goal_id=applied["goal_id"])

    assert confirmed["goal"]["id"] == applied["goal_id"]
