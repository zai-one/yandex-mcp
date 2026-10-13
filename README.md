🇬🇧 English · [🇷🇺 Русский](README.ru.md)

# Yandex MCP

<!-- mcp-name: io.github.zai-one/yandex-mcp -->

**Bring Yandex advertising, analytics and search data into one conversation.**

> MCP server for Yandex Direct, Metrika (incl. Logs API and offline conversions), Webmaster, Wordstat, Search API and Audience: 77 tools in one server, with toolsets, read-only mode and guarded writes.

Review Direct campaigns, examine Metrika statistics, build bounded Audience segments, explore search demand and investigate Webmaster diagnostics with the same assistant. Yandex MCP connects six API groups; configure the services relevant to your work and add others as you need them.

[Quick start](#quick-start) · [Connect your assistant](#connect-your-assistant) · [Issues](https://github.com/zai-one/yandex-mcp/issues)

Try asking your assistant:

> Show my Metrika counters. For the counter I choose, compare visits and goal conversions over two equal periods and highlight the largest changes.

## What you can do

| Your task | What the MCP server provides |
|---|---|
| Direct | Campaigns, supported inventory, portfolio strategies and statistics, including per-goal conversions and revenue; controlled changes when enabled. |
| Metrika | Counters, goals, filtered reports, period comparison and CSV with sampling metadata. |
| Search API | Asynchronous Yandex search-result jobs with saved results. |
| Wordstat | Top queries, demand trends, regional distribution and the region tree. |
| Webmaster | Indexing and site diagnostics, SQI history, important pages, search appearance/removal events, query statistics, sitemap and recrawl workflows. |
| Audience | Segments and pixels; guarded circle, polygon, pixel and pixel-viewer creates when explicitly enabled. |

[Report examples and local OAuth help](docs/METRIKA_REPORTS.md) explain how to compare periods, export rows and interpret partial results. [API coverage notes](docs/API_COVERAGE.md) list recently added tools with links to the Yandex documentation.

## Why this MCP

- Six Yandex API groups in one process; enable only the toolsets you need (`YANDEX_TOOLSETS`, `YANDEX_READ_ONLY`).
- Writes (Direct changes, Audience creates, Metrika uploads and Logs requests) are off until enabled by an env flag and use confirmation hashes or idempotency keys.
- Agent layer: router instructions, 4 prompts, `yandex://playbook` and guide resources, self-contained skills, structured errors with `hint`.
- Compared with other Yandex MCP servers on GitHub: [SvechaPVL/yandex-mcp](https://github.com/SvechaPVL/yandex-mcp) (132 tools) and [skiddgoddamn/yandex-marketing-mcp](https://github.com/skiddgoddamn/yandex-marketing-mcp) (153 tools) expose more endpoints; this server focuses on bounded, typed responses, per-service write gates and CI-tested install.

## Maintained and up to date

- Actively maintained by ZAI.ONE: current version 0.5.1; release notes in [CHANGELOG.md](CHANGELOG.md) and [Releases](https://github.com/zai-one/yandex-mcp/releases).
- Built-in update check: ask the assistant to run `yandex_check_update` (public GitHub API, no credentials, cached; nothing is installed automatically; `YANDEX_DISABLE_UPDATE_CHECK=1` turns it off).
- Follows current MCP practice: tool annotations (read-only / destructive hints), toolsets and read-only mode, server `instructions`, prompts and resources, structured errors with recovery hints, secrets kept out of tool output, a token budget for tool descriptions enforced in tests.
- CI runs the test suite on Linux and Windows for every pull request.

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

### Other clients

```bash
# Claude Code (MCP only)
claude mcp add yandex -- uvx --from "zai-yandex-mcp[standalone] @ git+https://github.com/zai-one/yandex-mcp@v0.5.1" yandex-mcp --config /ABSOLUTE/PATH/mcp.local.json
# Claude Code plugin (MCP + skills) from this repository; set YANDEX_MCP_CONFIG to your mcp.local.json
/plugin marketplace add zai-one/yandex-mcp
/plugin install yandex@zai-yandex
```

Codex `~/.codex/config.toml`:

```toml
[mcp_servers.yandex]
command = "uvx"
args = ["--from", "zai-yandex-mcp[standalone] @ git+https://github.com/zai-one/yandex-mcp@v0.5.1", "yandex-mcp", "--config", "/ABSOLUTE/PATH/mcp.local.json"]
env = { YANDEX_TOOLSETS = "default" }
```

Cursor `.cursor/mcp.json` and Claude Desktop use the `mcpServers` block above (or `"command": "uvx"` with the Codex args).

## Toolsets and read-only mode

Unset = every tool (previous behaviour). `YANDEX_TOOLSETS=default` keeps the read toolsets (account, direct_read, direct_reports, metrika, webmaster, wordstat, audience) - about 30% fewer tool tokens. The quota- and write-related `metrika_logs` and `metrika_offline` toolsets are opt-in: add them explicitly, e.g. `YANDEX_TOOLSETS=default,metrika_logs`.

| Variable | Effect |
|---|---|
| `YANDEX_TOOLSETS` | `all`, `default` or a comma list of `account`, `direct_read`, `direct_reports`, `direct_write`, `metrika`, `metrika_logs`, `metrika_logs_write`, `metrika_offline`, `metrika_write`, `webmaster`, `webmaster_write`, `wordstat`, `search`, `audience`, `audience_write`. Also `--toolsets`. |
| `YANDEX_TOOLS` / `YANDEX_DISABLED_TOOLS` | Add or hide individual tools. |
| `YANDEX_READ_ONLY=1` | Hide every prepare/apply/write/paid-submit tool (also `--read-only`). |

Scopes, write flags, approvals and idempotency keys still apply to whatever is visible. The assistant can read `yandex://toolsets`. Errors are JSON with `code`, `retryable`, `hint` and `docs`.

## Skills, prompts and guides

Self-contained skills in [`skills/`](skills/) (each with its own `references/` and `templates/`): `yandex-direct-audit`, `metrika-compare-periods`, `webmaster-seo-health`, `wordstat-demand-research`, `direct-safe-change`. Copy a folder into `~/.claude/skills/`, `.agents/skills/` or `.cursor/skills/`, or install the plugin. MCP prompts: `campaign_audit`, `weekly_report`, `seo_audit`, `budget_scaling`; resources `yandex://playbook`, `yandex://guide/{api-units|audit-checklist|strategies|metrics-dimensions|diagnostics-codes|wordstat-operators|prepare-apply|errors}`.

### Example tasks

- "Audit my Direct campaigns for the last 30 days and list the top 5 issues."
- "Compare organic traffic and goal reaches this week vs last week in Metrika."
- "Check SEO health of example.com in Webmaster and give me a fix list."
- "Collect Wordstat demand for 'купить диван' in Moscow and cluster it."
- "Which campaigns can take 20% more budget at CPA under 900 RUB? Prepare the change, do not apply."

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

## Updates

Ask your assistant to run `yandex_check_update`. It compares the installed version with the
[latest release](https://github.com/zai-one/yandex-mcp/releases) through the public GitHub API
(no credentials, no Yandex API calls or quota, 3-second timeout, result cached for 24 hours next to the state file) and
returns the current and latest versions, the release notes link and the matching update command
(`git pull --ff-only && uv sync --frozen --extra standalone` for a checkout, `uv tool install --force ...` for a release package).
When a newer version is already known from the cache, the server mentions it once in its startup
instructions. Nothing is ever installed automatically. Offline, the check reports `status: unknown`
and the server keeps working. Set `YANDEX_DISABLE_UPDATE_CHECK=1` to turn it off.

## Built by ZAI.ONE

[ZAI.ONE](https://zai.one) is a digital agency working on websites, SEO, advertising and analytics. We also build tools that connect AI assistants to everyday work. [Talk to us on Telegram](https://t.me/zai_one) about setup, automation or an integration for your team.

## License, use and contributing

- **Use:** you may install and use this MCP server freely for yourself and your organisation, including commercial work. If it helps, please consider giving the repository a ⭐ — appreciated, never required.
- **Contribute:** [issues](https://github.com/zai-one/yandex-mcp/issues/new/choose) and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Never post tokens, cookies, sessions or account data.
- **Not allowed without written permission from ZAI.ONE:** copying code into other projects, forking for redistribution, embedding the server in other products or offering it as a competing service.
- License: [LicenseRef-ZAI-ONE 2.0](LICENSE) (source-available, not open source). Third-party notices and license history: [NOTICE](NOTICE).
