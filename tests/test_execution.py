"""Host-independent original Search job step, with a minimal central-store facade."""

from types import SimpleNamespace

import pytest

from zai_yandex.execution import execute_search_job
from zai_yandex.transport import ProviderRateLimited, ProviderTimeoutError


class Registry:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = []

    def yandex_search(self):
        return self

    async def call(self, actor, provider, operation, factory):
        self.calls.append((actor, provider, operation))
        return await factory()

    async def read(self, actor, provider, operation, arguments, factory):
        return await self.call(actor, provider, operation, factory)

    async def submit_serp(self, **kwargs):
        if self.error:
            raise self.error
        return self.result

    async def operation(self, operation_id):
        return await self.submit_serp()


@pytest.mark.parametrize("response,expected", [({"id": "operation-1"}, "waiting"), ({}, "failed")])
async def test_original_submit_step(response, expected):
    registry = Registry(response)
    job = SimpleNamespace(principal_id="alice", result=None, payload={"query": "fixture"})
    status, _, _ = await execute_search_job(job, registry)
    assert status == expected
    assert registry.calls == [("alice", "yandex_search", "serp_submit")]


async def test_unknown_submit_is_reported_but_rate_limit_is_left_to_host():
    job = SimpleNamespace(principal_id="alice", result=None, payload={"query": "fixture"})
    assert await execute_search_job(job, Registry(error=ProviderTimeoutError("fixture"))) == (
        "failed",
        {"submission_outcome_unknown": True},
        "submission_outcome_unknown",
    )
    with pytest.raises(ProviderRateLimited):
        await execute_search_job(job, Registry(error=ProviderRateLimited("fixture")))


async def test_existing_operation_polls_and_never_submits():
    registry = Registry({"done": True, "error": {"code": 7}})
    job = SimpleNamespace(principal_id="alice", result={"operation_id": "existing"}, payload={})
    status, result, _ = await execute_search_job(job, registry)
    assert status == "completed" and result["raw_provenance"]["error"] == {"code": 7}
    assert registry.calls == [("alice", "yandex_search", "operation")]
