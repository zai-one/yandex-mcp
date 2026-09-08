from __future__ import annotations

import argparse
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

from zai_yandex import __version__
from zai_yandex.config import ServiceConfig
from zai_yandex.core_tools import register_core_tools
from zai_yandex.runtime import Runtime
from zai_yandex.webmaster_tools import register_webmaster_tools


class ToolRegistrar:
    def __init__(self, server: FastMCP, runtime: Runtime):
        self.server, self.runtime = server, runtime

    def tool(self, **options: Any) -> Callable[..., Any]:
        def decorate(function: Callable[..., Any]) -> Any:
            signature = inspect.signature(function)

            @wraps(function)
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                return await self.runtime.execute(
                    function.__name__, dict(bound.arguments), lambda: function(*args, **kwargs)
                )

            return self.server.tool(**options)(wrapped)

        return decorate


def create_server(
    config: ServiceConfig, *, transport: str = "http", http_factory: Callable[..., Any] | None = None
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
    server = FastMCP("Yandex MCP", version=__version__, auth=auth, mask_error_details=True)
    runtime = Runtime(config, transport, http_factory)
    registrar = ToolRegistrar(server, runtime)
    register_core_tools(registrar, runtime)
    register_webmaster_tools(registrar, runtime)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Yandex MCP")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8814)
    args = parser.parse_args()
    try:
        server = create_server(ServiceConfig.from_env(), transport=args.transport)
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
