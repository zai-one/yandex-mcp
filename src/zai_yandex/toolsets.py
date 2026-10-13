"""Static toolsets and read-only mode (filtered once at registration, no meta-tools).

Env: ``YANDEX_TOOLSETS`` = ``all`` (default, backward compatible) | ``default``
(every read toolset) | comma list; ``YANDEX_TOOLS`` adds tools;
``YANDEX_DISABLED_TOOLS`` hides tools; ``YANDEX_READ_ONLY=1`` hides every tool
whose reviewed annotation is not read-only (prepare/apply/write/paid submit).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from zai_yandex.coverage_tools import DIRECT_EXTRA_TOOLS, WEBMASTER_EXTRA_TOOLS
from zai_yandex.tool_annotations import TOOL_ANNOTATIONS

TOOLSETS: dict[str, dict[str, Any]] = {
    "account": {
        "description": (
            "Update check, named accounts (yandex_list_accounts when YANDEX_ACCOUNTS_FILE is set), "
            "OAuth self-setup (start/finish write local token files; stdio only)."
        ),
        "tools": (
            "yandex_check_update",
            "yandex_list_accounts",
            "yandex_auth_status",
            "yandex_auth_start",
            "yandex_auth_finish",
        ),
    },
    "direct_read": {
        "description": "Direct campaigns, inventory (ads, groups, keywords...), strategies, forecasts.",
        "tools": (
            "direct_list_campaigns",
            "direct_get_campaign",
            "direct_get_inventory",
            "direct_list_strategies",
            "direct_keyword_forecast",
            "direct_list_agency_clients",
        ),
    },
    "direct_extra": {
        "description": "Opt-in Direct reads: client, dictionaries, changes, bids, bid modifiers, audiences, feeds, creatives, extensions, turbo pages, leads.",
        "tools": DIRECT_EXTRA_TOOLS,
    },
    "direct_reports": {
        "description": "Direct Reports service: statistics and goal statistics.",
        "tools": ("direct_get_statistics", "direct_get_goal_statistics"),
    },
    "direct_write": {
        "description": "Direct changes: prepare -> human approval -> apply; generic direct_write.",
        "tools": ("direct_prepare_changes", "direct_apply_changes", "direct_write"),
    },
    "metrika": {
        "description": "Metrika counters, goals, Reporting API and period comparison.",
        "tools": (
            "metrika_list_counters",
            "metrika_list_goals",
            "metrika_get_statistics",
            "metrika_report",
        ),
    },
    "metrika_offline": {
        "description": "Offline conversions: local CSV validation and upload status (read).",
        "tools": (
            "metrika_validate_offline_conversions",
            "metrika_list_offline_conversion_uploads",
            "metrika_get_offline_conversion_upload",
        ),
    },
    "metrika_logs": {
        "description": "Metrika Logs API: evaluate, list, status, preview download (read).",
        "tools": (
            "metrika_logs_evaluate",
            "metrika_logs_list",
            "metrika_logs_get",
            "metrika_logs_download_part",
        ),
    },
    "metrika_logs_write": {
        "description": "Create Logs API exports and clean/cancel them (quota; YANDEX_METRIKA_LOGS_ENABLED).",
        "tools": ("metrika_logs_create", "metrika_logs_cleanup"),
    },
    "metrika_write": {
        "description": "Create/update/delete Metrika goals; upload offline conversions.",
        "tools": ("metrika_apply_goal", "metrika_upload_offline_conversions"),
    },
    "webmaster": {
        "description": "Webmaster hosts, summary, SQI, diagnostics, queries, indexing, links, sitemaps.",
        "tools": tuple(
            name
            for name in TOOL_ANNOTATIONS
            if name.startswith("webmaster_")
            and TOOL_ANNOTATIONS[name].readOnlyHint
            and name not in WEBMASTER_EXTRA_TOOLS
        ),
    },
    "webmaster_extra": {
        "description": "Opt-in Webmaster reads: owners, feeds, regions, query analytics with filters.",
        "tools": WEBMASTER_EXTRA_TOOLS,
    },
    "webmaster_write": {
        "description": "Recrawl and sitemap submission: prepare -> apply -> reconcile.",
        "tools": (
            "webmaster_prepare_recrawl",
            "webmaster_apply_recrawl",
            "webmaster_prepare_sitemap",
            "webmaster_apply_sitemap",
            "webmaster_reconcile_action",
        ),
    },
    "wordstat": {
        "description": "Wordstat top requests, dynamics, regions (paid per call).",
        "tools": (
            "yandex_wordstat_get_top",
            "yandex_wordstat_dynamics",
            "yandex_wordstat_regions",
            "yandex_wordstat_regions_tree",
        ),
    },
    "search": {
        "description": "Yandex Search API SERP jobs: prepare -> submit (paid) -> status -> result.",
        "tools": (
            "yandex_serp_prepare",
            "yandex_serp_submit",
            "yandex_serp_status",
            "yandex_serp_get_result",
        ),
    },
    "audience": {
        "description": "Audience segments and pixels (read) and offline create validation.",
        "tools": (
            "audience_list_segments",
            "audience_get_segment",
            "audience_list_pixels",
            "audience_get_pixel",
            "audience_validate_create",
        ),
    },
    "audience_write": {
        "description": "Create Audience geo/pixel segments with confirmation_hash; reconcile.",
        "tools": (
            "audience_create_geo_circle",
            "audience_create_geo_polygon",
            "audience_create_pixel",
            "audience_create_pixel_viewers",
            "audience_reconcile_create",
        ),
    },
}

DEFAULT_TOOLSETS = (
    "account",
    "direct_read",
    "direct_reports",
    "metrika",
    "webmaster",
    "wordstat",
    "audience",
)
ALL_TOOLSETS = tuple(TOOLSETS)
ALL_TOOLS = frozenset(tool for spec in TOOLSETS.values() for tool in spec["tools"])


def is_read_only(tool: str) -> bool:
    annotation = TOOL_ANNOTATIONS.get(tool)
    return bool(annotation and annotation.readOnlyHint)


def _split(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass(frozen=True)
class Surface:
    toolsets: tuple[str, ...] = ALL_TOOLSETS
    tools: frozenset[str] = ALL_TOOLS
    read_only: bool = False

    def exposes(self, tool: str) -> bool:
        # Tools outside every toolset (future additions) stay visible unless read-only hides them.
        if tool not in ALL_TOOLS:
            return not self.read_only or is_read_only(tool)
        return tool in self.tools

    def describe(self) -> dict[str, Any]:
        return {
            "active": list(self.toolsets),
            "read_only": self.read_only,
            "toolsets": {
                name: {
                    "description": spec["description"],
                    "tools": list(spec["tools"]),
                    "enabled": name in self.toolsets,
                }
                for name, spec in TOOLSETS.items()
            },
            "env": {
                "YANDEX_TOOLSETS": "all | default | comma list",
                "YANDEX_TOOLS": "extra tool names",
                "YANDEX_DISABLED_TOOLS": "tool names to hide",
                "YANDEX_READ_ONLY": "1 hides every prepare/apply/write/paid-submit tool",
            },
        }

    def describe_json(self) -> str:
        return json.dumps(self.describe(), ensure_ascii=False, separators=(",", ":"))


def build_surface(
    toolsets: str | Iterable[str] | None = None,
    *,
    extra_tools: Iterable[str] = (),
    disabled_tools: Iterable[str] = (),
    read_only: bool = False,
) -> Surface:
    if toolsets is None or (isinstance(toolsets, str) and toolsets.strip() in {"", "all"}):
        names = list(ALL_TOOLSETS)
    else:
        names = []
        for name in _split(toolsets) if isinstance(toolsets, str) else list(toolsets):
            expanded = DEFAULT_TOOLSETS if name == "default" else ALL_TOOLSETS if name == "all" else (name,)
            for item in expanded:
                if item not in TOOLSETS:
                    raise ValueError(f"unknown Yandex toolset: {item}")
                if item not in names:
                    names.append(item)
    tools = {tool for name in names for tool in TOOLSETS[name]["tools"]}
    for tool in extra_tools:
        if tool not in ALL_TOOLS:
            raise ValueError(f"unknown Yandex tool: {tool}")
        tools.add(tool)
    tools -= set(disabled_tools)
    if read_only:
        tools = {tool for tool in tools if is_read_only(tool)}
    return Surface(toolsets=tuple(names), tools=frozenset(tools), read_only=read_only)


def surface_from_env(
    environ: Mapping[str, str] | None = None, *, cli_toolsets: str | None = None, cli_read_only: bool = False
) -> Surface:
    env = os.environ if environ is None else environ
    return build_surface(
        cli_toolsets if cli_toolsets is not None else env.get("YANDEX_TOOLSETS"),
        extra_tools=_split(env.get("YANDEX_TOOLS")),
        disabled_tools=_split(env.get("YANDEX_DISABLED_TOOLS")),
        read_only=cli_read_only
        or env.get("YANDEX_READ_ONLY", "").strip().lower() in {"1", "true", "yes", "on"},
    )
