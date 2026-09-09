from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from zai_yandex.adapters.direct import (
    INVENTORY_READ_SERVICES,
    YandexDirectAdapter,
    build_statistics_request,
    prepare_changes,
)
from zai_yandex.adapters.metrika import YandexMetrikaAdapter
from zai_yandex.sanitizer import DURABLE_SANITIZER_LIMITS, sanitize_provider_response
from zai_yandex.transport import ProviderAdmissionDenied, ProviderError, ProviderRateLimited, request_hash


def register_core_tools(server: Any, runtime: Any) -> None:
    registry = runtime.registry
    settings = runtime.settings
    store = runtime.store
    jobs = runtime.jobs
    ApprovalRecord = runtime.approval_record
    current_access = runtime.current_access
    require_scopes = runtime.require_scopes
    safe_provider_error = runtime.safe_error
    _provider_read = runtime.read
    _provider_call = runtime.call
    _autonomous_write = runtime.write
    _owned_job = runtime.owned_job
    _job_view = runtime.job_view

    @server.tool(auth=require_scopes("yandex_search:execute"))
    async def yandex_wordstat_get_top(
        phrase: str, regions: list[str], num_phrases: int = 10
    ) -> dict[str, Any]:
        """Get Yandex Wordstat top requests with raw provenance and normalized output."""
        return await _wordstat_call(
            "wordstat_get_top",
            lambda: registry.yandex_search().wordstat_get_top(phrase, regions, num_phrases),
        )

    async def _wordstat_call(operation, factory):
        if not settings.yandex_wordstat_live_enabled:
            raise PermissionError("live Wordstat is disabled pending a separate approved runtime switch")
        access = current_access()
        cost = settings.yandex_wordstat_cost_per_call
        within_server_limit = (
            cost > 0
            and settings.yandex_wordstat_max_cost_per_call > 0
            and cost <= settings.yandex_wordstat_max_cost_per_call
        )
        if not within_server_limit or not await store.reserve_cost(
            access.principal_id,
            cost,
            access.monthly_cost_limit,
            unlimited=access.monthly_cost_unlimited,
            provider="yandex_search",
            operation=operation,
        ):
            raise PermissionError("Wordstat budget gate denied the live request")
        try:
            return cast(
                dict[str, Any],
                sanitize_provider_response(
                    await _provider_call(
                        "yandex_search",
                        operation,
                        factory,
                    ),
                    limits=DURABLE_SANITIZER_LIMITS,
                ),
            )
        except ProviderAdmissionDenied as exc:
            # Retry admission was denied AFTER at least one real attempt: the
            # provider may have charged, keep the reservation.
            raise safe_provider_error("yandex_search", exc) from None
        except (ProviderRateLimited, PermissionError, ValueError) as exc:
            # Denied before any upstream attempt (governor admission, provider
            # disabled, validation) or provider-declared 429: nothing was
            # charged, so the reservation goes back.
            await store.release_reserved_cost(
                access.principal_id,
                cost,
                provider="yandex_search",
                operation=operation,
            )
            raise safe_provider_error("yandex_search", exc) from None
        except ProviderError as exc:
            # Timeout/5xx/transport after a real attempt: the provider may have
            # charged, keep the reservation for manual reconciliation.
            raise safe_provider_error("yandex_search", exc) from None

    async def _wordstat_report(operation, arguments):
        from zai_yandex.wordstat import request_body

        request_body(operation, arguments)  # Validate before reserving any budget.
        return await _wordstat_call(
            "wordstat_" + operation,
            lambda: registry.yandex_search().wordstat_report(operation, arguments),
        )

    @server.tool(auth=require_scopes("yandex_search:execute"))
    async def yandex_wordstat_dynamics(
        phrase: str,
        from_date: str,
        to_date: str,
        period: str = "PERIOD_MONTHLY",
        regions: list[str] | None = None,
        devices: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get Wordstat demand over time using UTC dates YYYY-MM-DD and the server budget gate."""
        return await _wordstat_report(
            "dynamics",
            dict(
                phrase=phrase,
                from_date=from_date,
                to_date=to_date,
                period=period,
                regions=regions,
                devices=devices,
            ),
        )

    @server.tool(auth=require_scopes("yandex_search:execute"))
    async def yandex_wordstat_regions(
        phrase: str,
        region: str = "REGION_ALL",
        devices: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get Wordstat demand by region for the last 30 days through the server budget gate."""
        return await _wordstat_report("regions", dict(phrase=phrase, region=region, devices=devices))

    @server.tool(auth=require_scopes("yandex_search:execute"))
    async def yandex_wordstat_regions_tree() -> dict[str, Any]:
        """Get the Wordstat region tree through Cloud credentials and the server budget gate."""
        return await _wordstat_report("getRegionsTree", {})

    @server.tool(auth=require_scopes("yandex_search:execute"))
    async def yandex_serp_prepare(
        query: str, region: str = "225", response_format: str = "FORMAT_XML"
    ) -> dict[str, Any]:
        """Prepare an asynchronous Yandex SERP request without a live provider call."""
        access = current_access()
        return await jobs.prepare_yandex_serp(
            access.principal_id,
            query,
            region,
            response_format,
            access.monthly_cost_limit,
            settings.yandex_search_max_cost_per_approval,
            access.monthly_cost_unlimited,
        )

    @server.tool(auth=require_scopes("yandex_search:execute"))
    async def yandex_serp_submit(
        query: str,
        approval_id: str,
        idempotency_key: str,
        region: str = "225",
        response_format: str = "FORMAT_XML",
    ) -> dict[str, Any]:
        """Queue an approved asynchronous Yandex SERP request."""
        await registry.ensure_enabled("yandex_search")
        access = current_access()
        job = await jobs.enqueue_yandex_serp(
            access.principal_id,
            query,
            region,
            response_format,
            UUID(approval_id),
            idempotency_key,
        )
        return _job_view(job)

    @server.tool(auth=require_scopes("yandex_search:read"))
    async def yandex_serp_status(job_id: str) -> dict[str, Any]:
        """Return durable status for an asynchronous Yandex SERP job."""
        return await _owned_job(job_id)

    @server.tool(auth=require_scopes("yandex_search:read"))
    async def yandex_serp_get_result(job_id: str) -> dict[str, Any]:
        """Return raw provenance metadata and normalized Yandex SERP output."""
        return await _owned_job(job_id, include_result=True)

    async def _direct_read(service: str, params: dict[str, Any]) -> dict[str, Any]:
        adapter = registry.yandex_direct()
        return await _provider_read(
            "yandex_direct",
            f"read:{service}",
            {"service": service, "params": params},
            lambda: adapter.read(service, params),
            validate=lambda: YandexDirectAdapter.validate_read(service, params),
        )

    async def _list_campaigns(selection_criteria: dict[str, Any] | None = None) -> dict[str, Any]:
        return await _direct_read(
            "campaigns",
            {
                "method": "get",
                "params": {
                    "SelectionCriteria": selection_criteria or {},
                    "FieldNames": ["Id", "Name", "State", "Status", "Type"],
                },
            },
        )

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_list_campaigns(selection_criteria: dict[str, Any] | None = None) -> dict[str, Any]:
        """List Direct campaigns through the strict read allowlist."""
        return await _list_campaigns(selection_criteria)

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_get_campaign(campaign_id: int) -> dict[str, Any]:
        """Get one Direct campaign by numeric id."""
        return await _list_campaigns({"Ids": [campaign_id]})

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_get_inventory(service: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run a provider-native get against the explicit Direct inventory allowlist."""
        normalized = service.strip("/").lower()
        if normalized not in INVENTORY_READ_SERVICES:
            raise ValueError("Direct inventory service is not allowed")
        return await _direct_read(normalized, {"method": "get", "params": params})

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_get_statistics(
        date_from: str,
        date_to: str,
        campaign_ids: list[int] | None = None,
        field_names: list[str] | None = None,
        report_type: str = "CUSTOM_REPORT",
    ) -> dict[str, Any]:
        """Read campaign statistics; returns completed TSV rows or safe offline queue status."""
        adapter = registry.yandex_direct()
        return await _provider_read(
            "yandex_direct",
            "statistics",
            {
                "campaign_ids": campaign_ids,
                "date_from": date_from,
                "date_to": date_to,
                "field_names": field_names,
                "report_type": report_type,
            },
            lambda: adapter.statistics(campaign_ids, date_from, date_to, field_names, report_type),
            validate=lambda: build_statistics_request(
                campaign_ids, date_from, date_to, field_names, report_type
            ),
        )

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_keyword_forecast(phrases: list[str], region_ids: list[int]) -> dict[str, Any]:
        """Forecast search-volume presence by device for phrases and regions."""
        if not phrases or len(phrases) > 10_000:
            raise ValueError("phrases must contain between 1 and 10000 items")
        if len(set(phrases)) != len(phrases):
            raise ValueError("phrases must not contain duplicates")
        if not region_ids:
            raise ValueError("region_ids must not be empty")
        return await _direct_read(
            "keywordsresearch",
            {
                "method": "hasSearchVolume",
                "params": {
                    "SelectionCriteria": {"Keywords": phrases, "RegionIds": region_ids},
                    "FieldNames": [
                        "Keyword",
                        "RegionIds",
                        "AllDevices",
                        "MobilePhones",
                        "Tablets",
                        "Desktops",
                    ],
                },
            },
        )

    @server.tool(auth=require_scopes("yandex_direct:read"))
    async def direct_prepare_changes(changes: list[dict[str, Any]]) -> dict[str, Any]:
        """Draft a Direct change set and open an unaccepted, expiring approval.

        The draft carries a diff hash and an approval_id. A human must accept the
        approval before `direct_apply_changes` can execute it.
        """
        access = current_access()
        draft = prepare_changes(changes)
        digest = draft["draft_id"]
        approval_id = uuid4()
        await store.create_approval(
            ApprovalRecord(
                approval_id,
                access.principal_id,
                "yandex_direct",
                "apply_changes",
                digest,
                0.0,
                0.0,
                "prepared",
                datetime.now(UTC) + timedelta(minutes=30),
                True,
            )
        )
        return {
            **draft,
            "approval_id": str(approval_id),
            "request_hash": digest,
            "write_tool_available": settings.yandex_direct_write_enabled,
            "next_gate": (
                "accept the approval, then call direct_apply_changes with the same "
                "changes, approval_id and an idempotency_key"
            ),
        }

    @server.tool(auth=require_scopes("yandex_direct:write"))
    async def direct_apply_changes(
        service: str,
        method: str,
        campaigns: list[dict[str, Any]],
        approval_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Execute one accepted, allowlisted Direct write (Campaigns.update) with readback.

        Fail-closed: needs the runtime flag, an accepted approval whose hash
        matches these exact changes, and a bounded allowlisted service/method.
        The approval is consumed exactly once (idempotency); after the write the
        affected campaigns are re-read to confirm the applied state.
        """
        access = current_access()
        if not settings.yandex_direct_write_enabled:
            raise safe_provider_error(
                "yandex_direct", ProviderError("Yandex Direct write runtime is not enabled")
            )
        try:
            adapter = registry.yandex_direct()
            norm_service, norm_method, items = adapter.validate_apply(service, method, campaigns)
            digest = request_hash(campaigns)
            approval = await store.get_approval(UUID(approval_id))
            if (
                approval is None
                or approval.principal_id != access.principal_id
                or approval.provider != "yandex_direct"
                or approval.operation != "apply_changes"
                or approval.request_hash != digest
            ):
                raise PermissionError("an accepted approval matching these changes is required")
            if not await store.consume_approval(UUID(approval_id)):
                raise PermissionError("approval is not accepted or was already consumed")
            result = await registry.call(
                access.principal_id,
                "yandex_direct",
                "apply_changes",
                lambda: adapter.apply_changes(norm_service, norm_method, items),
            )
            readback = await adapter.readback_campaigns([int(item["Id"]) for item in items])
            return cast(
                dict[str, Any],
                sanitize_provider_response(
                    {
                        "applied": True,
                        "service": norm_service,
                        "method": norm_method,
                        "item_count": len(items),
                        "result": result,
                        "readback": readback,
                        "approval_id": approval_id,
                        "idempotency_key": idempotency_key,
                    }
                ),
            )
        except (ProviderError, PermissionError, ValueError) as exc:
            raise safe_provider_error("yandex_direct", exc) from None

    def _direct_readback_ids(items: list[dict[str, Any]], result: Any) -> list[int]:
        ids = [int(i["Id"]) for i in items if isinstance(i, dict) and isinstance(i.get("Id"), int)]
        if ids:
            return ids
        body = result.get("result") if isinstance(result, dict) else None
        if isinstance(body, dict):
            for value in body.values():
                if isinstance(value, list):
                    found = [
                        int(row["Id"])
                        for row in value
                        if isinstance(row, dict) and isinstance(row.get("Id"), int)
                    ]
                    if found:
                        return found
        return []

    @server.tool(auth=require_scopes("yandex_direct:write"))
    async def direct_write(
        service: str,
        method: str,
        items: list[dict[str, Any]],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Autonomously apply a Direct write across campaigns/adgroups/ads/keywords/bids.

        Allowlisted service/method only (add/update/delete/suspend/resume/archive/
        moderate, bids set/setAuto). Idempotent, with a post-write readback of the
        affected objects. campaigns:update also has an approval-gated path
        (direct_apply_changes) for a stricter human gate.
        """
        adapter = registry.yandex_direct()
        norm_service = str(service).strip("/").lower()
        return cast(
            dict[str, Any],
            await _autonomous_write(
                "yandex_direct",
                f"{norm_service}.{str(method).strip()}",
                {"service": service, "method": method, "items": items},
                idempotency_key,
                enabled=settings.yandex_direct_write_enabled,
                validate=lambda: adapter.validate_apply(service, method, items),
                apply_fn=lambda: adapter.apply_changes(service, method, items),
                readback_fn=lambda result: adapter.readback(
                    norm_service, _direct_readback_ids(items, result)
                ),
            ),
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_list_counters() -> dict[str, Any]:
        """List Metrika counters through a server-side read-only credential."""
        return await _provider_read("yandex_metrika", "counters", {}, registry.yandex_metrika().counters)

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_list_goals(counter_id: str) -> dict[str, Any]:
        """List goals for one positive Metrika counter id."""
        adapter = registry.yandex_metrika()
        return await _provider_read(
            "yandex_metrika",
            "goals",
            {"counter_id": counter_id},
            lambda: adapter.goals(counter_id),
            validate=lambda: YandexMetrikaAdapter.validate_counter_id(counter_id),
        )

    @server.tool(auth=require_scopes("yandex_metrika:read"))
    async def metrika_get_statistics(params: dict[str, Any]) -> dict[str, Any]:
        """Run a bounded read-only Metrika reporting request."""
        adapter = registry.yandex_metrika()
        return await _provider_read(
            "yandex_metrika",
            "statistics",
            params,
            lambda: adapter.statistics(params),
            validate=lambda: YandexMetrikaAdapter.validate_statistics(params),
        )

    @server.tool(auth=require_scopes("yandex_metrika:write"))
    async def metrika_apply_goal(
        operation: str,
        counter_id: int,
        params: dict[str, Any] | None = None,
        goal_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Autonomously create, update or delete a Metrika goal (idempotent, readback)."""
        adapter = registry.yandex_metrika()
        payload = params or {}
        return cast(
            dict[str, Any],
            await _autonomous_write(
                "yandex_metrika",
                operation,
                {
                    "operation": operation,
                    "counter_id": counter_id,
                    "params": payload,
                    "goal_id": goal_id,
                },
                idempotency_key,
                enabled=settings.yandex_metrika_write_enabled,
                validate=lambda: adapter.validate_write(operation, counter_id, payload),
                apply_fn=lambda: adapter.apply_write(operation, counter_id, payload, goal_id),
                readback_fn=lambda result: adapter.readback(
                    counter_id, result.get("goal_id") if isinstance(result, dict) else None
                ),
            ),
        )
