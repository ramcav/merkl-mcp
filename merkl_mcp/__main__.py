"""``merkl-mcp`` / ``python -m merkl_mcp`` — stdio by default, streamable HTTP
with ``--http``.

    merkl-mcp                  # stdio: what MCPServerStdio and Claude Desktop mount
    merkl-mcp --http :8765     # streamable HTTP on every interface, port 8765
    merkl-mcp --http 127.0.0.1:8765

This is the whole entry point on purpose: argument parsing here, everything
that can fail — a missing bundle file, a bad TOML table — inside
``build_runtime``, so the only way this prints a traceback is a bug, not a
config mistake.

Everything runs in one ``asyncio.run()``: the runtime is built, served
(``run_stdio_async`` / ``run_streamable_http_async``) and closed inside the
same loop. Building it in one loop and closing it in another left httpx
clients to close on a dead loop ("Event loop is closed").
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from mcp.server.fastmcp import FastMCP

from merkl_mcp import bundle
from merkl_mcp.runtime import Runtime, WiringError, build_runtime
from merkl_mcp.server import build_app


def _parse_http(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not port.isdigit():
        raise argparse.ArgumentTypeError(f"--http wants HOST:PORT or :PORT, got {value!r}")
    return (host or "0.0.0.0", int(port))


def _configured(app: FastMCP, host: str, port: int) -> FastMCP:
    app.settings.host = host
    app.settings.port = port
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="merkl-mcp",
        description="Merkl as a tool server: mount it over stdio or streamable HTTP.",
    )
    parser.add_argument(
        "--http",
        type=_parse_http,
        default=None,
        metavar="[HOST]:PORT",
        help="serve streamable HTTP instead of stdio",
    )
    arguments = parser.parse_args(argv)

    try:
        asyncio.run(serve(arguments.http))
    except (bundle.ConfigError, WiringError) as exc:
        print(f"configuration: {exc}", file=sys.stderr)
        return 2
    return 0


async def serve(http: tuple[str, int] | None) -> None:
    """Build the runtime, serve, and close — all inside one running loop, so the
    httpx clients are opened and closed by the loop that used them."""
    rt: Runtime = await build_runtime()
    app = build_app(rt)
    try:
        if http is None:
            await app.run_stdio_async()
        else:
            host, port = http
            await _configured(app, host, port).run_streamable_http_async()
    finally:
        await rt.aclose()


if __name__ == "__main__":
    raise SystemExit(main())
