"""Governed management catalogue for Metrika, Webmaster and Audience (opt-in toolsets).

One declarative allowlist of documented endpoints instead of one MCP tool per
CRUD verb: the default ``tools/list`` stays flat while every competitor capability
is reachable. Per provider there are three tools:

* ``<provider>_manage_read(operation, args)`` – documented GET endpoints
  (``operation="catalog"`` lists every operation locally, no provider call);
* ``<provider>_manage_prepare(operation, args)`` – opens an unaccepted, expiring
  approval bound to the exact request, principal and resolved account/endpoint;
* ``<provider>_manage_apply(operation, args, approval_id, idempotency_key)`` –
  runtime write flag + accepted approval + durable idempotency ledger + readback;
* ``<provider>_manage_reconcile(operation, args, idempotency_key)`` – settles an
  uncertain write by reading the provider; never re-sends it.

Paths come from the official references:
Metrika https://yandex.ru/dev/metrika/ru/management/openapi/ and
https://yandex.ru/dev/metrika/ru/stat/ ; Webmaster
https://yandex.ru/dev/webmaster/doc/ru/reference/ ; Audience
https://yandex.ru/dev/audience/ru/ref/openapi/ .
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from zai_yandex.models import ApprovalRecord
from zai_yandex.transport import ProviderError, request_hash

Kind = Literal["read", "write", "upload"]
MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_BODY_KEYS = 200
LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
RESOURCE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
INT_ARGS = frozenset({"counter_id", "id", "label_id", "uploading_id", "segment_id", "pixel_id", "client_id"})


@dataclass(frozen=True)
class Op:
    kind: Kind
    method: str
    path: str
    doc: str
    query: frozenset[str] = frozenset()
    body: str | None = None  # wrapper key ("" = raw object), None = no JSON body
    risk: str = "low"
    readback: str | None = None
    fixed_query: dict[str, str] = field(default_factory=dict)

    @property
    def path_args(self) -> tuple[str, ...]:
        return tuple(name for _, name, _, _ in string.Formatter().parse(self.path) if name)


M = "https://yandex.ru/dev/metrika/ru/management/openapi/"
STAT_QUERY = frozenset(
    {
        "ids",
        "date1",
        "date2",
        "metrics",
        "dimensions",
        "filters",
        "sort",
        "limit",
        "offset",
        "accuracy",
        "preset",
        "lang",
        "group",
        "top_keys",
        "row_ids",
        "include_undefined",
        "attribution",
        "currency",
        "direct_client_logins",
        "parent_id",
        "pretty",
        "date1_a",
        "date2_a",
        "date1_b",
        "date2_b",
        "filters_a",
        "filters_b",
    }
)


def _crud(prefix: str, coll: str, item: str, key: str, doc: str, *, counter: bool = True) -> dict[str, Op]:
    base = "/management/v1/counter/{counter_id}/" if counter else "/management/v1/"
    return {
        f"{prefix}_list": Op("read", "GET", base + coll, f"{M}{doc}"),
        f"{prefix}_get": Op("read", "GET", base + item + "/{id}", f"{M}{doc}"),
        f"{prefix}_create": Op(
            "write", "POST", base + coll, f"{M}{doc}", body=key, risk="medium", readback=f"{prefix}_list"
        ),
        f"{prefix}_update": Op(
            "write",
            "PUT",
            base + item + "/{id}",
            f"{M}{doc}",
            body=key,
            risk="medium",
            readback=f"{prefix}_get",
        ),
        f"{prefix}_delete": Op(
            "write", "DELETE", base + item + "/{id}", f"{M}{doc}", risk="high", readback=f"{prefix}_list"
        ),
    }


METRIKA_OPS: dict[str, Op] = {
    # Counters https://yandex.ru/dev/metrika/ru/management/openapi/counter/
    "counter_get": Op(
        "read",
        "GET",
        "/management/v1/counter/{counter_id}",
        M + "counter/counter",
        query=frozenset({"field"}),
    ),
    "counter_create": Op(
        "write", "POST", "/management/v1/counters", M + "counter/addCounter", body="counter", risk="medium"
    ),
    "counter_update": Op(
        "write",
        "PUT",
        "/management/v1/counter/{counter_id}",
        M + "counter/editCounter",
        body="counter",
        risk="medium",
        readback="counter_get",
    ),
    "counter_delete": Op(
        "write", "DELETE", "/management/v1/counter/{counter_id}", M + "counter/deleteCounter", risk="high"
    ),
    "counter_undelete": Op(
        "write",
        "POST",
        "/management/v1/counter/{counter_id}/undelete",
        M + "counter/undeleteCounter",
        risk="medium",
        readback="counter_get",
    ),
    **_crud("filter", "filters", "filter", "filter", "filter/"),
    **_crud("operation", "operations", "operation", "operation", "operation/"),
    **_crud("segment", "apisegment/segments", "apisegment/segment", "segment", "segment/"),
    **_crud("annotation", "chart_annotations", "chart_annotation", "chart_annotation", "chart_annotation/"),
    **_crud("access_filter", "access_filters", "access_filter", "access_filter", "access_filter/"),
    **_crud("label", "labels", "label", "label", "label/", counter=False),
    "goal_get": Op("read", "GET", "/management/v1/counter/{counter_id}/goal/{id}", M + "goal/goal"),
    "social_networks": Op("read", "GET", "/management/v1/social_networks", M + "goal/getSocialNetworks"),
    "messengers": Op("read", "GET", "/management/v1/messengers", M + "goal/getMessengers"),
    "counter_label_set": Op(
        "write",
        "POST",
        "/management/v1/counter/{counter_id}/label/{label_id}",
        M + "counter-label/setCounterLabel",
        risk="low",
        readback="counter_get",
    ),
    "counter_label_unset": Op(
        "write",
        "DELETE",
        "/management/v1/counter/{counter_id}/label/{label_id}",
        M + "counter-label/unsetCounterLabel",
        risk="medium",
        readback="counter_get",
    ),
    # Grants: the login is a query parameter (user_login), not a path segment.
    "grant_list": Op("read", "GET", "/management/v1/counter/{counter_id}/grants", M + "grant/grants"),
    "grant_get": Op(
        "read",
        "GET",
        "/management/v1/counter/{counter_id}/grant",
        M + "grant/grant",
        query=frozenset({"user_login"}),
    ),
    "my_grant": Op("read", "GET", "/management/v1/counter/{counter_id}/my_grant", M + "grant/myGrant"),
    "grant_create": Op(
        "write",
        "POST",
        "/management/v1/counter/{counter_id}/grants",
        M + "grant/addGrant",
        body="grant",
        risk="high",
        readback="grant_list",
    ),
    "grant_update": Op(
        "write",
        "PUT",
        "/management/v1/counter/{counter_id}/grant",
        M + "grant/editGrant",
        body="grant",
        risk="high",
        readback="grant_list",
    ),
    "grant_delete": Op(
        "write",
        "DELETE",
        "/management/v1/counter/{counter_id}/grant",
        M + "grant/deleteGrant",
        query=frozenset({"user_login"}),
        risk="high",
        readback="grant_list",
    ),
    "public_grant_create": Op(
        "write",
        "POST",
        "/management/v1/counter/{counter_id}/public_grant",
        M + "grant/addPublicGrant",
        risk="high",
        readback="grant_list",
    ),
    "public_grant_delete": Op(
        "write",
        "DELETE",
        "/management/v1/counter/{counter_id}/public_grant",
        M + "grant/deletePublicGrant",
        risk="medium",
        readback="grant_list",
    ),
    # Delegates and accounts (user level)
    "delegate_list": Op("read", "GET", "/management/v1/delegates", M + "delegates/delegates"),
    "delegate_add": Op(
        "write",
        "POST",
        "/management/v1/delegates",
        M + "delegates/addDelegate",
        body="delegate",
        risk="high",
        readback="delegate_list",
    ),
    "delegate_delete": Op(
        "write",
        "DELETE",
        "/management/v1/delegate",
        M + "delegates/deleteDelegate",
        query=frozenset({"user_login"}),
        risk="high",
        readback="delegate_list",
    ),
    "account_list": Op(
        "read", "GET", "/management/v1/accounts", M + "account/accounts", query=frozenset({"user_login"})
    ),
    "client_list": Op(
        "read", "GET", "/management/v1/clients", M + "clients/getClients", query=frozenset({"counters"})
    ),
    # Uploads (multipart CSV `file`) and their status
    "calls_upload": Op(
        "upload",
        "POST",
        "/management/v1/counter/{counter_id}/offline_conversions/upload_calls",
        M + "call/uploadCalls",
        query=frozenset({"comment", "new_goal_name"}),
        risk="medium",
        readback="calls_uploadings",
    ),
    "calls_uploadings": Op(
        "read",
        "GET",
        "/management/v1/counter/{counter_id}/offline_conversions/calls_uploadings",
        M + "call/findAllCallUploadings",
    ),
    "calls_uploading": Op(
        "read",
        "GET",
        "/management/v1/counter/{counter_id}/offline_conversions/calls_uploading/{id}",
        M + "call/findCallUploadingById",
    ),
    "chats_upload": Op(
        "upload",
        "POST",
        "/management/v1/counter/{counter_id}/offline_conversions/upload",
        M + "chats/upload_chats",
        query=frozenset({"comment"}),
        risk="medium",
        fixed_query={"type": "CHATS"},
    ),
    "expense_upload": Op(
        "upload",
        "POST",
        "/management/v1/counter/{counter_id}/expense/upload",
        M + "expense/uploadMultipart",
        query=frozenset({"provider", "comment"}),
        risk="medium",
        readback="expense_uploadings",
    ),
    "expense_delete": Op(
        "upload",
        "POST",
        "/management/v1/counter/{counter_id}/expense/delete",
        M + "expense/uploadRemoveBody_1",
        query=frozenset({"provider", "comment"}),
        risk="high",
        readback="expense_uploadings",
    ),
    "expense_uploadings": Op(
        "read", "GET", "/management/v1/counter/{counter_id}/expense/uploadings", M + "expense/findAll_2"
    ),
    "expense_uploading": Op(
        "read", "GET", "/management/v1/counter/{counter_id}/expense/uploading/{id}", M + "expense/findById_2"
    ),
    "user_params_upload": Op(
        "upload",
        "POST",
        "/management/v1/counter/{counter_id}/user_params/uploadings/upload",
        M + "user_params/upload",
        query=frozenset({"action", "client_id_type", "comment"}),
        risk="medium",
        readback="user_params_uploadings",
    ),
    "user_params_confirm": Op(
        "write",
        "POST",
        "/management/v1/counter/{counter_id}/user_params/uploading/{id}/confirm",
        M + "user_params/confirm",
        body="uploading",
        risk="medium",
        readback="user_params_uploading",
    ),
    "user_params_uploadings": Op(
        "read", "GET", "/management/v1/counter/{counter_id}/user_params/uploadings", M + "user_params/findAll"
    ),
    "user_params_uploading": Op(
        "read",
        "GET",
        "/management/v1/counter/{counter_id}/user_params/uploading/{id}",
        M + "user_params/findById",
    ),
    "crm_simple_orders_upload": Op(
        "upload",
        "POST",
        "/cdp/api/v1/counter/{counter_id}/data/simple_orders",
        M + "schema/uploadSimpleOrders",
        query=frozenset({"merge_mode", "delimiter_type"}),
        risk="medium",
        readback="crm_last_uploadings",
    ),
    "crm_last_uploadings": Op(
        "read",
        "GET",
        "/cdp/api/v1/counter/{counter_id}/last_uploadings",
        M + "schema/getLastUploadings",
        query=frozenset({"limit"}),
    ),
    # Reporting API variants (https://yandex.ru/dev/metrika/ru/stat/)
    "stat_bytime": Op(
        "read",
        "GET",
        "/stat/v1/data/bytime",
        "https://yandex.ru/dev/metrika/ru/stat/bytime",
        query=STAT_QUERY,
    ),
    "stat_drilldown": Op(
        "read",
        "GET",
        "/stat/v1/data/drilldown",
        "https://yandex.ru/dev/metrika/ru/stat/drilldown",
        query=STAT_QUERY,
    ),
    "stat_comparison": Op(
        "read",
        "GET",
        "/stat/v1/data/comparison",
        "https://yandex.ru/dev/metrika/ru/stat/comparison",
        query=STAT_QUERY,
    ),
    "stat_comparison_drilldown": Op(
        "read",
        "GET",
        "/stat/v1/data/comparison/drilldown",
        "https://yandex.ru/dev/metrika/ru/stat/comparison-drilldown",
        query=STAT_QUERY,
    ),
}

W = "https://yandex.ru/dev/webmaster/doc/ru/reference/"
H = "{host}"  # replaced by /user/{uid}/hosts/{host_id}
WEBMASTER_OPS: dict[str, Op] = {
    "host_add": Op("write", "POST", "{user}/hosts", W + "hosts-add-site", body="", risk="medium"),
    "host_delete": Op("write", "DELETE", H, W + "hosts-delete", risk="high"),
    "verification_start": Op(
        "write",
        "POST",
        H + "/verification",
        W + "host-verification-post",
        query=frozenset({"verification_type"}),
        risk="low",
    ),
    "sitemap_delete": Op(
        "write",
        "DELETE",
        H + "/user-added-sitemaps/{sitemap_id}",
        W + "host-user-added-sitemaps-sitemap-id-delete",
        risk="medium",
    ),
    "sitemap_recrawl_quota": Op(
        "read", "GET", "v4.1:" + H + "/sitemaps/recrawl", W + "host-priority-limit-sitemap-get"
    ),
    "sitemap_recrawl": Op(
        "write",
        "POST",
        "v4.1:" + H + "/sitemaps/{sitemap_id}/recrawl",
        W + "host-priority-re-crawl-initiation-sitemap",
        risk="low",
        readback="sitemap_recrawl_quota",
    ),
    "search_queries_all_history": Op(
        "read",
        "GET",
        H + "/search-queries/all/history",
        W + "host-search-queries-history-all",
        query=frozenset({"query_indicator", "device_type_indicator", "date_from", "date_to"}),
    ),
    "insearch_history": Op(
        "read",
        "GET",
        H + "/indexing/insearch/history",
        W + "hosts-indexing-insearch-history",
        query=frozenset({"date_from", "date_to"}),
    ),
    "insearch_samples": Op(
        "read",
        "GET",
        H + "/indexing/insearch/samples",
        W + "hosts-indexing-insearch-samples",
        query=frozenset({"offset", "limit"}),
    ),
    "indexing_archive_create": Op(
        "write", "POST", H + "/indexing/archive/", W + "host-id-important-archive-all-pages", risk="low"
    ),
    "indexing_archive_status": Op(
        "read", "GET", H + "/indexing/archive/{task_id}", W + "host-id-important-archive-all-pages"
    ),
    "feeds_add_start": Op(
        "write",
        "POST",
        H + "/feeds/add/start",
        W + "feeds-add-start",
        body="",
        risk="medium",
        readback="feeds_list",
    ),
    "feeds_add_info": Op(
        "read", "GET", H + "/feeds/add/info", W + "feeds-add-info", query=frozenset({"request_id"})
    ),
    "feeds_batch_add": Op(
        "write",
        "POST",
        H + "/feeds/batch/add",
        W + "feeds-batch-add",
        body="",
        risk="medium",
        readback="feeds_list",
    ),
    "feeds_batch_remove": Op(
        "write",
        "DELETE",
        H + "/feeds/batch/remove",
        W + "feeds-batch-delete",
        body="",
        risk="high",
        readback="feeds_list",
    ),
    "feeds_change_regions": Op(
        "write",
        "POST",
        H + "/feeds/change",
        W + "feeds-region-change",
        body="",
        risk="medium",
        readback="feeds_list",
    ),
    "feeds_moderation_recheck": Op(
        "write", "POST", H + "/feeds/moderation/recheck", W + "feeds-moderation-recheck", body="", risk="low"
    ),
    "feeds_list": Op("read", "GET", H + "/feeds/list", W + "feeds-list"),
    "feeds_status_list": Op("read", "GET", H + "/feeds/status/list", W + "feeds-status-list"),
    "feeds_summary": Op("read", "GET", H + "/feeds/summary", W + "feeds-summary"),
    "feeds_problems": Op(
        "read", "GET", H + "/feeds/problems", W + "feeds-problems", query=frozenset({"feed_url"})
    ),
    "feeds_rejected_offers": Op(
        "read",
        "GET",
        H + "/feeds/moderation/rejected-offers",
        W + "feeds-moderation-rejected-offers",
        query=frozenset({"feed_url"}),
    ),
}

A = "https://yandex.ru/dev/audience/ru/ref/openapi/"
AUDIENCE_OPS: dict[str, Op] = {
    "segment_upload_csv": Op(
        "upload", "POST", "/segments/upload_csv_file", A + "segments/uploadCsvFile", risk="medium"
    ),
    "segment_confirm": Op(
        "write",
        "POST",
        "/segment/{segment_id}/confirm",
        A + "segments/confirm",
        body="segment",
        risk="medium",
    ),
    "segment_confirm_client_id": Op(
        "write",
        "POST",
        "/segment/client_id/{segment_id}/confirm",
        A + "segments/confirmClientId",
        body="segment",
        risk="medium",
    ),
    "segment_modify_data": Op(
        "upload",
        "POST",
        "/segment/{segment_id}/modify_data",
        A + "segments/modifyUploadingData",
        query=frozenset({"modification_type"}),
        risk="high",
    ),
    "segment_update_geo_points": Op(
        "write",
        "POST",
        "/segment/{segment_id}/update_geo_points",
        A + "segments/updateGeoPoints",
        body="",
        risk="medium",
    ),
    "segment_create_lookalike": Op(
        "write",
        "POST",
        "/segments/create_lookalike",
        A + "segments/createLookalike",
        body="segment",
        risk="medium",
    ),
    "segment_create_metrika": Op(
        "write",
        "POST",
        "/segments/create_metrika",
        A + "segments/createMetrika",
        body="segment",
        risk="medium",
    ),
    "segment_create_appmetrica": Op(
        "write",
        "POST",
        "/segments/create_appmetrica",
        A + "segments/createAppMetrica",
        body="segment",
        risk="medium",
    ),
    "segment_update": Op(
        "write", "PUT", "/segment/{segment_id}", A + "segments/edit", body="segment", risk="medium"
    ),
    "segment_delete": Op("write", "DELETE", "/segment/{segment_id}", A + "segments/delete", risk="high"),
    "segment_reprocess": Op(
        "write", "PUT", "/segment/{segment_id}/reprocess", A + "segments/reprocessSegment", risk="low"
    ),
    "segment_grants": Op("read", "GET", "/segment/{segment_id}/grants", A + "grants/grants"),
    "segment_grant_add": Op(
        "write",
        "PUT",
        "/segment/{segment_id}/grant",
        A + "grants/addGrant",
        body="grant",
        risk="high",
        readback="segment_grants",
    ),
    "segment_grant_delete": Op(
        "write",
        "DELETE",
        "/segment/{segment_id}/grant",
        A + "grants/deleteGrant",
        query=frozenset({"user_login"}),
        risk="high",
        readback="segment_grants",
    ),
    "pixel_update": Op(
        "write", "PUT", "/pixel/{pixel_id}", A + "pixels/editPixel", body="pixel", risk="medium"
    ),
    "pixel_delete": Op("write", "DELETE", "/pixel/{pixel_id}", A + "pixels/deletePixel", risk="high"),
    "pixel_undelete": Op("write", "POST", "/pixel/{pixel_id}/undelete", A + "pixels/undelete", risk="low"),
    "delegate_list": Op("read", "GET", "/delegates", A + "delegates/delegates"),
    "delegate_add": Op(
        "write",
        "PUT",
        "/delegate",
        A + "delegates/addDelegate",
        body="delegate",
        risk="high",
        readback="delegate_list",
    ),
    "delegate_delete": Op(
        "write",
        "DELETE",
        "/delegate",
        A + "delegates/deleteDelegate",
        query=frozenset({"user_login"}),
        risk="high",
        readback="delegate_list",
    ),
    "account_list": Op("read", "GET", "/accounts", A + "accounts/accounts"),
}

CATALOG: dict[str, dict[str, Op]] = {
    "yandex_metrika": METRIKA_OPS,
    "yandex_webmaster": WEBMASTER_OPS,
    "yandex_audience": AUDIENCE_OPS,
}
WRITE_FLAGS = {
    "yandex_metrika": "yandex_metrika_write_enabled",
    "yandex_webmaster": "yandex_webmaster_write_enabled",
    "yandex_audience": "yandex_audience_write_enabled",
}


def catalog(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "operations": {
            name: {
                "kind": op.kind,
                "http": f"{op.method} {op.path.removeprefix('v4.1:')}",
                "args": list(op.path_args)
                + [f"query.{key}" for key in sorted(op.query)]
                + (["body"] if op.body is not None else [])
                + (["csv"] if op.kind == "upload" else []),
                "risk": op.risk if op.kind != "read" else "read",
                "doc": op.doc,
            }
            for name, op in sorted(CATALOG[provider].items())
        },
    }


def operation(provider: str, name: str, kinds: tuple[Kind, ...]) -> Op:
    op = CATALOG[provider].get(str(name).strip())
    if op is None or op.kind not in kinds:
        allowed = sorted(key for key, value in CATALOG[provider].items() if value.kind in kinds)
        raise ValueError(f"unknown operation; use operation='catalog' or one of: {', '.join(allowed)}")
    return op


def validate_args(provider: str, op: Op, args: dict[str, Any] | None) -> dict[str, Any]:
    """Fail closed: only the documented path ids, allowlisted query keys, body and csv."""
    from zai_yandex.adapters.webmaster import YandexWebmasterAdapter

    values = dict(args or {})
    allowed = set(op.path_args) - {"host", "user"} | {"query"}
    if op.body is not None:
        allowed.add("body")
    if op.kind == "upload":
        allowed.add("csv")
    if "{host}" in op.path:
        allowed.add("host_id")
    unknown = set(values) - allowed
    if unknown:
        raise ValueError("unsupported args: " + ", ".join(sorted(unknown)))
    clean: dict[str, Any] = {}
    for name in sorted(allowed - {"query", "body", "csv"}):
        if name not in values:
            raise ValueError(f"{name} is required")
        value = values[name]
        if name in INT_ARGS:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
                raise ValueError(f"{name} must be a positive integer")
        elif name == "host_id":
            YandexWebmasterAdapter.validate_host_id(str(value))
        elif not isinstance(value, str) or not RESOURCE_ID.fullmatch(value):
            raise ValueError(f"{name} must be a bounded identifier")
        clean[name] = value
    query = values.get("query") or {}
    if not isinstance(query, dict) or set(query) - op.query:
        raise ValueError("query keys must be from: " + (", ".join(sorted(op.query)) or "none"))
    for key, value in query.items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 4096:
            raise ValueError(f"query.{key} must be a bounded scalar")
        if key == "user_login" and not LOGIN.fullmatch(str(value)):
            raise ValueError("query.user_login must be a Yandex login")
    if "user_login" in op.query and op.method == "DELETE" and "user_login" not in query:
        raise ValueError("query.user_login is required")
    if query:
        clean["query"] = dict(sorted(query.items()))
    if op.body is not None:
        body = values.get("body")
        if op.kind == "write" and op.method in {"POST", "PUT"} and body is None:
            raise ValueError("body is required")
        if body is not None:
            if not isinstance(body, dict) or not body or len(body) > MAX_BODY_KEYS:
                raise ValueError("body must be a non-empty JSON object")
            if op.body and set(body) != {op.body}:
                body = {op.body: body}  # accept the bare object as well as the documented wrapper
            clean["body"] = body
    if op.kind == "upload":
        csv = values.get("csv")
        if not isinstance(csv, str) or not csv.strip() or "\x00" in csv:
            raise ValueError("csv must be non-empty CSV text")
        if len(csv.encode("utf-8")) > MAX_CSV_BYTES:
            raise ValueError("csv exceeds 8 MiB; split the upload")
        clean["csv"] = csv
    return clean


async def _url(provider: str, adapter: Any, op: Op, args: dict[str, Any]) -> str:
    path, base = op.path, adapter.base_url
    if path.startswith("v4.1:"):
        path, base = path.removeprefix("v4.1:"), base.removesuffix("/v4") + "/v4.1"
    ids = {key: value for key, value in args.items() if key not in {"query", "body", "csv"}}
    if provider == "yandex_webmaster":
        if "{host}" in path:
            ids["host"] = await adapter._host_path(ids.pop("host_id"))
        if "{user}" in path:
            ids["user"] = f"/user/{await adapter._uid()}"
    ids = {
        key: value if key in {"host", "user"} else quote(str(value), safe="") for key, value in ids.items()
    }
    return base + path.format(**ids)


async def execute(provider: str, adapter: Any, name: str, op: Op, args: dict[str, Any]) -> dict[str, Any]:
    url = await _url(provider, adapter, op, args)
    params = {**op.fixed_query, **args.get("query", {})} or None
    headers = adapter._headers()
    if op.kind == "upload":
        result = await adapter.http.request_multipart(
            op.method,
            url,
            headers=headers,
            params=params,
            files={"file": ("data.csv", args["csv"].encode("utf-8"), "text/csv")},
        )
    elif provider == "yandex_webmaster":
        result = await adapter._request(
            op.method, url, headers=headers, params=params, payload=args.get("body")
        )
    else:
        result = await adapter.http.request(
            op.method, url, headers=headers, params=params, payload=args.get("body")
        )
    if isinstance(result.get("errors"), list) and result["errors"]:
        raise ProviderError(f"Yandex returned an error envelope for {name}")
    return {"operation": name, "result": result}


def readback_args(op: Op, args: dict[str, Any]) -> dict[str, Any]:
    keep = set(op.path_args) | {"host_id"}
    return {key: value for key, value in args.items() if key in keep}


async def open_approval(runtime: Any, provider: str, operation: str, request: str) -> dict[str, Any]:
    """Open an unaccepted, expiring approval for one exact request on the resolved account."""
    approval_id, expires = uuid4(), datetime.now(UTC) + timedelta(minutes=30)
    await runtime.store.create_approval(
        ApprovalRecord(
            approval_id, runtime.current_access().principal_id, provider, operation,
            request, 0.0, 0.0, "prepared", expires, True,
        )
    )  # fmt: skip
    account = runtime.settings.selected_account()
    return {
        "status": "approval_required",
        **({"account": account} if account else {}),
        "approval_id": str(approval_id),
        "request_hash": request,
        "expires_at": expires.isoformat(),
    }


def ledger_hash(provider: str, operation: str, request: str) -> str:
    """The digest runtime.write records for this governed request (see Runtime.write)."""
    return request_hash(
        {
            "provider": provider,
            "tool": operation,
            "arguments": {"operation": operation, "request_hash": request},
        }
    )


async def governed_apply(
    runtime: Any,
    provider: str,
    operation: str,
    request: str,
    approval_id: str,
    idempotency_key: str,
    *,
    enabled: bool,
    apply_fn: Any,
    readback_fn: Any,
) -> dict[str, Any]:
    """Accepted approval (same principal, account, operation, request) -> consume once -> ledger write."""
    store = runtime.store
    principal = runtime.current_access().principal_id
    key = str(idempotency_key or "").strip()
    try:
        if not enabled:
            raise ProviderError("write runtime is not enabled")
        if not 8 <= len(key) <= 128:
            raise ValueError("idempotency_key must be 8..128 characters")
        cached = await store.provider_write_lookup(principal, provider, key)
        if cached is not None:
            if (
                cached["request_hash"] == ledger_hash(provider, operation, request)
                and cached["status"] == "applied"
            ):
                return {**(cached.get("result") or {}), "request_hash": request, "replayed": True}
            raise ProviderError("idempotency key belongs to another or an unreconciled write")
        approval = await store.get_approval(UUID(str(approval_id)))
        if (
            approval is None
            or str(approval.principal_id) != str(principal)
            or approval.provider != provider
            or approval.operation != operation
            or approval.request_hash != request
            or approval.status != "accepted"
            or approval.expires_at <= datetime.now(UTC)
        ):
            raise PermissionError("an accepted approval matching this exact request and account is required")
        if not await store.consume_approval(UUID(str(approval_id))):
            raise PermissionError("approval is not accepted or was already consumed")
    except (ProviderError, PermissionError, ValueError) as exc:
        raise runtime.safe_error(provider, exc) from None
    result = await runtime.write(
        provider,
        operation,
        {"operation": operation, "request_hash": request},
        key,
        enabled=True,
        validate=lambda: None,
        apply_fn=apply_fn,
        readback_fn=readback_fn,
    )
    return {**result, "request_hash": request, "approval_id": str(approval_id)}


def register_management_tools(server: Any, runtime: Any) -> None:
    for provider, prefix, scope in (
        ("yandex_metrika", "metrika", "yandex_metrika"),
        ("yandex_webmaster", "webmaster", "yandex_webmaster"),
        ("yandex_audience", "audience", "yandex_audience"),
    ):
        _register(server, runtime, provider, prefix, scope)


def _register(server: Any, runtime: Any, provider: str, prefix: str, scope: str) -> None:
    store = runtime.store

    def adapter_of() -> Any:
        return getattr(runtime.registry, provider)()

    tool = f"{prefix}_manage"

    async def run_read(name: str, args: dict[str, Any]) -> dict[str, Any]:
        op = CATALOG[provider][name]
        return await runtime.read(
            provider,
            f"manage:{name}",
            {"operation": name, "args": args},
            lambda: execute(provider, adapter_of(), name, op, args),
            validate=lambda: validate_args(provider, op, args),
        )

    def checked(name: str, args: dict[str, Any] | None, kinds: tuple[Kind, ...]) -> tuple[Op, dict[str, Any]]:
        try:
            op = operation(provider, name, kinds)
            return op, validate_args(provider, op, args)
        except ValueError as exc:
            raise runtime.safe_error(provider, exc) from None

    def digest(name: str, clean: dict[str, Any]) -> str:
        # The resolved named account / endpoint is part of the approved request.
        return request_hash(
            {
                "provider": provider,
                "operation": name,
                "args": clean,
                "target": runtime.settings.ledger_scope(provider),
            }
        )

    async def read_tool(operation: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if operation == "catalog":
            return catalog(provider)
        _, clean = checked(operation, args, ("read",))
        return await run_read(operation, clean)

    async def prepare_tool(operation: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        op, clean = checked(operation, args, ("write", "upload"))
        opened = await open_approval(runtime, provider, f"manage:{operation}", digest(operation, clean))
        preview = {key: value for key, value in clean.items() if key != "csv"}
        if "csv" in clean:
            preview["csv_rows"] = clean["csv"].count("\n") + 1
        return {
            **opened,
            "operation": operation,
            "http": f"{op.method} {op.path.removeprefix('v4.1:')}",
            "risk": op.risk,
            "args": preview,
            "write_enabled": bool(getattr(runtime.settings, WRITE_FLAGS[provider])),
            "next_gate": f"user accepts the approval, then {tool}_apply with the same operation/args, "
            "approval_id and a new idempotency_key",
        }

    async def apply_tool(
        operation: str, approval_id: str, idempotency_key: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        op, clean = checked(operation, args, ("write", "upload"))

        async def readback(_: Any) -> Any:
            if op.readback is None:
                return None
            rb_op = CATALOG[provider][op.readback]
            try:
                return (
                    await execute(provider, adapter_of(), op.readback, rb_op, readback_args(rb_op, clean))
                )["result"]
            except ProviderError:
                return {"confirmed": False}

        result = await governed_apply(
            runtime,
            provider,
            f"manage:{operation}",
            digest(operation, clean),
            approval_id,
            idempotency_key,
            enabled=bool(getattr(runtime.settings, WRITE_FLAGS[provider])),
            apply_fn=lambda: execute(provider, adapter_of(), operation, op, clean),
            readback_fn=readback,
        )
        return {**result, "risk": op.risk}

    async def reconcile_tool(
        operation: str, idempotency_key: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        op, clean = checked(operation, args, ("write", "upload"))
        principal = runtime.current_access().principal_id
        key = str(idempotency_key).strip()
        cached = await store.provider_write_lookup(principal, provider, key)
        request = digest(operation, clean)
        if cached is None:
            return {"status": "unknown_key", "next": f"nothing was dispatched; prepare with {tool}_prepare"}
        if cached["request_hash"] != ledger_hash(provider, f"manage:{operation}", request):
            raise runtime.safe_error(provider, PermissionError("idempotency key belongs to another request"))
        if cached["status"] == "applied":
            return {"status": "applied", "result": cached["result"]}
        if cached["status"] == "failed":
            return {"status": "settled_unconfirmed", "result": cached["result"]}
        observed = None
        if op.readback is not None:
            rb_op = CATALOG[provider][op.readback]
            observed = await run_read(op.readback, readback_args(rb_op, clean))
        payload = {
            "status": "settled_unconfirmed",
            "request_hash": request,
            "observed": observed,
            "note": "outcome was uncertain; inspect `observed`. Never resend: if the change is absent, "
            "prepare a new approval and use a new idempotency_key.",
        }
        await store.provider_write_settle(principal, provider, key, status="failed", result=payload)
        return payload

    read_tool.__doc__ = (
        f"Documented {prefix.title()} management/report reads by operation name; args = path ids, "
        "query{...}. operation='catalog' lists all operations with docs (local). Read-only."
    )
    prepare_tool.__doc__ = (
        f"Open an approval for one allowlisted {prefix.title()} write/upload (args: ids, query, body, csv). "
        "Bound to principal, account and exact request; nothing is sent."
    )
    apply_tool.__doc__ = (
        f"Execute an accepted {prefix.title()} write once (write flag, approval_id, idempotency_key), "
        "then read back. Same key replays the stored result. Writes."
    )
    reconcile_tool.__doc__ = (
        f"Settle an uncertain {prefix.title()} management write by reading the provider; never resends."
    )
    for function, name, suffix in (
        (read_tool, f"{tool}_read", "read"),
        (prepare_tool, f"{tool}_prepare", "write"),
        (apply_tool, f"{tool}_apply", "write"),
        (reconcile_tool, f"{tool}_reconcile", "write"),
    ):
        function.__name__ = function.__qualname__ = name
        server.tool(auth=runtime.require_scopes(f"{scope}:{suffix}"))(function)


DIRECT_GOVERNED_TOOLS = ("direct_prepare_write", "direct_apply_write")


def _direct_ids(items: list[dict[str, Any]], result: Any) -> list[int]:
    ids = [int(i["Id"]) for i in items if isinstance(i.get("Id"), int) and not isinstance(i.get("Id"), bool)]
    if ids:
        return ids
    body = result.get("result") if isinstance(result, dict) else None
    for value in (body or {}).values() if isinstance(body, dict) else ():
        if isinstance(value, list):
            found = [
                int(row["Id"]) for row in value if isinstance(row, dict) and isinstance(row.get("Id"), int)
            ]
            if found:
                return found
    return []


def register_direct_governed_tools(server: Any, runtime: Any) -> None:
    """Every allowlisted Direct service/method through prepare -> approval -> apply -> readback."""
    from zai_yandex.adapters.direct import YandexDirectAdapter

    provider = "yandex_direct"

    def checked(
        service: str, method: str, items: list[dict[str, Any]]
    ) -> tuple[str, str, list[dict[str, Any]]]:
        try:
            return YandexDirectAdapter.validate_apply(service, method, items)
        except (ProviderError, ValueError) as exc:
            raise runtime.safe_error(provider, exc) from None

    def digest(service: str, method: str, items: list[dict[str, Any]]) -> str:
        # Service, method, items and the resolved account/endpoint are all bound by the approval.
        return request_hash(
            {
                "service": service,
                "method": method,
                "items": items,
                "target": runtime.settings.ledger_scope(provider),
            }
        )

    @server.tool(auth=runtime.require_scopes("yandex_direct:write"))
    async def direct_prepare_write(service: str, method: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Open an approval for one allowlisted Direct write (any service/method; yandex://guide/prepare-apply).

        Bound to service, method, items, account and endpoint. Nothing is sent.
        """
        norm_service, norm_method, clean = checked(service, method, items)
        opened = await open_approval(
            runtime, provider, f"write:{norm_service}.{norm_method}", digest(norm_service, norm_method, clean)
        )
        return {
            **opened,
            "service": norm_service,
            "method": norm_method,
            "item_count": len(clean),
            "risk": YandexDirectAdapter.risk_tier(norm_service, norm_method),
            "sandbox": runtime.settings.effective().direct_sandbox,
            "next_gate": "user accepts the approval, then direct_apply_write with the same "
            "service/method/items, "
            "approval_id and a new idempotency_key",
        }

    @server.tool(auth=runtime.require_scopes("yandex_direct:write"))
    async def direct_apply_write(
        service: str, method: str, items: list[dict[str, Any]], approval_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Execute an accepted Direct write once (write flag, approval_id, idempotency_key), then read back.

        Same key replays the stored result. Writes; spends units.
        """
        norm_service, norm_method, clean = checked(service, method, items)

        async def readback(result: Any) -> Any:
            return await runtime.registry.yandex_direct().readback(norm_service, _direct_ids(clean, result))

        return await governed_apply(
            runtime,
            provider,
            f"write:{norm_service}.{norm_method}",
            digest(norm_service, norm_method, clean),
            approval_id,
            idempotency_key,
            enabled=runtime.settings.yandex_direct_write_enabled,
            apply_fn=lambda: runtime.registry.yandex_direct().apply_changes(norm_service, norm_method, clean),
            readback_fn=readback,
        )


MANAGEMENT_TOOLS: dict[str, tuple[str, ...]] = {
    prefix: tuple(f"{prefix}_manage_{verb}" for verb in ("read", "prepare", "apply", "reconcile"))
    for prefix in ("metrika", "webmaster", "audience")
}
