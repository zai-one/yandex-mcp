"""Standalone policy binding. Gateway bindings use their central owner instead."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import UUID, uuid4

from fastmcp.server.dependencies import get_access_token

from zai_yandex.adapters.audience import BASE_URL as AUDIENCE_BASE_URL
from zai_yandex.adapters.audience import YandexAudienceAdapter
from zai_yandex.adapters.direct import YandexDirectAdapter
from zai_yandex.adapters.metrika import YandexMetrikaAdapter
from zai_yandex.adapters.search import YandexSearchAdapter
from zai_yandex.adapters.webmaster import YandexWebmasterAdapter
from zai_yandex.admission import PolicyStore
from zai_yandex.audience_actions import AudienceActions
from zai_yandex.coalescing import AsyncSingleFlight
from zai_yandex.config import LIMITS, ServiceConfig
from zai_yandex.errors import SafeToolError, safe_provider_error
from zai_yandex.jobs import JobService
from zai_yandex.models import ApprovalRecord, JobRecord
from zai_yandex.sanitizer import DURABLE_SANITIZER_LIMITS, sanitize_provider_response
from zai_yandex.state import StateStore
from zai_yandex.transport import (
    JsonHttpClient,
    ProviderAdmissionDenied,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeoutError,
    canonical_json,
    request_hash,
)
from zai_yandex.webmaster_actions import WebmasterActions

T = TypeVar("T")
OPERATION_TIMEOUT_SECONDS = 75.0


class RequestOwnedFlight(AsyncSingleFlight):
    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        # Do not detach shielded single-flight tasks from their actor, lease or deadline.
        return await factory()


class AdmittedHttpClient(JsonHttpClient):
    def __init__(self, policy: PolicyStore, execution_id: str, actor: str):
        super().__init__(timeout=60)
        self.policy, self.execution_id, self.actor = policy, execution_id, actor
        self.admitted_count = 0

    async def _admit_attempt(self, attempt: int) -> None:
        try:
            self.policy.admit(self.execution_id, self.actor, self.admitted_count + 1)
        except ProviderAdmissionDenied as exc:
            if self.admitted_count == 0:
                # The original Wordstat reservation flow releases an initial
                # admission denial, but retains a denial after a real attempt.
                raise ProviderRateLimited(
                    "initial admission denied", retry_after_seconds=exc.retry_after_seconds
                ) from None
            raise
        self.admitted_count += 1


@dataclass(frozen=True)
class Access:
    principal_id: str
    monthly_cost_limit: float
    monthly_cost_unlimited: bool = False


@dataclass
class CallState:
    execution_id: str
    actor: str
    provider: str
    policy: PolicyStore
    http: Any
    adapters: dict[str, Any] = field(default_factory=dict)


class Registry:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    async def ensure_enabled(self, provider: str) -> None:
        if not self.runtime.config.enabled(provider):
            raise PermissionError("provider credentials are not configured")

    def _adapter(self, provider: str) -> Any:
        state, config = self.runtime.state.get(), self.runtime.config
        if state.provider != provider:
            raise PermissionError("provider does not match this execution")
        if provider not in state.adapters:
            kwargs = {"http": state.http, "coalescer": RequestOwnedFlight()}
            match provider:
                case "yandex_direct":
                    adapter = YandexDirectAdapter(
                        "https://api.direct.yandex.com/json/v5",
                        config.direct_token,
                        config.direct_client_login,
                        **kwargs,
                    )
                case "yandex_metrika":
                    adapter = YandexMetrikaAdapter(
                        "https://api-metrika.yandex.net", config.metrika_token, **kwargs
                    )
                case "yandex_search":
                    adapter = YandexSearchAdapter(
                        "https://searchapi.api.cloud.yandex.net/v2",
                        "https://operation.api.cloud.yandex.net/operations",
                        config.search_api_key,
                        config.search_folder_id,
                        **kwargs,
                    )
                case "yandex_webmaster":
                    adapter = YandexWebmasterAdapter(
                        "https://api.webmaster.yandex.net/v4", config.webmaster_token, **kwargs
                    )
                case "yandex_audience":
                    adapter = YandexAudienceAdapter(AUDIENCE_BASE_URL, config.audience_token, **kwargs)
                case _:
                    raise ValueError("unsupported provider")
            state.adapters[provider] = adapter
        return state.adapters[provider]

    def yandex_direct(self) -> YandexDirectAdapter:
        return self._adapter("yandex_direct")

    def yandex_metrika(self) -> YandexMetrikaAdapter:
        return self._adapter("yandex_metrika")

    def yandex_search(self) -> YandexSearchAdapter:
        return self._adapter("yandex_search")

    def yandex_webmaster(self) -> YandexWebmasterAdapter:
        return self._adapter("yandex_webmaster")

    def yandex_audience(self) -> YandexAudienceAdapter:
        return self._adapter("yandex_audience")

    async def call(self, actor: Any, provider: str, operation: str, factory: Callable[[], Awaitable[T]]) -> T:
        state = self.runtime.state.get()
        if str(actor) != state.actor or provider != state.provider:
            raise PermissionError("execution identity mismatch")
        await self.ensure_enabled(provider)
        try:
            return await factory()
        except Exception as exc:
            self.runtime.cooldown(exc)
            raise

    async def read(
        self,
        actor: Any,
        provider: str,
        operation: str,
        arguments: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        validate: Callable[[], Any] | None = None,
    ) -> T:
        if validate:
            validate()
        return await self.call(actor, provider, operation, factory)


class Runtime:
    approval_record = ApprovalRecord

    def __init__(self, config: ServiceConfig, transport: str, http_factory: Callable[..., Any] | None = None):
        self.config = self.settings = config
        self.transport, self.http_factory = transport, http_factory or AdmittedHttpClient
        self.store = StateStore(config)
        self.policies = {
            provider: PolicyStore(
                config.state_path,
                canonical_json([config.account_id, provider]),
                min(config.request_rate_limit, rate),
                min(config.principal_rate_limit, rate),
                min(config.max_concurrency, concurrent),
            )
            for provider, (rate, concurrent) in LIMITS.items()
        }
        self.state: ContextVar[CallState] = ContextVar("yandex_call")
        self.registry = Registry(self)
        self.jobs = JobService(self.store)
        self.webmaster_actions = WebmasterActions(config, self.store, self.registry)
        self.audience_actions = AudienceActions(config, self.store, self.registry, self.clean)

    def require_scopes(self, *scopes: str) -> Callable[[Any], bool]:
        def check(context: Any) -> bool:
            available = (
                self.config.local_scopes
                if self.transport == "stdio"
                else (context.token.scopes if context.token else [])
            )
            return set(scopes) <= set(available)

        return check

    def identity(self) -> str:
        if self.transport == "stdio":
            return self.config.principal_id
        token = get_access_token()
        claims = token.claims if token else {}
        actor, expires = claims.get("sub"), claims.get("exp")
        if (
            not isinstance(actor, str)
            or not 1 <= len(actor) <= 256
            or claims.get("account_id") != self.config.account_id
            or not isinstance(expires, (int, float))
            or isinstance(expires, bool)
            or not math.isfinite(expires)
            or expires <= time.time()
        ):
            raise PermissionError("account-bound authenticated identity required")
        return actor

    def current_access(self) -> Access:
        return Access(self.state.get().actor, self.config.principal_monthly_cost_limit)

    def clean(self, value: Any, *, durable: bool = False) -> Any:
        def literal(item: Any) -> Any:
            if isinstance(item, str):
                for credential in self.config.secrets:
                    if credential:
                        item = item.replace(credential, "***redacted***")
                return item
            if isinstance(item, list):
                return [literal(child) for child in item]
            if isinstance(item, dict):
                return {literal(key): literal(child) for key, child in item.items()}
            return item

        sanitized = (
            sanitize_provider_response(value, limits=DURABLE_SANITIZER_LIMITS)
            if durable
            else sanitize_provider_response(value)
        )
        return literal(sanitized)

    @staticmethod
    def safe_error(provider: str, exc: Exception) -> SafeToolError:
        return safe_provider_error(provider, exc)

    @staticmethod
    def provider_for(tool: str) -> str:
        for prefix, provider in (
            ("direct_", "yandex_direct"),
            ("metrika_", "yandex_metrika"),
            ("webmaster_", "yandex_webmaster"),
            ("audience_", "yandex_audience"),
            ("yandex_", "yandex_search"),
        ):
            if tool.startswith(prefix):
                return provider
        raise ValueError("unknown tool provider")

    async def execute(
        self, tool: str, arguments: dict[str, Any], factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        provider = self.provider_for(tool)
        try:
            return await self.run_owned(provider, self.identity(), tool, arguments, factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, SafeToolError):
                raise
            raise safe_provider_error(provider, exc) from None

    async def run_owned(
        self,
        provider: str,
        actor: str,
        tool: str,
        arguments: dict[str, Any],
        factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        execution_id, policy = uuid4().hex, self.policies[provider]
        policy.begin(execution_id, uuid4().hex, actor, request_hash({"tool": tool, "arguments": arguments}))
        context_token = None
        try:
            http = self.http_factory(policy, execution_id, actor)
            context_token = self.state.set(CallState(execution_id, actor, provider, policy, http))
            async with asyncio.timeout(OPERATION_TIMEOUT_SECONDS):
                result = await factory()
            policy.finish(execution_id, "success")
            return self.clean(result, durable=True)
        except asyncio.CancelledError:
            policy.finish(execution_id, "cancelled")
            raise
        except Exception as exc:
            policy.finish(execution_id, "error")
            if isinstance(exc, TimeoutError):
                raise ProviderTimeoutError("operation deadline exceeded") from None
            raise
        finally:
            if context_token is not None:
                self.state.reset(context_token)

    def cooldown(self, exc: Exception) -> None:
        if isinstance(exc, ProviderRateLimited) and not isinstance(exc, ProviderAdmissionDenied):
            self.state.get().policy.cooldown(exc.retry_after_seconds or 60)

    async def call(self, provider: str, operation: str, factory: Callable[[], Awaitable[T]]) -> T:
        return await self.registry.call(self.state.get().actor, provider, operation, factory)

    async def read(
        self,
        provider: str,
        operation: str,
        arguments: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        validate: Callable[[], Any] | None = None,
    ) -> T:
        try:
            return self.clean(
                await self.registry.read(
                    self.state.get().actor, provider, operation, arguments, factory, validate=validate
                )
            )
        except (ProviderError, PermissionError, ValueError) as exc:
            raise safe_provider_error(provider, exc) from None

    async def write(
        self,
        provider: str,
        tool: str,
        arguments: dict[str, Any],
        idempotency_key: str | None,
        *,
        enabled: bool,
        validate: Callable[[], Any],
        apply_fn: Callable[[], Awaitable[Any]],
        readback_fn: Callable[[Any], Awaitable[Any]] | None = None,
    ) -> Any:
        try:
            if not enabled:
                raise ProviderError("write runtime is not enabled")
            validate()
            digest = request_hash({"provider": provider, "tool": tool, "arguments": arguments})
            key = (idempotency_key.strip() if isinstance(idempotency_key, str) else "") or digest
            actor = self.state.get().actor
            fresh, cached = await self.store.provider_write_reserve(
                actor, provider, key, tool=tool, request_hash=digest
            )
            if not fresh:
                if cached is None or cached["request_hash"] != digest:
                    raise ProviderError("idempotency key belongs to another write")
                if cached["status"] == "applied":
                    return cached["result"]
                raise ProviderError("write requires reconciliation before any further dispatch")
            result = await self.call(provider, tool, apply_fn)
            readback = await readback_fn(result) if readback_fn else None
            payload = self.clean(
                {
                    "applied": True,
                    "provider": provider,
                    "tool": tool,
                    "result": result,
                    "readback": readback,
                    "idempotency_key": key,
                },
                durable=True,
            )
            await self.store.provider_write_settle(actor, provider, key, status="applied", result=payload)
            return payload
        except (ProviderError, PermissionError, ValueError) as exc:
            self.cooldown(exc)
            raise safe_provider_error(provider, exc) from None

    def job_view(self, job: JobRecord, *, include_result: bool = False) -> dict[str, Any]:
        result = {
            "job_id": str(job.job_id),
            "provider": job.provider,
            "operation": job.operation,
            "status": job.status,
            "error_code": job.error_code,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }
        if include_result:
            result["result"] = self.clean(job.result, durable=True)
        return result

    async def owned_job(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        job = await self.store.get_job(UUID(job_id), self.state.get().actor)
        if job is None:
            raise ValueError("job not found")
        return self.job_view(
            job, include_result=include_result and job.status in {"completed", "failed", "cancelled"}
        )
