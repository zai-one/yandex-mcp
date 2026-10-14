"""Local operator acceptance of an exact prepared approval; never an MCP tool."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from zai_yandex.config import ACTIVE_ACCOUNT, ServiceConfig
from zai_yandex.onboarding import load_config
from zai_yandex.state import StateStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--approval-id", type=UUID, required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--request-hash", required=True)
    parser.add_argument("--accept", action="store_true", required=True)
    parser.add_argument("--config", help="Operator JSON settings")
    parser.add_argument(
        "--named-account",
        help="Named account (YANDEX_ACCOUNTS_FILE) the change was prepared for; omit for the default",
    )
    args = parser.parse_args()
    if not args.state_path.is_file():
        parser.exit(2, "existing state file required\n")
    try:
        load_config(args.config)
        config = replace(ServiceConfig.from_env(), state_path=args.state_path, account_id=args.account)
        token = ACTIVE_ACCOUNT.set(args.named_account)
        try:
            target = config.selected_account()  # validates the name against the accounts file
            accepted = StateStore(config).accept(
                args.approval_id, principal=args.principal, request_hash=args.request_hash
            )
        finally:
            ACTIVE_ACCOUNT.reset(token)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"configuration error: {type(exc).__name__}\n")
    print(
        json.dumps(
            {"accepted": accepted, "approval_id": str(args.approval_id), "named_account": target}
        )
    )
    raise SystemExit(0 if accepted else 1)


if __name__ == "__main__":
    main()
