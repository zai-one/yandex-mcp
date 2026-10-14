"""Agent-facing layer: description overrides, router instructions, resources, prompts."""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from zai_yandex.toolsets import Surface

# Overrides only where the original docstring was over the 300-char budget.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "direct_get_goal_statistics": (
        "Direct Reports statistics broken down by 1-10 Metrika goals and attribution models "
        "(FCCD, LC, LSCCD, AUTO). Use for CPA/ROI by goal; for plain cost/clicks use `direct_get_statistics`. "
        "Goal columns: <Field>_<GoalId>_<Model>. Read-only."
    ),
    "direct_list_strategies": (
        "List Direct shared (portfolio) strategies via Strategies.get v501 with paging. For a campaign's own "
        "strategy use `direct_get_campaign`; type-specific fields via `direct_get_inventory` "
        "service=strategies. Read-only."
    ),
    "direct_apply_changes": (
        "Apply an accepted Direct change set (from `direct_prepare_changes`, approved by the operator) "
        "exactly once with approval_id + idempotency_key; re-reads campaigns to verify. Writes; spends "
        "units. Fails closed without approval."
    ),
    "direct_write": (
        "Idempotent non-destructive Direct write (no deletes or bid changes: use direct_prepare_write) "
        "behind the server write flag and audit log. Prefer `direct_prepare_changes` -> `direct_apply_changes` for campaign changes. "
        "Writes; spends units; confirm with the user first."
    ),
    "metrika_report": (
        "One-call Metrika report with optional period comparison (compare_date1/2): totals, abs/% deltas, "
        "sampling info, JSON or CSV. Use for period-over-period analysis; raw params via "
        "`metrika_get_statistics`. <=12 metrics, 5 dimensions, 93 days. Read-only."
    ),
    "yandex_list_accounts": (
        "List configured named accounts (agency clients/profiles): name, Direct Client-Login, which "
        "services have own tokens, sandbox. Pass account=<name> to any tool; omitted = default. Local."
    ),
    "yandex_check_update": (
        "Check GitHub for a newer zai-yandex-mcp release and suggest the update command. Suggest-only, "
        "24h cache unless force=true; never calls Yandex."
    ),
    "yandex_setup_toolsets": (
        "Pick Yandex platforms (direct, metrika, webmaster, audience, wordstat, serp, maps, manage) or a preset "
        "(direct-only, analytics, seo, everything) and tool_loading eager|lazy -> YANDEX_TOOLSETS + token cost. "
        "write=true saves to mcp.local.json (stdio). No secrets."
    ),
    "yandex_auth_status": (
        "Show which Yandex OAuth services have tokens (never values). Pending device-flow user_code if any. Local."
    ),
    "yandex_auth_start": (
        "Start Yandex OAuth device-code flow: returns verification URL + user_code. Uses MCP URL elicitation when "
        "the client supports it. Pass client_id or set YANDEX_OAUTH_CLIENT_ID. No token in output."
    ),
    "yandex_auth_finish": (
        "Poll pending device-code auth; on success write 0600 token files and hot-reload (no restart). "
        "Never returns the token. Call after the user confirms in the browser."
    ),
}

GUIDE_TOPICS = (
    "api-units",
    "audit-checklist",
    "strategies",
    "metrics-dimensions",
    "diagnostics-codes",
    "wordstat-operators",
    "prepare-apply",
    "errors",
    "offline-conversions",
    "accounts",
    "tool-map",
    "oauth",
    "management",
    "business",
    "maps-geosearch",
)


def guide(topic: str) -> str:
    if topic not in GUIDE_TOPICS:
        raise ValueError(f"unknown guide topic; available: {', '.join(GUIDE_TOPICS)}")
    return files("zai_yandex").joinpath(f"resources/guide/{topic}.md").read_text(encoding="utf-8")


