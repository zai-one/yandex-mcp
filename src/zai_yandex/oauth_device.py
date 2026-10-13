"""Yandex OAuth device-code flow (RFC 8628 style) and owner-private token persistence.

Official docs: https://yandex.ru/dev/id/doc/ru/codes/screen-code-oauth
Token refresh: https://yandex.ru/dev/id/doc/ru/tokens/refresh-client

Public clients may poll with client_id only; confidential apps also send client_secret.
Tokens are never returned to callers — only written to 0600 files and hot-reloaded.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

DEVICE_CODE_URL = "https://oauth.yandex.ru/device/code"
TOKEN_URL = "https://oauth.yandex.ru/token"
DEVICE_PAGE = "https://oauth.yandex.ru/device"
CLIENT_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")

# Services that accept a Yandex ID OAuth bearer (not Cloud API keys).
OAUTH_SERVICES = ("direct", "metrika", "webmaster", "audience")
SERVICE_TO_FIELD = {
    "direct": "direct_token",
    "metrika": "metrika_token",
    "webmaster": "webmaster_token",
    "audience": "audience_token",
}
SERVICE_TO_ENV = {
    "direct": "YANDEX_DIRECT_TOKEN_FILE",
    "metrika": "YANDEX_METRIKA_TOKEN_FILE",
    "webmaster": "YANDEX_WEBMASTER_TOKEN_FILE",
    "audience": "YANDEX_AUDIENCE_TOKEN_FILE",
}


class PollStatus(StrEnum):
    PENDING = "authorization_pending"
    SLOW_DOWN = "slow_down"
    SUCCESS = "success"
    EXPIRED = "expired"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DeviceCodes:
    device_code: str = field(repr=False)
    user_code: str
    verification_url: str
    interval: int
    expires_in: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class TokenBundle:
    access_token: str = field(repr=False)
    refresh_token: str = field(default="", repr=False)
    expires_in: int = 0
    scope: str = ""


@dataclass(frozen=True, slots=True)
class PollResult:
    status: PollStatus
    tokens: TokenBundle | None = None
    error: str = ""
    error_description: str = ""
    interval_hint: int | None = None


@dataclass
class PendingAuth:
    """Server-side pending device flow; device_code never leaves this object."""

    device_code: str = field(repr=False)
    client_id: str
    client_secret: str = field(default="", repr=False)
    user_code: str = ""
    verification_url: str = DEVICE_PAGE
    interval: int = 5
    expires_at: float = 0.0
    services: tuple[str, ...] = OAUTH_SERVICES
    account: str | None = None
    elicitation_id: str = ""
    created_at: float = field(default_factory=time.time)


class AuthSessionStore:
    """In-memory pending flows keyed by (principal, account-or-base)."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], PendingAuth] = {}

    @staticmethod
    def _key(principal: str, account: str | None) -> tuple[str, str]:
        return (principal, account or "__base__")

    def put(self, principal: str, pending: PendingAuth) -> None:
        self._pending[self._key(principal, pending.account)] = pending

    def get(self, principal: str, account: str | None = None) -> PendingAuth | None:
        return self._pending.get(self._key(principal, account))

    def pop(self, principal: str, account: str | None = None) -> PendingAuth | None:
        return self._pending.pop(self._key(principal, account), None)

    def clear(self) -> None:
        self._pending.clear()


def validate_client_id(client_id: str) -> str:
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise ValueError("use the 32-character Client ID from your Yandex OAuth application")
    return client_id


