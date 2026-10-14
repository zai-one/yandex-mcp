"""Token budget for the agent-facing surface (enforced in CI via tests/test_agent_layer.py).

Checks: router instructions <= 1700 chars (1800 with the key-gated Geosearch line), each tool
description <= 300 chars, tools/list JSON per preset <= BUDGET_BYTES, skills frontmatter and
file references valid.
Usage: python scripts/token_budget.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET_BYTES = {"default": 34_000, "all": 92_000, "default+maps": 37_000}
# Estimated tokens an agent pays at startup: instructions + tools/list + every skill frontmatter.
STARTUP_TOKENS = {"default": 9_500, "all": 24_500, "default+maps": 10_500}
# default/all stay at 1700; the Geosearch router line exists only with a key (default+maps).
INSTRUCTIONS_CHARS = {"default": 1700, "all": 1700, "default+maps": 1800}


def estimate_tokens(text: str) -> int:
    """Conservative estimate: ~4 ASCII chars or ~2 Cyrillic chars per token."""
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return ascii_chars // 4 + (len(text) - ascii_chars) // 2 + 1


def skill_frontmatters(root: Path = ROOT) -> str:
    parts = []
    for skill in sorted((root / "skills").iterdir()):
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        parts.append(text.split("\n---\n", 1)[0])
    return "\n".join(parts)


NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def skill_problems(root: Path = ROOT) -> list[str]:
    problems = []
    for skill in sorted((root / "skills").iterdir()):
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not match:
            problems.append(f"{skill.name}: missing frontmatter")
            continue
        lines = [line for line in match.group(1).splitlines() if ":" in line and not line.startswith(" ")]
        meta = dict(line.split(":", 1) for line in lines)
        name, description = meta.get("name", "").strip(), meta.get("description", "").strip()
        if name != skill.name or not NAME.match(name) or len(name) > 64:
            problems.append(f"{skill.name}: name must equal folder and match a-z0-9-")
        if not 1 <= len(description) <= 300:
            problems.append(f"{skill.name}: description must be 1..300 chars (got {len(description)})")
        if len(match.group(2).splitlines()) > 80:
            problems.append(f"{skill.name}: SKILL.md body over 80 lines")
        for ref in re.findall(r"`((?:references|templates|scripts|assets)/[^`]+)`", match.group(2)):
            if not (skill / ref).is_file():
                problems.append(f"{skill.name}: missing {ref}")
    return problems


async def surface_sizes(config) -> dict[str, dict]:
    from fastmcp import Client

    from zai_yandex.server import create_server
    from zai_yandex.toolsets import build_surface

    out = {}
    for preset in BUDGET_BYTES:
        toolsets, maps = preset.split("+")[0], preset.endswith("+maps")
        server = create_server(
            replace(config, maps_geosearch_api_key="synthetic" if maps else ""),
            transport="stdio",
            surface=build_surface(toolsets, maps_key=maps),
        )
        async with Client(server) as client:
            tools = await client.list_tools()
        payload = json.dumps([tool.model_dump(mode="json", exclude_none=True) for tool in tools])
        out[preset] = {
            "tools": len(tools),
            "bytes": len(payload.encode()),
            "instructions": len(server.instructions or ""),
            "startup_tokens": estimate_tokens((server.instructions or "") + payload + skill_frontmatters()),
            "long_descriptions": [t.name for t in tools if len(t.description or "") > 300],
        }
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from zai_yandex.config import ServiceConfig

    with tempfile.TemporaryDirectory() as tmp:
        config = ServiceConfig(
            state_path=Path(tmp) / "state.sqlite",
            direct_token="synthetic",
            metrika_token="synthetic",
            search_api_key="synthetic",
            search_folder_id="synthetic-folder",
            webmaster_token="synthetic",
            audience_token="synthetic",
        )
        sizes = asyncio.run(surface_sizes(config))
    problems = skill_problems()
    for preset, info in sizes.items():
        if info["bytes"] > BUDGET_BYTES[preset]:
            problems.append(f"{preset}: tools/list {info['bytes']} B > {BUDGET_BYTES[preset]} B")
        if info["instructions"] > INSTRUCTIONS_CHARS[preset]:
            limit = INSTRUCTIONS_CHARS[preset]
            problems.append(f"{preset}: instructions {info['instructions']} > {limit} chars")
        if info["startup_tokens"] > STARTUP_TOKENS[preset]:
            problems.append(f"{preset}: startup ~{info['startup_tokens']} tokens > {STARTUP_TOKENS[preset]}")
        problems += [f"{preset}: description over 300 chars: {name}" for name in info["long_descriptions"]]
    print(json.dumps({"sizes": sizes, "problems": problems}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
