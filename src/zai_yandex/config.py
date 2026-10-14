from __future__ import annotations

import json
import math
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

PROVIDERS = ("yandex_direct", "yandex_metrika", "yandex_search", "yandex_webmaster", "yandex_audience")
SCOPES = frozenset(
    f"{provider}:{action}"
    for provider in PROVIDERS
    for action in (("read", "execute") if provider == "yandex_search" else ("read", "write"))
)
# Source defaults are ceilings; operators may choose stricter local limits.
LIMITS = {
    "yandex_direct": (20, 2),
    "yandex_metrika": (30, 3),
    "yandex_search": (10, 2),
    "yandex_webmaster": (20, 2),
    "yandex_audience": (10, 1),
}


def secret_file(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        return ""
    return read_secret_path(value)


def read_secret_path(value: str) -> str:
    path = Path(value)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("credential file missing or too large")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("credential file must be owner-private")
    result = path.read_text(encoding="utf-8").strip()
    if not result:
        raise ValueError("credential file empty")
    return result


def _maps_key() -> str:
    from zai_yandex.maps_geosearch import load_key

    return load_key()


ACCOUNT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Per-call account selection (set by the tool registrar for one call only).
ACTIVE_ACCOUNT: ContextVar[str | None] = ContextVar("yandex_active_account", default=None)
PROFILE_TOKENS = ("direct_token", "metrika_token", "webmaster_token", "audience_token")
# Providers whose target a named account can change. Their write ledger and approvals
# are partitioned by the resolved account so nothing replays or carries across accounts.
PROFILE_PROVIDERS = frozenset({"yandex_direct", "yandex_metrika", "yandex_webmaster", "yandex_audience"})


def _private_file(value: str, base: Path) -> str:
    path = Path(value) if Path(value).is_absolute() else base / value
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("credential file missing or too large")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("credential file must be owner-private")
    result = path.read_text(encoding="utf-8").strip()
    if not result:
        raise ValueError("credential file empty")
    return result


@dataclass(frozen=True)
class AccountProfile:
    """A named account: own token files and/or a Direct Client-Login (agency client).

    Unset fields inherit the base (env) credentials, so an agency profile can be just
    ``{"direct_client_login": "client-a"}``.
    """

    name: str
    direct_token: str = field(default="", repr=False)
    direct_client_login: str = ""
    metrika_token: str = field(default="", repr=False)
    webmaster_token: str = field(default="", repr=False)
    audience_token: str = field(default="", repr=False)
    direct_sandbox: bool | None = None
    description: str = ""

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "direct_client_login": self.direct_client_login or None,
            "own_tokens": [key.removesuffix("_token") for key in PROFILE_TOKENS if getattr(self, key)],
            "direct_sandbox": self.direct_sandbox,
        }