def validate_services(services: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not services:
        return OAUTH_SERVICES
    chosen = []
    for name in services:
        key = name.strip().lower()
        if key not in SERVICE_TO_FIELD:
            raise ValueError(f"unknown service; choose from: {', '.join(OAUTH_SERVICES)}")
        if key not in chosen:
            chosen.append(key)
    return tuple(chosen)


async def request_device_codes(
    client_id: str,
    *,
    scope: str = "",
    device_id: str = "",
    device_name: str = "",
    http: httpx.AsyncClient | None = None,
) -> DeviceCodes:
    """POST /device/code — public client_id only (no secret)."""
    validate_client_id(client_id)
    data: dict[str, str] = {"client_id": client_id}
    if scope:
        data["scope"] = scope
    if device_id:
        data["device_id"] = device_id[:50]
        data["device_name"] = (device_name or "yandex-mcp")[:100]
    own = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            DEVICE_CODE_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        payload = response.json()
        if response.status_code != 200 or "device_code" not in payload:
            error = payload.get("error", f"http_{response.status_code}")
            raise ValueError(f"device code request failed: {error}")
        expires_in = int(payload.get("expires_in", 300))
        return DeviceCodes(
            device_code=str(payload["device_code"]),
            user_code=str(payload["user_code"]),
            verification_url=str(payload.get("verification_url") or DEVICE_PAGE),
            interval=max(1, int(payload.get("interval", 5))),
            expires_in=expires_in,
            expires_at=time.time() + expires_in,
        )
    finally:
        if own:
            await client.aclose()


async def poll_device_token(
    client_id: str,
    device_code: str,
    *,
    client_secret: str = "",
    http: httpx.AsyncClient | None = None,
) -> PollResult:
    """One token poll. Never logs or returns the access_token to callers of higher layers."""
    validate_client_id(client_id)
    data: dict[str, str] = {
        "grant_type": "device_code",
        "code": device_code,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    own = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        payload = response.json() if response.content else {}
        if response.status_code == 200 and "access_token" in payload:
            return PollResult(
                status=PollStatus.SUCCESS,
                tokens=TokenBundle(
                    access_token=str(payload["access_token"]),
                    refresh_token=str(payload.get("refresh_token") or ""),
                    expires_in=int(payload.get("expires_in") or 0),
                    scope=str(payload.get("scope") or ""),
                ),
            )
        error = str(payload.get("error") or f"http_{response.status_code}")
        description = str(payload.get("error_description") or "")
        if error == "authorization_pending":
            return PollResult(status=PollStatus.PENDING, error=error, error_description=description)
        if error == "slow_down":
            return PollResult(
                status=PollStatus.SLOW_DOWN,
                error=error,
                error_description=description,
                interval_hint=5,
            )
        if error in {"invalid_grant", "expired_token", "bad_verification_code"}:
            return PollResult(status=PollStatus.EXPIRED, error=error, error_description=description)
        return PollResult(status=PollStatus.ERROR, error=error, error_description=description)
    finally:
        if own:
            await client.aclose()


async def refresh_access_token(
    client_id: str,
    refresh_token: str,
    *,
    client_secret: str = "",
    http: httpx.AsyncClient | None = None,
) -> TokenBundle:
    validate_client_id(client_id)
    if not refresh_token:
        raise ValueError("refresh_token is required")
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    own = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        payload = response.json() if response.content else {}
        if response.status_code != 200 or "access_token" not in payload:
            error = payload.get("error", f"http_{response.status_code}")
            raise ValueError(f"refresh failed: {error}")
        return TokenBundle(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token") or refresh_token),
            expires_in=int(payload.get("expires_in") or 0),
            scope=str(payload.get("scope") or ""),
        )
    finally:
        if own:
            await client.aclose()


def write_private_file(path: Path, value: str) -> Path:
    """Write or replace a credential file as owner-private (0600). Never follow symlinks."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.parent.chmod(0o700)
    # Atomic replace: write temp then rename; keep 0600.
    tmp = path.with_name(path.name + f".tmp-{secrets.token_hex(4)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    return path


def secrets_dir(state_path: Path) -> Path:
    return state_path.parent / "secrets"


def token_path_for(
    service: str,
    *,
    state_path: Path,
    account: str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve where to persist a service token (env path wins for the base account)."""
    env = env or os.environ
    if not account:
        configured = env.get(SERVICE_TO_ENV[service], "").strip()
        if configured:
            return Path(configured).expanduser()
        return secrets_dir(state_path) / f"{service}.token"
    return secrets_dir(state_path) / f"{account}.{service}.token"


def refresh_path(state_path: Path, account: str | None = None) -> Path:
    name = f"{account}.oauth.refresh" if account else "oauth.refresh"
    return secrets_dir(state_path) / name


def client_id_path(state_path: Path) -> Path:
    return secrets_dir(state_path) / "oauth.client_id"


def persist_tokens(
    tokens: TokenBundle,
    services: tuple[str, ...],
    *,
    state_path: Path,
    account: str | None = None,
    client_id: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Write access (and refresh) tokens to 0600 files. Returns path map (no secret values)."""
    written: dict[str, str] = {}
    for service in services:
        path = token_path_for(service, state_path=state_path, account=account, env=env)
        write_private_file(path, tokens.access_token)
        written[service] = str(path)
    if tokens.refresh_token:
        path = refresh_path(state_path, account)
        write_private_file(path, tokens.refresh_token)
        written["refresh"] = str(path)
    if client_id:
        path = client_id_path(state_path)
        write_private_file(path, client_id)
        written["client_id"] = str(path)
    return written


def load_saved_client_id(state_path: Path) -> str:
    path = client_id_path(state_path)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def apply_tokens_to_config(
    config: Any,
    tokens: TokenBundle,
    services: tuple[str, ...],
    account: str | None = None,
) -> Any:
    """Return a new ServiceConfig / profiles with tokens hot-reloaded (frozen replace)."""
    from zai_yandex.config import ServiceConfig

    assert isinstance(config, ServiceConfig)
    if account:
        profiles = []
        for profile in config.accounts:
            if profile.name != account:
                profiles.append(profile)
                continue
            updates = {
                SERVICE_TO_FIELD[service]: tokens.access_token
                for service in services
                if service in SERVICE_TO_FIELD
            }
            profiles.append(replace(profile, **updates))
        if not any(p.name == account for p in config.accounts):
            raise ValueError(f"unknown account; configured: {', '.join(config.account_names) or 'none'}")
        return replace(config, accounts=tuple(profiles))
    updates = {
        SERVICE_TO_FIELD[service]: tokens.access_token
        for service in services
        if service in SERVICE_TO_FIELD
    }
    return replace(config, **updates)


def auth_status_public(config: Any, account: str | None = None) -> dict[str, Any]:
    """Public readiness view: which services have tokens, never the values."""
    from zai_yandex.config import ServiceConfig

    assert isinstance(config, ServiceConfig)
    if account:
        profile = config.profile(account)
        assert profile is not None
        return {
            "account": account,
            "services": {
                name: bool(getattr(profile, field_name) or getattr(config, field_name))
                for name, field_name in SERVICE_TO_FIELD.items()
            },
            "client_id_configured": bool(
                os.environ.get("YANDEX_OAUTH_CLIENT_ID")
            or load_saved_client_id(config.state_path)
            ),
            "refresh_token_saved": refresh_path(config.state_path, account).is_file(),
        }
    return {
        "account": None,
        "services": {
            name: bool(getattr(config, field_name))
            for name, field_name in SERVICE_TO_FIELD.items()
        },
        "client_id_configured": bool(
            os.environ.get("YANDEX_OAUTH_CLIENT_ID")
            or load_saved_client_id(config.state_path)
        ),
        "refresh_token_saved": refresh_path(config.state_path).is_file(),
    }


def verification_link(verification_url: str, user_code: str) -> str:
    """Prefer a deep link that pre-fills the user code when the page supports it."""
    base = verification_url.rstrip("/")
    if "user_code=" in base:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({'user_code': user_code})}"


def resolve_client_credentials(
    client_id: str | None,
    client_secret: str | None,
    state_path: Path,
) -> tuple[str, str]:
    env_id = os.environ.get("YANDEX_OAUTH_CLIENT_ID") or load_saved_client_id(state_path)
    cid = (client_id or env_id or "").strip()
    secret = (client_secret or os.environ.get("YANDEX_OAUTH_CLIENT_SECRET") or "").strip()
    if not cid:
        raise ValueError(
            "OAuth client_id required: pass client_id, set YANDEX_OAUTH_CLIENT_ID, "
            "or complete a prior auth that saved it"
        )
    return validate_client_id(cid), secret


# Keep a module-level store for the running process (one MCP server = one store).
PENDING = AuthSessionStore()


def public_start_payload(pending: PendingAuth, *, elicitation: str = "none") -> dict[str, Any]:
    """Tool-safe start result: no device_code, no secrets."""
    link = verification_link(pending.verification_url, pending.user_code)
    return {
        "status": "pending",
        "user_code": pending.user_code,
        "verification_url": pending.verification_url,
        "verification_link": link,
        "expires_in": max(0, int(pending.expires_at - time.time())),
        "interval": pending.interval,
        "services": list(pending.services),
        "account": pending.account,
        "elicitation": elicitation,
        "next": "Open verification_link, enter user_code if asked, then call yandex_auth_finish.",
        "docs": "yandex://guide/oauth",
    }
