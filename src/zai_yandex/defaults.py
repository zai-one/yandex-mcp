"""Zero-config defaults so `yandex-mcp` starts from any client without --config.

Resolution order for operator settings: ``--config`` > ``YANDEX_MCP_CONFIG`` > the per-user
``mcp.local.json`` (if present). Without any of them the server still starts: state lives in
the per-user data directory (never the client's cwd), and tokens written by the OAuth
device flow (``yandex_auth_start`` -> ``yandex_auth_finish``) are picked up on the next start.
Explicit environment always wins; nothing here calls a provider.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

APP = "zai-yandex-mcp"
SERVICE_TOKEN_ENV = {
    "direct": "YANDEX_DIRECT_TOKEN_FILE",
    "metrika": "YANDEX_METRIKA_TOKEN_FILE",
    "webmaster": "YANDEX_WEBMASTER_TOKEN_FILE",
    "audience": "YANDEX_AUDIENCE_TOKEN_FILE",
}


def user_dir(env: MutableMapping[str, str] | None = None, *, platform: str | None = None) -> Path:
    """Per-user data/config directory: %APPDATA% on Windows, ~/Library/... on macOS, XDG elsewhere."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    override = env.get("YANDEX_MCP_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    if platform.startswith("win"):
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP


def default_config_file(env: MutableMapping[str, str] | None = None) -> str | None:
    """`YANDEX_MCP_CONFIG`, else the per-user mcp.local.json when it exists."""
    env = os.environ if env is None else env
    explicit = env.get("YANDEX_MCP_CONFIG", "").strip()
    if explicit:
        return explicit
    candidate = user_dir(env) / "mcp.local.json"
    return str(candidate) if candidate.is_file() else None


def apply_defaults(env: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Fill unset state/token settings with per-user defaults. Returns what was set (paths only)."""
    env = os.environ if env is None else env
    applied: dict[str, str] = {}
    if not env.get("YANDEX_STATE_PATH", "").strip():
        applied["YANDEX_STATE_PATH"] = str(user_dir(env) / "state" / "yandex.sqlite")
    state = Path(applied.get("YANDEX_STATE_PATH") or env["YANDEX_STATE_PATH"]).expanduser()
    secrets = state.parent / "secrets"
    for service, name in SERVICE_TOKEN_ENV.items():
        token = secrets / f"{service}.token"
        if not env.get(name, "").strip() and token.is_file():
            applied[name] = str(token)
    env.update(applied)
    return applied
