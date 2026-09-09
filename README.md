🇬🇧 English · [🇷🇺 Русский](README.ru.md)

# Yandex MCP

MCP server for Yandex Direct, Metrika, Search API, Wordstat and Webmaster. Connect an AI assistant to advertising, website analytics, search results and search-demand data.

## What you can do

- Work with the supported Direct, Metrika and Webmaster API operations.
- Submit search jobs through Yandex Cloud Search API and retrieve their results.
- Get Wordstat query data, trends, regional distribution and the region tree.

## Quick start

Install Python 3.12+ (below 3.15), [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.

Each API group needs its own credentials. The wizard starts with Metrika; add Direct, Webmaster and Yandex Cloud credentials for the services you want to use. Cloud Search also needs a folder ID. Follow the [service-specific setup](INSTALL.md#from-a-clone-or-source-zip).

```sh
git clone https://github.com/zai-one/yandex-mcp.git
cd yandex-mcp
uv sync --frozen --extra standalone
uv run --frozen --extra standalone python scripts/configure.py
uv run --frozen --extra standalone yandex-mcp --config mcp.local.json --check-config
uv run --frozen --extra standalone yandex-mcp --config mcp.local.json
```

The last command starts stdio and waits for an MCP client; it is not an interactive chat.
See [INSTALL.md](INSTALL.md) for credentials, client configuration, HTTP and package integration.
`--check-config` checks local settings only; it never validates a provider account over the network.

## Scope and limits

Search jobs need a separate worker. Wordstat requests require explicit enabling and configured cost limits. OAuth login setup and Metrika Logs export are not included. See [runtime configuration](docs/RUNTIME.md).

## Verification

```sh
uv sync --frozen --all-groups --extra standalone
uv run --frozen --extra standalone python scripts/verify.py
uv run --frozen --extra standalone python scripts/verify_install.py
```

Tests use synthetic fixtures. A passing test run does not establish live provider connectivity.

## Use and feedback

You may install and use this project for your own accounts under [LicenseRef-ZAI-ONE](LICENSE).
This is not an open-source license. Third-party notices remain in [NOTICE](NOTICE).
If it helps, give the repository a ⭐. Missing something or found a bug? [Open an issue](https://github.com/zai-one/yandex-mcp/issues/new/choose).
I'm working on this project; accepted improvements are implemented here. Support is not guaranteed.
