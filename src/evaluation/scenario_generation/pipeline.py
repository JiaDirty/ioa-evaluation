"""Conversion and compilation helpers for the two-stage scenario pipeline.

This module deliberately does not alter source candidates.  Reference-source runtime
cases can be *extracted* into a kernel/effect draft, while newly generated
kernel/effect pairs can be compiled through the existing authoring compiler.
The distinction is recorded in the resulting manifest so a draft is never
mistaken for a formally accepted scenario.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..business_protocol.models import AgentInput, BusinessCaseSpec, ReferenceBehavior
from ..catalog import load_evaluation_catalog
from ..business_protocol.scoring_contract import PATH_PATTERN
from ..candidate_review.deterministic import CandidateRecord
from .authoring import (
    AuthoringCaseSpec,
    AuthoringScoringOracle,
    AuthoringStepSpec,
    BehaviorPattern,
    ConditionBehaviorOracle,
    FinalStatePattern,
    StepBehaviorOracle,
    ToolCallCriterion,
    compile_authoring_case,
)
from .pipeline_models import (
    EffectSpec,
    EffectSpecDraft,
    EffectStepSpec,
    EffectToolSpec,
    KernelRole,
    KernelSource,
    KernelStep,
    ScenarioKernel,
    ScenarioKernelDraft,
    stable_json,
    seal_effect_spec,
    seal_kernel,
    verify_effect_kernel_binding,
)


_CONDITION_MAP = {"baseline": "normal", "mechanism": "risk", "recovery": "recovery"}
_REVERSE_CONDITION_MAP = {value: key for key, value in _CONDITION_MAP.items()}
_MISSING = object()


def runtime_to_authoring_condition(condition: str) -> str:
    """Translate a runtime condition to the authoring-layer name explicitly.

    The authoring schema intentionally uses ``normal``/``risk`` while the
    runtime protocol uses ``baseline``/``mechanism``.  Keeping this conversion
    in one strict function prevents a missing or misspelled condition from
    being silently copied from another branch.
    """

    try:
        return _CONDITION_MAP[condition]
    except KeyError as exc:
        raise PipelineConversionError(
            f"unknown runtime condition: {condition!r}; expected baseline, mechanism or recovery"
        ) from exc


def authoring_to_runtime_condition(condition: str) -> str:
    """Translate an authoring condition to the runtime protocol name."""

    try:
        return _REVERSE_CONDITION_MAP[condition]
    except KeyError as exc:
        raise PipelineConversionError(
            f"unknown authoring condition: {condition!r}; expected normal, risk or recovery"
        ) from exc


class PipelineConversionError(ValueError):
    """Raised when an intermediate representation cannot be built safely."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_case(case: BusinessCaseSpec) -> str:
    return hashlib.sha256(stable_json(case).encode("utf-8")).hexdigest()


def kernel_id_for_candidate(candidate_uid: str) -> str:
    return "kernel-" + hashlib.sha256(candidate_uid.encode("utf-8")).hexdigest()[:24]


def effect_id_for_kernel(kernel_id: str) -> str:
    return "effect-" + hashlib.sha256(kernel_id.encode("utf-8")).hexdigest()[:24]


def materialize_kernel_draft(
    draft: ScenarioKernelDraft | dict[str, Any],
    *,
    candidate_uid: str,
    source_path: str | None = None,
    generator_model_id: str | None = None,
    generation_seed: int | str | None = None,
    prompt_version: str | None = None,
    source_case_id: str | None = None,
) -> ScenarioKernel:
    """Attach local identity/provenance to a model-produced kernel draft."""

    parsed = draft if isinstance(draft, ScenarioKernelDraft) else ScenarioKernelDraft.model_validate(draft)
    source = KernelSource(
        source_kind="generated",
        source_candidate_uid=candidate_uid,
        source_case_id=source_case_id,
        source_path=source_path,
        generator_model_id=generator_model_id,
        generation_seed=generation_seed,
        prompt_version=prompt_version,
    )
    payload = parsed.model_dump(mode="json")
    payload.pop("schema_version", None)
    payload.update(
        {
            "schema_version": "scenario_kernel_v1",
            "kernel_id": kernel_id_for_candidate(candidate_uid),
            "source": source.model_dump(mode="json"),
        }
    )
    return seal_kernel(ScenarioKernel.model_validate(payload))


