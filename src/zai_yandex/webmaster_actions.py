"""Governed Webmaster writes using the platform approval and durable write ledger."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from zai_yandex.adapters.webmaster import YandexWebmasterAdapter
from zai_yandex.models import ApprovalRecord
from zai_yandex.transport import ProviderError, ProviderHttpError, request_hash

WebmasterAction = Literal["recrawl", "sitemap"]
PROVIDER = "yandex_webmaster"


class WebmasterActions:
    def __init__(self, settings: Any, store: Any, registry: Any) -> None:
        self.settings = settings
        self.store = store
        self.registry = registry

    @staticmethod
    def payload(action: WebmasterAction, host_id: str, url: str) -> dict[str, str]:
        if action not in ("recrawl", "sitemap"):
            raise ValueError("unsupported Webmaster action")
        YandexWebmasterAdapter.validate_host_id(host_id)
        checked_url = YandexWebmasterAdapter.validate_url_for_host(url, host_id)
        return {"action": action, "host_id": host_id, "url": checked_url}

    async def _call(self, principal: UUID, method: str, **arguments: Any) -> dict[str, Any]:
        await self.registry.ensure_enabled(PROVIDER)
        adapter = self.registry.yandex_webmaster()
        return await self.registry.call(
            principal, PROVIDER, method, lambda: getattr(adapter, method)(**arguments)
        )

    async def _find(
        self,
        principal: UUID,
        action: WebmasterAction,
        host_id: str,
        url: str,
        *,
        active_only: bool = False,
        terminal_ids: list[str] | None = None,
        exclude_ids: set[str] | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Bounded duplicate scan; incomplete absence never authorizes a POST."""
        offset = 0
        cursor: str | None = None
        for _ in range(10):
            if action == "recrawl":
                page = await self._call(principal, "recrawl_queue", host_id=host_id, offset=offset, limit=100)
                rows = page.get("data", {}).get("tasks")
            else:
                page = await self._call(principal, "user_sitemaps", host_id=host_id, cursor=cursor, limit=100)
                rows = page.get("data", {}).get("sitemaps")
            if not isinstance(rows, list):
                return None, False
            for row in rows:
                if isinstance(row, dict) and row.get("url") == url:
                    identifier = row.get("task_id" if action == "recrawl" else "sitemap_id")
                    try:
                        if not isinstance(identifier, str):
                            return None, False
                        YandexWebmasterAdapter.validate_resource_id(identifier)
                    except ValueError:
                        return None, False
                    if action == "recrawl":
                        task_id = row.get("task_id")
                        if not isinstance(task_id, str) or not task_id:
                            return None, False
                        if exclude_ids and task_id in exclude_ids:
                            continue
                        if active_only and row.get("state") in ("DONE", "FAILED"):
                            if terminal_ids is not None:
                                terminal_ids.append(task_id)
                            continue
                        if row.get("state") not in ("IN_PROGRESS", "DONE", "FAILED"):
                            return None, False
                    return row, True
            paging = page.get("pagination") or {}
            if paging.get("complete") is True:
                return None, page.get("availability") in ("available", "empty")
            if action == "recrawl":
                following = paging.get("next_offset")
                if not isinstance(following, int) or following <= offset:
                    return None, False
                offset = following
            else:
                following = paging.get("next_cursor")
                if not isinstance(following, str) or following == cursor:
                    return None, False
                cursor = following
        return None, False

    async def _preflight(
        self, principal: UUID, action: WebmasterAction, host_id: str, url: str
    ) -> tuple[dict[str, Any] | None, list[str]]:
        await self._call(principal, "verified_host", host_id=host_id)
        terminal_ids: list[str] = []
        existing, complete = await self._find(
            principal, action, host_id, url, active_only=True, terminal_ids=terminal_ids
        )
        if existing is not None:
            return existing, terminal_ids
        if not complete:
            raise PermissionError("duplicate check is incomplete; no write is authorized")
        if action == "recrawl":
            quota = await self._call(principal, "recrawl_quota", host_id=host_id)
            remainder = quota.get("data", {}).get("quota_remainder")
            if not isinstance(remainder, int) or isinstance(remainder, bool) or remainder < 1:
                raise PermissionError("recrawl quota is exhausted or unavailable")
        return None, terminal_ids

    async def prepare(
        self, principal: UUID, action: WebmasterAction, host_id: str, url: str
    ) -> dict[str, Any]:
        payload = self.payload(action, host_id, url)
        existing, _terminal_ids = await self._preflight(principal, action, host_id, payload["url"])
        if existing is not None:
            return {"status": "already_present", "action": payload, "readback": existing}
        approval_id = uuid4()
        digest = request_hash(payload)
        expires = datetime.now(UTC) + timedelta(minutes=30)
        await self.store.create_approval(
            ApprovalRecord(
                approval_id,
                principal,
                PROVIDER,
                action,
                digest,
                0.0,
                0.0,
                "prepared",
                expires,
                True,
            )
        )
        return {
            "status": "prepared",
            "action": payload,
            "request_hash": digest,
            "approval_id": str(approval_id),
            "expires_at": expires.isoformat(),
            "write_enabled": self.settings.yandex_webmaster_write_enabled,
            "next_gate": "Human acceptance in the platform approval UI, then apply this exact action.",
        }

    async def _reconcile(
        self,
        principal: UUID,
        action: WebmasterAction,
        payload: dict[str, str],
        key: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        result = record.get("result") or {}
        if record.get("status") != "pending":
            return {**result, "replayed": True}
        await self._call(principal, "verified_host", host_id=payload["host_id"])
        receipt = result.get("receipt") or {}
        id_name = "task_id" if action == "recrawl" else "sitemap_id"
        identifier = receipt.get(id_name)
        found: dict[str, Any] | None = None
        if isinstance(identifier, str):
            arguments: dict[str, Any] = {"host_id": payload["host_id"], id_name: identifier}
            if action == "sitemap":
                arguments["user_added"] = True
            response = await self._call(
                principal, "recrawl_task" if action == "recrawl" else "sitemap", **arguments
            )
            data = response.get("data")
            if (
                isinstance(data, dict)
                and data.get("url") == payload["url"]
                and data.get(id_name) == identifier
                and (action != "recrawl" or data.get("state") in ("IN_PROGRESS", "DONE", "FAILED"))
            ):
                found = data
        elif result.get("stage") == "dispatching":
            found, _complete = await self._find(
                principal,
                action,
                payload["host_id"],
                payload["url"],
                exclude_ids=set(result.get("known_terminal_task_ids", [])),
            )
        if found is None:
            return {
                "status": "pending_reconciliation",
                "request_hash": record["request_hash"],
                "idempotency_key": key,
                "post_retried": False,
                "next_action": "Repeat reconciliation with the same key; absence is not proof of failure.",
            }
        confirmed = {
            "status": "accepted" if action == "recrawl" else "registered",
            "request_hash": record["request_hash"],
            "idempotency_key": key,
            "readback": found,
            "post_retried": False,
            "crawl_state": found.get("state") if action == "recrawl" else None,
            "indexed": None,
        }
        await self.store.provider_write_settle(principal, PROVIDER, key, status="applied", result=confirmed)
        return confirmed

    async def apply(
        self,
        principal: UUID,
        action: WebmasterAction,
        host_id: str,
        url: str,
        approval_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = self.payload(action, host_id, url)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key) is None:
            raise ValueError("idempotency_key must contain 8..128 safe characters")
        digest = request_hash(payload)
        await self.registry.ensure_enabled(PROVIDER)
        existing = await self.store.provider_write_lookup(principal, PROVIDER, idempotency_key)
        if existing is not None:
            if existing.get("request_hash") != digest:
                raise PermissionError("idempotency key belongs to a different action")
            return await self._reconcile(principal, action, payload, idempotency_key, existing)
        if not self.settings.yandex_webmaster_write_enabled:
            raise PermissionError("Webmaster write runtime is disabled")
        duplicate, terminal_ids = await self._preflight(principal, action, host_id, payload["url"])
        # Admission consumes approval and reserves the exact action in one DB transaction.
        fresh, existing = await self.store.webmaster_write_admit(
            principal,
            idempotency_key,
            approval_id=UUID(approval_id),
            operation=action,
            request_hash=digest,
        )
        if not fresh:
            if existing is None or existing.get("request_hash") != digest:
                raise PermissionError("idempotency key belongs to a different action")
            return await self._reconcile(principal, action, payload, idempotency_key, existing)
        if duplicate is not None:
            result = {"status": "already_present", "readback": duplicate, "post_retried": False}
            await self.store.provider_write_settle(
                principal, PROVIDER, idempotency_key, status="applied", result=result
            )
            return result
        checkpoint: dict[str, Any] = {
            "stage": "dispatching",
            "started_at": datetime.now(UTC).isoformat(),
            "known_terminal_task_ids": terminal_ids,
        }
        if not await self.store.provider_write_checkpoint(
            principal, PROVIDER, idempotency_key, digest, checkpoint
        ):
            raise PermissionError("write reservation was lost")
        try:
            receipt = await self._call(
                principal,
                "submit_recrawl" if action == "recrawl" else "submit_sitemap",
                host_id=host_id,
                url=payload["url"],
            )
        except ProviderHttpError as exc:
            if 400 <= exc.status_code < 500 and exc.status_code not in (408, 409, 429):
                await self.store.provider_write_settle(
                    principal,
                    PROVIDER,
                    idempotency_key,
                    status="failed",
                    result={"status": "failed", "http_status": exc.status_code},
                )
                raise
            # A conflict or uncertain response is only reconciled by reading. Never resend.
        except ProviderError:
            # Timeout, transport failure, malformed/partial receipt or 429: fail closed.
            pass
        else:
            checkpoint = {**checkpoint, "receipt": receipt}
            await self.store.provider_write_checkpoint(
                principal, PROVIDER, idempotency_key, digest, checkpoint
            )
        current = await self.store.provider_write_lookup(principal, PROVIDER, idempotency_key)
        assert current is not None
        try:
            return await self._reconcile(principal, action, payload, idempotency_key, current)
        except ProviderError:
            return {
                "status": "pending_reconciliation",
                "request_hash": digest,
                "idempotency_key": idempotency_key,
                "post_retried": False,
            }

    async def reconcile(
        self,
        principal: UUID,
        action: WebmasterAction,
        host_id: str,
        url: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = self.payload(action, host_id, url)
        await self.registry.ensure_enabled(PROVIDER)
        record = await self.store.provider_write_lookup(principal, PROVIDER, idempotency_key)
        if record is None or record.get("request_hash") != request_hash(payload):
            raise PermissionError("no matching action for this principal and key")
        return await self._reconcile(principal, action, payload, idempotency_key, record)
