from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

PROVIDERS = ("yandex_direct", "yandex_metrika", "yandex_search", "yandex_webmaster")
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
}


def secret_file(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        return ""
    path = Path(value)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("credential file missing or too large")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("credential file must be owner-private")
    result = path.read_text(encoding="utf-8").strip()
    if not result:
        raise ValueError("credential file empty")
    return result


def flag(name: str) -> bool:
    value = os.environ.get(name, "false")
    if value not in {"true", "false"}:
        raise ValueError("boolean flags must be true or false")
    return value == "true"


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
    public_key: str = ""
    issuer: str = "yandex-operator"
    audience: str = "yandex-mcp"
    local_scopes: frozenset[str] = SCOPES
    yandex_direct_write_enabled: bool = False
    yandex_metrika_write_enabled: bool = False
    yandex_webmaster_write_enabled: bool = False
    yandex_wordstat_live_enabled: bool = False
    yandex_wordstat_cost_per_call: float = 0
    yandex_wordstat_max_cost_per_call: float = 0
    yandex_search_max_cost_per_approval: float = 0
    principal_monthly_cost_limit: float = 0
    account_monthly_cost_limit: float = 0
    request_rate_limit: int = 10
    principal_rate_limit: int = 10
    max_concurrency: int = 2

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
        return (self.direct_token, self.metrika_token, self.search_api_key, self.webmaster_token)

    def enabled(self, provider: str) -> bool:
        return {
            "yandex_direct": bool(self.direct_token),
            "yandex_metrika": bool(self.metrika_token),
            "yandex_search": bool(self.search_api_key and self.search_folder_id),
            "yandex_webmaster": bool(self.webmaster_token),
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
            public_key=Path(public_path).read_text(encoding="utf-8") if public_path else "",
            issuer=os.environ.get("YANDEX_MCP_ISSUER", "yandex-operator"),
            audience=os.environ.get("YANDEX_MCP_AUDIENCE", "yandex-mcp"),
            yandex_direct_write_enabled=flag("YANDEX_DIRECT_WRITE_ENABLED"),
            yandex_metrika_write_enabled=flag("YANDEX_METRIKA_WRITE_ENABLED"),
            yandex_webmaster_write_enabled=flag("YANDEX_WEBMASTER_WRITE_ENABLED"),
            yandex_wordstat_live_enabled=flag("YANDEX_WORDSTAT_LIVE_ENABLED"),
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
        )
