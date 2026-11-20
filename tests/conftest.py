"""Test setup. Clears GSC env before the server module is imported."""

from __future__ import annotations

import os

os.environ["FASTMCP_CHECK_FOR_UPDATES"] = "off"
os.environ["FASTMCP_SHOW_SERVER_BANNER"] = "false"

for _name in list(os.environ):
    if _name.startswith("GSC_MCP_"):
        os.environ.pop(_name, None)
