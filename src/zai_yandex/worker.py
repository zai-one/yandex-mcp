"""Durable Search worker. Run alongside the MCP server using the same state/config."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import Any

from zai_yandex.adapters.search import split_operation
from zai_yandex.config import ServiceConfig
from zai_yandex.onboarding import load_config
from zai_yandex.runtime import Runtime
from zai_yandex.state import BudgetDenied
from zai_yandex.transport import (
    ProviderAdmissionDenied,
    ProviderError,
    ProviderRateLimited,
)


class SearchWorker:
    def __init__(self, runtime: Runtime):
        self.runtime, self.store = runtime, runtime.store

    async def run_once(self) -> bool:
        if not self.runtime.config.enabled("yandex_search"):
            return False
        claim = self.store.claim_job()
        if claim is None:
            return False
        job, owner = claim
        operation_id = (job.result or {}).get("operation_id")
        dispatch_started = False

        def settle(
            status: str,
            result: dict[str, Any] | None,
            *,
            error: str | None = None,
            stage: str | None = None,
            delay: int = 5,
        ) -> None:
            if not self.store.settle_job(
                job.job_id,
                owner,
                status=status,
                result=self.runtime.clean(result, durable=True),
                error=error,
                stage=stage,
                delay=delay,
            ):
                raise PermissionError("job claim expired before settlement")

        async def execute() -> None:
            nonlocal dispatch_started
            adapter = self.runtime.registry.yandex_search()
            if operation_id:
                if not isinstance(operation_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,256}", operation_id
                ):
                    settle("failed", job.result, error="invalid_operation_id")
                    return
                response = await self.runtime.call(
                    "yandex_search", "operation", lambda: adapter.operation(operation_id)
                )
                if response.get("done"):
                    settle("completed", split_operation(response), stage="polling")
                else:
                    settle("waiting", {"operation_id": operation_id}, stage="polling")
                return
            # Durable intent precedes the paid POST. A crash anywhere after here
            # is reconciled as unknown, never as a fresh submission.
            self.store.dispatch_intent(job.job_id, owner)
            dispatch_started = True
            try:
                response = await self.runtime.call(
                    "yandex_search", "serp_submit", lambda: adapter.submit_serp(**job.payload)
                )
            except ProviderAdmissionDenied:
                # Exact per-attempt admission is before the first socket operation.
                # A no-attempt denial can safely return to the queue.
                if self.runtime.state.get().http.admitted_count == 0:
                    dispatch_started = False
                raise
            identifier = response.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", identifier):
                settle("failed", {"submission_outcome_unknown": True}, error="submission_outcome_unknown")
                return
            settle("waiting", {"operation_id": identifier}, stage="polling")

        try:
            await self.runtime.run_owned(
                "yandex_search",
                str(job.principal_id),
                "worker:" + job.operation,
                {"job_id": str(job.job_id), "request_hash": job.request_hash},
                execute,
            )
        except asyncio.CancelledError:
            # Keep the durable dispatch marker on cancellation/crash. Polling with
            # a known operation ID can be reclaimed once its lease expires.
            raise
        except Exception as exc:
            if isinstance(exc, BudgetDenied):
                settle("waiting", None, error="budget_denied", stage="queued", delay=60)
            elif operation_id:
                settle(
                    "waiting",
                    job.result,
                    error="provider_transient_error",
                    stage="polling",
                    delay=getattr(exc, "retry_after_seconds", None) or 5,
                )
            elif isinstance(exc, ProviderRateLimited) and not isinstance(exc, ProviderAdmissionDenied):
                # Explicit provider 429 is the baseline's uncharged deferral path.
                settle(
                    "waiting",
                    None,
                    error="provider_rate_limited",
                    stage="queued",
                    delay=exc.retry_after_seconds or 60,
                )
            elif dispatch_started:
                settle("failed", {"submission_outcome_unknown": True}, error="submission_outcome_unknown")
            else:
                settle(
                    "waiting",
                    None,
                    error="provider_rate_limited" if isinstance(exc, ProviderError) else "provider_disabled",
                    stage="queued",
                    delay=60,
                )
        return True


async def run(once: bool) -> None:
    worker = SearchWorker(Runtime(ServiceConfig.from_env(), "stdio"))
    while True:
        processed = await worker.run_once()
        if once:
            print(json.dumps({"processed": processed}))
            return
        await asyncio.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", help="Operator JSON settings")
    args = parser.parse_args()
    try:
        load_config(args.config)
        asyncio.run(run(args.once))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"configuration/state error: {type(exc).__name__}; check local configuration\n")


if __name__ == "__main__":
    main()
