from __future__ import annotations

import argparse
import inspect
import json
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

from zai_yandex import __version__
from zai_yandex.agent_layer import TOOL_DESCRIPTIONS, build_instructions, register_agent_layer
from zai_yandex.audience_tools import register_audience_tools
from zai_yandex.auth_tools import register_auth_tools
from zai_yandex.config import ACTIVE_ACCOUNT, SCOPES, ServiceConfig
from zai_yandex.core_tools import register_core_tools
from zai_yandex.coverage_tools import register_coverage_tools
from zai_yandex.expansion_tools import register_expansion_tools
from zai_yandex.metrika_reports import register_reports
from zai_yandex.offline_conversions import register_offline_conversion_tools
from zai_yandex.onboarding import check_config, load_config
from zai_yandex.runtime import Runtime
from zai_yandex.tool_annotations import annotations_for
from zai_yandex.toolsets import Surface, build_surface, surface_from_env
from zai_yandex.update_check import check_for_update, refresh_in_background, startup_hint
from zai_yandex.webmaster_tools import register_webmaster_tools


class ToolRegistrar:
    def __init__(self, server: FastMCP, runtime: Runtime, surface: Surface | None = None):
        self.server, self.runtime = server, runtime
        self.surface = surface or build_surface()

    def tool(self, **options: Any) -> Callable[..., Any]:
        def decorate(function: Callable[..., Any]) -> Any:
            # Fail closed on unclassified tools before any toolset filtering.
            annotations = annotations_for(function.__name__)
            if not self.surface.exposes(function.__name__):
                return function
            if function.__name__ in TOOL_DESCRIPTIONS:
                options.setdefault("description", TOOL_DESCRIPTIONS[function.__name__])
            signature = inspect.signature(function)
            accounts = self.runtime.config.account_names

            @wraps(function)
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                account = kwargs.pop("account", None)
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                arguments = dict(bound.arguments)
                if account is not None:
                    arguments["account"] = account
                token = ACTIVE_ACCOUNT.set(account)
                try:
                    if accounts:
                        try:
                            self.runtime.config.profile(account)
                        except ValueError as exc:
                            raise self.runtime.safe_error(
                                self.runtime.provider_for(function.__name__), exc
                            ) from None
                    return await self.runtime.execute(
                        function.__name__, arguments, lambda: function(*args, **kwargs)
                    )
                finally:
                    ACTIVE_ACCOUNT.reset(token)

            if accounts:
                # Per-call account selection, advertised only when profiles are configured.
                extra = inspect.Parameter(
                    "account",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Literal[accounts] | None,  # type: ignore[valid-type]
                )
                params = list(signature.parameters.values())
                wrapped.__signature__ = signature.replace(parameters=[*params, extra])  # type: ignore[attr-defined]
                wrapped.__annotations__ = {**function.__annotations__, "account": extra.annotation}
            options.setdefault("annotations", annotations)
            return self.server.tool(**options)(wrapped)

        return decorate


def update_cache_path(config: ServiceConfig) -> Any:
    return config.state_path.parent / "update-check.json"


def register_update_tool(
    server: FastMCP, config: ServiceConfig, transport: str, surface: Surface | None = None
) -> None:
    """Register yandex_check_update outside the provider ledger: it never calls a Yandex API."""
    if surface is not None and not surface.exposes("yandex_check_update"):
        return

    def authenticated(context: Any) -> bool:
        if transport == "stdio":
            return bool(config.local_scopes)
        return bool(context.token and set(context.token.scopes) & SCOPES)

    @server.tool(
        auth=authenticated,
        annotations=annotations_for("yandex_check_update"),
        description=TOOL_DESCRIPTIONS["yandex_check_update"],
    )
    async def yandex_check_update(force: bool = False) -> dict[str, Any]:
        """Check whether a newer Yandex MCP release exists on GitHub. Suggests, never installs.

        Uses the public GitHub API with a short timeout and a 24h cache; never calls Yandex.
        After a failed check it waits 30 minutes; force=true is honoured at most once a minute.
        Set YANDEX_DISABLE_UPDATE_CHECK=1 to turn it off.
        """
        return await check_for_update(update_cache_path(config), force=force)


