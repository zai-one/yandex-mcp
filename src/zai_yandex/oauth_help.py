"""Local OAuth helpers: implicit-flow URL and device-code CLI (never echo tokens)."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from zai_yandex.oauth_device import (
    PollStatus,
    persist_tokens,
    poll_device_token,
    request_device_codes,
    validate_client_id,
    validate_services,
    verification_link,
)


def authorization_url(client_id: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", client_id):
        raise ValueError("use the 32-character Client ID from your Yandex OAuth application")
    return "https://oauth.yandex.ru/authorize?" + urlencode(
        {"response_type": "token", "client_id": client_id}
    )


async def run_device_flow(
    client_id: str,
    *,
    client_secret: str = "",
    services: list[str] | None = None,
    scope: str = "",
    directory: Path,
    poll: bool = True,
) -> int:
    """Interactive device-code flow for a local terminal; writes 0600 token files."""
    validate_client_id(client_id)
    chosen = validate_services(services)
    codes = await request_device_codes(client_id, scope=scope, device_name="yandex-mcp-cli")
    link = verification_link(codes.verification_url, codes.user_code)
    print(f"Open: {link}")
    print(f"User code: {codes.user_code}")
    print(f"Expires in {codes.expires_in}s. Confirm access in the browser.")
    if not poll:
        print(
        "Polling skipped (--no-poll). Use yandex_auth_finish from the MCP server, "
        "or re-run without --no-poll."
    )
        return 0
    interval = codes.interval
    deadline = time.time() + codes.expires_in
    while time.time() < deadline:
        result = await poll_device_token(client_id, codes.device_code, client_secret=client_secret)
        if result.status is PollStatus.SUCCESS and result.tokens is not None:
            state_path = directory / "state" / "yandex.sqlite"
            paths = persist_tokens(
                result.tokens,
                chosen,
                state_path=state_path,
                client_id=client_id,
            )
            print("Authorized. Token files (owner-private):")
            for service, path in paths.items():
                if service not in {"refresh", "client_id"}:
                    print(f"  {service}: {path}")
            if "refresh" in paths:
                print(f"  refresh: {paths['refresh']}")
            print(
                "Point YANDEX_*_TOKEN_FILE at these paths (or re-run yandex-mcp-setup). "
                "Token value not printed."
            )
            return 0
        if result.status is PollStatus.EXPIRED:
            print("Code expired. Re-run the command.", file=sys.stderr)
            return 2
        if result.status is PollStatus.ERROR:
            print(f"OAuth error: {result.error}", file=sys.stderr)
            if result.error == "invalid_client" and not client_secret:
                print(
                    "Hint: some Yandex apps require client_secret for /token; "
                    "pass --client-secret or set YANDEX_OAUTH_CLIENT_SECRET.",
                    file=sys.stderr,
                )
            return 2
        if result.status is PollStatus.SLOW_DOWN:
            interval += 5
        await asyncio.sleep(interval)
    print("Timed out waiting for confirmation.", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local Yandex OAuth help: implicit URL or device-code flow (tokens never printed)"
    )
    parser.add_argument(
        "--client-id", required=True, help="Public application Client ID (32 hex chars)"
    )
    parser.add_argument(
        "--device",
        action="store_true",
        help="Run device-code flow (POST /device/code + poll /token) instead of printing an implicit URL",
    )
    parser.add_argument(
        "--client-secret",
        default="",
        help="Optional client secret for confidential apps (device flow token exchange)",
    )
    parser.add_argument(
        "--services",
        default="metrika,direct,webmaster,audience",
        help="Comma list of services to write token files for (device flow)",
    )
    parser.add_argument("--scope", default="", help="Optional OAuth scope string for device flow")
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="Directory for secrets/ (device flow); default: cwd",
    )
    parser.add_argument(
        "--no-poll",
        action="store_true",
        help="Print user_code and exit without polling (device flow)",
    )
    args = parser.parse_args(argv)
    try:
        client_id = validate_client_id(args.client_id)
    except ValueError as exc:
        parser.error(str(exc))

    if args.device:
        services = [part.strip() for part in args.services.split(",") if part.strip()]
        raise SystemExit(
            asyncio.run(
                run_device_flow(
                    client_id,
                    client_secret=args.client_secret or "",
                    services=services,
                    scope=args.scope,
                    directory=args.directory,
                    poll=not args.no_poll,
                )
            )
        )

    try:
        url = authorization_url(client_id)
    except ValueError as exc:
        parser.error(str(exc))
    print("Use your own OAuth application with the API permissions you need.")
    print("Set its Redirect URI to https://oauth.yandex.ru/verification_code first.")
    print(url)
    print("Open the link locally. Paste the issued token only into the hidden yandex-mcp-setup prompt.")
    print("Prefer device flow (no copy-paste): yandex-mcp-oauth --device --client-id CLIENT_ID")
    print("Cloud Search/Wordstat uses separate Cloud API credentials; this URL does not configure it.")


if __name__ == "__main__":
    main()
