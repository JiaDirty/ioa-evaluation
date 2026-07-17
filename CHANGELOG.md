# Changelog

## Unreleased

- Added unified event/span observability with redacted payload persistence and SQLite replay.
- Added dynamic system, execution, and interaction graph APIs plus resumable task/experiment WebSockets.
- Added the responsive IoA runtime console for live progress and step-level input/output inspection.

## v1.1.0

- Added MCP server registry, JSON-RPC HTTP MCP client, and MCP ToolGateway provider.
- Added provider-aware ToolGateway with local and MCP tool dispatch.
- Added ToolExecutionContext injection for AgentRuntime tool calls.
- Added SQLite-backed Task, Event, ToolCall, and Artifact stores.
- Added in-process background task runner with queued/running/cancel/retry states.
- Added orchestration ExecutionGraph models and task execution graph API.
- Added tool input schema validation and sensitive argument redaction.
- Added MCP registry, execution graph, live events, tool history, and retry/cancel frontend views.
- Added production hardening docs for MCP integration and task lifecycle.