def register_accounts_tool(
    server: FastMCP, config: ServiceConfig, transport: str, surface: Surface | None = None
) -> None:
    """yandex_list_accounts: local, never calls Yandex; only when profiles are configured."""
    if not config.accounts or (surface is not None and not surface.exposes("yandex_list_accounts")):
        return

    def authenticated(context: Any) -> bool:
        if transport == "stdio":
            return bool(config.local_scopes)
        return bool(context.token and set(context.token.scopes) & SCOPES)

    @server.tool(
        auth=authenticated,
        annotations=annotations_for("yandex_list_accounts"),
        description=TOOL_DESCRIPTIONS["yandex_list_accounts"],
    )
    async def yandex_list_accounts() -> dict[str, Any]:
        return {
            "default": config.default_account,
            "accounts": [profile.public() for profile in config.accounts],
            "usage": "pass account=<name> to any tool; omitted = default",
        }


def create_server(
    config: ServiceConfig,
    *,
    transport: str = "http",
    http_factory: Callable[..., Any] | None = None,
    surface: Surface | None = None,
) -> FastMCP:
    if transport not in {"stdio", "http"}:
        raise ValueError("unsupported transport")
    if transport == "http" and not config.public_key:
        raise ValueError("HTTP requires an RSA public verification key")
    auth = (
        JWTVerifier(
            public_key=config.public_key, algorithm="RS256", issuer=config.issuer, audience=config.audience
        )
        if transport == "http"
        else None
    )
    surface = surface or surface_from_env()
    hint = startup_hint(update_cache_path(config))
    server = FastMCP(
        "Yandex MCP",
        version=__version__,
        auth=auth,
        mask_error_details=True,
        instructions=build_instructions(
            surface, hint, accounts=config.account_names, direct_sandbox=config.direct_sandbox
        ),
    )
    runtime = Runtime(config, transport, http_factory)
    registrar = ToolRegistrar(server, runtime, surface)
    register_core_tools(registrar, runtime)
    register_reports(registrar, runtime)
    register_webmaster_tools(registrar, runtime)
    register_audience_tools(registrar, runtime)
    register_expansion_tools(registrar, runtime)
    register_offline_conversion_tools(registrar, runtime)
    register_coverage_tools(registrar, runtime)
    register_update_tool(server, config, transport, surface)
    register_accounts_tool(server, config, transport, surface)
    register_auth_tools(server, config, transport, surface, runtime=runtime)
    register_agent_layer(server, surface)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Yandex MCP")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8814)
    parser.add_argument("--config", help="Operator JSON settings; paths relative to this file")
    parser.add_argument("--toolsets", help="Toolsets: comma list, 'default' or 'all' (YANDEX_TOOLSETS)")
    parser.add_argument("--read-only", action="store_true", help="Hide every write tool (YANDEX_READ_ONLY)")
    parser.add_argument(
        "--check-config", action="store_true", help="Check local settings without provider calls"
    )
    args = parser.parse_args()
    try:
        load_config(args.config)
        config = ServiceConfig.from_env()
        if args.check_config:
            result = check_config(config, args.transport)
            print(json.dumps(result, sort_keys=True))
            parser.exit(0 if result["ready"] else 2)
        surface = surface_from_env(cli_toolsets=args.toolsets, cli_read_only=args.read_only)
        server = create_server(config, transport=args.transport, surface=surface)
        # Refresh the cached update hint for the next start; never blocks startup.
        refresh_in_background(update_cache_path(config))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"configuration error: {type(exc).__name__}; check credential and policy files\n")
    if args.transport == "http":
        server.run(
            transport="http",
            host=args.host,
            port=args.port,
            stateless_http=True,
            json_response=True,
            show_banner=False,
            log_level="warning",
        )
    else:
        server.run(transport="stdio", show_banner=False, log_level="warning")
