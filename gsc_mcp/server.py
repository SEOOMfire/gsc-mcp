"""Entry point for the read-only Search Console MCP server."""

from __future__ import annotations

import os
import sys

from gsc_mcp.coordinator import mcp, missing_runtime_env


def run_server() -> None:
    """Serve streamable HTTP on port 8080. Refuse to start without OAuth env."""
    missing = missing_runtime_env()
    if missing:
        print(
            "Missing required environment variables: " + ", ".join(missing),
            file=sys.stderr,
        )
        raise SystemExit(1)
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        show_banner=False,
        uvicorn_config={"access_log": False},
    )


if __name__ == "__main__":
    run_server()