def materialize_effect_draft(
    draft: EffectSpecDraft | dict[str, Any],
    *,
    kernel: ScenarioKernel,
    source: KernelSource | None = None,
) -> EffectSpec:
    """Bind a model-produced effect draft to an immutable kernel hash."""

    parsed = draft if isinstance(draft, EffectSpecDraft) else EffectSpecDraft.model_validate(draft)
    if parsed.kernel_id != kernel.kernel_id or parsed.kernel_sha256 != kernel.content_sha256:
        raise PipelineConversionError(
            "EffectSpec draft 的 kernel_id/kernel_sha256 与当前 ScenarioKernel 不一致"
        )
    payload = parsed.model_dump(mode="json")
    payload.update(
        {
            "schema_version": "effect_spec_v1",
            "effect_id": effect_id_for_kernel(kernel.kernel_id),
            "status": "READY_FOR_COMPILE",
            "source": (source or kernel.source).model_dump(mode="json"),
        }
    )
    effect = EffectSpec.model_validate(payload)
    verify_effect_kernel_binding(kernel, seal_effect_spec(effect))
    return seal_effect_spec(effect)


def _category_name(category: str) -> str:
    catalog = load_evaluation_catalog()
    if category in catalog.category_names_zh:
        return category
    for item in catalog.categories:
        if item.code == category:
            return item.name_zh
    raise PipelineConversionError(f"unknown evaluation category: {category}")


def _authoring_execution_plan(case: BusinessCaseSpec):
    """Translate the runtime execution plan without changing its semantics."""

    from .authoring import AuthoringExecutionPlan

    runtime = case.execution_plan
    return AuthoringExecutionPlan(
        pairing=runtime.pairing,
        shared_prefix_step_ids=list(runtime.shared_prefix_step_ids),
        normal_state_overrides=deepcopy(runtime.baseline_state_overrides),
        recovery_policy={
            "on_mechanism_unsafe": "on_risk_unsafe",
            "always": "always",
            "never": "never",
        }[runtime.recovery_policy],
        recovery_step_ids=(
            list(runtime.recovery_step_ids)
            if runtime.recovery_step_ids is not None
            else None
        ),
    )


def _source_from_record(record: CandidateRecord, source_sha256: str | None) -> KernelSource:
    metadata = record.case.metadata.get("generation_provenance", {})
    if not isinstance(metadata, dict):
        metadata = {}
    # A source candidate can be extracted repeatedly during resume/repair.
    # Use the immutable source file mtime for provenance instead of ``now`` so
    # the semantic kernel/effect digest remains stable across those runs.
    try:
        extracted_at = datetime.fromtimestamp(
            record.source_path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except OSError:
        extracted_at = "1970-01-01T00:00:00+00:00"
    return KernelSource(
        source_kind="reference_extracted",
        source_candidate_uid=record.candidate_uid,
        source_case_id=record.case.case_id,
        source_path=str(record.source_path),
        source_sha256=source_sha256,
        generator_model_id=record.generator_model_id,
        generation_seed=metadata.get("generation_seed"),
        prompt_version=metadata.get("prompt_version"),
        extracted_at=extracted_at,
    )


def _input_for(case_step: Any, condition: str) -> AgentInput:
    # Do not use AgentStepSpec.input_for() here: that helper intentionally
    # falls back to the mechanism input for runtime convenience, whereas an
    # extraction must never silently turn a missing condition into a copied
    # condition.  Missing inputs are a migration finding, not a guess.
    if condition not in case_step.inputs:
        raise PipelineConversionError(
            f"step {case_step.step_id} has no explicit {condition} input"
        )
    return deepcopy(case_step.inputs[condition])


def _history_for(case_step: Any, condition: str) -> list[dict[str, Any]]:
    if condition not in case_step.history_fixtures:
        return []
    return deepcopy(case_step.history_fixtures[condition])


def _short_evidence(inputs: Iterable[AgentInput], *, limit: int = 1800) -> str:
    chunks: list[str] = []
    for item in inputs:
        for record in item.records:
            chunks.append(f"{record.record_type}: {record.content}")
        for artifact in item.upstream_artifacts:
            chunks.append(f"交接产物: {artifact.content}")
    text = "；".join(chunk.strip() for chunk in chunks if chunk.strip())
    return text[:limit] if text else "未提供可提取的业务证据，需补充。"


def _risk_impacts(case: BusinessCaseSpec) -> list[str]:
    paths: set[str] = set()
    for step in case.steps:
        unsafe_names = set(step.reference.objective_unsafe_tools if step.reference else [])
        for tool in step.tools:
            if tool.name not in unsafe_names:
                continue
            for update in tool.state_updates.values():
                paths.update(_leaf_paths(update))
            for binding in tool.state_bindings:
                paths.add(binding.target_path)
    return sorted(paths)


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict) and value:
        result: set[str] = set()
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_leaf_paths(item, child))
        return result
    return {prefix} if prefix else set()


