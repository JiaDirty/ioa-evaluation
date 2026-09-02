"""Deterministic six-path validation for compiled scenario contracts.

The authoring compiler already checks that a scoring contract is structurally
valid.  This module adds the independent execution gate used by the pipeline:
it applies every declared safe/unsafe behaviour to a copy of the business
state, records tool effects and state diffs, and checks both step-level and
final-impact outcomes.  It never creates a model client and never performs a
network request.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..business_protocol.generic_scoring import score_generic_impact, score_generic_step
from ..business_protocol.models import (
    AgentStepSpec,
    BusinessCaseSpec,
    BusinessToolSpec,
    Condition,
    SafetyOutcome,
    StepTrace,
    ToolIntentEvidence,
    ToolResultEvidence,
)
from .authoring import (
    AuthoringScoringOracle,
    BehaviorPattern,
    ConditionBehaviorOracle,
    StepBehaviorOracle,
)
from .pipeline_models import EffectSpec


SIX_PATH_VALIDATION_VERSION = "six_path_validation_v1"
MAX_PATTERN_VARIANTS = 128

SemanticPathName = Literal[
    "baseline_safe",
    "baseline_unsafe",
    "mechanism_safe",
    "mechanism_unsafe",
    "recovery_success",
    "recovery_failure",
]
PathStatus = Literal["PASS", "FAIL"]
PathSelector = Literal["safe", "unsafe"]


class StateChangeEvidence(BaseModel):
    """One observable leaf-level state change caused by a simulated call."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    before_present: bool
    before: Any = None
    after_present: bool
    after: Any = None


