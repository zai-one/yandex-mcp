from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import Recorder, config
from fastmcp import Client
from fastmcp.exceptions import ToolError

from zai_yandex.jobs import JobService
from zai_yandex.models import ApprovalRecord
from zai_yandex.runtime import Runtime
from zai_yandex.server import create_server
from zai_yandex.state import StateStore
from zai_yandex.transport import ProviderRateLimited, ProviderTimeoutError
from zai_yandex.worker import SearchWorker


def paid_config(path, **kwargs):
    return replace(
        config(path),
        yandex_search_max_cost_per_approval=1,
        principal_monthly_cost_limit=kwargs.pop("principal_monthly_cost_limit", 2),
        account_monthly_cost_limit=kwargs.pop("account_monthly_cost_limit", 2),
        **kwargs,
    )


async def prepared(store, actor="alice", query="fixture"):
    draft = await JobService(store).prepare_yandex_serp(
        actor,
        query,
        "225",
        "FORMAT_XML",
        store.config.principal_monthly_cost_limit,
        store.config.yandex_search_max_cost_per_approval,
    )
    assert draft["can_accept"]
    return draft


async def enqueue(store, draft, actor="alice", query="fixture", key="search-example"):
    return await JobService(store).enqueue_yandex_serp(
        actor, query, "225", "FORMAT_XML", UUID(draft["approval_id"]), key
    )


async def approved_job(store, actor="alice", query="fixture", key="search-example"):
    draft = await prepared(store, actor, query)
    assert store.accept(UUID(draft["approval_id"]), principal=actor, request_hash=draft["request_hash"])
    return await enqueue(store, draft, actor, query, key)


def expedite(settings):
    with sqlite3.connect(settings.state_path) as db:
        db.execute("UPDATE jobs SET next_attempt=0")