# Router: intent words (ru/en, also used by the routing eval) -> first tool | skill | guide.
# Each line names where the full detail lives so the short router loses no capability.
ROUTES: tuple[tuple[str, str, str, str, str], ...] = (
    # (route id, intent words, first tool, skill or "", guide topic)
    ("direct_audit", "аудит Директа, CPA вырос, сливаем", "direct_list_campaigns", "yandex-direct-audit", "audit-checklist"),
    ("direct_stats", "статистика Директа, расход, потратили, клики, запросы", "direct_get_statistics", "yandex-direct-audit", "api-units"),
    ("direct_change", "изменить ставки/бюджет, пауза кампании, минус-слова, объявления", "direct_prepare_changes", "direct-safe-change", "prepare-apply"),
    ("direct_extra", "ставки фраз, аукцион, быстрые ссылки, картинки, фиды, лиды, Changes", "direct_get_keyword_bids", "", "tool-map"),
    ("agency", "агентство, клиенты, аккаунты, песочница, потестить", "direct_list_agency_clients", "yandex-agency-accounts", "accounts"),
    ("auth", "подключить, OAuth, токен, connect", "yandex_auth_start", "", "oauth"),
    ("setup", "настройка, выбрать платформы, сэкономить токены (default=all), setup", "yandex_setup_toolsets", "", "tool-map"),
    ("metrika_compare", "Метрика, трафик, цели, сравнить периоды", "metrika_list_counters", "metrika-compare-periods", "metrics-dimensions"),
    ("metrika_logs", "сырые визиты, выгрузка логов", "metrika_logs_evaluate", "", "tool-map"),
    ("offline", "офлайн-конверсии, CRM, CSV", "metrika_validate_offline_conversions", "metrika-offline-conversions", "offline-conversions"),
    ("seo", "Вебмастер, SEO, индексация, ИКС, страницы, Pro limits", "webmaster_list_hosts", "webmaster-seo-health", "diagnostics-codes"),
    ("wordstat", "Wordstat, спрос, частотность, семантика", "yandex_wordstat_get_top", "wordstat-demand-research", "wordstat-operators"),
    ("serp", "выдача, позиции, SERP", "yandex_serp_prepare", "", "tool-map"),
    ("audience", "аудитории, сегменты, пиксель", "audience_list_segments", "", "tool-map"),
    ("manage", "права, представители, метки, примечания, звонки, затраты, фид, хост", "metrika_manage_read", "yandex-account-admin", "management"),
    ("business", "Бизнес, отзывы, геореклама", "", "", "business"),
    # Shown only when a Geosearch key exposes the maps tools (default/all routers stay unchanged).
    ("maps", "найти кафе/фирму рядом, адрес и телефон компании, геопоиск", "maps_search_organizations", "", "maps-geosearch"),
    ("errors", "ошибка, баллы, лимит", "", "", "errors"),
)  # fmt: skip


KEY_GATED_ROUTES = frozenset({"maps"})


def build_instructions(
    surface: Surface,
    update_hint: str | None = None,
    accounts: tuple[str, ...] = (),
    direct_sandbox: bool = False,
) -> str:
    mode = "read-only" if surface.read_only else "read+write"
    lines = [
        f"Yandex MCP: Direct/Metrika/Webmaster/Wordstat/SERP/Audience ({mode}; yandex://toolsets).",
        "Line: tool; skill; yandex://guide/<topic>.",
    ]
    for rid, words, tool, skill, topic in ROUTES:
        if rid in KEY_GATED_ROUTES and not surface.exposes(tool):
            continue
        # tool-map is named once on the "Unsure?" line below; repeating it per route only costs tokens.
        parts = (tool, skill, "" if topic == "tool-map" else topic)
        lines.append(f"- {words}: " + "; ".join(part for part in parts if part))
    lines.append("Writes: prepare -> approval -> apply(idempotency_key). Wordstat/SERP paid.")
    lines.append("Unsure: yandex://playbook, yandex://guide/tool-map.")
    if direct_sandbox:
        lines.append("Direct SANDBOX: test data only (api-sandbox); say so in answers.")
    if accounts:
        lines.append(f"Accounts: {len(accounts)} profiles; yandex_list_accounts, then account=<name> per call.")
    router = "\n".join(lines)
    return f"{router}\n{update_hint}" if update_hint else router


