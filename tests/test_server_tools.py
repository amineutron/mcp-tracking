"""Le serveur MCP doit s'importer et exposer ses outils (regression : mcp 2 a retire mcp.server.fastmcp)."""
import asyncio

import server


def test_server_exposes_tracking_tools():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"tracking_create", "tracking_update", "tracking_list"} <= names
