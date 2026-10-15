"""OAuth device-code flow: mocks only (no live Yandex accounts)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import config, connection, token
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.exceptions import ToolError

from zai_yandex.config import AccountProfile, load_accounts
from zai_yandex.oauth_device import (
    PENDING,
    SERVICE_TO_ENV,
    PollStatus,
    TokenBundle,
    apply_tokens_to_config,
    auth_status_public,
    backup_path,
    link_account_token_files,
    persist_tokens,
    poll_device_token,
    public_token_locations,
    replace_private_file,
    request_device_codes,
    validate_client_id,
    write_private_file,
)
from zai_yandex.sanitizer import sanitize_provider_response
from zai_yandex.server import create_server

CLIENT_ID = "a" * 32
DEVICE_CODE = "device-secret-code-never-leak"
USER_CODE = "h5nbcr6c"
ACCESS = "AQAAAA-access-token-fixture-value"
REFRESH = "1:refresh-token-fixture-value"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHttp:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post(self, url: str, *, data=None, headers=None):
        self.calls.append((url, dict(data or {})))
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_pending():
    PENDING.clear()
    yield
    PENDING.clear()


def test_validate_client_id():
    assert validate_client_id(CLIENT_ID) == CLIENT_ID
    with pytest.raises(ValueError):
        validate_client_id("short")


async def test_request_device_codes_success():
    http = FakeHttp(
        [
            FakeResponse(
                200,
                {
                    "device_code": DEVICE_CODE,
                    "user_code": USER_CODE,
                    "verification_url": "https://oauth.yandex.ru/device",
                    "interval": 5,
                    "expires_in": 300,
                },
            )
        ]
    )
    codes = await request_device_codes(CLIENT_ID, http=http)
    assert codes.user_code == USER_CODE
    assert codes.device_code == DEVICE_CODE
    assert "client_id" in http.calls[0][1]
    assert "client_secret" not in http.calls[0][1]


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"error": "authorization_pending"}, PollStatus.PENDING),
        ({"error": "slow_down"}, PollStatus.SLOW_DOWN),
        ({"error": "invalid_grant"}, PollStatus.EXPIRED),
        ({"error": "invalid_client"}, PollStatus.ERROR),
        (
            {
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "expires_in": 3600,
                "token_type": "bearer",
            },
            PollStatus.SUCCESS,
        ),
    ],
)
async def test_poll_device_token_states(payload, status):
    http = FakeHttp([FakeResponse(200 if "access_token" in payload else 400, payload)])
    result = await poll_device_token(CLIENT_ID, DEVICE_CODE, http=http)
    assert result.status is status
    if status is PollStatus.SUCCESS:
        assert result.tokens is not None
        assert result.tokens.access_token == ACCESS
        assert result.tokens.refresh_token == REFRESH
    body = http.calls[0][1]
    assert body["grant_type"] == "device_code"
    assert body["code"] == DEVICE_CODE


def test_persist_tokens_owner_private(tmp_path):
    state = tmp_path / "state" / "yandex.sqlite"
    state.parent.mkdir()
    tokens = TokenBundle(access_token=ACCESS, refresh_token=REFRESH)
    paths = persist_tokens(tokens, ("metrika",), state_path=state, client_id=CLIENT_ID)
    metrika = Path(paths["metrika"])
    assert metrika.read_text(encoding="utf-8") == ACCESS
    if os.name != "nt":
        assert metrika.stat().st_mode & 0o777 == 0o600
        assert Path(paths["refresh"]).stat().st_mode & 0o777 == 0o600
    assert Path(paths["client_id"]).read_text(encoding="utf-8") == CLIENT_ID


def test_hot_reload_base_and_account(tmp_path):
    base = config(tmp_path, direct_token="", metrika_token="")
    tokens = TokenBundle(access_token=ACCESS, refresh_token=REFRESH)
    updated = apply_tokens_to_config(base, tokens, ("metrika", "direct"))
    assert updated.metrika_token == ACCESS and updated.direct_token == ACCESS
    assert base.metrika_token == ""  # frozen original unchanged

    profile = AccountProfile(name="shop-a", metrika_token="")
    with_accounts = replace(base, accounts=(profile,), default_account="shop-a")
    updated_acct = apply_tokens_to_config(with_accounts, tokens, ("metrika",), account="shop-a")
    assert updated_acct.profile("shop-a").metrika_token == ACCESS
    assert updated_acct.metrika_token == ""  # base untouched


def test_redaction_hides_device_and_access_tokens():
    payload = {
        "device_code": DEVICE_CODE,
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "note": f"token={ACCESS}",
    }
    cleaned = sanitize_provider_response(payload)
    blob = json.dumps(cleaned)
    assert DEVICE_CODE not in blob
    assert ACCESS not in blob
    assert REFRESH not in blob
    assert cleaned["device_code"] == "***redacted***"


@pytest.fixture(autouse=True)
def _no_configured_token_files(monkeypatch):
    # Never let a developer's real token file settings leak into these tests.
    for name in (*SERVICE_TO_ENV.values(), "YANDEX_ACCOUNTS_FILE", "YANDEX_OAUTH_CLIENT_ID"):
        monkeypatch.delenv(name, raising=False)


async def test_auth_tools_flow_pending_then_success(tmp_path, monkeypatch):
    settings = config(tmp_path, metrika_token="", direct_token="")
    server = create_server(settings, transport="stdio")

    start_http = FakeHttp(
        [
            FakeResponse(
                200,
                {
                    "device_code": DEVICE_CODE,
                    "user_code": USER_CODE,
                    "verification_url": "https://oauth.yandex.ru/device",
                    "interval": 1,
                    "expires_in": 300,
                },
            )
        ]
    )
    async def fake_request(client_id, **kwargs):
        return await request_device_codes(
            client_id,
            http=start_http,
            **{k: v for k, v in kwargs.items() if k not in {"http", "client_id"}},
        )

    queue = [
        FakeResponse(400, {"error": "authorization_pending"}),
        FakeResponse(
            200,
            {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 99, "token_type": "bearer"},
        ),
    ]

    async def fake_poll(client_id, device_code, **kwargs):
        http = FakeHttp([queue.pop(0)])
        return await poll_device_token(
            client_id, device_code, http=http, client_secret=kwargs.get("client_secret", "")
        )

    monkeypatch.setattr("zai_yandex.auth_tools.request_device_codes", fake_request)
    monkeypatch.setattr("zai_yandex.auth_tools.poll_device_token", fake_poll)

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        assert {"yandex_auth_start", "yandex_auth_finish", "yandex_auth_status"} <= tools

        started = (
            await client.call_tool(
                "yandex_auth_start", {"client_id": CLIENT_ID, "services": ["metrika"]}
            )
        ).data
        assert started["user_code"] == USER_CODE
        assert started["status"] == "pending"
        blob = json.dumps(started)
        assert DEVICE_CODE not in blob and ACCESS not in blob

        pending = (await client.call_tool("yandex_auth_finish", {"wait": False})).data
        assert pending["status"] == "authorization_pending"
        assert ACCESS not in json.dumps(pending)

        done = (await client.call_tool("yandex_auth_finish", {"wait": True, "max_wait_seconds": 5})).data
        assert done["status"] == "authorized"
        assert done["token_returned"] is False
        assert ACCESS not in json.dumps(done)
        # No absolute server paths: the location is relative to the state directory.
        assert done["files"]["metrika"] == "secrets/metrika.token"
        assert str(tmp_path) not in json.dumps(done)
        # The default secrets/metrika.token is auto-loaded at the next start (zai_yandex.defaults).
        assert done["kept_after_restart"] == {"metrika": True} and "restart_hint" not in done
        metrika_path = settings.state_path.parent / done["files"]["metrika"]
        with open(metrika_path, encoding="utf-8") as handle:  # noqa: ASYNC230
            assert handle.read() == ACCESS
        if os.name != "nt":
            assert os.stat(metrika_path).st_mode & 0o777 == 0o600

        status = (await client.call_tool("yandex_auth_status", {})).data
        assert status["services"]["metrika"] is True
        assert ACCESS not in json.dumps(status)


async def test_auth_finish_expired(tmp_path, monkeypatch):
    settings = config(tmp_path)
    server = create_server(settings, transport="stdio")

    async def fake_request(client_id, **kwargs):
        http = FakeHttp(
            [
                FakeResponse(
                    200,
                    {
                        "device_code": DEVICE_CODE,
                        "user_code": USER_CODE,
                        "verification_url": "https://oauth.yandex.ru/device",
                        "interval": 1,
                        "expires_in": 300,
                    },
                )
            ]
        )
        return await request_device_codes(client_id, http=http)

    async def fake_poll(client_id, device_code, **kwargs):
        return await poll_device_token(
            client_id,
            device_code,
            http=FakeHttp([FakeResponse(400, {"error": "invalid_grant"})]),
        )

    monkeypatch.setattr("zai_yandex.auth_tools.request_device_codes", fake_request)
    monkeypatch.setattr("zai_yandex.auth_tools.poll_device_token", fake_poll)

    async with Client(server) as client:
        await client.call_tool("yandex_auth_start", {"client_id": CLIENT_ID})
        result = (await client.call_tool("yandex_auth_finish", {"wait": False})).data
        assert result["status"] == "expired"


async def test_per_account_token_persistence(tmp_path, monkeypatch):
    token_file = tmp_path / "shop.token"
    token_file.write_text("old", encoding="utf-8")
    os.chmod(token_file, 0o600)
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps({"accounts": {"shop": {"metrika_token_file": "shop.token"}}, "default": "shop"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("YANDEX_ACCOUNTS_FILE", str(accounts))
    profiles, default = load_accounts(str(accounts))
    settings = config(tmp_path, accounts=profiles, default_account=default, metrika_token="")
    server = create_server(settings, transport="stdio")

    async def fake_request(client_id, **kwargs):
        http = FakeHttp(
            [
                FakeResponse(
                    200,
                    {
                        "device_code": DEVICE_CODE,
                        "user_code": USER_CODE,
                        "verification_url": "https://oauth.yandex.ru/device",
                        "interval": 1,
                        "expires_in": 300,
                    },
                )
            ]
        )
        return await request_device_codes(client_id, http=http)

    async def fake_poll(client_id, device_code, **kwargs):
        return await poll_device_token(
            client_id,
            device_code,
            http=FakeHttp(
                [
                    FakeResponse(
                        200,
                        {"access_token": ACCESS, "refresh_token": REFRESH, "token_type": "bearer"},
                    )
                ]
            ),
        )

    monkeypatch.setattr("zai_yandex.auth_tools.request_device_codes", fake_request)
    monkeypatch.setattr("zai_yandex.auth_tools.poll_device_token", fake_poll)

    async with Client(server) as client:
        await client.call_tool(
            "yandex_auth_start",
            {"client_id": CLIENT_ID, "services": ["metrika"], "account": "shop"},
        )
        done = (
            await client.call_tool(
                "yandex_auth_finish",
                {"wait": True, "account": "shop", "max_wait_seconds": 5},
            )
        ).data
        assert done["status"] == "authorized"
        assert done["account"] == "shop"
        # The account's configured metrika_token_file is replaced; the old token is kept as .bak.
        assert done["files"]["metrika"] == "YANDEX_ACCOUNTS_FILE:shop.metrika_token_file"
        assert done["kept_after_restart"] == {"metrika": True} and "restart_hint" not in done
        assert token_file.read_text(encoding="utf-8") == ACCESS
        assert (tmp_path / "shop.token.bak").read_text(encoding="utf-8") == "old"
        status = (await client.call_tool("yandex_auth_status", {"account": "shop"})).data
        assert status["services"]["metrika"] is True
    # A restart loads the new token from the accounts file.
    assert load_accounts(str(accounts))[0][0].metrika_token == ACCESS


async def test_new_account_token_file_is_linked_into_accounts_file(tmp_path, monkeypatch):
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"accounts": {"shop": {"direct_client_login": "shop"}}}), encoding="utf-8")
    monkeypatch.setenv("YANDEX_ACCOUNTS_FILE", str(accounts))
    profiles, default = load_accounts(str(accounts))
    settings = config(tmp_path, accounts=profiles, default_account=default)
    paths = persist_tokens(
        TokenBundle(access_token=ACCESS), ("metrika",), state_path=settings.state_path, account="shop"
    )
    assert link_account_token_files("shop", paths, ("metrika",)) == {"metrika"}
    spec = json.loads(accounts.read_text(encoding="utf-8"))["accounts"]["shop"]
    assert spec == {"direct_client_login": "shop", "metrika_token_file": "secrets/shop.metrika.token"}
    assert load_accounts(str(accounts))[0][0].metrika_token == ACCESS
    locations, kept = public_token_locations(
        paths, ("metrika",), state_path=settings.state_path, account="shop"
    )
    assert kept == {"metrika": True} and str(tmp_path) not in json.dumps(locations)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_token_write_only_tightens_directories_it_creates(tmp_path):
    shared = tmp_path / "home"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    write_private_file(shared / "direct.token", "value")
    assert shared.stat().st_mode & 0o777 == 0o755
    nested = shared / "new" / "secrets"
    write_private_file(nested / "x.token", "value")
    assert nested.stat().st_mode & 0o777 == 0o700
    assert nested.parent.stat().st_mode & 0o777 == 0o700
    assert shared.stat().st_mode & 0o777 == 0o755


def test_replacing_a_token_keeps_one_private_backup(tmp_path):
    path = tmp_path / "metrika.token"
    write_private_file(path, "first")
    replace_private_file(path, "second")
    replace_private_file(path, "second")  # Same value: the backup keeps the previous token.
    assert path.read_text(encoding="utf-8") == "second"
    assert backup_path(path).read_text(encoding="utf-8") == "first"
    if os.name != "nt":
        assert backup_path(path).stat().st_mode & 0o777 == 0o600


async def test_http_never_offers_credential_replacement(tmp_path, pair, monkeypatch):
    """Any HTTP principal, whatever its scopes or account, cannot run the device flow."""

    async def fake_request(*args, **kwargs):
        raise AssertionError("device flow must not start over HTTP")

    monkeypatch.setattr("zai_yandex.auth_tools.request_device_codes", fake_request)
    monkeypatch.setattr("zai_yandex.auth_tools.poll_device_token", fake_request)
    server = create_server(config(tmp_path, public_key=pair.public_key), transport="http")
    for bearer in (
        token(pair, scopes=["yandex_metrika:read"], actor="mallory", account="other-account"),
        token(pair),  # Every Yandex scope and the bound account: still not over HTTP.
    ):
        async with connection(server, BearerAuth(bearer)) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert "yandex_auth_status" in names
            assert not names & {"yandex_auth_start", "yandex_auth_finish"}
            with pytest.raises(ToolError):
                await client.call_tool("yandex_auth_start", {"client_id": CLIENT_ID})
            with pytest.raises(ToolError):
                await client.call_tool("yandex_auth_finish", {"wait": False})
    async with connection(server, BearerAuth(token(pair, scopes=["unrelated:scope"]))) as client:
        assert "yandex_auth_status" not in {tool.name for tool in await client.list_tools()}
    assert not (tmp_path / "secrets").exists()


def test_write_private_file_mode(tmp_path):
    path = tmp_path / "secrets" / "x.token"
    write_private_file(path, "secret-value")
    assert path.read_text(encoding="utf-8") == "secret-value"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_auth_status_public_never_embeds_secrets(tmp_path):
    settings = config(tmp_path, metrika_token=ACCESS)
    view = auth_status_public(settings)
    assert view["services"]["metrika"] is True
    assert ACCESS not in json.dumps(view)
