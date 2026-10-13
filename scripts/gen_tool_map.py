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


def build_management() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from zai_yandex.management import CATALOG, MANAGEMENT_TOOLS

    lines = [
        "# Management catalogue",
        "",
        "Opt-in toolsets metrika_manage, webmaster_manage, audience_manage. Generated from",
        "src/zai_yandex/management.py (python scripts/gen_tool_map.py). Each provider has",
        "`<p>_manage_read` (GET; operation='catalog' lists all), `<p>_manage_prepare` -> user",
        "accepts the approval (approvals CLI, same account) -> `<p>_manage_apply` with idempotency_key",
        "-> readback; on timeout `<p>_manage_reconcile` (never resend). Writes need the provider",
        "write flag (YANDEX_METRIKA/WEBMASTER/AUDIENCE_WRITE_ENABLED).",
        "args: path ids (counter_id, id, label_id, host_id, sitemap_id, task_id, segment_id, pixel_id),",
        "query{...} (only listed keys), body (documented object; wrapper key optional), csv (uploads).",
        "",
        "Accepting an approval (operator, on the server machine; never an MCP tool):",
        "",
        "```",
        "python -m zai_yandex.approvals --state-path <state.sqlite> --account <YANDEX_ACCOUNT_ID> \\",
        "  --approval-id <approval_id> --principal <principal> --request-hash <request_hash> \\",
        "  [--named-account <account from the prepare result>] --accept",
        "```",
        "",
        "`--named-account` must be the account the change was prepared for (omit for the default).",
        "A mismatch is refused; apply also rejects approvals from another account, endpoint or request.",
        "",
    ]
    for (provider, ops), prefix in zip(CATALOG.items(), MANAGEMENT_TOOLS, strict=True):
        tools = ", ".join(f"`{name}`" for name in MANAGEMENT_TOOLS[prefix])
        header = ["| operation | kind/risk | HTTP | doc |", "|---|---|---|---|"]
        lines += [f"## {provider} ({tools})", "", *header]
        for name, op in sorted(ops.items()):
            risk = "read" if op.kind == "read" else f"{op.kind}/{op.risk}"
            lines.append(f"| {name} | {risk} | {op.method} {op.path.removeprefix('v4.1:')} | {op.doc} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    target = ROOT / "src/zai_yandex/resources/guide/tool-map.md"
    target.write_text(asyncio.run(build()), encoding="utf-8")
    management = ROOT / "src/zai_yandex/resources/guide/management.md"
    text = build_management()
    management.write_text(text, encoding="utf-8")
    # Self-contained skill: its reference is a byte-identical copy of the guide.
    (ROOT / "skills/yandex-account-admin/references/management.md").write_text(text, encoding="utf-8")
    print(target, management)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
