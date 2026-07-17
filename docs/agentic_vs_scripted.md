# Agentic vs Scripted Modes

## Agentic

`execution_mode="agentic"` is the default API mode.

Input:

```json
{"prompt": "自然语言任务", "constraints": {}}
```

Properties:

- `task_type=dynamic`
- no caller-supplied target domains
- no caller-supplied required capabilities
- no hop chain
- Planner creates capability nodes with `assigned_agent_id=null`
- Gateway binds Agents at runtime
- Tool calls, delegation, human input, and replanning are structured actions

## Offline Deterministic

`execution_mode="offline_deterministic"` uses the same agentic state machine,
ExecutionGraph, Gateway, Registry, runtime loop, and evidence reporting. It
replaces live LLM calls with deterministic clients so CI and local validation do
not require external API keys.

This mode is allowed for framework verification, but live model behavior still
requires an LLM client.

## Scripted

`execution_mode="scripted"` is the compatibility boundary for older fixed-route
mechanism probes.

Scripted mode may use legacy fields:

- `task_type`
- `origin_sub_ioa`
- `target_sub_ioas`
- `required_capabilities`
- `payload.hop_chain`

Scripted paths must remain visibly named legacy/scripted and must not become the
default for prompt-only tasks.

## Rule Of Thumb

If the caller already knows the Agent order or route, it is scripted. If the
caller only states the goal and constraints, and the runtime discovers and binds
capabilities during execution, it is agentic.
