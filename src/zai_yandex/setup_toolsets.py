"""Setup: pick the Yandex platforms you need and persist YANDEX_TOOLSETS (default stays ``all``).

The agent asks which platforms are needed (prompt ``yandex_setup``), then calls
``yandex_setup_toolsets``. With ``write=true`` (local stdio only) it sets only the
``YANDEX_TOOLSETS`` key in the active ``mcp.local.json`` (or creates the per-user one).
Settings from that file win over the client env, so the choice applies on the next start.
No secrets are read, asked for or returned; keys stay in env/config files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from zai_yandex.defaults import default_config_file, user_dir
from zai_yandex.toolsets import ALL_TOOLSETS, TOOLSETS

PLATFORMS: dict[str, tuple[str, ...]] = {
    "direct": ("direct_read", "direct_extra", "direct_reports", "direct_write"),
    "metrika": ("metrika", "metrika_offline", "metrika_logs", "metrika_logs_write", "metrika_write"),
    "webmaster": ("webmaster", "webmaster_extra", "webmaster_write"),
    "audience": ("audience", "audience_write"),
    "wordstat": ("wordstat",),
    "serp": ("search",),
    "maps": ("maps_geosearch",),
    "manage": ("metrika_manage", "webmaster_manage", "audience_manage"),
}
PRESETS: dict[str, tuple[str, ...]] = {
    "direct-only": ("direct",),
    "analytics": ("metrika", "audience"),
    "seo": ("webmaster", "wordstat", "serp"),
    "everything": tuple(PLATFORMS),
}
# Measured with scripts/token_budget.py logic (tools/list JSON + router, no Geosearch key);
# ~tokens = ASCII/4. Kept honest by tests/test_setup_toolsets.py.
PRESET_COST: dict[str, dict[str, int]] = {
    "direct-only": {"tools": 40, "bytes": 29_932, "tokens": 8_055},
    "analytics": {"tools": 30, "bytes": 20_854, "tokens": 5_786},
    "seo": {"tools": 55, "bytes": 33_596, "tokens": 8_971},
    "everything": {"tools": 127, "bytes": 85_656, "tokens": 21_986},
}

# Startup tokens with YANDEX_TOOLSETS=all (tools/list + instructions, ~chars/4); lazy lists a core plus
# yandex_enable_toolsets and adds toolsets on demand. Kept honest by tests/test_lazy_loading.py.
LOADING_COST: dict[str, int] = {"eager": 21_872, "lazy": 2_574}

ACTIVE_CONFIG: str | None = None  # set by server.main to the file it loaded


def resolve_toolsets(platforms: list[str] | None = None, preset: str | None = None) -> str:
    if preset:
        if preset not in PRESETS:
            raise ValueError(f"unknown preset; available: {', '.join(PRESETS)}")
        if preset == "everything":
            return "all"
        platforms = list(PRESETS[preset]) + list(platforms or [])
    if not platforms:
        raise ValueError(f"choose platforms ({', '.join(PLATFORMS)}) or a preset ({', '.join(PRESETS)})")
    names = ["account"]
    for platform in platforms:
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform {platform!r}; available: {', '.join(PLATFORMS)}")
        names += [name for name in PLATFORMS[platform] if name not in names]
    if set(names) >= set(ALL_TOOLSETS):
        return "all"
    assert all(name in TOOLSETS for name in names)
    return ",".join(names)


def target_config(env: Any = None) -> Path:
    env = os.environ if env is None else env
    chosen = ACTIVE_CONFIG or default_config_file(env)
    return Path(chosen).expanduser() if chosen else user_dir(env) / "mcp.local.json"


def write_toolsets(path: Path, value: str, tool_loading: str | None = None) -> None:
    """Set only env.YANDEX_TOOLSETS (and YANDEX_TOOL_LOADING if chosen); keep every other key; 0600."""
    data: dict[str, Any] = {"env": {}}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"env"} or not isinstance(data["env"], dict):
            raise ValueError("configuration must contain only an env object")
    data["env"]["YANDEX_TOOLSETS"] = value
    if tool_loading:
        data["env"]["YANDEX_TOOL_LOADING"] = tool_loading
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def plan(platforms: list[str] | None, preset: str | None, tool_loading: str | None = None) -> dict[str, Any]:
    value = resolve_toolsets(platforms, preset)
    if tool_loading not in (None, "eager", "lazy"):
        raise ValueError("tool_loading must be eager or lazy")
    client_env = {"YANDEX_TOOLSETS": value}
    if tool_loading:
        client_env["YANDEX_TOOL_LOADING"] = tool_loading
    result: dict[str, Any] = {
        "YANDEX_TOOLSETS": value,
        "client_env": client_env,
        "measured_cost": PRESET_COST,
        "tool_loading_cost": LOADING_COST,
        "note": "Default without YANDEX_TOOLSETS is all. Maps tools also need "
        "YANDEX_MAPS_GEOSEARCH_API_KEY_FILE set by the user outside chat. Restart the client to apply.",
    }
    if tool_loading:
        result["YANDEX_TOOL_LOADING"] = tool_loading
    return result


def register_setup_tool(server: Any, config: Any, transport: str, surface: Any = None) -> None:
    """yandex_setup_toolsets + prompt yandex_setup. Local only; never calls Yandex."""
    from zai_yandex.agent_layer import TOOL_DESCRIPTIONS
    from zai_yandex.config import SCOPES
    from zai_yandex.tool_annotations import annotations_for

    if surface is not None and not surface.exposes("yandex_setup_toolsets"):
        return

    def authenticated(context: Any) -> bool:
        if transport == "stdio":
            return bool(config.local_scopes)
        return bool(context.token and set(context.token.scopes) & SCOPES)

    @server.tool(
        auth=authenticated,
        annotations=annotations_for("yandex_setup_toolsets"),
        description=TOOL_DESCRIPTIONS["yandex_setup_toolsets"],
    )
    async def yandex_setup_toolsets(
        platforms: list[str] | None = None,
        preset: str | None = None,
        write: bool = False,
        tool_loading: str | None = None,
    ) -> dict[str, Any]:
        result = plan(platforms, preset, tool_loading)
        if write:
            if transport != "stdio":
                raise ValueError("write=true is local stdio only; on HTTP the operator sets YANDEX_TOOLSETS")
            path = target_config()
            write_toolsets(path, result["YANDEX_TOOLSETS"], result.get("YANDEX_TOOL_LOADING"))
            result.update(written=str(path), restart_required=True)
        return result

    @server.prompt(
        name="yandex_setup",
        description="First-run setup: ask which Yandex platforms and eager/lazy tool loading; save both.",
    )
    def yandex_setup() -> str:
        return (
            "Set up Yandex MCP. Default is ALL tools; picking platforms saves tokens. Ask me which I need: "
            f"{', '.join(PLATFORMS)} - or a preset: {', '.join(PRESETS)}. Show the measured cost per preset "
            "from yandex_setup_toolsets. Then ask tool loading: eager (every tool at start, default) or lazy "
            "(small core + yandex_enable_toolsets, tools added on demand; needs a client that handles "
            "tools/list_changed; see tool_loading_cost). Call it with write=true and tool_loading=<choice>, "
            "then tell me to restart the client. "
            "Never ask me to paste tokens or keys in chat: they go into env/config files "
            "(OAuth: yandex_auth_start)."
        )
