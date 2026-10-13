from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from zai_yandex.adapters.audience import PROVIDER, SEGMENT_OPERATIONS, create_digest
from zai_yandex.transport import ProviderError, ProviderHttpError

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
_TRANSITIONAL_STATUSES = frozenset({"uploaded", "is_processed", "is_updated"})
_TERMINAL_FAILURE_STATUSES = frozenset({"processing_failed", "few_data"})
_KNOWN_STATUSES = _TRANSITIONAL_STATUSES | _TERMINAL_FAILURE_STATUSES | {"processed"}


def _contains_requested(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_requested(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _contains_requested(got, wanted) for got, wanted in zip(actual, expected, strict=True)
        )
    return actual == expected


class AudienceActions:
    def __init__(self, config: Any, store: Any, registry: Any, clean: Callable[..., Any]) -> None:
        self.config, self.store, self.registry, self.clean = config, store, registry, clean

    @staticmethod
    def validate_key(value: str) -> str:
        if not isinstance(value, str) or _KEY.fullmatch(value) is None:
            raise ValueError("idempotency_key must contain 8..128 safe characters")
        return value

    @staticmethod
    def validate_confirmation(value: str, digest: str) -> None:
        if not isinstance(value, str) or value != digest:
            raise PermissionError("confirmation_hash must match the immutable validated payload")

    async def _readback(self, operation: str, object_id: int) -> dict[str, Any]:
        adapter = self.registry.yandex_audience()
        if operation in SEGMENT_OPERATIONS:
            return await self.registry.call(
                self.registry.runtime.state.get().actor,
                PROVIDER,
                "readback_segment",
                lambda: adapter.get_segment(object_id),
            )
        return await self.registry.call(
            self.registry.runtime.state.get().actor,
            PROVIDER,
            "readback_pixel",
            lambda: adapter.get_pixel(object_id),
        )

    @staticmethod
    def _object(operation: str, response: dict[str, Any]) -> dict[str, Any] | None:
        resource = "segment" if operation in SEGMENT_OPERATIONS else "pixel"
        item = response.get(resource)
        return item if isinstance(item, dict) else None

    async def _reconcile_record(
        self,
        principal: str,
        operation: str,
        payload: dict[str, Any],
        digest: str,
        key: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        if record["request_hash"] != digest or record["tool"] != operation:
            raise PermissionError("idempotency key belongs to a different Audience create")
        if record["status"] in {"applied", "failed"}:
            result = record["result"]
            return {**result, "readback_fresh": False} if isinstance(result, dict) else result
        checkpoint = record.get("result") or {}
        object_id = checkpoint.get("object_id") if isinstance(checkpoint, dict) else None
        if isinstance(object_id, bool) or not isinstance(object_id, int) or object_id <= 0:
            return {
                "status": "pending_unknown",
                "provider": PROVIDER,
                "operation": operation,
                "request_hash": digest,
                "idempotency_key": key,
                "retryable": False,
                "reconciliation_required": True,
                "post_retried": False,
            }
        try:
            readback = await self._readback(operation, object_id)
        except ProviderError:
            return {
                "status": "pending_reconciliation",
                "provider": PROVIDER,
                "operation": operation,
                "object_id": object_id,
                "request_hash": digest,
                "idempotency_key": key,
                "retryable": False,
                "reconciliation_required": True,
                "post_retried": False,
            }
        safe_readback = self.clean(readback, durable=True)
        item = self._object(operation, safe_readback)
        if item is None or not _contains_requested(item, payload):
            return {
                "status": "pending_reconciliation",
                "provider": PROVIDER,
                "operation": operation,
                "object_id": object_id,
                "request_hash": digest,
                "idempotency_key": key,
                "readback_match": False,
                "retryable": False,
                "reconciliation_required": True,
                "post_retried": False,
            }
        provider_status = item.get("status") if operation in SEGMENT_OPERATIONS else None
        if operation in SEGMENT_OPERATIONS and provider_status not in _KNOWN_STATUSES:
            return {
                "status": "pending_reconciliation",
                "provider": PROVIDER,
                "operation": operation,
                "object_id": object_id,
                "request_hash": digest,
                "idempotency_key": key,
                "readback_match": True,
                "provider_status": provider_status,
                "reason_code": "audience_status_unrecognized",
                "retryable": False,
                "reconciliation_required": True,
                "post_retried": False,
            }
        failed = provider_status in _TERMINAL_FAILURE_STATUSES
        result = {
            "status": "failed" if failed else "applied",
            "provider": PROVIDER,
            "operation": operation,
            "object_id": object_id,
            "request_hash": digest,
            "idempotency_key": key,
            "readback_match": True,
            "provider_status": provider_status,
            "ready": provider_status == "processed" if operation in SEGMENT_OPERATIONS else None,
            "processing": provider_status in _TRANSITIONAL_STATUSES,
            "terminal_failure": provider_status in _TERMINAL_FAILURE_STATUSES,
            "created": True,
            "readback_fresh": True,
            "readback": safe_readback,
        }
        await self.store.provider_write_settle(
            principal, PROVIDER, key, status="failed" if failed else "applied", result=result
        )
        return result

    async def apply(
        self,
        principal: str,
        operation: str,
        payload: dict[str, Any],
        confirmation_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not self.config.yandex_audience_write_enabled:
            raise PermissionError("Yandex Audience write runtime is not enabled")
        key = self.validate_key(idempotency_key)
        digest, normalized = create_digest(operation, payload)
        self.validate_confirmation(confirmation_hash, digest)
        await self.registry.ensure_enabled(PROVIDER)
        fresh, existing = await self.store.provider_create_reserve(
            principal, PROVIDER, key, tool=operation, request_hash=digest
        )
        if not fresh:
            assert existing is not None
            return await self._reconcile_record(principal, operation, normalized, digest, key, existing)
        if not await self.store.provider_write_checkpoint(
            principal,
            PROVIDER,
            key,
            digest,
            {"stage": "dispatching", "post_retried": False},
        ):
            raise PermissionError("Audience write reservation was lost before dispatch")
        adapter = self.registry.yandex_audience()
        try:
            receipt = await self.registry.call(
                principal, PROVIDER, operation, lambda: adapter.create(operation, normalized)
            )
        except ProviderHttpError as exc:
            if 400 <= exc.status_code < 500 and exc.status_code not in {408, 409, 429}:
                result = {
                    "status": "failed",
                    "provider": PROVIDER,
                    "operation": operation,
                    "request_hash": digest,
                    "idempotency_key": key,
                    "http_status": exc.status_code,
                    "retryable": False,
                    "post_retried": False,
                }
                await self.store.provider_write_settle(
                    principal, PROVIDER, key, status="failed", result=result
                )
                raise
            return await self._reconcile_record(
                principal,
                operation,
                normalized,
                digest,
                key,
                {"request_hash": digest, "tool": operation, "status": "pending", "result": None},
            )
        except ProviderError:
            return await self._reconcile_record(
                principal,
                operation,
                normalized,
                digest,
                key,
                {"request_hash": digest, "tool": operation, "status": "pending", "result": None},
            )
        safe_receipt = self.clean(receipt, durable=True)
        item = self._object(operation, safe_receipt)
        object_id = item.get("id") if item else None
        if isinstance(object_id, bool) or not isinstance(object_id, int) or object_id <= 0:
            return await self._reconcile_record(
                principal,
                operation,
                normalized,
                digest,
                key,
                {"request_hash": digest, "tool": operation, "status": "pending", "result": None},
            )
        checkpoint = {
            "stage": "receipt_saved",
            "object_id": object_id,
            "receipt": safe_receipt,
            "post_retried": False,
        }
        if not await self.store.provider_write_checkpoint(principal, PROVIDER, key, digest, checkpoint):
            return await self._reconcile_record(
                principal,
                operation,
                normalized,
                digest,
                key,
                {"request_hash": digest, "tool": operation, "status": "pending", "result": None},
            )
        return await self._reconcile_record(
            principal,
            operation,
            normalized,
            digest,
            key,
            {"request_hash": digest, "tool": operation, "status": "pending", "result": checkpoint},
        )

    async def reconcile(
        self, principal: str, operation: str, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        key = self.validate_key(idempotency_key)
        digest, normalized = create_digest(operation, payload)
        await self.registry.ensure_enabled(PROVIDER)
        record = await self.store.provider_write_lookup(principal, PROVIDER, key)
        if record is None:
            raise PermissionError("no matching Audience create for this principal and key")
        return await self._reconcile_record(principal, operation, normalized, digest, key, record)
