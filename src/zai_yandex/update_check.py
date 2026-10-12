"""Opt-out, read-only update checker.

Compares the installed package version with the latest GitHub release (or tag)
of the public repository. It never installs anything: it only reports the
current and latest versions, the release notes link and the update command.

* Public GitHub API, no credentials, short timeout.
* Results are cached for 24 hours in a small JSON file.
* Every fetch attempt is recorded; after a failure no new fetch happens for
  30 minutes, and ``force`` is honoured at most once a minute.
* Any network or parsing failure degrades to ``status="unknown"``.
* Only plain version tags and release links of this repository are trusted,
  whether they come from GitHub or from the cache file.
* ``YANDEX_DISABLE_UPDATE_CHECK=1`` disables all network access.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from zai_yandex import __version__

REPOSITORY = "zai-one/yandex-mcp"
DIST_NAME = "zai-yandex-mcp"
WHEEL_NAME = "zai_yandex_mcp-{version}-py3-none-any.whl"
DISABLE_ENV = "YANDEX_DISABLE_UPDATE_CHECK"
API_ROOT = "https://api.github.com"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
CACHE_TTL_SECONDS = 24 * 3600
FAILURE_BACKOFF_SECONDS = 30 * 60
FORCE_MIN_INTERVAL_SECONDS = 60
CLOCK_SKEW_SECONDS = 60
TIMEOUT_SECONDS = 3.0
MAX_RESPONSE_BYTES = 512_000
_VERSION = re.compile(r"v?(\d+(?:\.\d+){0,3})")
_NOTES_URL = re.compile(re.escape(RELEASES_URL) + r"(?:/tag/v?\d+(?:\.\d+){0,3})?")
_ERROR_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_TRUE = {"1", "true", "yes", "on"}


def disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in _TRUE


def parse_version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (3 - len(parts)) if len(parts) < 3 else parts


def safe_notes_url(value: object) -> str | None:
    """Accept only this repository's releases page or a plain-version tag page."""
    return value if isinstance(value, str) and _NOTES_URL.fullmatch(value) else None


def _is_checkout() -> bool:
    return (Path(__file__).resolve().parents[2] / ".git").exists()


def update_commands(latest: str | None) -> dict[str, str]:
    version = (latest or "").lstrip("v")
    commands = {
        "git_checkout": "git pull --ff-only && uv sync --frozen --extra standalone",
    }
    if version:
        wheel = WHEEL_NAME.format(version=version)
        commands["release_package"] = (
            f'uv tool install --force "{DIST_NAME}[standalone] @ https://github.com/{REPOSITORY}'
            f'/releases/download/v{version}/{wheel}"'
        )
    return commands


def _result(latest: str | None, notes_url: str | None, *, status: str, source: str, **extra: Any):
    current = parse_version(__version__)
    newest = parse_version(latest)
    if newest is None:
        # Only a plain version tag may reach commands, payloads or server instructions.
        latest = None
    notes_url = safe_notes_url(notes_url) or (RELEASES_URL if latest else None)
    available = bool(current and newest and newest > current)
    commands = update_commands(latest if available else None)
    method = "git_checkout" if _is_checkout() else "release_package"
    payload: dict[str, Any] = {
        "current_version": __version__,
        "latest_version": latest.strip().lstrip("v") if isinstance(latest, str) else None,
        "update_available": available,
        "status": status,
        "source": source,
        "release_notes_url": notes_url,
        "install_method": method,
        "update_command": commands.get(method) if available else None,
        "update_commands": commands if available else {},
        "auto_update": False,
        "disable_with": f"{DISABLE_ENV}=1",
    }
    payload.update(extra)
    return payload


def _timestamp(value: object, now: float) -> float | None:
    # A future timestamp (tampered file or clock jump) must not suppress checks indefinitely.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0 <= value <= now + CLOCK_SKEW_SECONDS else None


def _read_cache(path: Path | None, now: float | None = None) -> dict[str, Any] | None:
    """Return the validated cache state; untrusted values are dropped, never passed on.

    Shape: ``{"good": {...} | None, "attempted_at": float | None, "failed": bool, "error": str | None}``.
    """
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("repository") != REPOSITORY:
        return None
    now = time.time() if now is None else now
    checked_at = _timestamp(data.get("checked_at"), now)
    latest = data.get("latest")
    good = None
    if checked_at is not None and parse_version(latest) is not None:
        good = {
            "latest": latest.strip(),
            "release_notes_url": safe_notes_url(data.get("release_notes_url")),
            "checked_at": checked_at,
        }
    # Caches written before attempt tracking only have checked_at.
    attempted_at = _timestamp(data.get("attempted_at"), now)
    if attempted_at is None:
        attempted_at = checked_at
    error = data.get("error")
    return {
        "good": good,
        "attempted_at": attempted_at,
        "failed": data.get("failed") is True,
        "error": error if isinstance(error, str) and _ERROR_NAME.fullmatch(error) else None,
    }


