"""MCP tool annotations (behaviour hints for clients); server-side gates stay authoritative.

Every registered tool must be classified here: registration fails closed for an
unclassified tool so a new mutating tool cannot ship with read-only defaults.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

from zai_yandex.coverage_tools import DIRECT_EXTRA_TOOLS, WEBMASTER_EXTRA_TOOLS
from zai_yandex.management import MANAGEMENT_TOOLS
from zai_yandex.maps_geosearch import MAPS_TOOLS

_MANAGE = MANAGEMENT_TOOLS.values()

# Provider reads: no provider or local business state changes.
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Provider reads that spend the server-side cost budget on every call.
PAID_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
# Reads of this server's own durable state (jobs, offline validation).
LOCAL_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
# Opens a local, expiring approval or draft; never calls a provider write.
PREPARE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
# Reconciles local uncertain-write state by reading the provider; never re-sends a write.
RECONCILE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
# Additive provider writes keyed by an idempotency key.
ADDITIVE_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
# Provider writes that may update, suspend, archive or delete existing objects.
DESTRUCTIVE_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)

_GROUPS: tuple[tuple[ToolAnnotations, tuple[str, ...]], ...] = (
    (
        READ,
        (
            # Geosearch: licensed per request but nothing changes; no server-side cost ledger.
            *MAPS_TOOLS,
            "direct_list_campaigns",
            "direct_get_campaign",
            "direct_get_inventory",
            "direct_get_statistics",
            "direct_get_goal_statistics",
            "direct_list_strategies",
            "direct_keyword_forecast",
            "metrika_list_counters",
            "metrika_list_goals",
            "metrika_get_statistics",
            "metrika_report",
            "webmaster_get_user",
            "webmaster_list_hosts",
            "webmaster_get_host",
            "webmaster_get_verification",
            "webmaster_get_summary",
            "webmaster_get_diagnostics",
            "webmaster_get_query_history",
            "webmaster_list_popular_queries",
            *DIRECT_EXTRA_TOOLS,
            *WEBMASTER_EXTRA_TOOLS,
            *(names[0] for names in _MANAGE),
            "webmaster_get_indexing_history",
            "webmaster_list_indexing_samples",
            "webmaster_list_search_urls",
            "webmaster_get_search_urls_history",
            "webmaster_get_search_events_history",
            "webmaster_list_search_events",
            "webmaster_get_sqi_history",
            "webmaster_list_important_urls",
            "webmaster_get_important_url_history",
            "webmaster_list_sitemaps",
            "webmaster_get_sitemap",
            "webmaster_list_user_sitemaps",
            "webmaster_list_external_links",
            "webmaster_get_external_links_history",
            "webmaster_list_broken_links",
            "webmaster_get_broken_links_history",
            "webmaster_list_recrawl_queue",
            "webmaster_get_recrawl_quota",
            "webmaster_get_recrawl_task",
            "audience_list_segments",
            "audience_get_segment",
            "audience_list_pixels",
            "audience_get_pixel",
            "direct_list_agency_clients",
            "metrika_logs_evaluate",
            "metrika_logs_list",
            "metrika_logs_get",
            "metrika_logs_download_part",
            "metrika_list_offline_conversion_uploads",
            "metrika_get_offline_conversion_upload",
            # Public GitHub release lookup; suggests an update, never installs.
            "yandex_check_update",
        ),
    ),
    (
        PAID_READ,
        (
            "yandex_wordstat_get_top",
            "yandex_wordstat_dynamics",
            "yandex_wordstat_regions",
            "yandex_wordstat_regions_tree",
        ),
    ),
    (
        LOCAL_READ,
        (
            "yandex_serp_status",
            "yandex_serp_get_result",
            "audience_validate_create",
            "metrika_validate_offline_conversions",
            "yandex_list_accounts",
            "yandex_auth_status",
        ),
    ),
    (
        PREPARE,
        (
            "direct_prepare_changes",
            *(names[1] for names in _MANAGE),
            "direct_prepare_write",
            "webmaster_prepare_recrawl",
            "webmaster_prepare_sitemap",
            "yandex_serp_prepare",
            "yandex_auth_start",
        ),
    ),
    (
        RECONCILE,
        ("webmaster_reconcile_action", "audience_reconcile_create", *(names[3] for names in _MANAGE)),
    ),
    (
        ADDITIVE_WRITE,
        (
            "yandex_serp_submit",
            "webmaster_apply_recrawl",
            "webmaster_apply_sitemap",
            "audience_create_geo_circle",
            "audience_create_geo_polygon",
            "audience_create_pixel",
            "audience_create_pixel_viewers",
            # Spends the per-counter Logs API storage quota; keyed by idempotency key.
            "metrika_logs_create",
            # Hash-confirmed CSV upload; YANDEX_METRIKA_WRITE_ENABLED.
            "metrika_upload_offline_conversions",
            "yandex_auth_finish",
            # Enhanced SERP export; YANDEX_WEBMASTER_WRITE_ENABLED + idempotency.
            "webmaster_start_pro_export",
        ),
    ),
    (
        DESTRUCTIVE_WRITE,
        (
            "direct_write",
            "direct_apply_changes",
            "metrika_apply_goal",
            "metrika_logs_cleanup",
            *(names[2] for names in _MANAGE),
            "direct_apply_write",
        ),
    ),
)

TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    name: annotation for annotation, names in _GROUPS for name in names
}


def annotations_for(tool: str) -> ToolAnnotations:
    try:
        return TOOL_ANNOTATIONS[tool]
    except KeyError:
        raise ValueError(f"tool {tool} has no reviewed MCP annotations") from None
