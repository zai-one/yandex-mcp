"""Typed, closed Webmaster tool surface. SEO methodology belongs to AI Kit."""

from __future__ import annotations

from typing import Any, Literal, cast

from zai_yandex.sanitizer import sanitize_provider_response
from zai_yandex.transport import ProviderError
from zai_yandex.webmaster_actions import WebmasterAction

Device = Literal["ALL", "DESKTOP", "MOBILE_AND_TABLET", "MOBILE", "TABLET"]
Indicator = Literal["TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION", "AVG_CLICK_POSITION"]

WEBMASTER_READ_TOOLS = (
    "get_user",
    "list_hosts",
    "get_host",
    "get_verification",
    "get_summary",
    "get_diagnostics",
    "get_query_history",
    "list_popular_queries",
    "get_indexing_history",
    "list_indexing_samples",
    "list_search_urls",
    "get_search_urls_history",
    "list_sitemaps",
    "get_sitemap",
    "list_user_sitemaps",
    "list_external_links",
    "get_external_links_history",
    "list_broken_links",
    "get_broken_links_history",
    "list_recrawl_queue",
    "get_recrawl_quota",
    "get_recrawl_task",
    "reconcile_action",
)


def register_webmaster_tools(server: Any, runtime: Any) -> None:
    registry = runtime.registry
    actions = runtime.webmaster_actions
    require_scopes = runtime.require_scopes
    current_access = runtime.current_access
    safe_provider_error = runtime.safe_error

    async def read(method: str, **arguments: Any) -> dict[str, Any]:
        access = current_access()
        try:
            await registry.ensure_enabled("yandex_webmaster")
            adapter = registry.yandex_webmaster()
            result = await registry.read(
                access.principal_id,
                "yandex_webmaster",
                method,
                arguments,
                lambda: getattr(adapter, method)(**arguments),
            )
            return cast(dict[str, Any], sanitize_provider_response(result))
        except (ProviderError, PermissionError, ValueError) as exc:
            raise safe_provider_error("yandex_webmaster", exc) from None

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_user() -> dict[str, Any]:
        """Read the OAuth account user identifier; OAuth stays on the server."""
        return await read("user")

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_hosts(offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read one bounded page of sites accessible to the server account."""
        return await read("hosts", offset=offset, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_host(host_id: str) -> dict[str, Any]:
        """Read metadata for an exact Webmaster host identifier."""
        return await read("host", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_verification(host_id: str) -> dict[str, Any]:
        """Read ownership verification state without starting verification."""
        return await read("verification", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_summary(host_id: str) -> dict[str, Any]:
        """Read indexing and site summary; missing metrics remain missing."""
        return await read("summary", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_diagnostics(host_id: str) -> dict[str, Any]:
        """Read problems with native severity, presence state and state-change time."""
        return await read("diagnostics", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_query_history(
        host_id: str,
        date_from: str,
        date_to: str,
        query_indicator: list[Indicator] | None = None,
        device_type_indicator: Device = "ALL",
        query_id: str | None = None,
    ) -> dict[str, Any]:
        """Read at most 31 days of aggregate or selected-query indicators."""
        arguments: dict[str, Any] = {
            "host_id": host_id,
            "date_from": date_from,
            "date_to": date_to,
            "device_type_indicator": device_type_indicator,
            "query_id": query_id,
        }
        if query_indicator is not None:
            arguments["query_indicator"] = query_indicator
        return await read("query_history", **arguments)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_popular_queries(
        host_id: str,
        query_indicator: Indicator = "TOTAL_SHOWS",
        device_type_indicator: Device = "ALL",
        order_by: Indicator = "TOTAL_SHOWS",
        offset: int = 0,
        limit: int = 100,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """Read one page of popular queries; explicit periods are at most 31 days."""
        return await read(
            "popular_queries",
            host_id=host_id,
            query_indicator=query_indicator,
            device_type_indicator=device_type_indicator,
            order_by=order_by,
            offset=offset,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_indexing_history(host_id: str, date_from: str, date_to: str) -> dict[str, Any]:
        """Read at most 31 days of indexing history."""
        return await read("indexing_history", host_id=host_id, date_from=date_from, date_to=date_to)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_indexing_samples(
        host_id: str, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Read one page of indexing samples with continuation metadata."""
        return await read("indexing_samples", host_id=host_id, offset=offset, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_search_urls(host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read one page of URLs in search; a sample is not the full index."""
        return await read("search_urls", host_id=host_id, offset=offset, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_search_urls_history(host_id: str, date_from: str, date_to: str) -> dict[str, Any]:
        """Read at most 31 days of search URL count history."""
        return await read("search_urls_history", host_id=host_id, date_from=date_from, date_to=date_to)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_sitemaps(
        host_id: str,
        cursor: str | None = None,
        limit: int = 100,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Read one sitemap page using the upstream sitemap identifier cursor."""
        return await read("sitemaps", host_id=host_id, cursor=cursor, limit=limit, parent_id=parent_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_sitemap(
        host_id: str, sitemap_id: str, user_added: bool = False
    ) -> dict[str, Any]:
        """Read detected sitemap details or registration of a submitted sitemap."""
        return await read("sitemap", host_id=host_id, sitemap_id=sitemap_id, user_added=user_added)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_user_sitemaps(
        host_id: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Read one page of submitted sitemaps with an exclusive identifier cursor."""
        return await read("user_sitemaps", host_id=host_id, cursor=cursor, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_external_links(
        host_id: str, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Read one page of external link samples."""
        return await read("external_links", host_id=host_id, offset=offset, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_external_links_history(
        host_id: str,
        date_from: str,
        date_to: str,
    ) -> dict[str, Any]:
        """Read at most 31 days of external link history."""
        return await read("external_links_history", host_id=host_id, date_from=date_from, date_to=date_to)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_broken_links(host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read one page of broken internal link samples."""
        return await read("broken_links", host_id=host_id, offset=offset, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_broken_links_history(
        host_id: str,
        date_from: str,
        date_to: str,
    ) -> dict[str, Any]:
        """Read at most 31 days of broken internal link history."""
        return await read("broken_links_history", host_id=host_id, date_from=date_from, date_to=date_to)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_list_recrawl_queue(host_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Read one page of asynchronous recrawl requests."""
        return await read("recrawl_queue", host_id=host_id, offset=offset, limit=limit)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_recrawl_quota(host_id: str) -> dict[str, Any]:
        """Read the daily quota and remaining submissions."""
        return await read("recrawl_quota", host_id=host_id)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_get_recrawl_task(host_id: str, task_id: str) -> dict[str, Any]:
        """Read IN_PROGRESS/DONE/FAILED; DONE does not prove search indexing."""
        return await read("recrawl_task", host_id=host_id, task_id=task_id)

    async def govern(method: str, action: WebmasterAction, **arguments: Any) -> dict[str, Any]:
        try:
            result = await getattr(actions, method)(current_access().principal_id, action, **arguments)
            return cast(dict[str, Any], sanitize_provider_response(result))
        except (ProviderError, PermissionError, ValueError) as exc:
            raise safe_provider_error("yandex_webmaster", exc) from None

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_prepare_recrawl(host_id: str, url: str) -> dict[str, Any]:
        """Prepare exactly one URL for human approval after ownership, quota and duplicate checks."""
        return await govern("prepare", "recrawl", host_id=host_id, url=url)

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_prepare_sitemap(host_id: str, url: str) -> dict[str, Any]:
        """Prepare one sitemap registration for expiring human approval."""
        return await govern("prepare", "sitemap", host_id=host_id, url=url)

    @server.tool(auth=require_scopes("yandex_webmaster:write"))
    async def webmaster_apply_recrawl(
        host_id: str,
        url: str,
        approval_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Submit one accepted URL exactly once. Uncertain POSTs are reconciled without retransmission."""
        return await govern(
            "apply",
            "recrawl",
            host_id=host_id,
            url=url,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
        )

    @server.tool(auth=require_scopes("yandex_webmaster:write"))
    async def webmaster_apply_sitemap(
        host_id: str,
        url: str,
        approval_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Submit one accepted sitemap with a durable key and registration readback."""
        return await govern(
            "apply",
            "sitemap",
            host_id=host_id,
            url=url,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
        )

    @server.tool(auth=require_scopes("yandex_webmaster:read"))
    async def webmaster_reconcile_action(
        action: WebmasterAction,
        host_id: str,
        url: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Read and reconcile this principal's existing action. This tool cannot send a POST."""
        return await govern("reconcile", action, host_id=host_id, url=url, idempotency_key=idempotency_key)