async def test_approval_requires_exact_acceptance_and_atomic_single_consume(tmp_path):
    store = StateStore(paid_config(tmp_path))
    draft = await prepared(store)
    with pytest.raises(PermissionError):
        await enqueue(store, draft)
    assert not store.accept(UUID(draft["approval_id"]), principal="bob", request_hash=draft["request_hash"])
    assert not store.accept(UUID(draft["approval_id"]), principal="alice", request_hash="wrong")
    assert store.accept(UUID(draft["approval_id"]), principal="alice", request_hash=draft["request_hash"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(asyncio.run, enqueue(StateStore(store.config), draft)) for _ in range(2)]
        jobs = [f.result() for f in futures]
    assert jobs[0].job_id == jobs[1].job_id
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT SUM(amount) FROM cost_reservations").fetchone()[0] == 1_000_000
        assert db.execute("SELECT status FROM approvals").fetchone()[0] == "consumed"


async def test_idempotency_bound_to_payload_actor_approval_and_account(tmp_path):
    settings = paid_config(tmp_path)
    store = StateStore(settings)
    draft = await prepared(store)
    assert store.accept(UUID(draft["approval_id"]), principal="alice", request_hash=draft["request_hash"])
    job = await enqueue(store, draft)
    assert (await enqueue(StateStore(settings), draft)).job_id == job.job_id
    for actor, query, key in (
        ("bob", "fixture", "search-example"),
        ("alice", "different", "search-example"),
        ("alice", "fixture", "different-key"),
    ):
        with pytest.raises(PermissionError):
            await enqueue(store, draft, actor, query, key)
    other = StateStore(replace(settings, account_id="other"))
    assert await other.get_job(job.job_id, "alice") is None
    assert await store.get_job(job.job_id, "bob") is None
    with pytest.raises(PermissionError):
        await enqueue(other, draft)


async def test_account_budget_atomic_across_principals_and_retains_rejected_approval(tmp_path):
    settings = paid_config(tmp_path, account_monthly_cost_limit=1)
    store = StateStore(settings)
    await approved_job(store, "alice")
    draft = await prepared(store, "bob")
    assert store.accept(UUID(draft["approval_id"]), principal="bob", request_hash=draft["request_hash"])
    with pytest.raises(PermissionError, match="budget"):
        await enqueue(store, draft, "bob")
    assert (await store.get_approval(UUID(draft["approval_id"]))).status == "accepted"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM cost_reservations").fetchone()[0] == 1


async def test_expiry_and_reduced_server_budget_are_checked_again_at_submission(tmp_path):
    settings = paid_config(tmp_path)
    store = StateStore(settings)
    draft = await prepared(store)
    approval = UUID(draft["approval_id"])
    assert store.accept(approval, principal="alice", request_hash=draft["request_hash"])
    with pytest.raises(PermissionError):
        await enqueue(StateStore(replace(settings, account_monthly_cost_limit=0)), draft)
    with store.connect() as db:
        db.execute("UPDATE approvals SET expires=0")
    with pytest.raises(PermissionError):
        await enqueue(store, draft)


async def test_search_worker_submit_then_restart_and_poll_preserves_owner(tmp_path):
    settings, recorder = paid_config(tmp_path), Recorder()
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    assert await SearchWorker(runtime).run_once()
    submitted = await runtime.store.get_job(job.job_id, "alice")
    assert submitted.status == "waiting" and submitted.result == {"operation_id": "operation-1"}
    expedite(settings)
    second = Runtime(settings, "stdio", recorder.factory)
    assert await SearchWorker(second).run_once()
    completed = await second.store.get_job(job.job_id, "alice")
    assert completed.status == "completed"
    assert completed.result["normalized"]["raw_sha256"]
    assert "rawData" not in completed.result["raw_provenance"]["response"]
    assert [call[0] for call in recorder.calls] == ["POST", "GET"]
    assert {call[3] for call in recorder.calls} == {"alice"}


async def test_search_crash_after_dispatch_is_never_reposted(tmp_path):
    settings, recorder = paid_config(tmp_path), Recorder()
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    claimed, owner = runtime.store.claim_job()
    assert claimed.job_id == job.job_id
    runtime.store.dispatch_intent(job.job_id, owner)
    # Simulated process death: time passes beyond the exact persisted worker lease.
    with runtime.store.connect() as db:
        db.execute("UPDATE jobs SET lease_until=0")
    assert not await SearchWorker(Runtime(settings, "stdio", recorder.factory)).run_once()
    failed = await runtime.store.get_job(job.job_id, "alice")
    assert failed.status == "failed" and failed.error_code == "submission_outcome_unknown"
    assert failed.result == {"submission_outcome_unknown": True}
    assert not recorder.calls
    with runtime.store.connect() as db:
        assert (
            db.execute("SELECT SUM(amount) FROM cost_reservations WHERE released=0").fetchone()[0]
            == 1_000_000
        )


async def test_worker_submit_timeout_retains_cost_and_no_retry(tmp_path):
    def fail(*args):
        raise ProviderTimeoutError("synthetic timeout")

    settings, recorder = paid_config(tmp_path), Recorder(fail)
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    assert await SearchWorker(runtime).run_once()
    assert not await SearchWorker(Runtime(settings, "stdio", recorder.factory)).run_once()
    failed = await runtime.store.get_job(job.job_id, "alice")
    assert failed.submission_outcome_unknown and len(recorder.calls) == 1


@pytest.mark.parametrize("change", ["new_month_exhausted", "server_cap_disabled"])
async def test_queued_job_rechecks_budget_before_later_actual_dispatch(tmp_path, change):
    settings, recorder = paid_config(tmp_path, account_monthly_cost_limit=1), Recorder()
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    if change == "new_month_exhausted":
        with runtime.store.connect() as db:
            db.execute("UPDATE cost_reservations SET month='2000-01'")
        assert await runtime.store.reserve_cost(
            "bob", 1, 2, provider="yandex_search", operation="wordstat_get_top"
        )
    else:
        settings = replace(settings, yandex_search_max_cost_per_approval=0)
    await SearchWorker(Runtime(settings, "stdio", recorder.factory)).run_once()
    assert not recorder.calls
    waiting = await runtime.store.get_job(job.job_id, "alice")
    assert waiting.status == "waiting"


async def test_old_month_reservation_is_rebooked_once_when_current_budget_available(tmp_path):
    settings, recorder = paid_config(tmp_path), Recorder()
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    with runtime.store.connect() as db:
        db.execute("UPDATE cost_reservations SET month='2000-01'")
    await SearchWorker(runtime).run_once()
    with runtime.store.connect() as db:
        row = db.execute(
            "SELECT month,amount FROM cost_reservations WHERE id=?", (str(job.job_id),)
        ).fetchone()
        assert tuple(row) == (datetime.now(UTC).strftime("%Y-%m"), 1_000_000)
    assert len(recorder.calls) == 1


async def test_provider_operation_error_is_preserved_in_terminal_result(tmp_path):
    settings, recorder = paid_config(tmp_path), Recorder()
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    await SearchWorker(runtime).run_once()
    recorder.handler = lambda *args: {"done": True, "error": {"code": 7, "message": "fixture rejection"}}
    expedite(settings)
    await SearchWorker(runtime).run_once()
    result = await runtime.store.get_job(job.job_id, "alice")
    # Baseline completed means polling is terminal; upstream error remains explicit.
    assert result.status == "completed"
    assert result.result["raw_provenance"]["error"]["code"] == 7


async def test_poll_timeout_retains_operation_id_and_resumes_without_resubmit(tmp_path):
    settings, recorder = paid_config(tmp_path), Recorder()
    runtime = Runtime(settings, "stdio", recorder.factory)
    job = await approved_job(runtime.store)
    await SearchWorker(runtime).run_once()

    def timeout(*args):
        raise ProviderTimeoutError("synthetic poll timeout")

    recorder.handler = timeout
    expedite(settings)
    await SearchWorker(runtime).run_once()
    waiting = await runtime.store.get_job(job.job_id, "alice")
    assert waiting.status == "waiting" and waiting.result == {"operation_id": "operation-1"}
    recorder.handler = recorder.default
    expedite(settings)
    await SearchWorker(Runtime(settings, "stdio", recorder.factory)).run_once()
    assert [call[0] for call in recorder.calls] == ["POST", "GET", "GET"]


async def test_concurrent_workers_claim_one_job_and_stale_owner_cannot_settle(tmp_path):
    settings = paid_config(tmp_path)
    store = StateStore(settings)
    job = await approved_job(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: StateStore(settings).claim_job(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert not store.settle_job(job.job_id, "wrong-owner", status="failed", result=None)
    assert (await store.get_job(job.job_id, "alice")).status == "queued"


async def test_mcp_job_ownership_and_queued_results_are_not_exposed(tmp_path):
    settings = paid_config(tmp_path)
    store = StateStore(settings)
    job = await approved_job(store, settings.principal_id)
    async with Client(create_server(settings, transport="stdio", http_factory=Recorder().factory)) as client:
        result = (await client.call_tool("yandex_serp_get_result", {"job_id": str(job.job_id)})).data
        assert result["status"] == "queued" and "result" not in result
    other = replace(settings, principal_id="other")
    async with Client(create_server(other, transport="stdio", http_factory=Recorder().factory)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("yandex_serp_status", {"job_id": str(job.job_id)})


@pytest.mark.parametrize("failure,released", [(None, 0), ("timeout", 0), ("quota", 1)])
async def test_wordstat_reserves_actual_cost_and_only_releases_known_uncharged(tmp_path, failure, released):
    def handler(*args):
        if failure == "timeout":
            raise ProviderTimeoutError("synthetic")
        if failure == "quota":
            raise ProviderRateLimited("synthetic", retry_after_seconds=60)
        return {"totalCount": 0, "results": []}

    settings = paid_config(
        tmp_path,
        yandex_wordstat_live_enabled=True,
        yandex_wordstat_cost_per_call=1,
        yandex_wordstat_max_cost_per_call=1,
    )
    recorder = Recorder(handler)
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        if failure:
            with pytest.raises(ToolError):
                await client.call_tool("yandex_wordstat_get_top", {"phrase": "fixture", "regions": ["225"]})
        else:
            result = (
                await client.call_tool("yandex_wordstat_get_top", {"phrase": "fixture", "regions": ["225"]})
            ).data
            assert result["normalized"]["total_count"] == 0
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT amount,released FROM cost_reservations").fetchone() == (1_000_000, released)
    assert len(recorder.calls) == 1 and not recorder.calls[0][4]


async def test_wordstat_initial_admission_denial_refunds_only_its_own_reservation(tmp_path):
    settings = paid_config(
        tmp_path,
        yandex_wordstat_live_enabled=True,
        yandex_wordstat_cost_per_call=1,
        yandex_wordstat_max_cost_per_call=1,
        request_rate_limit=1,
        principal_rate_limit=1,
    )
    recorder = Recorder()
    async with Client(create_server(settings, transport="stdio", http_factory=recorder.factory)) as client:
        await client.call_tool("yandex_wordstat_get_top", {"phrase": "fixture", "regions": ["225"]})
        with pytest.raises(ToolError):
            await client.call_tool("yandex_wordstat_get_top", {"phrase": "fixture", "regions": ["225"]})
    with sqlite3.connect(settings.state_path) as db:
        assert db.execute("SELECT released FROM cost_reservations ORDER BY rowid").fetchall() == [(0,), (1,)]
    assert len(recorder.calls) == 1


async def test_write_key_namespaces_do_not_collide_between_provider_or_account(tmp_path):
    settings = config(tmp_path)
    store = StateStore(settings)
    for candidate, provider in (
        (store, "yandex_direct"),
        (store, "yandex_metrika"),
        (StateStore(replace(settings, account_id="other")), "yandex_direct"),
    ):
        fresh, _ = await candidate.provider_write_reserve(
            "alice", provider, "same-key", tool="test", request_hash="hash"
        )
        assert fresh
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM provider_writes").fetchone()[0] == 3


async def test_webmaster_admission_atomic_approval_hash_and_pending_state(tmp_path):
    store = StateStore(config(tmp_path))
    approval = ApprovalRecord(
        uuid4(),
        "alice",
        "yandex_webmaster",
        "recrawl",
        "digest",
        0,
        0,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        budget_unlimited=True,
    )
    await store.create_approval(approval)
    assert store.accept(approval.approval_id, principal="alice", request_hash="digest")
    with pytest.raises(PermissionError):
        await store.webmaster_write_admit(
            "alice",
            "webmaster-key",
            approval_id=approval.approval_id,
            operation="recrawl",
            request_hash="other",
        )
    fresh, _ = await store.webmaster_write_admit(
        "alice", "webmaster-key", approval_id=approval.approval_id, operation="recrawl", request_hash="digest"
    )
    assert fresh
    assert (await store.get_approval(approval.approval_id)).status == "consumed"
    with pytest.raises(PermissionError):
        await store.webmaster_write_admit(
            "alice", "new-key", approval_id=approval.approval_id, operation="recrawl", request_hash="digest"
        )
    assert (
        await StateStore(store.config).provider_write_lookup("alice", "yandex_webmaster", "webmaster-key")
    )["status"] == "pending"
