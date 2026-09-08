from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from zai_yandex.models import ApprovalRecord, JobRecord
from zai_yandex.transport import request_hash


class JobService:
    def __init__(self, store: Any, *, approval_record: Any = ApprovalRecord, job_record: Any = JobRecord):
        self.store = store
        self.approval_record, self.job_record = approval_record, job_record

    async def enqueue_yandex_serp(
        self,
        principal_id: UUID,
        query: str,
        region: str,
        response_format: str,
        approval_id: UUID,
        idempotency_key: str,
    ) -> JobRecord:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key is required and must be at most 128 characters")
        payload = {"query": query, "region": region, "response_format": response_format}
        return await self.store.admit_paid_job(
            self.job_record(
                uuid4(),
                principal_id,
                "yandex_search",
                "serp_submit",
                payload,
                idempotency_key=idempotency_key,
                approval_id=approval_id,
            ),
            approval_operation="serp_submit",
            request_hash=request_hash(payload),
        )

    async def prepare_yandex_serp(
        self,
        principal_id: UUID,
        query: str,
        region: str,
        response_format: str,
        principal_budget_limit: float,
        server_budget_limit: float,
        principal_budget_unlimited: bool = False,
    ) -> dict[str, Any]:
        if response_format not in {"FORMAT_XML", "FORMAT_HTML"}:
            raise ValueError("response_format must be FORMAT_XML or FORMAT_HTML")
        payload = {"query": query, "region": region, "response_format": response_format}
        estimated_cost = 1.0
        within_server_limit = server_budget_limit > 0 and estimated_cost <= server_budget_limit
        budget_unlimited = principal_budget_unlimited and within_server_limit
        budget_limit = (
            principal_budget_limit
            if principal_budget_limit > 0 and within_server_limit and not budget_unlimited
            else 0.0
        )
        approval = self.approval_record(
            uuid4(),
            principal_id,
            "yandex_search",
            "serp_submit",
            request_hash(payload),
            estimated_cost,
            budget_limit,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            budget_unlimited=budget_unlimited,
        )
        await self.store.create_approval(approval)
        return {
            "request_hash": approval.request_hash,
            "estimated_cost_units": approval.estimated_cost,
            "approval_id": str(approval.approval_id),
            "approval_status": approval.status,
            "approval_expires_at": approval.expires_at.isoformat(),
            "budget_limit": budget_limit,
            "server_max_cost_per_approval": server_budget_limit,
            "budget_unlimited": budget_unlimited,
            "can_accept": within_server_limit
            and (budget_unlimited or budget_limit >= approval.estimated_cost),
            "live_call": False,
        }