def _write_cache(
    path: Path | None, good: dict[str, Any] | None, *, attempted_at: float, error: str | None = None
) -> None:
    if path is None:
        return
    record: dict[str, Any] = {
        "repository": REPOSITORY,
        "attempted_at": attempted_at,
        "failed": error is not None,
    }
    if error is not None:
        record["error"] = error
    if good is not None:
        record.update(
            latest=good["latest"], release_notes_url=good["release_notes_url"], checked_at=good["checked_at"]
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return


def _from_cache(good: dict[str, Any] | None, *, status: str, source: str, **extra: Any) -> dict[str, Any]:
    if good is None:
        return _result(None, None, status=status, source="none", **extra)
    return _result(
        good["latest"],
        good["release_notes_url"],
        status=status,
        source=source,
        checked_at=int(good["checked_at"]),
        **extra,
    )


def _decode(response: httpx.Response) -> Any:
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return response.json()


async def _fetch_latest(client: httpx.AsyncClient) -> tuple[str, str | None]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"{DIST_NAME}/{__version__}"}
    response = await client.get(f"{API_ROOT}/repos/{REPOSITORY}/releases/latest", headers=headers)
    if response.status_code == 200:
        data = _decode(response)
        tag = data.get("tag_name") if isinstance(data, dict) else None
        if parse_version(tag) is None:
            raise ValueError("release without a version tag")
        tag = str(tag).strip()
        notes = safe_notes_url(data.get("html_url"))
        return tag, notes or f"{RELEASES_URL}/tag/{tag}"
    if response.status_code != 404:
        raise ValueError(f"GitHub API returned HTTP {response.status_code}")
    response = await client.get(f"{API_ROOT}/repos/{REPOSITORY}/tags?per_page=100", headers=headers)
    if response.status_code != 200:
        raise ValueError(f"GitHub API returned HTTP {response.status_code}")
    data = _decode(response)
    names = [item.get("name") for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    versions = [(parse_version(name), name) for name in names if parse_version(name) is not None]
    if not versions:
        raise ValueError("no version tags")
    tag = str(max(versions)[1]).strip()
    return tag, f"{RELEASES_URL}/tag/{tag}"


def cached_status(cache_path: Path | None) -> dict[str, Any] | None:
    """Return the last known good result without any network access."""
    if disabled():
        return None
    state = _read_cache(cache_path)
    if state is None or state["good"] is None:
        return None
    return _from_cache(state["good"], status="ok", source="cache")


async def check_for_update(
    cache_path: Path | None,
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    if disabled():
        return _result(None, None, status="disabled", source="none")
    now = time.time()
    state = _read_cache(cache_path, now) or {
        "good": None,
        "attempted_at": None,
        "failed": False,
        "error": None,
    }
    good = state["good"]
    attempted_at = state["attempted_at"]
    extra: dict[str, Any] = {}
    throttled = bool(force and attempted_at is not None and now - attempted_at < FORCE_MIN_INTERVAL_SECONDS)
    if throttled:
        # A forced check is honoured at most once a minute, with or without a good cache.
        extra["force_throttled"] = True
    if not force or throttled:
        if good is not None and now - good["checked_at"] < CACHE_TTL_SECONDS:
            return _from_cache(good, status="ok", source="cache", **extra)
        backoff = bool(
            state["failed"] and attempted_at is not None and now - attempted_at < FAILURE_BACKOFF_SECONDS
        )
        if backoff or throttled:
            wait = FAILURE_BACKOFF_SECONDS if backoff else FORCE_MIN_INTERVAL_SECONDS
            last = attempted_at if attempted_at is not None else now
            return _from_cache(
                good,
                status="unknown",
                source="stale_cache",
                error=state["error"] or "unavailable",
                retry_after_seconds=max(1, int(last + wait - now) + 1),
                **extra,
            )
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as owned:
                latest, notes = await _fetch_latest(owned)
        else:
            latest, notes = await _fetch_latest(client)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        _write_cache(cache_path, good, attempted_at=now, error=type(exc).__name__)
        return _from_cache(good, status="unknown", source="stale_cache", error=type(exc).__name__)
    _write_cache(
        cache_path, {"latest": latest, "release_notes_url": notes, "checked_at": now}, attempted_at=now
    )
    return _result(latest, notes, status="ok", source="github")


def startup_hint(cache_path: Path | None) -> str:
    """One short sentence for server instructions; empty unless an update is known."""
    status = cached_status(cache_path)
    if not status or not status["update_available"]:
        return ""
    return (
        f"Update available: {DIST_NAME} {status['latest_version']} "
        f"(installed {status['current_version']}). Tell the user once; do not update automatically. "
        f"Details: {status['release_notes_url']}"
    )


def refresh_in_background(cache_path: Path | None) -> threading.Thread | None:
    """Best-effort cache refresh on a daemon thread; never blocks or raises.

    Follows the same cache, failure back-off and throttle rules as the tool.
    """
    if disabled():
        return None
    import asyncio

    def run() -> None:
        try:
            asyncio.run(check_for_update(cache_path))
        except Exception:  # noqa: BLE001 - background hint must never crash the server
            return

    thread = threading.Thread(target=run, name="update-check", daemon=True)
    thread.start()
    return thread