def register_agent_layer(server: Any, surface: Surface) -> None:
    @server.resource(
        "yandex://toolsets",
        name="toolsets",
        mime_type="application/json",
        description="Active toolsets, read-only mode and the env vars to change them.",
    )
    def toolsets_resource() -> str:
        return surface.describe_json()

    @server.resource(
        "yandex://playbook",
        name="playbook",
        mime_type="text/markdown",
        description="How to work with Yandex services here: flows, costs, safe writes, guides.",
    )
    def playbook_resource() -> str:
        topics = "\n".join(f"- yandex://guide/{topic}" for topic in GUIDE_TOPICS)
        return (
            "# Yandex MCP playbook\n\n"
            "| Task | Flow | Prompt |\n|---|---|---|\n"
            "| Direct audit | campaigns -> statistics -> goals -> strategies -> search queries | campaign_audit |\n"
            "| Weekly report | metrika_report with compare dates + direct_get_statistics | weekly_report |\n"
            "| SEO health | webmaster summary -> diagnostics -> indexing -> queries | seo_audit |\n"
            "| Budget scaling | statistics by campaign -> headroom -> prepare change | budget_scaling |\n\n"
            "Skills (skills/<name>/SKILL.md): " + ", ".join(sorted({r[3] for r in ROUTES if r[3]})) + ".\n\n"
            "Rules: never guess IDs; minimal FieldNames (units); compare equal periods; flag sampling; "
            "writes only via prepare -> approval -> apply.\n\nGuides:\n" + topics + "\n"
        )

    @server.resource(
        "yandex://guide/{topic}",
        name="guide",
        mime_type="text/markdown",
        description="Reference by topic: " + ", ".join(GUIDE_TOPICS) + ".",
    )
    def guide_resource(topic: str) -> str:
        return guide(topic)

    @server.prompt(name="campaign_audit", description="Audit Yandex Direct campaigns and recommend fixes.")
    def campaign_audit(period_days: int = 30) -> str:
        return (
            f"Audit my Yandex Direct account for the last {period_days} days vs the previous {period_days}. "
            "Follow yandex://guide/audit-checklist: direct_list_campaigns, direct_get_statistics "
            "(CAMPAIGN_PERFORMANCE_REPORT, then SEARCH_QUERY_PERFORMANCE_REPORT for top spenders), goals via "
            "direct_get_goal_statistics, strategies via direct_get_campaign. Output a prioritized issue table "
            "with evidence and recommendations. Do not change anything."
        )

    @server.prompt(
        name="weekly_report", description="Weekly marketing report: Metrika traffic/goals + Direct spend."
    )
    def weekly_report(counter_id: str = "") -> str:
        counter = f"counter {counter_id}" if counter_id else "the counter I choose (list them first)"
        return (
            f"Build a weekly report for {counter}: metrika_report for last 7 full days with compare dates "
            "for the previous 7 (visits, users, goal reaches by trafficSource), plus direct_get_statistics "
            "for the same weeks. Show a delta table, top movers and 3 bullet explanations; flag sampling."
        )

    @server.prompt(name="seo_audit", description="Yandex Webmaster SEO health check with a fix list.")
    def seo_audit(host: str = "") -> str:
        target = f"host {host}" if host else "the host I choose (webmaster_list_hosts first)"
        return (
            f"Check SEO health of {target}: webmaster_get_summary, webmaster_get_diagnostics (FATAL/CRITICAL "
            "first), indexing and search-URL history for 90 days, removed pages via "
            "webmaster_list_search_events, top 20 popular queries. Give a prioritized action list; propose "
            "recrawl/sitemap only via prepare and wait for approval."
        )

    @server.prompt(
        name="budget_scaling",
        description="Find Direct campaigns that can take more budget and prepare a change.",
    )
    def budget_scaling(target_cpa: str = "") -> str:
        goal = f" while keeping CPA <= {target_cpa}" if target_cpa else ""
        return (
            f"Find Direct campaigns where more budget would pay off{goal}: last 30 days statistics by "
            "campaign (Cost, Conversions, CostPerConversion), strategy and weekly budget limits. Propose "
            "budget changes in rubles and %, then call direct_prepare_changes and show the diff. Do not apply."
        )
