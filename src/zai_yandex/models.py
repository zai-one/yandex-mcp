from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class JobRecord:
    job_id: UUID
    principal_id: UUID
    provider: str
    operation: str
    payload: dict[str, Any]
    status: str = "queued"
    idempotency_key: str | None = None
    approval_id: UUID | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    next_attempt_at: datetime | None = None
    attempts: int = 0
    request_hash: str | None = None
    submission_outcome_unknown: bool = False


@dataclass(slots=True)
class ApprovalRecord:
    approval_id: UUID
    principal_id: UUID
    provider: str
    operation: str
    request_hash: str
    estimated_cost: float
    budget_limit: float
    status: str = "prepared"
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=30))
    budget_unlimited: bool = False
    acceptance_type: str | None = None
    acceptance_origin: str | None = None
    standing_policy_id: str | None = None
