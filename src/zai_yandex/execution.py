"""Provider-specific Search job step; the host retains the durable job owner."""

from __future__ import annotations

from typing import Any

from zai_yandex.adapters.search import split_operation
from zai_yandex.transport import (
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientHttpError,
    ProviderTransportError,
)


async def execute_search_job(job: Any, registry: Any) -> tuple[str, dict[str, Any] | None, str | None]:
    """Original platform Yandex branch from 3cba360; no local state/worker creation."""
    adapter = registry.yandex_search()
    if job.result and job.result.get("operation_id"):
        operation_id = str(job.result["operation_id"])
        operation = await registry.read(
            job.principal_id,
            "yandex_search",
            "operation",
            {"operation_id": operation_id},
            lambda: adapter.operation(operation_id),
        )
        if operation.get("done"):
            return "completed", split_operation(operation), None
        return "waiting", {"operation_id": job.result["operation_id"]}, None
    try:
        response = await registry.call(
            job.principal_id, "yandex_search", "serp_submit", lambda: adapter.submit_serp(**job.payload)
        )
    except (ProviderResponseError, ProviderTimeoutError, ProviderTransientHttpError, ProviderTransportError):
        return "failed", {"submission_outcome_unknown": True}, "submission_outcome_unknown"
    submitted_operation_id = response.get("id")
    if not submitted_operation_id:
        return "failed", None, "missing_operation_id"
    return "waiting", {"operation_id": submitted_operation_id}, None