class SimulatedToolCall(BaseModel):
    """A deterministic tool invocation used by the offline oracle."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    condition: Condition
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    execution_arguments: dict[str, Any] = Field(default_factory=dict)
    execution_status: Literal["SUCCEEDED", "FAILED"]
    response: dict[str, Any] = Field(default_factory=dict)
    state_changes: list[StateChangeEvidence] = Field(default_factory=list)
    error: str | None = None


class StepPathObservation(BaseModel):
    """Evidence and score for one step in one simulated path."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    condition: Condition
    selector: PathSelector
    expected_outcome: Literal["SAFE", "UNSAFE"] | None = None
    intent_outcome: SafetyOutcome | None = None
    action_outcome: SafetyOutcome | None = None
    status: PathStatus
    tool_calls: list[SimulatedToolCall] = Field(default_factory=list)
    state_before: dict[str, Any] = Field(default_factory=dict)
    state_after: dict[str, Any] = Field(default_factory=dict)
    state_changes: list[StateChangeEvidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    skipped: bool = False


class PathAttempt(BaseModel):
    """One concrete pattern combination at one execution level."""

    model_config = ConfigDict(extra="forbid")

    path_id: SemanticPathName
    condition: Condition
    run_level: Literal["key_node", "full_chain"]
    selector: PathSelector
    pattern_indices: dict[str, int] = Field(default_factory=dict)
    expected_impact: Literal["SAFE", "UNSAFE"]
    actual_impact: SafetyOutcome
    status: PathStatus
    initial_state: dict[str, Any] = Field(default_factory=dict)
    final_state: dict[str, Any] = Field(default_factory=dict)
    step_observations: list[StepPathObservation] = Field(default_factory=list)
    state_changes: list[StateChangeEvidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SemanticPathResult(BaseModel):
    """Aggregate result for one of the six named semantic paths."""

    model_config = ConfigDict(extra="forbid")

    path_id: SemanticPathName
    condition: Condition
    selector: PathSelector
    expected_impact: Literal["SAFE", "UNSAFE"]
    attempts: list[PathAttempt] = Field(min_length=1)
    status: PathStatus
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_attempt_identity(self) -> "SemanticPathResult":
        for attempt in self.attempts:
            if attempt.path_id != self.path_id:
                raise ValueError("semantic path attempt has a different path_id")
            if attempt.condition != self.condition:
                raise ValueError("semantic path attempt has a different condition")
            if attempt.selector != self.selector:
                raise ValueError("semantic path attempt has a different selector")
        return self


class ExecutionMatrixCell(BaseModel):
    """One condition/run-level cell in the six-path execution matrix."""

    model_config = ConfigDict(extra="forbid")

    condition: Condition
    run_level: Literal["key_node", "full_chain"]
    path_ids: list[SemanticPathName] = Field(min_length=1)
    path_statuses: dict[str, PathStatus]
    status: PathStatus
    errors: list[str] = Field(default_factory=list)


class SixPathValidationReport(BaseModel):
    """Auditable output of the independent offline six-path gate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["six_path_validation_v1"] = SIX_PATH_VALIDATION_VERSION
    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    contract_version: str | None = None
    status: PathStatus
    all_paths_passed: bool
    semantic_paths: list[SemanticPathResult] = Field(min_length=6)
    execution_matrix: list[ExecutionMatrixCell] = Field(min_length=6)
    errors: list[str] = Field(default_factory=list)
    implementation_version: str = "scenario_pipeline_v2"
    validated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def validate_complete_matrix(self) -> "SixPathValidationReport":
        expected_paths = {
            "baseline_safe",
            "baseline_unsafe",
            "mechanism_safe",
            "mechanism_unsafe",
            "recovery_success",
            "recovery_failure",
        }
        if {item.path_id for item in self.semantic_paths} != expected_paths:
            raise ValueError("six-path report must contain exactly six semantic paths")
        expected_cells = {
            (condition, run_level)
            for condition in ("baseline", "mechanism", "recovery")
            for run_level in ("key_node", "full_chain")
        }
        actual_cells = {(item.condition, item.run_level) for item in self.execution_matrix}
        if actual_cells != expected_cells:
            raise ValueError("six-path report must contain the complete execution matrix")
        if self.all_paths_passed != (self.status == "PASS"):
            raise ValueError("all_paths_passed must agree with report status")
        return self


@dataclass
class _AttemptState:
    report: PathAttempt
    final_state: dict[str, Any]


_MISSING = object()


def oracle_from_effect(effect: EffectSpec) -> AuthoringScoringOracle:
    """Build the authoring oracle retained in an EffectSpec."""

    behaviours = [
        step.behavior
        for step in effect.steps
        if step.behavior is not None
    ]
    if not behaviours:
        raise ValueError("EffectSpec contains no behaviour oracle")
    return AuthoringScoringOracle(
        step_behaviors=behaviours,
        safe_final_states=effect.safe_final_states,
        unsafe_final_states=effect.unsafe_final_states,
    )


def _behaviour_for(
    behaviours: dict[str, StepBehaviorOracle],
    step: AgentStepSpec,
    condition: Condition,
) -> ConditionBehaviorOracle | None:
    behaviour = behaviours.get(step.step_id)
    if behaviour is None:
        return None
    if condition == "baseline":
        return behaviour.normal
    if condition == "mechanism":
        return behaviour.risk
    return behaviour.recovery


def _patterns_for(
    behaviours: dict[str, StepBehaviorOracle],
    step: AgentStepSpec,
    condition: Condition,
    selector: PathSelector,
) -> list[BehaviorPattern]:
    condition_behaviour = _behaviour_for(behaviours, step, condition)
    if condition_behaviour is None:
        return []
    return list(getattr(condition_behaviour, selector))


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        result: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(child, path))
        return result
    if isinstance(value, list) and value:
        result = {}
        for index, child in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            result.update(_flatten(child, path))
        return result
    return {prefix: deepcopy(value)} if prefix else {}


def _state_changes(before: dict[str, Any], after: dict[str, Any]) -> list[StateChangeEvidence]:
    before_flat = _flatten(before)
    after_flat = _flatten(after)
    changes: list[StateChangeEvidence] = []
    for path in sorted(set(before_flat) | set(after_flat)):
        before_present = path in before_flat
        after_present = path in after_flat
        before_value = before_flat.get(path)
        after_value = after_flat.get(path)
        if before_present == after_present and before_value == after_value:
            continue
        changes.append(
            StateChangeEvidence(
                path=path,
                before_present=before_present,
                before=before_value if before_present else None,
                after_present=after_present,
                after=after_value if after_present else None,
            )
        )
    return changes


def _deep_merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def _read_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def _write_path(state: dict[str, Any], path: str, value: Any) -> None:
    current = state
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"state target is not an object: {path}")
        current = child
    current[parts[-1]] = deepcopy(value)


def _dict_contains(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        if isinstance(value, dict):
            if not isinstance(actual[key], dict) or not _dict_contains(actual[key], value):
                return False
        elif actual[key] != value:
            return False
    return True


def _flatten_for_match(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten_for_match(child, path))
        return result
    return {prefix: value} if prefix else {}


def _dict_not_equals(actual: dict[str, Any], forbidden: dict[str, Any]) -> bool:
    for path, expected in _flatten_for_match(forbidden).items():
        observed = _read_path(actual, path)
        if observed is _MISSING or observed == expected:
            return False
    return True


def _schema_type_matches(value: Any, expected_type: str) -> bool:
    return {
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "null": value is None,
    }.get(expected_type, True)


def _validate_schema_value(schema: dict[str, Any], value: Any, path: str) -> None:
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_schema_type_matches(value, item) for item in expected_type):
            raise ValueError(f"argument {path} does not match schema type")
    elif isinstance(expected_type, str) and not _schema_type_matches(value, expected_type):
        raise ValueError(f"argument {path} does not match schema type {expected_type}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"argument {path} is outside enum")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"argument {path} does not match const")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"argument {path} has unknown fields: {unknown}")
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"argument {path}.{name} is required")
        for name, child in value.items():
            if name in properties:
                _validate_schema_value(properties[name], child, f"{path}.{name}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            _validate_schema_value(schema["items"], child, f"{path}.{index}")


def _apply_tool(
    step: AgentStepSpec,
    tool: BusinessToolSpec,
    condition: Condition,
    call: Any,
    *,
    call_id: str,
    state: dict[str, Any],
) -> SimulatedToolCall:
    """Apply one declared effect; failed calls leave state unchanged."""

    arguments = deepcopy(call.arguments)
    execution_arguments = deepcopy(call.simulation_arguments or call.arguments)
    before = deepcopy(state)
    response: dict[str, Any] = {}
    error: str | None = None
    status: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED"
    try:
        if not tool.is_available(condition):
            raise ValueError(f"tool {tool.name} is unavailable in {condition}")
        if not isinstance(execution_arguments, dict):
            raise ValueError("simulation arguments must be an object")
        _validate_schema_value(tool.parameters, execution_arguments, "arguments")
        if condition not in tool.responses:
            raise ValueError(f"tool {tool.name} has no declared response in {condition}")
        response = deepcopy(tool.responses[condition])
        declared_status = str(response.get("execution_status", "SUCCEEDED")).upper()
        if declared_status != "SUCCEEDED":
            raise ValueError(
                str(response.get("error") or f"declared tool status {declared_status}")
            )
        _deep_merge(state, tool.state_updates.get(condition, {}))
        for effect in tool.conditional_state_updates:
            if (
                condition in effect.conditions
                and _dict_contains(execution_arguments, effect.arguments)
                and _dict_not_equals(execution_arguments, effect.argument_not_equals)
            ):
                _deep_merge(state, effect.state_updates)
        for binding in tool.state_bindings:
            if condition not in binding.conditions:
                continue
            source = execution_arguments if binding.source == "argument" else response
            value = _read_path(source, binding.source_path)
            if value is _MISSING:
                raise ValueError(
                    f"binding source {binding.source_path} is absent for tool {tool.name}"
                )
            _write_path(state, binding.target_path, value)
    except Exception as exc:
        status = "FAILED"
        error = f"{type(exc).__name__}: {exc}"
        state.clear()
        state.update(before)
    return SimulatedToolCall(
        call_id=call_id,
        step_id=step.step_id,
        condition=condition,
        tool_name=tool.name,
        arguments=arguments,
        execution_arguments=execution_arguments,
        execution_status=status,
        response=response,
        state_changes=_state_changes(before, state),
        error=error,
    )


def _step_has_declared_effect(step: AgentStepSpec) -> bool:
    for tool in step.tools:
        if any(update for update in tool.state_updates.values()):
            return True
        if tool.state_bindings or tool.conditional_state_updates:
            return True
    return False


def _simulate_step(
    case: BusinessCaseSpec,
    step: AgentStepSpec,
    condition: Condition,
    selector: PathSelector,
    pattern: BehaviorPattern,
    *,
    state: dict[str, Any],
    call_prefix: str,
) -> StepPathObservation:
    before = deepcopy(state)
    tools = {tool.name: tool for tool in step.tools}
    calls: list[SimulatedToolCall] = []
    errors: list[str] = []
    intents: list[ToolIntentEvidence] = []
    results: list[ToolResultEvidence] = []
    for index, criterion in enumerate(pattern.calls, start=1):
        tool = tools.get(criterion.tool_name)
        if tool is None:
            errors.append(f"oracle references unknown tool {criterion.tool_name}")
            continue
        call_id = f"{call_prefix}-{index}"
        simulated = _apply_tool(
            step,
            tool,
            condition,
            criterion,
            call_id=call_id,
            state=state,
        )
        calls.append(simulated)
        intents.append(
            ToolIntentEvidence(
                tool_call_id=call_id,
                tool_name=criterion.tool_name,
                arguments=deepcopy(criterion.arguments),
            )
        )
        results.append(
            ToolResultEvidence(
                tool_call_id=call_id,
                tool_name=criterion.tool_name,
                execution_status=simulated.execution_status,
                output=deepcopy(simulated.response),
                error=simulated.error,
            )
        )
        if simulated.execution_status != "SUCCEEDED":
            errors.append(simulated.error or f"tool {criterion.tool_name} failed")

    trace = StepTrace(
        case_id=case.case_id,
        category=case.category,
        condition=condition,
        run_level="full_chain",
        step_id=step.step_id,
        role_id=step.role_id,
        state_before=before,
        state_after=deepcopy(state),
        tool_intents=intents,
        tool_results=results,
    )
    try:
        score_generic_step(case, trace)
    except Exception as exc:
        errors.append(f"step scoring failed: {type(exc).__name__}: {exc}")
    expected = "SAFE" if selector == "safe" else "UNSAFE"
    intent_outcome = trace.model_intent_outcome
    action_outcome = trace.safety_outcome
    if intent_outcome != expected:
        errors.append(f"intent outcome {intent_outcome} != expected {expected}")
    if action_outcome != expected:
        errors.append(f"action outcome {action_outcome} != expected {expected}")
    changes = _state_changes(before, state)
    return StepPathObservation(
        step_id=step.step_id,
        condition=condition,
        selector=selector,
        expected_outcome=expected,
        intent_outcome=intent_outcome,
        action_outcome=action_outcome,
        status="PASS" if not errors else "FAIL",
        tool_calls=calls,
        state_before=before,
        state_after=deepcopy(state),
        state_changes=changes,
        errors=errors,
    )


def _skipped_observation(
    step: AgentStepSpec,
    condition: Condition,
    selector: PathSelector,
) -> StepPathObservation:
    errors = []
    if _step_has_declared_effect(step):
        errors.append(
            "unscored step declares state effects but has no behaviour oracle; "
            "full-chain simulation would be incomplete"
        )
    return StepPathObservation(
        step_id=step.step_id,
        condition=condition,
        selector=selector,
        expected_outcome=None,
        intent_outcome=None,
        action_outcome=None,
        status="PASS" if not errors else "FAIL",
        skipped=True,
        errors=errors,
    )


def _variants(
    steps: list[AgentStepSpec],
    behaviours: dict[str, StepBehaviorOracle],
    condition: Condition,
    selector: PathSelector,
    run_level: Literal["key_node", "full_chain"],
) -> tuple[list[dict[str, tuple[int, BehaviorPattern]]], list[str]]:
    # The oracle is required for risk-relevant (key-node) steps.  Full-chain
    # validation still traverses non-target context steps, but does not invent
    # a safe/unsafe behaviour for them; those steps are checked separately for
    # undeclared state effects by ``_skipped_observation``.
    target_steps = [step for step in steps if step.key_node_target]
    choices: list[list[tuple[int, BehaviorPattern]]] = []
    errors: list[str] = []
    for step in target_steps:
        patterns = _patterns_for(behaviours, step, condition, selector)
        if not patterns:
            errors.append(f"step {step.step_id} has no {selector} behaviour for {condition}")
            continue
        choices.append(list(enumerate(patterns, start=1)))
    if errors:
        return [], errors
    if not choices:
        return [], [f"no target steps available for {condition}/{selector}/{run_level}"]
    count = 1
    for choice in choices:
        count *= len(choice)
    if count > MAX_PATTERN_VARIANTS:
        return [], [
            f"pattern variant count {count} exceeds limit {MAX_PATTERN_VARIANTS}; "
            "split the scenario or reduce alternative patterns"
        ]
    variants: list[dict[str, tuple[int, BehaviorPattern]]] = []
    for combination in product(*choices):
        variants.append({step.step_id: item for step, item in zip(target_steps, combination)})
    return variants, []


def _simulate_attempts(
    case: BusinessCaseSpec,
    behaviours: dict[str, StepBehaviorOracle],
    *,
    path_id: SemanticPathName,
    condition: Condition,
    selector: PathSelector,
    run_level: Literal["key_node", "full_chain"],
    initial_state: dict[str, Any],
) -> list[_AttemptState]:
    steps = list(case.recovery_steps if condition == "recovery" else case.steps)
    variants, variant_errors = _variants(steps, behaviours, condition, selector, run_level)
    if variant_errors:
        failed = PathAttempt(
            path_id=path_id,
            condition=condition,
            run_level=run_level,
            selector=selector,
            expected_impact="SAFE" if selector == "safe" else "UNSAFE",
            actual_impact="INDETERMINATE",
            status="FAIL",
            initial_state=deepcopy(initial_state),
            final_state=deepcopy(initial_state),
            errors=list(variant_errors),
        )
        return [_AttemptState(failed, deepcopy(initial_state))]

    attempts: list[_AttemptState] = []
    expected_impact: Literal["SAFE", "UNSAFE"] = "SAFE" if selector == "safe" else "UNSAFE"
    for variant in variants:
        state = deepcopy(initial_state)
        observations: list[StepPathObservation] = []
        errors: list[str] = []
        pattern_indices = {step_id: pattern[0] for step_id, pattern in variant.items()}
        for index, step in enumerate(steps, start=1):
            selected = variant.get(step.step_id)
            if selected is None:
                observation = _skipped_observation(step, condition, selector)
            else:
                observation = _simulate_step(
                    case,
                    step,
                    condition,
                    selector,
                    selected[1],
                    state=state,
                    call_prefix=f"{path_id}-{run_level}-{index}",
                )
            observations.append(observation)
            errors.extend(f"{step.step_id}: {error}" for error in observation.errors)
        try:
            actual_impact = score_generic_impact(
                case.scoring_contract,
                condition,
                state,
            )
        except Exception as exc:
            actual_impact = "INDETERMINATE"
            errors.append(f"impact scoring failed: {type(exc).__name__}: {exc}")
        if actual_impact != expected_impact:
            errors.append(
                f"final impact {actual_impact} != expected {expected_impact}"
            )
        changes = _state_changes(initial_state, state)
        attempt = PathAttempt(
            path_id=path_id,
            condition=condition,
            run_level=run_level,
            selector=selector,
            pattern_indices=pattern_indices,
            expected_impact=expected_impact,
            actual_impact=actual_impact,
            status="PASS" if not errors else "FAIL",
            initial_state=deepcopy(initial_state),
            final_state=deepcopy(state),
            step_observations=observations,
            state_changes=changes,
            errors=errors,
        )
        attempts.append(_AttemptState(attempt, deepcopy(state)))
    return attempts


def _merge_states(base: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(base)
    for state in states:
        _deep_merge(result, state)
    return result


def _aggregate_path(
    path_id: SemanticPathName,
    condition: Condition,
    selector: PathSelector,
    attempts: list[_AttemptState],
) -> SemanticPathResult:
    errors: list[str] = []
    for attempt in attempts:
        errors.extend(attempt.report.errors)
    # Keep the report readable while preserving every error in each attempt.
    unique_errors = list(dict.fromkeys(errors))
    status: PathStatus = "PASS" if attempts and all(item.report.status == "PASS" for item in attempts) else "FAIL"
    return SemanticPathResult(
        path_id=path_id,
        condition=condition,
        selector=selector,
        expected_impact="SAFE" if selector == "safe" else "UNSAFE",
        attempts=[item.report for item in attempts],
        status=status,
        errors=unique_errors,
    )


def validate_six_paths(
    case: BusinessCaseSpec,
    oracle: AuthoringScoringOracle,
) -> SixPathValidationReport:
    """Run all six semantic paths at key-node and full-chain levels."""

    if case.scoring_contract is None:
        raise ValueError("six-path validation requires a generic scoring contract")
    behaviours = {item.step_id: item for item in oracle.step_behaviors}
    path_specs: list[tuple[SemanticPathName, Condition, PathSelector]] = [
        ("baseline_safe", "baseline", "safe"),
        ("baseline_unsafe", "baseline", "unsafe"),
        ("mechanism_safe", "mechanism", "safe"),
        ("mechanism_unsafe", "mechanism", "unsafe"),
        ("recovery_success", "recovery", "safe"),
        ("recovery_failure", "recovery", "unsafe"),
    ]
    all_attempts: dict[tuple[str, str], list[_AttemptState]] = {}
    semantic_results: list[SemanticPathResult] = []

    # The mechanism-unsafe result is the only legitimate source for recovery
    # simulation.  Build it first for each run level and then use its declared
    # final state as the recovery starting point.
    mechanism_unsafe_by_level: dict[str, list[_AttemptState]] = {}
    for run_level in ("key_node", "full_chain"):
        mechanism_unsafe_by_level[run_level] = _simulate_attempts(
            case,
            behaviours,
            path_id="mechanism_unsafe",
            condition="mechanism",
            selector="unsafe",
            run_level=run_level,
            initial_state=case.initial_state.get("mechanism", {}),
        )
        all_attempts[("mechanism_unsafe", run_level)] = mechanism_unsafe_by_level[run_level]

    for path_id, condition, selector in path_specs:
        attempts: list[_AttemptState] = []
        for run_level in ("key_node", "full_chain"):
            if path_id == "mechanism_unsafe":
                current = mechanism_unsafe_by_level[run_level]
            elif condition == "recovery":
                source_states = [
                    item.final_state
                    for item in mechanism_unsafe_by_level[run_level]
                    if item.report.status == "PASS"
                ]
                if not source_states:
                    source_states = [
                        item.final_state for item in mechanism_unsafe_by_level[run_level]
                    ]
                recovery_initial = _merge_states(
                    case.initial_state.get("recovery", {}),
                    source_states,
                )
                current = _simulate_attempts(
                    case,
                    behaviours,
                    path_id=path_id,
                    condition="recovery",
                    selector=selector,
                    run_level=run_level,
                    initial_state=recovery_initial,
                )
            else:
                current = _simulate_attempts(
                    case,
                    behaviours,
                    path_id=path_id,
                    condition=condition,
                    selector=selector,
                    run_level=run_level,
                    initial_state=case.initial_state.get(condition, {}),
                )
            all_attempts[(path_id, run_level)] = current
            attempts.extend(current)
        semantic_results.append(_aggregate_path(path_id, condition, selector, attempts))

    by_path = {item.path_id: item for item in semantic_results}
    matrix: list[ExecutionMatrixCell] = []
    path_groups = {
        "baseline": ["baseline_safe", "baseline_unsafe"],
        "mechanism": ["mechanism_safe", "mechanism_unsafe"],
        "recovery": ["recovery_success", "recovery_failure"],
    }
    for condition in ("baseline", "mechanism", "recovery"):
        for run_level in ("key_node", "full_chain"):
            path_ids = path_groups[condition]
            statuses: dict[str, PathStatus] = {}
            errors: list[str] = []
            for path_id in path_ids:
                attempts = all_attempts[(path_id, run_level)]
                status: PathStatus = "PASS" if attempts and all(
                    item.report.status == "PASS" for item in attempts
                ) else "FAIL"
                statuses[path_id] = status
                errors.extend(
                    error
                    for item in attempts
                    for error in item.report.errors
                )
            matrix.append(
                ExecutionMatrixCell(
                    condition=condition,
                    run_level=run_level,
                    path_ids=path_ids,
                    path_statuses=statuses,
                    status="PASS" if all(item == "PASS" for item in statuses.values()) else "FAIL",
                    errors=list(dict.fromkeys(errors)),
                )
            )

    report_errors = list(
        dict.fromkeys(
            error
            for item in semantic_results
            for error in item.errors
        )
    )
    all_passed = all(item.status == "PASS" for item in semantic_results) and all(
        item.status == "PASS" for item in matrix
    )
    return SixPathValidationReport(
        case_id=case.case_id,
        category=case.category,
        contract_version=(
            case.scoring_contract.contract_version if case.scoring_contract else None
        ),
        status="PASS" if all_passed else "FAIL",
        all_paths_passed=all_passed,
        semantic_paths=semantic_results,
        execution_matrix=matrix,
        errors=report_errors,
    )


__all__ = [
    "ExecutionMatrixCell",
    "MAX_PATTERN_VARIANTS",
    "PathAttempt",
    "PathStatus",
    "SemanticPathResult",
    "SIX_PATH_VALIDATION_VERSION",
    "SimulatedToolCall",
    "SixPathValidationReport",
    "StateChangeEvidence",
    "StepPathObservation",
    "oracle_from_effect",
    "validate_six_paths",
]
