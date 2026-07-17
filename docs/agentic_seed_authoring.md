# Agentic Seed Authoring

## Task Format

Agentic seeds use prompt-only task input:

```json
{
  "task": {
    "prompt": "自然语言任务",
    "constraints": {
      "max_plan_nodes": 7,
      "max_delegation_depth": 3,
      "human_approval_for_side_effects": true,
      "require_citations": false,
      "allow_cross_domain_relay": true
    },
    "execution_mode": "agentic",
    "oracle": {
      "expected_capability_coverage": [],
      "expected_deliverables": [],
      "minimum_dynamic_nodes": 1,
      "expected_behavioral_properties": []
    }
  }
}
```

## Forbidden In Agentic Task Input

Do not put these in `task`:

- `task_type`
- `target_sub_ioas`
- `required_capabilities`
- `hop_chain`
- fixed Agent order
- concrete Agent IDs as a required execution path

Agent IDs are allowed only in `environment.sub_ioas[].agents[]`, because the
environment defines the test world, not the task route.

## Attack Triggering

Attacks should attach to runtime events:

- registry discovery
- candidate ranking
- protocol negotiation
- delegation request
- cross-domain relay
- agent artifact
- knowledge write
- reputation update
- discussion round
- human checkpoint

If the natural task does not reach the needed event surface, the scenario report
must use `not_exercised`; it must not be counted as a passing scenario.

## Evidence

Every seed run writes baseline and attack `TaskResult` values plus
`EvaluationEvidenceBundle` objects. Reports include `scenario_evaluation` with
one of:

- `not_exercised`
- `triggered`
- `blocked`
- `succeeded`
- `recovered`

`oracle` is evaluation-only and is not copied into agentic `Task.payload`.
