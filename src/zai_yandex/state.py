"""Account-bound durable approvals, write reconciliation and paid Search jobs.

SQLite transactions are deliberately synchronous: there is no suspension between
checking a gate and persisting its reservation. No upstream I/O runs in a transaction.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from zai_yandex.config import PROVIDERS, ServiceConfig
from zai_yandex.models import ApprovalRecord, JobRecord
from zai_yandex.transport import request_hash as hash_request


def units(value: float) -> int:
    amount = Decimal(str(value)) * 1_000_000
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
        raise ValueError("finite nonnegative cost with at most six decimals required")
    return int(amount)


class BudgetDenied(PermissionError):
    """A queued job has no permission to spend under current local budget policy."""


class StateStore:
    def __init__(self, config: ServiceConfig):
        self.config, self.account, self.path = config, config.account_id, config.state_path
        self.reservation: ContextVar[str | None] = ContextVar("yandex_cost_reservation", default=None)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS approvals(
                    account TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
                    provider TEXT NOT NULL, operation TEXT NOT NULL, digest TEXT NOT NULL,
                    cost INTEGER NOT NULL, budget INTEGER NOT NULL, expires REAL NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY(account, id));
                CREATE TABLE IF NOT EXISTS state_events(
                    account TEXT NOT NULL, object_id TEXT NOT NULL, event TEXT NOT NULL,
                    origin TEXT NOT NULL, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cost_reservations(
                    account TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
                    provider TEXT NOT NULL, operation TEXT NOT NULL, month TEXT NOT NULL,
                    amount INTEGER NOT NULL, released INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account, id));
                CREATE TABLE IF NOT EXISTS provider_writes(
                    account TEXT NOT NULL, actor TEXT NOT NULL, provider TEXT NOT NULL,
                    key TEXT NOT NULL, tool TEXT NOT NULL, digest TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, PRIMARY KEY(account, actor, provider, key));
                CREATE TABLE IF NOT EXISTS jobs(
                    account TEXT NOT NULL, id TEXT NOT NULL, actor TEXT NOT NULL,
                    provider TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL,
                    digest TEXT NOT NULL, idempotency_key TEXT NOT NULL, approval_id TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, error_code TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL, next_attempt REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'queued',
                    owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(account, id), UNIQUE(account, actor, idempotency_key),
                    UNIQUE(account, approval_id));
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _event(self, db: sqlite3.Connection, identifier: str, event: str, origin: str) -> None:
        db.execute(
            "INSERT INTO state_events VALUES(?,?,?,?,?)",
            (self.account, identifier, event, origin, time.time()),
        )

    async def create_approval(self, approval: ApprovalRecord) -> None:
        if approval.status != "prepared" or approval.provider not in PROVIDERS:
            raise PermissionError("only unaccepted Yandex drafts may be created")
        with self.connect() as db:
            db.execute(
                "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,'prepared')",
                (
                    self.account,
                    str(approval.approval_id),
                    str(approval.principal_id),
                    approval.provider,
                    approval.operation,
                    approval.request_hash,
                    units(approval.estimated_cost),
                    units(approval.budget_limit),
                    approval.expires_at.timestamp(),
                ),
            )
            self._event(db, str(approval.approval_id), "prepared", "mcp-prepare")

    @staticmethod
    def _approval(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            UUID(row["id"]),
            row["actor"],
            row["provider"],
            row["operation"],
            row["digest"],
            row["cost"] / 1_000_000,
            row["budget"] / 1_000_000,
            row["status"],
            datetime.fromtimestamp(row["expires"], UTC),
            row["cost"] == 0,
        )

    async def get_approval(self, approval_id: UUID) -> ApprovalRecord | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=?", (self.account, str(approval_id))
            ).fetchone()
        return self._approval(row) if row else None

    def _paid_gate(self, row: sqlite3.Row) -> bool:
        return (
            row["provider"] == "yandex_search"
            and row["operation"] == "serp_submit"
            and 0 < row["cost"] <= row["budget"]
            and row["cost"] <= units(self.config.yandex_search_max_cost_per_approval)
            and row["cost"] <= units(self.config.principal_monthly_cost_limit)
            and row["cost"] <= units(self.config.account_monthly_cost_limit)
        )

    def accept(self, approval_id: UUID, *, principal: str, request_hash: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=? AND actor=? "
                "AND digest=? AND status='prepared' AND expires>?",
                (self.account, str(approval_id), principal, request_hash, time.time()),
            ).fetchone()
            if row is None or (row["cost"] > 0 and not self._paid_gate(row)):
                return False
            db.execute(
                "UPDATE approvals SET status='accepted' WHERE account=? AND id=?",
                (self.account, str(approval_id)),
            )
            self._event(db, str(approval_id), "accepted", "local-operator-cli")
        return True

    def _consume(self, db: sqlite3.Connection, approval_id: str) -> bool:
        changed = db.execute(
            "UPDATE approvals SET status='consumed' WHERE account=? AND id=? "
            "AND status='accepted' AND expires>?",
            (self.account, approval_id, time.time()),
        ).rowcount
        if changed:
            self._event(db, approval_id, "consumed", "mcp-apply")
        return changed == 1

    async def consume_approval(self, approval_id: UUID) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._consume(db, str(approval_id))

    def _reserve_cost(
        self,
        db: sqlite3.Connection,
        actor: str,
        amount: int,
        limit: int,
        provider: str,
        operation: str,
        identifier: str,
    ) -> bool:
        # The month belongs to the reservation; releasing across midnight/month-end
        # must never free someone else's new-month reservation.
        month = datetime.now(UTC).strftime("%Y-%m")
        limit = min(limit, units(self.config.principal_monthly_cost_limit))
        account_limit = units(self.config.account_monthly_cost_limit)
        total, own = db.execute(
            "SELECT COALESCE(SUM(amount),0), "
            "COALESCE(SUM(CASE WHEN actor=? THEN amount ELSE 0 END),0) "
            "FROM cost_reservations WHERE account=? AND month=? AND released=0",
            (actor, self.account, month),
        ).fetchone()
        if amount <= 0 or own + amount > limit or total + amount > account_limit:
            return False
        db.execute(
            "INSERT INTO cost_reservations VALUES(?,?,?,?,?,?,?,0)",
            (self.account, identifier, actor, provider, operation, month, amount),
        )
        self._event(db, identifier, "cost_reserved", operation)
        return True

    async def reserve_cost(
        self,
        principal: Any,
        cost: float,
        limit: float,
        *,
        unlimited: bool = False,
        provider: str,
        operation: str,
    ) -> bool:
        identifier = uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            admitted = self._reserve_cost(
                db, str(principal), units(cost), units(limit), provider, operation, identifier
            )
        self.reservation.set(identifier if admitted else None)
        return admitted

    async def release_reserved_cost(
        self, principal: Any, cost: float, *, provider: str, operation: str
    ) -> None:
        identifier = self.reservation.get()
        if identifier is None:
            return
        with self.connect() as db:
            changed = db.execute(
                "UPDATE cost_reservations SET released=1 WHERE account=? AND id=? "
                "AND actor=? AND amount=? AND provider=? AND operation=? AND released=0",
                (self.account, identifier, str(principal), units(cost), provider, operation),
            ).rowcount
            if changed:
                self._event(db, identifier, "cost_released", operation)
        self.reservation.set(None)

    def _lookup_write(
        self, db: sqlite3.Connection, principal: Any, provider: str, key: str
    ) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT * FROM provider_writes WHERE account=? AND actor=? AND provider=? AND key=?",
            (self.account, str(principal), provider, key),
        ).fetchone()
        if row is None:
            return None
        return {
            "request_hash": row["digest"],
            "status": row["status"],
            "tool": row["tool"],
            "result": json.loads(row["result"]) if row["result"] is not None else None,
        }

    async def provider_write_lookup(self, principal: Any, provider: str, key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._lookup_write(db, principal, provider, key)

    def _insert_write(
        self, db: sqlite3.Connection, principal: Any, provider: str, key: str, tool: str, digest: str
    ) -> None:
        if not 1 <= len(key) <= 128:
            raise ValueError("idempotency key must contain 1..128 characters")
        db.execute(
            "INSERT INTO provider_writes VALUES(?,?,?,?,?,?,'pending',NULL)",
            (self.account, str(principal), provider, key, tool, digest),
        )
        self._event(db, hash_request([str(principal), provider, key]), "write_reserved", tool)

    async def provider_write_reserve(
        self, principal: Any, provider: str, key: str, *, tool: str, request_hash: str
    ) -> tuple[bool, dict[str, Any] | None]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._lookup_write(db, principal, provider, key)
            if existing:
                return False, existing
            self._insert_write(db, principal, provider, key, tool, request_hash)
        return True, None

    async def provider_create_reserve(
        self, principal: Any, provider: str, key: str, *, tool: str, request_hash: str
    ) -> tuple[bool, dict[str, Any] | None]:
        """Reserve one create and anti-join both its key and immutable payload hash."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._lookup_write(db, principal, provider, key)
            if existing:
                return False, existing
            same_payload = db.execute(
                "SELECT 1 FROM provider_writes WHERE account=? AND provider=? AND digest=?",
                (self.account, provider, request_hash),
            ).fetchone()
            if same_payload is not None:
                raise PermissionError("the same create payload is already reserved under another key")
            self._insert_write(db, principal, provider, key, tool, request_hash)
        return True, None

    async def provider_write_settle(
        self, principal: Any, provider: str, key: str, *, status: str, result: dict[str, Any] | None
    ) -> None:
        if status not in {"pending", "applied", "failed"}:
            raise ValueError("invalid write settlement")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE provider_writes SET status=?,result=? WHERE account=? AND actor=? "
                "AND provider=? AND key=? AND status='pending'",
                (status, json.dumps(result), self.account, str(principal), provider, key),
            ).rowcount
            if changed:
                self._event(db, hash_request([str(principal), provider, key]), "write_" + status, provider)

    async def provider_write_checkpoint(
        self, principal: Any, provider: str, key: str, digest: str, checkpoint: dict[str, Any]
    ) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "UPDATE provider_writes SET result=? WHERE account=? AND actor=? "
                    "AND provider=? AND key=? AND digest=? AND status='pending'",
                    (json.dumps(checkpoint), self.account, str(principal), provider, key, digest),
                ).rowcount
                == 1
            )

    async def webmaster_write_admit(
        self, principal: Any, key: str, *, approval_id: UUID, operation: str, request_hash: str
    ) -> tuple[bool, dict[str, Any] | None]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._lookup_write(db, principal, "yandex_webmaster", key)
            if existing:
                return False, existing
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=? AND actor=? "
                "AND provider='yandex_webmaster' AND operation=? AND digest=?",
                (self.account, str(approval_id), str(principal), operation, request_hash),
            ).fetchone()
            if row is None or not self._consume(db, str(approval_id)):
                raise PermissionError("matching accepted unexpired approval required")
            self._insert_write(db, principal, "yandex_webmaster", key, operation, request_hash)
        return True, None

    @staticmethod
    def _job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            UUID(row["id"]),
            row["actor"],
            row["provider"],
            row["operation"],
            json.loads(row["payload"]),
            row["status"],
            row["idempotency_key"],
            UUID(row["approval_id"]),
            json.loads(row["result"]) if row["result"] else None,
            row["error_code"],
            datetime.fromtimestamp(row["created"], UTC),
            datetime.fromtimestamp(row["updated"], UTC),
            datetime.fromtimestamp(row["next_attempt"], UTC),
            row["attempts"],
            row["digest"],
            row["error_code"] == "submission_outcome_unknown",
        )

    async def get_job(self, job_id: UUID, principal: Any) -> JobRecord | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE account=? AND id=? AND actor=?",
                (self.account, str(job_id), str(principal)),
            ).fetchone()
        return self._job(row) if row else None

    async def admit_paid_job(
        self, job: JobRecord, *, approval_operation: str, request_hash: str
    ) -> JobRecord:
        if job.provider != "yandex_search" or job.operation != "serp_submit" or not job.approval_id:
            raise PermissionError("only approved Search submissions may be queued")
        if request_hash != hash_request(job.payload) or not job.idempotency_key:
            raise ValueError("exact payload hash and idempotency key required")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM jobs WHERE account=? AND actor=? AND (idempotency_key=? OR approval_id=?)",
                (self.account, str(job.principal_id), job.idempotency_key, str(job.approval_id)),
            ).fetchall()
            if existing:
                if len(existing) != 1 or (
                    existing[0]["digest"],
                    existing[0]["idempotency_key"],
                    existing[0]["approval_id"],
                ) != (request_hash, job.idempotency_key, str(job.approval_id)):
                    raise PermissionError("approval or idempotency key belongs to another request")
                return self._job(existing[0])
            row = db.execute(
                "SELECT * FROM approvals WHERE account=? AND id=? AND actor=? AND "
                "provider=? AND operation=? AND digest=? AND status='accepted' AND expires>?",
                (
                    self.account,
                    str(job.approval_id),
                    str(job.principal_id),
                    job.provider,
                    approval_operation,
                    request_hash,
                    now,
                ),
            ).fetchone()
            if row is None or not self._paid_gate(row):
                raise PermissionError("matching accepted affordable approval required")
            if not self._reserve_cost(
                db,
                str(job.principal_id),
                row["cost"],
                row["budget"],
                job.provider,
                job.operation,
                str(job.job_id),
            ):
                raise PermissionError("monthly budget exhausted")
            if not self._consume(db, str(job.approval_id)):
                raise PermissionError("approval already consumed")
            db.execute(
                "INSERT INTO jobs(account,id,actor,provider,operation,payload,digest,idempotency_key,"
                "approval_id,status,created,updated,next_attempt) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
                (
                    self.account,
                    str(job.job_id),
                    str(job.principal_id),
                    job.provider,
                    job.operation,
                    json.dumps(job.payload),
                    request_hash,
                    job.idempotency_key,
                    str(job.approval_id),
                    now,
                    now,
                    now,
                ),
            )
            self._event(db, str(job.job_id), "job_queued", "mcp-submit")
            saved = db.execute(
                "SELECT * FROM jobs WHERE account=? AND id=?", (self.account, str(job.job_id))
            ).fetchone()
        return self._job(saved)

    def claim_job(self) -> tuple[JobRecord, str] | None:
        now, owner = time.time(), uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A crashed submission may already have been charged. Never dispatch it again.
            interrupted = db.execute(
                "SELECT id FROM jobs WHERE account=? AND stage='dispatching' "
                "AND status IN ('queued','waiting') AND lease_until<=?",
                (self.account, now),
            ).fetchall()
            for row in interrupted:
                db.execute(
                    "UPDATE jobs SET status='failed',error_code='submission_outcome_unknown',"
                    "result=?,updated=?,owner=NULL,lease_until=0 WHERE account=? AND id=?",
                    (json.dumps({"submission_outcome_unknown": True}), now, self.account, row["id"]),
                )
                self._event(db, row["id"], "submission_outcome_unknown", "worker-recovery")
            row = db.execute(
                "SELECT * FROM jobs WHERE account=? AND status IN ('queued','waiting') "
                "AND lease_until<=? AND next_attempt<=? ORDER BY created,id LIMIT 1",
                (self.account, now, now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET owner=?,lease_until=?,attempts=attempts+1 WHERE account=? AND id=?",
                (owner, now + 90, self.account, row["id"]),
            )
            return self._job(row), owner

    def dispatch_intent(self, job_id: UUID, owner: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT a.*,r.month,r.amount FROM jobs j JOIN approvals a "
                "ON a.account=j.account AND a.id=j.approval_id "
                "JOIN cost_reservations r ON r.account=j.account AND r.id=j.id "
                "WHERE j.account=? AND j.id=? AND j.owner=? AND j.lease_until>? "
                "AND j.stage='queued' AND r.released=0 AND a.status='consumed'",
                (self.account, str(job_id), owner, time.time()),
            ).fetchone()
            if row is None:
                raise PermissionError("exclusive reserved undispatched job required")
            if not self._paid_gate(row):
                raise BudgetDenied("current server budget denies dispatch")
            month = datetime.now(UTC).strftime("%Y-%m")
            total, own = db.execute(
                "SELECT COALESCE(SUM(amount),0),"
                "COALESCE(SUM(CASE WHEN actor=? THEN amount ELSE 0 END),0) "
                "FROM cost_reservations WHERE account=? AND month=? AND released=0",
                (row["actor"], self.account, month),
            ).fetchone()
            extra = row["amount"] if row["month"] != month else 0
            if total + extra > units(self.config.account_monthly_cost_limit) or own + extra > min(
                row["budget"], units(self.config.principal_monthly_cost_limit)
            ):
                raise BudgetDenied("current month budget denies dispatch")
            if extra:
                db.execute(
                    "UPDATE cost_reservations SET month=? WHERE account=? AND id=?",
                    (month, self.account, str(job_id)),
                )
                self._event(db, str(job_id), "cost_rebooked_current_month", "worker")
            changed = db.execute(
                "UPDATE jobs SET stage='dispatching',updated=? WHERE account=? AND id=? "
                "AND owner=? AND lease_until>? AND stage='queued'",
                (time.time(), self.account, str(job_id), owner, time.time()),
            ).rowcount
            if changed != 1:
                raise PermissionError("exclusive undispatched job claim required")
            self._event(db, str(job_id), "dispatch_intent", "worker")

    def settle_job(
        self,
        job_id: UUID,
        owner: str,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: str | None = None,
        delay: int = 5,
        stage: str | None = None,
    ) -> bool:
        if status not in {"waiting", "completed", "failed"} or stage not in {None, "queued", "polling"}:
            raise ValueError("invalid job settlement")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE jobs SET status=?,result=?,error_code=?,updated=?,next_attempt=?,"
                "stage=COALESCE(?,stage),owner=NULL,lease_until=0 WHERE account=? AND id=? "
                "AND owner=? AND lease_until>?",
                (
                    status,
                    json.dumps(result),
                    error,
                    time.time(),
                    time.time() + max(1, min(delay, 3600)),
                    stage,
                    self.account,
                    str(job_id),
                    owner,
                    time.time(),
                ),
            ).rowcount
            if changed:
                self._event(db, str(job_id), "job_" + status, "worker")
            return changed == 1
