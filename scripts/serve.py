"""Launch the stdio MCP directly, or optionally supervise it through a tunnel."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from scripts.config import read_settings

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
SERVER = ROOT / "server.py"


def build_environment(
    settings: dict[str, str],
    current: dict[str, str] | None = None,
    *,
    tunnel: bool = False,
) -> dict[str, str]:
    """Keep credentials in environment variables, never argv or a dotenv file."""
    env = dict(os.environ if current is None else current)
    env["THREADS_TOKEN"] = settings["threads-token"]
    env["PYTHONUNBUFFERED"] = "1"
    if tunnel:
        env.update(
            {
                "CONTROL_PLANE_API_KEY": settings["tunnel-runtime-key"],
                "CONTROL_PLANE_TUNNEL_ID": settings["tunnel-id"],
                "MCP_COMMAND": shlex.join([str(PYTHON), "-u", str(SERVER)]),
            }
        )
    return env


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv not in ([], ["--tunnel"]):
        print("Usage: python -m scripts.serve [--tunnel]", file=sys.stderr)
        return 2
    tunnel = argv == ["--tunnel"]
    if not PYTHON.exists():
        print("Missing virtual environment; run 'make setup'", file=sys.stderr)
        return 1
    try:
        settings = read_settings(include_tunnel=tunnel)
    except RuntimeError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 1
    env = build_environment(settings, tunnel=tunnel)
    if not tunnel:
        # The MCP client owns stdin/stdout. Do not print to stdout or launch
        # anything that could corrupt the JSON-RPC stream.
        os.execvpe(str(PYTHON), [str(PYTHON), "-u", str(SERVER)], env)
        return 0  # unreachable when exec succeeds

    tunnel_client = shutil.which("tunnel-client")
    if not tunnel_client:
        print("Install tunnel-client or use 'make serve' for direct stdio", file=sys.stderr)
        return 1
    print("Starting optional tunnel (Ctrl-C stops).", file=sys.stderr)
    try:
        return subprocess.run(
            [tunnel_client, "run"],
            cwd=ROOT,
            env=env,
            check=False,
        ).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