def _causal_variable(case: BusinessCaseSpec) -> str:
    metadata = case.metadata
    for key in ("condition_difference", "causal_variable", "mechanism_difference"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    differences: list[str] = []
    for step in case.steps:
        normal = step.input_for("baseline").model_dump(mode="json")
        risk = step.input_for("mechanism").model_dump(mode="json")
        if normal != risk:
            differences.append(f"步骤 {step.step_id} 的可见业务输入不同")
    if case.initial_state.get("baseline") != case.initial_state.get("mechanism"):
        differences.append("正常与风险初始状态不同")
    return "；".join(differences) or "正常与风险条件的关键差异尚未明确，需要语义修复。"


def extract_scenario_kernel(
    record: CandidateRecord,
    *,
    source_sha256: str | None = None,
) -> ScenarioKernel:
    """Extract business semantics from a source candidate without guessing effects."""

    case = record.case
    roles: dict[str, KernelRole] = {}
    kernel_steps: list[KernelStep] = []
    for step in case.steps:
        roles.setdefault(
            step.role_id,
            KernelRole(role_id=step.role_id, responsibility=step.role_responsibility),
        )
        reference = step.reference
        kernel_steps.append(
            KernelStep(
                step_id=step.step_id,
                upstream_step_ids=list(step.upstream_step_ids),
                role_id=step.role_id,
                role_responsibility=step.role_responsibility,
                task_id=step.task_id,
                task_content=step.task_content,
                current_time=step.current_time,
                business_object=step.business_object,
                inputs={
                    "normal": _input_for(step, "baseline"),
                    "risk": _input_for(step, "mechanism"),
                },
                raw_user_message=step.raw_user_message,
                history_fixtures={
                    "normal": _history_for(step, "baseline"),
                    "risk": _history_for(step, "mechanism"),
                },
                current_times={
                    runtime_to_authoring_condition(condition): step.current_time_for(condition)
                    for condition in ("baseline", "mechanism")
                    if condition in step.current_times
                },
                safe_behavior=(
                    reference.safe_summary if reference else "安全行为尚未明确声明，需要语义修复。"
                ),
                unsafe_behavior=(
                    reference.unsafe_summary if reference else "危险行为尚未明确声明，需要语义修复。"
                ),
                key_node_fixture=step.key_node_fixture,
                key_node_target=step.key_node_target,
                visible_state_paths=list(step.visible_state_paths),
                observable_risk_impacts=_risk_impacts_for_step(step),
                metadata=deepcopy(step.metadata),
            )
        )
    recovery_kernel_steps: list[KernelStep] = []
    main_ids = {step.step_id for step in case.steps}
    for step in case.recovery_steps:
        roles.setdefault(
            step.role_id,
            KernelRole(role_id=step.role_id, responsibility=step.role_responsibility),
        )
        # Runtime compilation keeps recovery-source links in metadata and
        # reserves upstream_step_ids for dependencies inside the recovery
        # flow.  The kernel stores the two concepts separately.
        source_ids = step.metadata.get("recovery_source_step_ids", [])
        if not isinstance(source_ids, list):
            source_ids = []
        source_ids = [str(value) for value in source_ids]
        if not source_ids:
            source_ids = [
                str(value)
                for value in step.upstream_step_ids
                if str(value) in main_ids
            ]
        internal_upstream_ids = [
            str(value)
            for value in step.upstream_step_ids
            if str(value) not in main_ids
        ]
        reference = step.reference
        recovery_kernel_steps.append(
            KernelStep(
                step_id=step.step_id,
                upstream_step_ids=internal_upstream_ids,
                role_id=step.role_id,
                role_responsibility=step.role_responsibility,
                task_id=step.task_id,
                task_content=step.task_content,
                current_time=step.current_time,
                business_object=step.business_object,
                inputs={"recovery": _input_for(step, "recovery")},
                raw_user_message=step.raw_user_message,
                history_fixtures={"recovery": _history_for(step, "recovery")},
                current_times={
                    runtime_to_authoring_condition("recovery"): step.current_time_for("recovery")
                }
                if "recovery" in step.current_times
                else {},
                safe_behavior=(
                    reference.safe_summary if reference else "安全恢复行为尚未明确声明，需要语义修复。"
                ),
                unsafe_behavior=(
                    reference.unsafe_summary if reference else "错误恢复行为尚未明确声明，需要语义修复。"
                ),
                recovery_source_step_ids=source_ids,
                key_node_fixture=step.key_node_fixture,
                key_node_target=step.key_node_target,
                visible_state_paths=list(step.visible_state_paths),
                observable_risk_impacts=_risk_impacts_for_step(step),
                metadata=deepcopy(step.metadata),
            )
        )
    metadata = dict(case.metadata)
    domain = str(metadata.get("industry_domain") or metadata.get("business_domain") or "未标注业务领域")
    object_text = str(
        metadata.get("business_object")
        or (case.steps[0].business_object if case.steps else "未标注业务对象")
    )
    risk_tools = sorted({
        tool.name
        for step in case.steps
        for tool in step.tools
        if step.reference and tool.name in step.reference.objective_unsafe_tools
    })
    kernel = ScenarioKernel(
        kernel_id=kernel_id_for_candidate(record.candidate_uid),
        category=_category_name(case.category),
        subtype=metadata.get("sub_mechanism") if isinstance(metadata.get("sub_mechanism"), str) else None,
        title=case.title,
        purpose=case.purpose,
        business_domain=domain,
        business_object=object_text,
        roles=list(roles.values()),
        steps=kernel_steps,
        recovery_steps=recovery_kernel_steps,
        initial_state={
            "normal": deepcopy(case.initial_state.get("baseline", {})),
            "risk": deepcopy(case.initial_state.get("mechanism", {})),
            "recovery": deepcopy(case.initial_state.get("recovery", {})),
        },
        causal_variable=_causal_variable(case),
        normal_evidence_summary=_short_evidence(
            [_input_for(step, "baseline") for step in case.steps]
        ),
        risk_evidence_summary=_short_evidence(
            [_input_for(step, "mechanism") for step in case.steps]
        ),
        risk_consequences=(
            [f"危险工具 {name} 成功执行并造成可观察业务影响" for name in risk_tools]
            or ["危险后果尚未能从旧数据明确推导，需要语义修复。"]
        ),
        recovery_goal=(
            "；".join(
                step.safe_behavior
                for step in recovery_kernel_steps
                if step.safe_behavior
            )
            or "原候选没有恢复步骤或未明确恢复目标，需要语义修复。"
        ),
        execution_plan=_authoring_execution_plan(case),
        source=_source_from_record(record, source_sha256),
        metadata={
            **metadata,
            "extraction_version": "reference_case_to_kernel_v1",
            "source_case_sha256": sha256_case(case),
        },
    )
    return seal_kernel(kernel)


def _risk_impacts_for_step(step: Any) -> list[str]:
    unsafe_names = set(step.reference.objective_unsafe_tools if step.reference else [])
    paths: set[str] = set()
    for tool in step.tools:
        if tool.name not in unsafe_names:
            continue
        for update in tool.state_updates.values():
            paths.update(_leaf_paths(update))
        for binding in tool.state_bindings:
            paths.add(binding.target_path)
    return sorted(paths)


def _runtime_tool_to_effect(tool: Any) -> EffectToolSpec:
    """Convert an expanded runtime tool while preserving non-compressible effects."""

    available = list(tool.available_conditions)
    # Pick a deterministic existing condition as the shared response.  Reference-source
    # candidates occasionally expose only ``mechanism`` or list conditions in
    # a different order; assuming baseline in those cases would silently lose
    # a provider response.
    base_condition = next(
        (condition for condition in ("baseline", "mechanism", "recovery") if condition in available),
        None,
    )
    if base_condition is None:
        raise PipelineConversionError(f"tool {tool.name} has no available condition")
    base_response = deepcopy(tool.responses.get(base_condition, {}))
    response_overrides: dict[str, dict[str, Any]] = {}
    for runtime_condition in _CONDITION_MAP:
        if runtime_condition not in available or runtime_condition == base_condition:
            continue
        response = tool.responses.get(runtime_condition, {})
        if response != base_response:
            response_overrides[runtime_to_authoring_condition(runtime_condition)] = deepcopy(response)

    state_maps = {
        runtime_condition: deepcopy(tool.state_updates.get(runtime_condition, {}))
        for runtime_condition in available
    }
    flattened = {condition: _flatten_values(value) for condition, value in state_maps.items()}
    fixed_state: dict[str, Any] = {}
    source_effects: dict[str, Any] | None = None
    all_paths_valid = all(
        PATH_PATTERN.fullmatch(path)
        for values in flattened.values()
        for path in values
    )
    binding_paths_valid = all(
        PATH_PATTERN.fullmatch(path)
        for binding in tool.state_bindings
        for path in (binding.target_path, binding.source_path)
    )
    same_effect = bool(flattened) and all(
        value == next(iter(flattened.values())) for value in flattened.values()
    )
    if same_effect and all_paths_valid and binding_paths_valid:
        fixed_state = deepcopy(next(iter(flattened.values())))
    elif any(flattened.values()) or not all_paths_valid or not binding_paths_valid:
        source_effects = {
            "responses": deepcopy(tool.responses),
            "state_updates": state_maps,
            "available_conditions": available,
            "reason": (
                "unsupported state path or condition-specific effect"
                if not all_paths_valid or not binding_paths_valid
                else "condition-specific state effects"
            ),
        }
    return EffectToolSpec(
        name=tool.name,
        description=tool.description,
        kind=_tool_kind(tool),
        parameters=deepcopy(tool.parameters),
        response=base_response,
        response_overrides=response_overrides,
        fixed_state=fixed_state,
        state_bindings=deepcopy(tool.state_bindings),
        conditional_state_updates=deepcopy(tool.conditional_state_updates),
        risk_level=tool.risk_level,
        condition_effects=source_effects,
    )


def _flatten_values(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        result: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten_values(item, child))
        return result
    return {prefix: deepcopy(value)} if prefix else {}


def _tool_kind(tool: Any) -> str:
    name = tool.name.lower()
    if name.startswith(("query_", "get_", "fetch_", "read_", "lookup_", "verify_", "inspect_", "check_")):
        return "observe"
    if tool.risk_level in {"high", "critical"}:
        return "commit"
    return "request"


def _criterion(tool_name: str) -> ToolCallCriterion:
    return ToolCallCriterion(tool_name=tool_name)


def _infer_step_behavior(step: Any) -> StepBehaviorOracle | None:
    tools = {tool.name: tool for tool in step.tools}
    unsafe = [
        name
        for name in (step.reference.objective_unsafe_tools if step.reference else [])
        if name in tools
    ]
    safe = [
        tool.name
        for tool in step.tools
        if tool.name not in unsafe and tool.risk_level == "low"
    ]
    if not unsafe or not safe:
        return None
    safe_pattern = BehaviorPattern(calls=[_criterion(safe[0])])
    unsafe_pattern = BehaviorPattern(calls=[_criterion(unsafe[0])])
    return StepBehaviorOracle(
        step_id=step.step_id,
        normal=ConditionBehaviorOracle(safe=[safe_pattern], unsafe=[unsafe_pattern]),
        risk=ConditionBehaviorOracle(safe=[safe_pattern], unsafe=[unsafe_pattern]),
    )


def _infer_recovery_behavior(step: Any) -> StepBehaviorOracle | None:
    safe = [tool.name for tool in step.tools if tool.risk_level == "low" and _tool_kind(tool) == "recover"]
    if not safe:
        safe = [tool.name for tool in step.tools if tool.risk_level == "low"]
    unsafe = [tool.name for tool in step.tools if tool.risk_level in {"high", "critical"}]
    if not safe or not unsafe:
        return None
    return StepBehaviorOracle(
        step_id=step.step_id,
        recovery=ConditionBehaviorOracle(
            safe=[BehaviorPattern(calls=[_criterion(safe[0])])],
            unsafe=[BehaviorPattern(calls=[_criterion(unsafe[0])])],
        )
    )


def extract_effect_spec(record: CandidateRecord, kernel: ScenarioKernel) -> EffectSpec:
    """Extract an explicit draft; never fabricate a ready contract for source data.

    The source candidates do not declare a general scoring oracle.  It is tempting
    to infer a safe call from the first low-risk tool and an unsafe call from
    the first high-risk tool, but that changes the experiment when a tool is
    merely preparatory or when the decisive argument is meaningful.  We keep
    the executable effects and objective tool hints as material for a later
    semantic repair stage and leave every behaviour rule unset.
    """

    verify_kernel_binding = kernel.source.source_candidate_uid == record.candidate_uid
    if not verify_kernel_binding:
        raise PipelineConversionError("kernel source candidate does not match record")
    effect_steps: list[EffectStepSpec] = []
    notes: list[str] = []
    for step in [*record.case.steps, *record.case.recovery_steps]:
        # Intentionally do not infer a scoring pattern from risk_level or tool
        # order. A source case must be semantically repaired before it can be
        # marked READY_FOR_COMPILE.
        behavior = None
        notes.append(
            f"step {step.step_id}: 旧候选没有可验证的通用安全/危险行为契约，需语义修复"
        )
        effect_tools = [_runtime_tool_to_effect(tool) for tool in step.tools]
        if any(tool.condition_effects is not None for tool in effect_tools):
            notes.append(f"step {step.step_id}: 条件相关状态效果无法安全压缩，需要重写 EffectSpec")
        effect_steps.append(
            EffectStepSpec(
                step_id=step.step_id,
                tools=effect_tools,
                behavior=behavior,
                scored=step.key_node_target,
                objective_unsafe_tools=[
                    name
                    for name in (
                        step.reference.objective_unsafe_tools
                        if step.reference
                        else []
                    )
                    if any(tool.name == name for tool in step.tools)
                ],
            )
        )
    effect = EffectSpec(
        effect_id=effect_id_for_kernel(kernel.kernel_id),
        kernel_id=kernel.kernel_id,
        kernel_sha256=kernel.content_sha256 or "0" * 64,
        status="DRAFT",
        steps=effect_steps,
        safe_final_states=[],
        unsafe_final_states=[],
        execution_plan=kernel.execution_plan,
        notes=notes or ["旧候选缺少通用判分契约，EffectSpec 仅为迁移草案。"],
        source=kernel.source,
        metadata={
            "extraction_version": "reference_case_to_effect_spec_v1",
            "source_case_sha256": sha256_case(record.case),
        },
    )
    return seal_effect_spec(effect)


def compile_kernel_effect(
    kernel: ScenarioKernel,
    effect: EffectSpec,
    *,
    case_id: str,
    category: str | None = None,
    provenance: dict[str, Any] | None = None,
) -> BusinessCaseSpec:
    """Compile a ready kernel/effect pair through the existing strict compiler."""

    verify_effect_kernel_binding(kernel, effect)
    if effect.status != "READY_FOR_COMPILE":
        raise PipelineConversionError("only READY_FOR_COMPILE EffectSpec can be compiled")
    effect_by_step = {step.step_id: step for step in effect.steps}
    kernel_by_step = {step.step_id: step for step in [*kernel.steps, *kernel.recovery_steps]}
    if set(effect_by_step) != set(kernel_by_step):
        raise PipelineConversionError("EffectSpec step IDs must exactly match ScenarioKernel")
    authored_steps: list[AuthoringStepSpec] = []
    authored_recovery: list[AuthoringStepSpec] = []
    oracle_steps: list[StepBehaviorOracle] = []
    for is_recovery, steps in ((False, kernel.steps), (True, kernel.recovery_steps)):
        for kernel_step in steps:
            effect_step = effect_by_step[kernel_step.step_id]
            if effect_step.behavior is None and kernel_step.key_node_target:
                raise PipelineConversionError(
                    f"scored step {kernel_step.step_id} has no behaviour specification"
                )
            tools = [tool.to_authoring_tool() for tool in effect_step.tools]
            reference = ReferenceBehavior(
                safe_summary=kernel_step.safe_behavior,
                unsafe_summary=kernel_step.unsafe_behavior,
                objective_unsafe_tools=effect_step.objective_unsafe_tools,
            )
            authored = AuthoringStepSpec(
                step_id=kernel_step.step_id,
                upstream_step_ids=[
                    *list(kernel_step.upstream_step_ids),
                    *(
                        list(kernel_step.recovery_source_step_ids)
                        if is_recovery
                        else []
                    ),
                ],
                role_id=kernel_step.role_id,
                role_responsibility=kernel_step.role_responsibility,
                task_id=kernel_step.task_id,
                task_content=kernel_step.task_content,
                current_time=kernel_step.current_time,
                current_times=deepcopy(kernel_step.current_times),
                business_object=kernel_step.business_object,
                visible_state_paths=list(kernel_step.visible_state_paths),
                inputs=deepcopy(kernel_step.inputs),
                raw_user_message=kernel_step.raw_user_message,
                history_fixtures=deepcopy(kernel_step.history_fixtures),
                tools=tools,
                reference=reference,
                key_node_fixture=kernel_step.key_node_fixture,
                key_node_target=kernel_step.key_node_target,
                metadata=deepcopy(kernel_step.metadata),
            )
            (authored_recovery if is_recovery else authored_steps).append(authored)
            if effect_step.behavior is not None and kernel_step.key_node_target:
                oracle_steps.append(effect_step.behavior)
    oracle = AuthoringScoringOracle(
        step_behaviors=oracle_steps,
        safe_final_states=effect.safe_final_states,
        unsafe_final_states=effect.unsafe_final_states,
    )
    authored_case = AuthoringCaseSpec(
        title=kernel.title,
        purpose=kernel.purpose,
        steps=authored_steps,
        recovery_steps=authored_recovery,
        initial_state=deepcopy(kernel.initial_state),
        execution_plan=effect.execution_plan,
        metadata={
            **deepcopy(kernel.metadata),
            "pipeline": {
                "kernel_id": kernel.kernel_id,
                "kernel_sha256": kernel.content_sha256,
                "effect_id": effect.effect_id,
                "effect_sha256": effect.content_sha256,
            },
        },
    )
    return compile_authoring_case(
        authored_case,
        oracle,
        case_id=case_id,
        category=category or kernel.category,
        provenance=provenance,
    )


__all__ = [
    "PipelineConversionError",
    "authoring_to_runtime_condition",
    "compile_kernel_effect",
    "effect_id_for_kernel",
    "extract_effect_spec",
    "extract_scenario_kernel",
    "kernel_id_for_candidate",
    "materialize_effect_draft",
    "materialize_kernel_draft",
    "sha256_case",
    "sha256_file",
    "runtime_to_authoring_condition",
]
