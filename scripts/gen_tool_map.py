"""Regenerate src/zai_yandex/resources/guide/tool-map.md (every tool, by toolset, one line each).

Usage: python scripts/gen_tool_map.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    match = re.match(r"(.+?[.;])(\s|$)", text)
    return (match.group(1) if match else text)[:150].rstrip(";")


async def build() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from fastmcp import Client

    from zai_yandex.config import ServiceConfig
    from zai_yandex.server import create_server
    from zai_yandex.toolsets import DEFAULT_TOOLSETS, TOOLSETS, build_surface

    with tempfile.TemporaryDirectory() as tmp:
        config = ServiceConfig(state_path=Path(tmp) / "s.sqlite", direct_token="x", webmaster_token="x")
        server = create_server(config, transport="stdio", surface=build_surface("all"))
        async with Client(server) as client:
            descriptions = {tool.name: tool.description or "" for tool in await client.list_tools()}
    lines = [
        "# Tool map",
        "",
        "Every tool by toolset. Enable a toolset with YANDEX_TOOLSETS (default = marked *).",
        "yandex_list_accounts appears only with YANDEX_ACCOUNTS_FILE.",
        "",
    ]
    for name, spec in TOOLSETS.items():
        star = "*" if name in DEFAULT_TOOLSETS else ""
        lines.append(f"## {name}{star}")
        lines.append(spec["description"])
        for tool in spec["tools"]:
            text = descriptions.get(tool) or "Lists configured named accounts (local)."
            lines.append(f"- `{tool}` - {first_sentence(text)}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    target = ROOT / "src/zai_yandex/resources/guide/tool-map.md"
    target.write_text(asyncio.run(build()), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
