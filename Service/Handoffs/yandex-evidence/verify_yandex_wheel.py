"""Clean locked wheel install and independent stdio/HTTP probes without provider calls."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class ClosingDirectory(tempfile.TemporaryDirectory):
    def cleanup(self) -> None:
        # Windows may release a terminated process's inherited log handle just
        # after wait() completes. Retry only deletion of this owned temp directory.
        target = Path(self.name).resolve()
        parent = Path(tempfile.gettempdir()).resolve()
        if not target.is_relative_to(parent) or not target.name.startswith("yandex-wheel-probe-"):
            raise ValueError("temporary cleanup escaped the owned probe directory")
        for attempt in range(50):
            try:
                super().cleanup()
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.1)


async def probe() -> None:
    import httpx
    import zai_yandex
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from fastmcp.exceptions import ToolError
    from fastmcp.server.auth.providers.jwt import RSAKeyPair

    assert Path(zai_yandex.__file__).is_relative_to(Path(sys.prefix))
    assert importlib.util.find_spec("mcp_platform") is None
    package = Path(zai_yandex.__file__).parent
    assert (package / "_contracts/yandex.json").is_file()
    assert (package / "_licenses/webmaster/weselow-LICENSE").is_file()
    assert (package / "_licenses/webmaster/webkoth-LICENSE").is_file()
    with ClosingDirectory(prefix="yandex-wheel-probe-") as directory:
        root = Path(directory)
        credential = root / "synthetic.token"
        credential.write_text("synthetic-wheel-credential-no-live-value")
        credential.chmod(0o600)
        env = {"YANDEX_METRIKA_TOKEN_FILE": str(credential),
               "YANDEX_STATE_PATH": str(root / "state.sqlite"), "YANDEX_METRIKA_WRITE_ENABLED": "false",
               "YANDEX_ACCOUNT_ID": "default", "YANDEX_MCP_ISSUER": "yandex-operator",
               "YANDEX_MCP_AUDIENCE": "yandex-mcp"}
        transport = StdioTransport(command=sys.executable, args=["-m", "zai_yandex"],
                                   cwd=str(root), env=env, keep_alive=False)
        async with Client(transport, timeout=30) as client:
            assert client.initialize_result.serverInfo.version == "0.1.0"
            assert len(await client.list_tools()) == 44
            try:
                await client.call_tool("metrika_list_goals", {"counter_id": "invalid"})
            except ToolError as exc:
                assert "validation_failed" in str(exc)
            else:
                raise AssertionError("invalid project accepted")
        pair = RSAKeyPair.generate()
        public = root / "public.pem"
        public.write_text(pair.public_key)
        env["YANDEX_MCP_PUBLIC_KEY_FILE"] = str(public)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (root / "http.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "zai_yandex", "--transport", "http",
                                        "--host", "127.0.0.1", "--port", str(port)],
                                       cwd=root, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                for _ in range(300):
                    if process.poll() is not None:
                        raise AssertionError("HTTP entrypoint exited before readiness")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        await asyncio.sleep(0.1)
                else:
                    raise AssertionError("HTTP entrypoint did not become ready")
                url = f"http://127.0.0.1:{port}/mcp"
                async with httpx.AsyncClient() as http:
                    denied = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                    assert denied.status_code in {401, 403}
                bearer = pair.create_token(subject="probe", issuer="yandex-operator", audience="yandex-mcp",
                                            scopes=["yandex_metrika:read"], expires_in_seconds=60,
                                            additional_claims={"account_id": "default"})
                async with Client(url, auth=bearer, timeout=30) as client:
                    assert client.initialize_result.serverInfo.version == "0.1.0"
                    assert len(await client.list_tools()) == 3
                    try:
                        await client.call_tool("metrika_list_goals", {"counter_id": "invalid"})
                    except ToolError as exc:
                        assert "validation_failed" in str(exc)
                    else:
                        raise AssertionError("HTTP invalid project accepted")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                for _ in range(50):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            pass
                    except OSError:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("owned HTTP fixture port remained open after termination")
    print("PASS clean wheel import without mcp_platform; real stdio and authenticated HTTP entrypoints")


def main() -> None:
    if sys.argv[1:] == ["--probe"]:
        asyncio.run(probe())
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.environment.exists() or args.manifest.exists():
        raise SystemExit("create-only: environment/manifest already exists")
    wheel = args.service / "dist/zai_yandex_mcp-0.1.0-py3-none-any.whl"
    requirements = args.environment.parent / (args.environment.name + "-requirements.txt")
    python = args.environment / "Scripts/python.exe"
    commands = [
        ["uv", "export", "--frozen", "--no-dev", "--extra", "standalone", "--no-emit-project", "-o", str(requirements)],
        ["uv", "venv", "--python", str(args.service / ".venv/Scripts/python.exe"), str(args.environment)],
        ["uv", "pip", "install", "--python", str(python), "--require-hashes", "-r", str(requirements)],
        ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
        [str(python), str(Path(__file__).resolve()), "--probe"],
    ]
    for command in commands:
        subprocess.run(command, cwd=args.service, check=True)
    manifest = {"package": "zai-yandex-mcp", "version": "0.1.0", "wheel": wheel.name,
                "source_revision": subprocess.check_output(["git", "-C", str(args.service), "rev-parse", "HEAD"]).decode().strip(),
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "uv_lock_sha256": hashlib.sha256((args.service / "uv.lock").read_bytes()).hexdigest(),
                "verification": "clean non-editable wheel; hashed frozen dependencies; real stdio and HTTP",
                "production": "not deployed", "container": "not built; Docker unavailable"}
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
