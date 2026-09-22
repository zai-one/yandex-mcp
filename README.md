🇬🇧 English · [🇷🇺 Русский](README.ru.md)

# Yandex MCP

**Bring Yandex advertising, analytics and search data into one conversation.**

Review Direct campaigns, examine Metrika statistics, build bounded Audience segments, explore search demand and investigate Webmaster diagnostics with the same assistant. Yandex MCP connects six API groups; configure the services relevant to your work and add others as you need them.

[Quick start](#quick-start) · [Connect your assistant](#connect-your-assistant) · [Issues](https://github.com/zai-one/yandex-mcp/issues)

Try asking your assistant:

> Show my Metrika counters. For the counter I choose, compare visits and goal conversions over two equal periods and highlight the largest changes.

## What you can do

| Your task | What the MCP server provides |
|---|---|
| Direct | Campaigns, supported inventory and statistics; controlled changes when enabled. |
| Metrika | Counters, goals, filtered reports, period comparison and CSV with sampling metadata. |
| Search API | Asynchronous Yandex search-result jobs with saved results. |
| Wordstat | Top queries, demand trends, regional distribution and the region tree. |
| Webmaster | Indexing and site diagnostics, query statistics, sitemap and recrawl workflows. |
| Audience | Segments and pixels; guarded circle, polygon, pixel and pixel-viewer creates when explicitly enabled. |

[Report examples and local OAuth help](docs/METRIKA_REPORTS.md) explain how to compare periods, export rows and interpret partial results.

## Quick start

Prefer a ready package? [Install the release and generate your client configuration](INSTALL.md#install-a-release-package). No source checkout is required.

Install **Python 3.12–3.14** and [uv](https://docs.astral.sh/uv/getting-started/installation/). Clone with Git or [download the ZIP](https://github.com/zai-one/yandex-mcp/archive/refs/heads/main.zip). With a ZIP, open the extracted directory and skip the first two commands.

Each API group needs its own credentials. The wizard starts with Metrika; add Direct, Audience, Webmaster and Yandex Cloud credentials for the services you want to use. Cloud Search also needs a folder ID. Follow the [service-specific setup](INSTALL.md#from-a-clone-or-source-zip). Audience safety and status semantics are documented in [Yandex Audience](docs/AUDIENCE.md).

```sh
git clone https://github.com/zai-one/yandex-mcp.git
cd yandex-mcp
uv sync --frozen --extra standalone
uv run --frozen --extra standalone python scripts/configure.py
uv run --frozen --extra standalone yandex-mcp --config mcp.local.json --check-config
```

The wizard creates a local configuration and stores secrets in private files. It refuses to overwrite an existing setup. `--check-config` validates local settings; the first request below checks your account connection.

## Connect your assistant

Add this configuration to an MCP client that uses `mcpServers`, such as Claude Desktop or Cursor. Replace `/ABSOLUTE/PATH/` with your absolute path; Windows JSON paths can use forward slashes, such as `D:/Tools/`.

```json
{
  "mcpServers": {
    "yandex": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/yandex-mcp",
        "run",
        "--frozen",
        "--extra",
        "standalone",
        "yandex-mcp",
        "--config",
        "/ABSOLUTE/PATH/yandex-mcp/mcp.local.json"
      ]
    }
  }
}
```

The client starts the MCP server for you. Refresh its tool list, then make your first request. For clients with a different config format, reuse the same `command` and `args`; `uv` must be available to the client process.

### First request

> List my Metrika counters and the goals of the counter I select.

The default setup starts with Metrika. Once its token is accepted, the assistant can list accessible counters and read their goals. Other API groups use separately configured credentials.

If tools do not appear, check the absolute path, whether the client can find `uv`, and the `--check-config` result. For access errors, check account credentials and permissions. [Installation and troubleshooting](INSTALL.md).

## Access and limits

Search API jobs need a separate worker and a Cloud folder ID. Wordstat uses Cloud credentials and explicit cost limits. Write operations depend on service permissions and server policy. Interactive OAuth setup and Metrika Logs export are not included.

Authenticated HTTP is available for a server deployment. See [HTTP setup](INSTALL.md#http), [configuration and permissions](docs/RUNTIME.md) and [Python package integration](INSTALL.md#python-package-and-platform-integration).

<details>
<summary>For developers: project checks</summary>

```sh
uv sync --frozen --all-groups --extra standalone
uv run --frozen --extra standalone python scripts/verify.py
uv run --frozen --extra standalone python scripts/verify_install.py
```

Tests use synthetic fixtures. A passing test run does not establish live provider connectivity.

</details>

## Built by ZAI.ONE

[ZAI.ONE](https://zai.one) is a digital agency working on websites, SEO, advertising and analytics. We also build tools that connect AI assistants to everyday work. [Talk to us on Telegram](https://t.me/zai_one) about setup, automation or an integration for your team.

## Use and feedback

You may install and use this project for your own accounts under [LicenseRef-ZAI-ONE](LICENSE).
This is not an open-source license. Third-party notices remain in [NOTICE](NOTICE).
If it helps, give the repository a ⭐. Missing something or found a bug? [Open an issue](https://github.com/zai-one/yandex-mcp/issues/new/choose).
I'm working on this project; accepted improvements are implemented here. Support is not guaranteed.
