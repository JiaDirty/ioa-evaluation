# MCP Integration

## Configuration

MCP servers are configured in `config/mcp_servers.yaml`.

Each server has:

- `server_id`
- `enabled`
- `transport`
- `endpoint` or `command`
- `allowed_tools`
- `risk_level`
- `timeout_seconds`

The first production hardening pass supports HTTP JSON-RPC MCP calls. Stdio
servers may be declared in configuration, but the runtime treats them as a
future transport boundary.

## Tool Sync

Use:

- `GET /api/mcp/servers`
- `POST /api/mcp/sync-tools`
- `POST /api/mcp/servers/{server_id}/sync-tools`

Only enabled servers participate in default sync.

## Tool ID Rule

Synced MCP tools use:

```text
mcp:{server_id}:{tool_name}
```

Example:

```text
mcp:local-demo-mcp:demo_echo
```

## Allowlist

`allowed_tools: []` denies all tools.

`allowed_tools: ["*"]` allows all tools exposed by that server.

Any other list only allows those exact tool names.

## Security Notes

- MCP is Agent-to-Tool only in this project.
- MCP must not be used as the Agent-to-Agent transport.
- MCP tools still pass through ToolGateway policy checks.
- High-risk MCP tools require the `high_risk_tool` scope.
- Critical tools additionally require the `critical_tool` scope.
- Tool arguments persisted in history are redacted for sensitive keys.

## Fake Server Testing

Tests use a fake JSON-RPC style server/client with:

- `tools/list`
- `tools/call`

This keeps MCP behavior deterministic while preserving the integration boundary.
