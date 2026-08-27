"""Placeholder MCP client boundary for future external tool servers."""

from __future__ import annotations


class MCPClient:
    async def call(self, *_args, **_kwargs):
        raise NotImplementedError("External MCP server integration is not enabled in this testbed")
