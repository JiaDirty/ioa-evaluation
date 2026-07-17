# Agentic Execution Architecture

## Default Flow

```text
POST /api/tasks {prompt, constraints}
  -> TaskSpecificationAgent
  -> AgenticOrchestrationPlanner
  -> PlanValidator
  -> AgenticOrchestrator
  -> Gateway.discover_and_select
  -> Gateway.dispatch_agentic_subtask
  -> SynthesisAgent
  -> TaskResult + ExecutionGraph + EvidenceBundle
```

## Control Boundary

LLM-backed or deterministic decision agents may propose:

- task specifications
- capability-level plans
- AgentAction values: final, tool_call, delegate, ask_user, replan, fail
- synthesis decisions

Deterministic infrastructure decides:

- identity and certificate validity
- capability discovery and ranking input set
- protocol negotiation
- authorization scope
- delegation scope monotonicity
- ToolGateway schema and permission checks
- human checkpoints

## Planning Contract

Planner nodes describe capabilities, dependencies, expected outputs, and input
bindings. They must not contain concrete Agent IDs, direct endpoints, target
Sub-IoA routes, or `hop_chain`. Runtime binding occurs only after Gateway
discovery and verification.

## Runtime Loop

`Gateway.dispatch_agentic_subtask()` invokes the selected runtime in a bounded
loop. Tool actions go through `ToolGateway`; tool results are appended to
`turn_history` and returned to the same Agent runtime before finalization.

Delegation actions go through `DelegationController`; rejected delegation fails
closed, and allowed delegation appends a dynamic graph node plus plan revision.

## Observability

EventBus and artifact metadata record:

- TaskSpec
- initial execution graph
- runtime Agent/domain bindings
- candidate discovery and selection
- protocol decisions
- Agent actions
- tool calls
- human checkpoints
- plan revisions
- final evidence map

Scenario reports additionally persist `EvaluationEvidenceBundle` objects under
`results/.../*.json`.
