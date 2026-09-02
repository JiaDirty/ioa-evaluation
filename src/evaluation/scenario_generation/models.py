"""Strict batch wrapper for AI-generated BusinessCaseSpec candidates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..business_protocol.models import BusinessCaseSpec
from ..catalog import TEN_CATEGORY_NAMES_ZH, load_evaluation_catalog

# Core pipeline models are exposed here alongside batch authoring models.  The
# implementation lives in the production orchestrator module to avoid a
# second copy of the validation logic.
from .orchestrator import CompiledCase, ScenarioTask, TaskProvenance, seal_compiled_case, seal_task

__all__ = [
    "BatchAudit", "CompiledCase", "GenerationConfig", "GenerationStatus",
    "ScenarioGenerationBatch", "ScenarioTask", "TaskProvenance",
    "seal_compiled_case", "seal_task",
]


GenerationStatus = Literal["COMPLETED", "FAILED_QUALITY_GATE"]


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_category: str
    scenario_count: int = Field(ge=1, le=10)
    batch_id: str = Field(min_length=1)
    generator_id: str = Field(min_length=1)
    generator_model_id: str = Field(min_length=1)
    generation_seed: int | str | None = None
    required_case_id: str | None = None
    excluded_case_ids: list[str] = Field(default_factory=list)
    excluded_scenario_count: int = Field(ge=0)

    @field_validator("generation_seed", mode="before")
    @classmethod
    def normalize_numeric_seed(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped.lstrip("-").isdigit():
                return int(stripped)
        return value

    @model_validator(mode="after")
    def validate_category(self) -> "GenerationConfig":
        if self.target_category not in TEN_CATEGORY_NAMES_ZH:
            raise ValueError(
                f"target_category must be one of {TEN_CATEGORY_NAMES_ZH}"
            )
        return self


class BatchAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count_matches_request: bool
    unique_case_ids: bool
    unique_industry_domains: bool
    unique_business_actions: bool
    unique_chain_or_round_structures: bool
    excluded_scenarios_not_reused: bool
    all_cases_pass_hard_gates: bool
    known_open_questions: list[str] = Field(default_factory=list)


class ScenarioGenerationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_version: Literal["ioa_scenario_generation_v4"]
    generation_status: GenerationStatus
    generation_config: GenerationConfig
    cases: list[BusinessCaseSpec]
    batch_audit: BatchAudit

    @model_validator(mode="after")
    def validate_batch_contract(self) -> "ScenarioGenerationBatch":
        expected = self.generation_config.scenario_count
        if self.generation_status == "FAILED_QUALITY_GATE":
            if self.cases:
                raise ValueError("failed batches must not contain cases")
            if self.batch_audit.all_cases_pass_hard_gates:
                raise ValueError("failed batches cannot pass all hard gates")
            if not self.batch_audit.known_open_questions:
                raise ValueError("failed batches must explain the failure")
            return self

        if len(self.cases) != expected:
            raise ValueError(f"completed batch requires exactly {expected} cases")
        if not self.batch_audit.all_cases_pass_hard_gates:
            raise ValueError("completed batch must pass all hard gates")
        if not self.batch_audit.case_count_matches_request:
            raise ValueError("completed batch must confirm the requested case count")
        required_audits = (
            "unique_case_ids",
            "unique_industry_domains",
            "unique_business_actions",
            "unique_chain_or_round_structures",
            "excluded_scenarios_not_reused",
        )
        failed_audits = [
            name for name in required_audits if not getattr(self.batch_audit, name)
        ]
        if failed_audits:
            raise ValueError(f"completed batch failed audits: {failed_audits}")

        category = load_evaluation_catalog().code_for_name_zh(
            self.generation_config.target_category
        )
        mismatched = [case.case_id for case in self.cases if case.category != category]
        if mismatched:
            raise ValueError(f"cases outside target category: {mismatched}")

        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case IDs must be unique within a batch")
        excluded = set(self.generation_config.excluded_case_ids)
        reused = sorted(excluded.intersection(case_ids))
        if reused:
            raise ValueError(f"case IDs reuse excluded IDs: {reused}")
        return self


class CompactScenarioGenerationBatch(BaseModel):
    """Batch contract whose cases use the compact authoring representation."""

    model_config = ConfigDict(extra="forbid")

    prompt_version: Literal[
        "ioa_scenario_generation_v5_compact",
        "ioa_scenario_generation_v6_compact_scored",
    ]
    generation_status: GenerationStatus
    generation_config: GenerationConfig
    cases: list[dict[str, object]]
    batch_audit: BatchAudit

    @model_validator(mode="after")
    def validate_compact_batch(self) -> "CompactScenarioGenerationBatch":
        from .compact import expand_compact_case

        expected = self.generation_config.scenario_count
        if self.generation_status == "FAILED_QUALITY_GATE":
            if self.cases:
                raise ValueError("failed compact batches must not contain cases")
            if self.batch_audit.all_cases_pass_hard_gates:
                raise ValueError("failed compact batches cannot pass all hard gates")
            if not self.batch_audit.known_open_questions:
                raise ValueError("failed compact batches must explain the failure")
            return self
        if len(self.cases) != expected:
            raise ValueError(f"completed compact batch requires exactly {expected} cases")
        if not self.batch_audit.all_cases_pass_hard_gates:
            raise ValueError("completed compact batch must pass all hard gates")
        if not self.batch_audit.case_count_matches_request:
            raise ValueError("completed compact batch must confirm the requested case count")
        category = load_evaluation_catalog().code_for_name_zh(
            self.generation_config.target_category
        )
        is_v6 = self.prompt_version == "ioa_scenario_generation_v6_compact_scored"
        expanded = [
            expand_compact_case(case, generic_scored=is_v6)
            for case in self.cases
        ]
        if is_v6:
            required_case_id = self.generation_config.required_case_id
            if not required_case_id:
                raise ValueError("v6 generation requires required_case_id")
            if any(case.case_id != required_case_id for case in expanded):
                raise ValueError("v6 case_id does not match required_case_id")
            if any(case.scoring_contract is None for case in expanded):
                raise ValueError("v6 cases require a generic scoring contract")
            from ..business_protocol.validation import validate_generated_case

            for case in expanded:
                validate_generated_case(case)
        if any(case.category != category for case in expanded):
            raise ValueError("compact cases outside target category")
        case_ids = [case.case_id for case in expanded]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("compact case IDs must be unique within a batch")
        excluded = set(self.generation_config.excluded_case_ids)
        reused = sorted(excluded.intersection(case_ids))
        if reused:
            raise ValueError(f"compact cases reuse excluded IDs: {reused}")
        return self
