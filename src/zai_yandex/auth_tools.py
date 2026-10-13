"""OAuth self-setup tools: device-code start/finish with hot reload (no restart).

Registered outside the provider ledger (like yandex_check_update): calls oauth.yandex.ru
only, never a marketing API. Tokens are written to 0600 files and never returned.

start/finish replace the server's own credentials, so they exist only on the local stdio
transport, where the caller is the owner of the process and its files. An HTTP server is
shared: there the operator provisions token files (``yandex-mcp-oauth --device``) and only
the read-only ``yandex_auth_status`` is offered, to callers holding a Yandex scope.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

from mcp.types import ClientCapabilities, ElicitationCapability, UrlElicitationCapability

from zai_yandex.agent_layer import TOOL_DESCRIPTIONS
from zai_yandex.config import SCOPES, ServiceConfig
from zai_yandex.oauth_device import (
    PENDING,
    SERVICE_TO_ENV,
    SERVICE_TO_FIELD,
    PendingAuth,
    PollStatus,
    apply_tokens_to_config,
    auth_status_public,
    link_account_token_files,
    persist_tokens,
    poll_device_token,
    public_start_payload,
    public_token_locations,
    request_device_codes,
    resolve_client_credentials,
    validate_services,
    verification_link,
)
from zai_yandex.tool_annotations import annotations_for
from zai_yandex.toolsets import Surface

try:
    from fastmcp.server.dependencies import get_context
except ImportError:  # pragma: no cover
    get_context = None  # type: ignore[assignment]


# Tools that replace server credentials: local stdio only, never on a shared HTTP server.
CREDENTIAL_TOOLS = ("yandex_auth_start", "yandex_auth_finish")


def _authenticated(config: ServiceConfig, transport: str):
    def check(context: Any) -> bool:
        if transport == "stdio":
            return bool(config.local_scopes)
        return bool(context.token and set(context.token.scopes) & SCOPES)

    return check


def _principal(config: ServiceConfig, transport: str) -> str:
    if transport == "stdio":
        return config.principal_id
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        claims = token.claims if token else {}
        actor = claims.get("sub")
        if isinstance(actor, str) and actor:
            return actor
    except Exception:
        pass
    return config.principal_id


def _require_account(config: ServiceConfig, account: str | None) -> None:
    if account is None:
        return
    if not config.accounts:
        raise ValueError("no named accounts configured")
    config.profile(account)


def _restart_hint(
    locations: dict[str, str], persistent: dict[str, bool], account: str | None
) -> dict[str, str]:
    missing = sorted(service for service, kept in persistent.items() if not kept)
    if not missing:
        return {}
    if account:
        where = ", ".join(f'"{SERVICE_TO_FIELD[s]}_file": "<state dir>/{locations[s]}"' for s in missing)
        hint = f"Active now; to keep after a restart add to {account} in YANDEX_ACCOUNTS_FILE: {where}"
    else:
        where = ", ".join(f"{SERVICE_TO_ENV[s]}=<state dir>/{locations[s]}" for s in missing)
        hint = f"Active now; to keep after a restart set {where}"
    return {"restart_hint": hint}


async def _maybe_elicit_url(verification_url: str, user_code: str) -> str:
    """Try MCP URL elicitation when the client declares it; otherwise 'none'/'fallback'."""
    if get_context is None:
        return "none"
    try:
        ctx = get_context()
        session = ctx.session
    except Exception:
        return "none"
    supports = False
    try:
        supports = session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability()))
        )
    except Exception:
        caps = getattr(getattr(session, "client_params", None), "capabilities", None)
        elicitation = getattr(caps, "elicitation", None) if caps else None
        supports = elicitation is not None
    if not supports:
        return "unsupported"
    link = verification_link(verification_url, user_code)
    elicitation_id = uuid.uuid4().hex
    try:
        await session.elicit_url(
            message=f"Authorize Yandex MCP: open the link and enter code {user_code}",
            url=link,
            elicitation_id=elicitation_id,
            related_request_id=getattr(ctx, "request_id", None),
        )
        return "url"
    except Exception:
        return "fallback"


def register_auth_tools(
    server: Any,
    config: ServiceConfig,
    transport: str,
    surface: Surface | None = None,
    *,
    runtime: Any | None = None,
    http_factory: Any | None = None,
) -> None:
    """Register yandex_auth_* when the account toolset exposes them (start/finish: stdio only)."""
    if surface is not None and not any(
        surface.exposes(name) for name in ("yandex_auth_start", "yandex_auth_finish", "yandex_auth_status")
    ):
        return

    auth = _authenticated(config, transport)
    local = transport == "stdio"

    if surface is None or surface.exposes("yandex_auth_status"):

        @server.tool(
            auth=auth,
            annotations=annotations_for("yandex_auth_status"),
            description=TOOL_DESCRIPTIONS["yandex_auth_status"],
        )
        async def yandex_auth_status(account: str | None = None) -> dict[str, Any]:
            """Show which Yandex OAuth services have tokens configured (never the values)."""
            live = runtime.config if runtime is not None else config
            _require_account(live, account)
            pending = PENDING.get(_principal(live, transport), account)
            status = auth_status_public(live, account)
            status["pending_device_flow"] = bool(pending and pending.expires_at > time.time())
            if pending and pending.expires_at > time.time():
                status["pending_user_code"] = pending.user_code
                status["pending_expires_in"] = max(0, int(pending.expires_at - time.time()))
            return status

    if local and (surface is None or surface.exposes("yandex_auth_start")):

        @server.tool(
            auth=auth,
            annotations=annotations_for("yandex_auth_start"),
            description=TOOL_DESCRIPTIONS["yandex_auth_start"],
        )
        async def yandex_auth_start(
            client_id: str | None = None,
            services: list[str] | None = None,
            scope: str = "",
            account: str | None = None,
            client_secret: str | None = None,
        ) -> dict[str, Any]:
            """Start Yandex OAuth device-code flow; returns verification URL + user_code (no token)."""
            live = runtime.config if runtime is not None else config
            _require_account(live, account)
            cid, secret = resolve_client_credentials(client_id, client_secret, live.state_path)
            chosen = validate_services(services)
            http = http_factory() if http_factory is not None else None
            codes = await request_device_codes(
                cid, scope=scope.strip(), device_name="yandex-mcp", http=http
            )
            principal = _principal(live, transport)
            pending = PendingAuth(
                device_code=codes.device_code,
                client_id=cid,
                client_secret=secret,
                user_code=codes.user_code,
                verification_url=codes.verification_url,
                interval=codes.interval,
                expires_at=codes.expires_at,
                services=chosen,
                account=account,
            )
            elicitation = await _maybe_elicit_url(codes.verification_url, codes.user_code)
            pending.elicitation_id = uuid.uuid4().hex if elicitation == "url" else ""
            PENDING.put(principal, pending)
            return public_start_payload(pending, elicitation=elicitation)

    if local and (surface is None or surface.exposes("yandex_auth_finish")):

        @server.tool(
            auth=auth,
            annotations=annotations_for("yandex_auth_finish"),
            description=TOOL_DESCRIPTIONS["yandex_auth_finish"],
        )
        async def yandex_auth_finish(
            wait: bool = True,
            account: str | None = None,
            max_wait_seconds: int = 60,
        ) -> dict[str, Any]:
            """Poll the pending device-code flow; on success save tokens (0600) and hot-reload."""
            live = runtime.config if runtime is not None else config
            _require_account(live, account)
            principal = _principal(live, transport)
            pending = PENDING.get(principal, account)
            if pending is None:
                raise ValueError("no pending OAuth flow; call yandex_auth_start first")
            if pending.expires_at <= time.time():
                PENDING.pop(principal, account)
                return {
                    "status": "expired",
                    "hint": "call yandex_auth_start again",
                    "docs": "yandex://guide/oauth",
                }

            deadline = time.time() + (max(5, min(max_wait_seconds, 70)) if wait else 0)
            interval = pending.interval
            http = http_factory() if http_factory is not None else None

            while True:
                result = await poll_device_token(
                    pending.client_id,
                    pending.device_code,
                    client_secret=pending.client_secret,
                    http=http,
                )
                if result.status is PollStatus.SUCCESS and result.tokens is not None:
                    paths = persist_tokens(
                        result.tokens,
                        pending.services,
                        state_path=live.state_path,
                        account=pending.account,
                        client_id=pending.client_id,
                    )
                    if pending.account:
                        # Unlinked files are reported via kept_after_restart / restart_hint.
                        with contextlib.suppress(OSError, ValueError):
                            link_account_token_files(pending.account, paths, pending.services)
                    locations, persistent = public_token_locations(
                        paths, pending.services, state_path=live.state_path, account=pending.account
                    )
                    new_config = apply_tokens_to_config(
                        live, result.tokens, pending.services, account=pending.account
                    )
                    if runtime is not None:
                        runtime.reload_credentials(new_config)
                    PENDING.pop(principal, account)
                    if pending.elicitation_id and get_context is not None:
                        try:
                            ctx = get_context()
                            await ctx.session.send_elicit_complete(pending.elicitation_id)
                        except Exception:
                            pass
                    return {
                        "status": "authorized",
                        "services": list(pending.services),
                        "account": pending.account,
                        # Locations relative to the state directory or the setting that names them.
                        "files": locations,
                        "kept_after_restart": persistent,
                        **_restart_hint(locations, persistent, pending.account),
                        "refresh_token_saved": "refresh" in paths,
                        "hot_reloaded": runtime is not None,
                        "token_returned": False,
                        "docs": "yandex://guide/oauth",
                    }
                if result.status is PollStatus.EXPIRED:
                    PENDING.pop(principal, account)
                    return {
                        "status": "expired",
                        "error": result.error,
                        "hint": "call yandex_auth_start again",
                        "docs": "yandex://guide/oauth",
                    }
                if result.status is PollStatus.ERROR:
                    return {
                        "status": "error",
                        "error": result.error,
                        "hint": "check client_id / client_secret; public apps usually need no secret",
                        "docs": "yandex://guide/oauth",
                    }
                if result.status is PollStatus.SLOW_DOWN:
                    interval = max(interval + 5, result.interval_hint or interval + 5)
                if not wait or time.time() + interval > deadline:
                    return {
                        "status": "authorization_pending",
                        "user_code": pending.user_code,
                        "verification_link": verification_link(
                            pending.verification_url, pending.user_code
                        ),
                        "interval": interval,
                        "expires_in": max(0, int(pending.expires_at - time.time())),
                        "hint": "user has not confirmed yet; call yandex_auth_finish again",
                        "docs": "yandex://guide/oauth",
                    }
                await asyncio.sleep(interval)
