from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import RSAKeyPair

from zai_yandex.config import SCOPES, ServiceConfig
from zai_yandex.runtime import AdmittedHttpClient

SECRET = "synthetic-yandex-credential-never-live"
HOST = "https:example.test:443"
URL = "https://example.test/page"


@pytest.fixture(autouse=True)
def _no_update_check_network(monkeypatch):
    # Tests never contact GitHub; update-check tests re-enable it with mocked HTTP.
    monkeypatch.setenv("YANDEX_DISABLE_UPDATE_CHECK", "1")


@pytest.fixture(scope="session")
def pair():
    return RSAKeyPair.generate()


def config(path, **kwargs):
    return replace(
        ServiceConfig(
            state_path=path / "state.sqlite",
            direct_token=SECRET,
            metrika_token=SECRET,
            search_api_key=SECRET,
            search_folder_id="synthetic-folder",
            webmaster_token=SECRET,
            audience_token=SECRET,
            request_rate_limit=30,
            principal_rate_limit=30,
        ),
        **kwargs,
    )


def token(pair, *, scopes=None, actor="alice", account="default", **kwargs):
    return pair.create_token(
        subject=actor,
        issuer="yandex-operator",
        audience="yandex-mcp",
        scopes=list(SCOPES) if scopes is None else scopes,
        expires_in_seconds=60,
        additional_claims={"account_id": account, **kwargs},
    )


@asynccontextmanager
async def connection(server, bearer):
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    async with (
        app.router.lifespan_context(app),
        Client(
            StreamableHttpTransport("http://fixture/mcp", auth=bearer, httpx_client_factory=client_factory)
        ) as client,
    ):
        yield client


class Recorder:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or self.default

    @staticmethod
    def default(method, url, payload, params):
        if "api.direct." in url:
            return {"result": {"Campaigns": [{"Id": 7, "Name": "Example"}]}, "note": SECRET}
        if "api-metrika." in url:
            return {"goal": {"id": 9, "name": "Example"}, "goals": [], "counters": []}
        if url.endswith("/user"):
            return {"user_id": 42}
        if url.endswith("/hosts"):
            return {"hosts": []}
        if url.endswith("/recrawl/queue"):
            return {"tasks": [], "count": 0}
        if url.endswith("/recrawl/quota"):
            return {"daily_quota": 10, "quota_remainder": 10}
        if "/hosts/" in url:
            return {"host_id": HOST, "verified": True, "ascii_host_url": "https://example.test"}
        if url.endswith("/web/searchAsync"):
            return {"id": "operation-1"}
        if "/operations/" in url:
            return {"done": True, "response": {"@type": "fixture", "rawData": "aGVsbG8="}}
        return {"totalCount": 0, "results": []}

    def factory(self, policy, execution_id, actor):
        recorder = self

        class Http(AdmittedHttpClient):
            async def request(
                self, method: str, url: str, *, headers=None, payload=None, params=None, idempotent=False
            ) -> Any:
                await self._admit_attempt(1)
                recorder.calls.append((method, url, payload, actor, idempotent))
                result = recorder.handler(method, url, payload, params)
                if hasattr(result, "__await__"):
                    return await result
                return result

        return Http(policy, execution_id, actor)
