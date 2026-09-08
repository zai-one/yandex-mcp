"""Freeze original authenticated Yandex contracts before replacing either gateway binding."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp_platform.app import create_platform
from mcp_platform.auth.scopes import ROLE_SCOPES
from mcp_platform.auth.service import TokenService
from mcp_platform.providers.registry import ProviderRegistry
from mcp_platform.settings import Settings
from mcp_platform.store import InMemoryStore


async def main():
    output, group = Path(sys.argv[1]), sys.argv[2]
    assert group in {"core", "webmaster"}
    if group == "webmaster":
        import httpx2 as client_http
    else:
        client_http = httpx
    if output.exists():
        raise SystemExit("baseline already exists")
    settings = Settings(environment="test", public_base_url="http://fixture")
    store = InMemoryStore()
    principal = await store.create_principal("contract-freeze")
    scopes = ROLE_SCOPES["operator"] | {
        "yandex_direct:read", "yandex_direct:write", "yandex_metrika:read", "yandex_metrika:write",
        "yandex_search:read", "yandex_search:execute"}
    if group == "webmaster":
        scopes |= {"yandex_webmaster:read", "yandex_webmaster:write"}
    issued = await TokenService(store, settings.token_pepper()).issue(principal, "contract-freeze", scopes)
    server = create_platform(settings, store, ProviderRegistry(settings, store))
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    def factory(**kwargs):
        kwargs.pop("verify", None)
        return client_http.AsyncClient(transport=client_http.ASGITransport(app=app), base_url="http://fixture", **kwargs)
    transport = StreamableHttpTransport("http://fixture/mcp", auth=issued.plaintext, httpx_client_factory=factory)
    prefixes = ("webmaster_",) if group == "webmaster" else ("direct_", "metrika_", "yandex_wordstat_", "yandex_serp_")
    async with app.router.lifespan_context(app), Client(transport) as client:
        tools = {tool.name: {"inputSchema": tool.inputSchema, "outputSchema": tool.outputSchema,
                             "description": tool.description}
                 for tool in await client.list_tools() if tool.name.startswith(prefixes)}
    assert len(tools) == (27 if group == "webmaster" else 17), list(tools)
    receipt = {"gateway_revision": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
               "method": "authenticated runtime discovery; no provider API calls", "group": group, "tools": tools}
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Froze {len(tools)} original {group} schemas/descriptions")


asyncio.run(main())
