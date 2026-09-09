"""Print the official manual OAuth route; never receive credentials in a tool call."""

import argparse
import re
from urllib.parse import urlencode


def authorization_url(client_id: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", client_id):
        raise ValueError("use the 32-character Client ID from your Yandex OAuth application")
    return "https://oauth.yandex.ru/authorize?" + urlencode(
        {"response_type": "token", "client_id": client_id}
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local Yandex OAuth setup help; no token capture")
    parser.add_argument(
        "--client-id", required=True, help="Public application Client ID, never a client secret"
    )
    args = parser.parse_args(argv)
    try:
        url = authorization_url(args.client_id)
    except ValueError as exc:
        parser.error(str(exc))
    print("Use your own OAuth application with the API permissions you need.")
    print("Set its Redirect URI to https://oauth.yandex.ru/verification_code first.")
    print(url)
    print("Open the link locally. Paste the issued token only into the hidden yandex-mcp-setup prompt.")
    print("Cloud Search/Wordstat uses separate Cloud API credentials; this URL does not configure it.")


if __name__ == "__main__":
    main()