def load_accounts(filename: str) -> tuple[tuple[AccountProfile, ...], str | None]:
    """Parse YANDEX_ACCOUNTS_FILE: {"default": "name"?, "accounts": {name: {...}}}."""
    if not filename:
        return (), None
    path = Path(filename)
    if not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("accounts file missing or too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or not 1 <= len(accounts) <= 500 or set(data) - {"default", "accounts"}:
        raise ValueError("accounts file needs an accounts object with 1..500 entries")
    allowed = {f"{key}_file" for key in PROFILE_TOKENS} | {
        "direct_client_login",
        "direct_sandbox",
        "description",
    }
    profiles = []
    for name, spec in accounts.items():
        if not isinstance(name, str) or not ACCOUNT_NAME.match(name):
            raise ValueError("account names must match [a-z0-9][a-z0-9_-]{0,63}")
        if not isinstance(spec, dict) or set(spec) - allowed:
            raise ValueError(f"account {name}: allowed keys are {', '.join(sorted(allowed))}")
        tokens = {
            key: _private_file(str(spec[f"{key}_file"]), path.parent)
            for key in PROFILE_TOKENS
            if spec.get(f"{key}_file")
        }
        sandbox = spec.get("direct_sandbox")
        if sandbox is not None and not isinstance(sandbox, bool):
            raise ValueError(f"account {name}: direct_sandbox must be true or false")
        profiles.append(
            AccountProfile(
                name=name,
                direct_client_login=str(spec.get("direct_client_login", ""))[:255],
                direct_sandbox=sandbox,
                description=str(spec.get("description", ""))[:200],
                **tokens,
            )
        )
    default = data.get("default")
    if default is not None and default not in accounts:
        raise ValueError("default account is not defined")
    return tuple(profiles), default


def flag(name: str) -> bool:
    value = os.environ.get(name, "false")
    if value not in {"true", "false"}:
        raise ValueError("boolean flags must be true or false")
    return value == "true"


DIRECT_API_URL = "https://api.direct.yandex.com/json/v5"
DIRECT_SANDBOX_URL = "https://api-sandbox.direct.yandex.com/json/v5"


@dataclass(frozen=True)
class ServiceConfig:
    state_path: Path
    account_id: str = "default"
    principal_id: str = "local-operator"
    direct_token: str = field(default="", repr=False)
    direct_client_login: str = ""
    metrika_token: str = field(default="", repr=False)
    search_api_key: str = field(default="", repr=False)
    search_folder_id: str = ""
    webmaster_token: str = field(default="", repr=False)
    audience_token: str = field(default="", repr=False)
    # Opt-in Maps organization search (Geosearch) key; tools exist only when it is set.
    maps_geosearch_api_key: str = field(default="", repr=False)
    public_key: str = ""
    issuer: str = "yandex-operator"
    audience: str = "yandex-mcp"
    local_scopes: frozenset[str] = SCOPES
    yandex_direct_write_enabled: bool = False
    yandex_metrika_write_enabled: bool = False
    # Logs API requests spend the per-counter 10 GB storage quota; opt-in like other writes.
    yandex_metrika_logs_enabled: bool = False
    yandex_webmaster_write_enabled: bool = False
    yandex_audience_write_enabled: bool = False
    yandex_wordstat_live_enabled: bool = False
    # https://yandex.ru/dev/direct/doc/ru/concepts/sandbox : same API, test data only.
    direct_sandbox: bool = False
    yandex_wordstat_cost_per_call: float = 0
    yandex_wordstat_max_cost_per_call: float = 0
    yandex_search_max_cost_per_approval: float = 0
    principal_monthly_cost_limit: float = 0
    account_monthly_cost_limit: float = 0
    request_rate_limit: int = 10
    principal_rate_limit: int = 10
    max_concurrency: int = 2
    accounts: tuple[AccountProfile, ...] = ()
    default_account: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(v, str) and 1 <= len(v) <= 256
            for v in (self.account_id, self.principal_id, self.issuer, self.audience)
        ):
            raise ValueError("bounded identity fields required")
        if not self.local_scopes <= SCOPES:
            raise ValueError("unknown local scopes")
        if any(not isinstance(v, str) or len(v) > 4096 for v in self.secrets):
            raise ValueError("invalid credentials")
        if not 1 <= self.principal_rate_limit <= self.request_rate_limit <= 1000:
            raise ValueError("invalid request limits")
        if not 1 <= self.max_concurrency <= 20:
            raise ValueError("invalid concurrency limit")
        for value in (
            self.yandex_wordstat_cost_per_call,
            self.yandex_wordstat_max_cost_per_call,
            self.yandex_search_max_cost_per_approval,
            self.principal_monthly_cost_limit,
            self.account_monthly_cost_limit,
        ):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1_000_000:
                raise ValueError("finite nonnegative cost limits required")
            scaled = Decimal(str(value)) * 1_000_000
            if scaled != scaled.to_integral_value():
                raise ValueError("cost precision is limited to six decimal places")

    @property
    def secrets(self) -> tuple[str, ...]:
        return (
            self.direct_token,
            self.metrika_token,
            self.search_api_key,
            self.webmaster_token,
            self.audience_token,
            self.maps_geosearch_api_key,
            *(getattr(profile, key) for profile in self.accounts for key in PROFILE_TOKENS),
        )

    @property
    def direct_url(self) -> str:
        return DIRECT_SANDBOX_URL if self.direct_sandbox else DIRECT_API_URL

    @property
    def account_names(self) -> tuple[str, ...]:
        return tuple(profile.name for profile in self.accounts)

    def profile(self, name: str | None) -> AccountProfile | None:
        name = name or self.default_account
        if name is None:
            return None
        for profile in self.accounts:
            if profile.name == name:
                return profile
        raise ValueError(f"unknown account; configured: {', '.join(self.account_names) or 'none'}")

    def selected_account(self) -> str | None:
        """Name of the account resolved for this call (explicit or default); None = base."""
        profile = self.profile(ACTIVE_ACCOUNT.get())
        return profile.name if profile else None

    def ledger_scope(self, provider: str) -> str | None:
        """Write target beyond (account_id, actor, provider), mixed into ledger keys and approvals.

        The resolved named account and, for Direct, the sandbox endpoint. None for the
        base production target, so ledger rows and approvals recorded before named
        accounts and the sandbox existed keep their meaning.
        """
        if provider not in PROFILE_PROVIDERS:
            return None
        name = self.selected_account()
        parts = [f"account={name}"] if name else []
        if provider == "yandex_direct" and self.effective().direct_sandbox:
            parts.append("endpoint=sandbox")
        return ";".join(parts) or None

    def effective(self) -> ServiceConfig:
        """Credentials of the account selected for this call (base config when none)."""
        profile = self.profile(ACTIVE_ACCOUNT.get())
        if profile is None:
            return self
        overrides: dict[str, object] = {
            key: getattr(profile, key) for key in PROFILE_TOKENS if getattr(profile, key)
        }
        if profile.direct_client_login:
            overrides["direct_client_login"] = profile.direct_client_login
        if profile.direct_sandbox is not None:
            overrides["direct_sandbox"] = profile.direct_sandbox
        return replace(self, accounts=(), default_account=None, **overrides)

    def enabled(self, provider: str) -> bool:
        return {
            "yandex_direct": bool(self.direct_token),
            "yandex_metrika": bool(self.metrika_token),
            "yandex_search": bool(self.search_api_key and self.search_folder_id),
            "yandex_webmaster": bool(self.webmaster_token),
            "yandex_audience": bool(self.audience_token),
        }.get(provider, False)

    @classmethod
    def from_env(cls) -> ServiceConfig:
        public_path = os.environ.get("YANDEX_MCP_PUBLIC_KEY_FILE", "")
        return cls(
            state_path=Path(os.environ.get("YANDEX_STATE_PATH", "state/yandex.sqlite")),
            account_id=os.environ.get("YANDEX_ACCOUNT_ID", "default"),
            principal_id=os.environ.get("YANDEX_LOCAL_PRINCIPAL", "local-operator"),
            direct_token=secret_file("YANDEX_DIRECT_TOKEN_FILE"),
            direct_client_login=os.environ.get("YANDEX_DIRECT_CLIENT_LOGIN", ""),
            metrika_token=secret_file("YANDEX_METRIKA_TOKEN_FILE"),
            search_api_key=secret_file("YANDEX_SEARCH_API_KEY_FILE"),
            search_folder_id=os.environ.get("YANDEX_SEARCH_FOLDER_ID", ""),
            webmaster_token=secret_file("YANDEX_WEBMASTER_TOKEN_FILE"),
            audience_token=secret_file("YANDEX_AUDIENCE_TOKEN_FILE"),
            maps_geosearch_api_key=_maps_key(),
            public_key=Path(public_path).read_text(encoding="utf-8") if public_path else "",
            issuer=os.environ.get("YANDEX_MCP_ISSUER", "yandex-operator"),
            audience=os.environ.get("YANDEX_MCP_AUDIENCE", "yandex-mcp"),
            yandex_direct_write_enabled=flag("YANDEX_DIRECT_WRITE_ENABLED"),
            yandex_metrika_write_enabled=flag("YANDEX_METRIKA_WRITE_ENABLED"),
            yandex_metrika_logs_enabled=flag("YANDEX_METRIKA_LOGS_ENABLED"),
            yandex_webmaster_write_enabled=flag("YANDEX_WEBMASTER_WRITE_ENABLED"),
            yandex_audience_write_enabled=flag("YANDEX_AUDIENCE_WRITE_ENABLED"),
            yandex_wordstat_live_enabled=flag("YANDEX_WORDSTAT_LIVE_ENABLED"),
            direct_sandbox=flag("YANDEX_DIRECT_SANDBOX"),
            **{
                name: float(os.environ.get(name.upper(), "0"))
                for name in (
                    "yandex_wordstat_cost_per_call",
                    "yandex_wordstat_max_cost_per_call",
                    "yandex_search_max_cost_per_approval",
                )
            },
            principal_monthly_cost_limit=float(os.environ.get("YANDEX_PRINCIPAL_MONTHLY_COST_LIMIT", "0")),
            account_monthly_cost_limit=float(os.environ.get("YANDEX_ACCOUNT_MONTHLY_COST_LIMIT", "0")),
            request_rate_limit=int(os.environ.get("YANDEX_RATE_LIMIT", "10")),
            principal_rate_limit=int(os.environ.get("YANDEX_PRINCIPAL_RATE_LIMIT", "10")),
            max_concurrency=int(os.environ.get("YANDEX_MAX_CONCURRENCY", "2")),
            **dict(
                zip(
                    ("accounts", "default_account"),
                    load_accounts(os.environ.get("YANDEX_ACCOUNTS_FILE", "")),
                    strict=True,
                )
            ),
        )
