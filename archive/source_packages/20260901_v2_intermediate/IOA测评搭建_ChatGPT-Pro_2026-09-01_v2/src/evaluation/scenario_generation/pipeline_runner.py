"""Resumable filesystem pipeline for generated and legacy scenarios.

The runner is intentionally conservative: extraction can create a kernel and
an EffectSpec *draft* for every legacy candidate, but it never upgrades a
draft to a runnable/accepted case merely because it is syntactically valid.
Every stage writes an independent artifact and updates a versioned manifest.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ..candidate_review import CandidateRecord, audit_candidates, discover_candidates
from ..catalog import load_evaluation_catalog
from .pipeline import (
    PipelineConversionError,
    compile_kernel_effect,
    extract_effect_spec,
    extract_scenario_kernel,
    sha256_file,
)
from .pipeline_models import (
    PipelineError,
    PipelineManifest,
    PipelineManifestEntry,
    PipelineStatus,
    EffectSpec,
    ScenarioKernel,
    verify_effect_kernel_binding,
    verify_effect_spec_hash,
    verify_kernel_hash,
)
from .repair import (
    RepairApplicationResult,
    RepairPlan,
    apply_effect_repair,
    build_repair_plan,
    render_repair_prompt,
)
from .pipeline_api import PipelineAPI, StageCallConfig
from .path_validation import (
    SixPathValidationReport,
    oracle_from_effect,
    validate_six_paths,
)
from .quality_records import (
    HumanDecisionRecord,
    RuntimeCheckRecord,
    SemanticReviewRecord,
)


DEFAULT_PIPELINE_DIRNAME = "场景生产流水线-第01轮"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    def _jsonable(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return _jsonable(item.model_dump(mode="json"))
        if isinstance(item, dict):
            return {str(key): _jsonable(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [_jsonable(child) for child in item]
        return item

    value = _jsonable(value)
    return json.dumps(value, ensure_ascii=False, indent=2)


def _atomic_write(path: Path, value: Any, *, newline: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _json_dump(value)
    if newline:
        text += "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _candidate_key(candidate_uid: str) -> str:
    import hashlib

    return hashlib.sha256(candidate_uid.encode("utf-8")).hexdigest()[:24]


def _path_for(root: Path, stage: str, key: str, suffix: str = ".json") -> Path:
    return root / stage / f"{key}{suffix}"


def _category_slug(category: str) -> str:
    """Return a stable ASCII slug for a catalog code or Chinese name."""

    catalog = load_evaluation_catalog()
    for item in catalog.categories:
        if category in {item.code, item.name_zh}:
            return item.slug
    # This should only be reachable for a malformed manifest.  Keep the
    # fallback deterministic and filesystem-safe rather than using Chinese
    # text directly in a case identifier.
    import hashlib

    return "category-" + hashlib.sha256(category.encode("utf-8")).hexdigest()[:12]


def _append_error(entry: PipelineManifestEntry, error: PipelineError) -> None:
    """Append a stage error once, keeping earlier evidence intact."""

    if not any(
        existing.stage == error.stage
        and existing.code == error.code
        and existing.message == error.message
        for existing in entry.errors
    ):
        entry.errors.append(error)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineConversionError(f"无法读取 JSON 产物 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineConversionError(f"JSON 产物必须是对象：{path}")
    return value


_REWRITE_CODES = {
    "NO_CAUSAL_CONTRAST",
    "UNKNOWN_UNSAFE_TOOL",
    "DUPLICATE_TOOL_NAME",
    "UNBOUND_RECOVERY",
    "MISSING_RECOVERY_FLOW",
    "INVALID_UPSTREAM_REFERENCE",
    "FUTURE_INFORMATION",
    "INVALID_TRUST_VARIANT",
    "DUPLICATE_STEP_ID",
    "NO_KEY_TARGET",
    "NO_MAIN_STEP",
}
_REVISE_CODES = {
    "DUPLICATE_CASE_ID",
    "INVALID_CURRENT_TIME",
    "INVALID_OBJECT_TIME",
}
_ACTIONABLE_WARNING_CODES = {"MISSING_RECOVERY_FLOW", "UNBOUND_RECOVERY"}
_COMPLETED_COMPILE_STATUSES = {
    "SIX_PATH_VALID",
    "COMPILED",
    "RUNTIME_VALID",
    "SEMANTIC_ACCEPTED",
    "HUMAN_ACCEPTED",
    "FORMAL_ACCEPTED",
}


def _status_from_review(codes: set[str]) -> PipelineStatus:
    if codes & _REWRITE_CODES:
        return "REWRITE_REQUIRED"
    if codes & _REVISE_CODES:
        return "REVISE_REQUIRED"
    return "ADAPTED_PENDING_REVIEW"


def _review_payload(review: Any) -> dict[str, Any]:
    return review.model_dump(mode="json") if hasattr(review, "model_dump") else dict(review)


class ScenarioPipeline:
    """Run extraction/compilation stages while preserving every prior artifact."""

    STAGES = (
        "raw",
        "kernels",
        "kernel_reviews",
        "effect_specs",
        "repair_requests",
        "repair_responses",
        "repaired_effect_specs",
        "repair_results",
        "compiled",
        "validations",
        "runtime_checks",
        "semantic_reviews",
        "accepted",
        "quarantine",
    )

    def __init__(
        self,
        source: str | Path,
        output: str | Path,
        *,
        source_kind: Literal["legacy", "generated", "manual"] = "legacy",
    ):
        self.source = Path(source).expanduser().resolve()
        self.output = Path(output).expanduser().resolve()
        self.source_kind = source_kind
        self.manifest_path = self.output / "pipeline_manifest.json"
        for stage in self.STAGES:
            (self.output / stage).mkdir(parents=True, exist_ok=True)

    def _load_manifest(self) -> PipelineManifest | None:
        if not self.manifest_path.exists():
            return None
        try:
            manifest = PipelineManifest.model_validate_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
            # Upgrade an older manifest in memory.  Existing stage artifacts
            # and hashes remain untouched; the next atomic save records the
            # new repair-capable manifest version.
            if manifest.schema_version == "scenario_pipeline_manifest_v1":
                manifest = manifest.model_copy(
                    update={
                        "schema_version": "scenario_pipeline_manifest_v2",
                        "pipeline_version": "scenario_pipeline_v2",
                    }
                )
            return manifest
        except Exception as exc:
            raise ValueError(f"流水线 manifest 无法读取或版本不兼容：{exc}") from exc

    def _save_manifest(self, manifest: PipelineManifest) -> None:
        manifest.updated_at = _now()
        _atomic_write(self.manifest_path, manifest)

    def _manifest_for(self, entries: list[PipelineManifestEntry]) -> PipelineManifest:
        previous = self._load_manifest()
        if previous is not None and Path(previous.source_root).resolve() != self.source:
            raise ValueError(
                "现有 manifest 的 source_root 与本次输入不同；请换 output 或显式删除旧 manifest"
            )
        if previous is None:
            return PipelineManifest(
                source_root=str(self.source),
                output_root=str(self.output),
                entries=entries,
            )
        old = {entry.candidate_uid: entry for entry in previous.entries}
        current = {entry.candidate_uid: entry for entry in entries}
        # A sample run and a later full run may share one output directory.
        # Preserve already processed entries and add newly selected entries;
        # never make a sample invocation erase the rest of the manifest.
        merged: dict[str, PipelineManifestEntry] = dict(old)
        for candidate_uid, entry in current.items():
            prior = old.get(candidate_uid)
            if prior is not None and prior.source_sha256 == entry.source_sha256:
                if prior.source_kind != entry.source_kind:
                    raise ValueError(
                        f"candidate {candidate_uid} 的 source_kind 已从 "
                        f"{prior.source_kind} 改为 {entry.source_kind}；请使用新的 output 目录"
                    )
                merged[candidate_uid] = prior
            else:
                merged[candidate_uid] = entry
        return previous.model_copy(
            update={"entries": [merged[key] for key in sorted(merged)]}
        )

    def discover(self) -> list[CandidateRecord]:
        records = discover_candidates(self.source)
        if not records:
            raise ValueError(f"没有找到候选数据：{self.source}")
        return records

    @staticmethod
    def select(
        records: list[CandidateRecord],
        *,
        candidate_uids: set[str] | None = None,
        case_ids: set[str] | None = None,
        sample_per_item: int | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[CandidateRecord]:
        if offset < 0:
            raise ValueError("offset must not be negative")
        selected = records
        if candidate_uids:
            selected = [record for record in selected if record.candidate_uid in candidate_uids]
        if case_ids:
            selected = [record for record in selected if record.case.case_id in case_ids]
        if sample_per_item is not None:
            if sample_per_item < 1:
                raise ValueError("sample_per_item must be positive")
            grouped: dict[str, list[CandidateRecord]] = {}
            for record in selected:
                grouped.setdefault(record.item_name, []).append(record)
            selected = []
            import hashlib

            for item_name in sorted(grouped):
                group = sorted(
                    grouped[item_name],
                    key=lambda record: hashlib.sha256(
                        record.candidate_uid.encode("utf-8")
                    ).hexdigest(),
                )
                selected.extend(group[:sample_per_item])
        selected = sorted(selected, key=lambda record: record.candidate_uid)
        if offset:
            selected = selected[offset:]
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            selected = selected[:limit]
        return selected

    def initialize_manifest(self, records: list[CandidateRecord]) -> PipelineManifest:
        entries = [
            PipelineManifestEntry(
                candidate_uid=record.candidate_uid,
                source_case_id=record.case.case_id,
                source_path=str(record.source_path.resolve()),
                source_sha256=sha256_file(record.source_path),
                source_hash_verified=True,
                category=record.case.category,
                evaluation_item=record.item_name,
                subtype=record.case.metadata.get("sub_mechanism")
                if isinstance(record.case.metadata.get("sub_mechanism"), str)
                else None,
                source_kind=self.source_kind,
                generator_model_id=record.generator_model_id,
                batch_id=record.batch_id,
            )
            for record in records
        ]
        manifest = self._manifest_for(entries)
        self._save_manifest(manifest)
        return manifest

    @staticmethod
    def build_plan(
        records: list[CandidateRecord],
        *,
        selected: list[CandidateRecord] | None = None,
    ) -> dict[str, Any]:
        """Build a side-effect-free execution plan for preview/approval."""

        chosen = selected if selected is not None else records
        all_items = Counter(record.item_name for record in records)
        selected_items = Counter(record.item_name for record in chosen)
        models = Counter(record.generator_model_id for record in chosen)
        categories = Counter(record.case.category for record in chosen)
        return {
            "schema_version": "scenario_pipeline_plan_v1",
            "source_candidate_count": len(records),
            "selected_candidate_count": len(chosen),
            "evaluation_item_count": len(all_items),
            "source_item_counts": dict(sorted(all_items.items())),
            "selected_item_counts": dict(sorted(selected_items.items())),
            "selected_category_counts": dict(sorted(categories.items())),
            "selected_generator_model_counts": dict(sorted(models.items())),
            "stages": list(ScenarioPipeline.STAGES),
            "will_call_live_api": False,
            "raw_source_untouched": True,
            "repair_is_explicit": True,
        }

    def extract(
        self,
        records: list[CandidateRecord],
        *,
        audit_records: list[CandidateRecord] | None = None,
        force: bool = False,
    ) -> PipelineManifest:
        manifest = self.initialize_manifest(records)
        by_uid = {record.candidate_uid: record for record in records}
        # Duplicate IDs and near-duplicates must be checked against the whole
        # source collection even when only a deterministic sample is being
        # processed.  Otherwise a sample can look clean while colliding with
        # an unselected candidate.
        audit_scope = audit_records if audit_records is not None else records
        reviews, duplicates = audit_candidates(audit_scope)
        reviews_by_uid = {review.candidate_uid: review for review in reviews}
        duplicate_by_uid: dict[str, list[dict[str, Any]]] = {}
        for duplicate in duplicates:
            for uid in (
                duplicate.get("candidate_uids", [])
                if duplicate.get("candidate_uids")
                else [duplicate.get("candidate_uid_a"), duplicate.get("candidate_uid_b")]
            ):
                if uid:
                    duplicate_by_uid.setdefault(uid, []).append(duplicate)

        for entry in manifest.entries:
            record = by_uid.get(entry.candidate_uid)
            if record is None:
                # This entry belongs to a previous sample/full invocation and
                # is intentionally not part of the current selection.
                continue
            key = _candidate_key(entry.candidate_uid)
            kernel_path = _path_for(self.output, "kernels", key)
            effect_path = _path_for(self.output, "effect_specs", key)
            if (
                not force
                and entry.kernel_id
                and entry.effect_id
                and kernel_path.exists()
                and effect_path.exists()
                and entry.source_sha256 == sha256_file(record.source_path)
            ):
                continue
            entry.attempts["extract"] = entry.attempts.get("extract", 0) + 1
            try:
                current_source_hash = sha256_file(record.source_path)
                entry.source_hash_verified = current_source_hash == entry.source_sha256
                if not entry.source_hash_verified:
                    raise PipelineConversionError(
                        "原始候选文件哈希已变化，拒绝覆盖既有迁移证据；请建立新的流水线输出目录。"
                    )
                raw_path = _path_for(self.output, "raw", key)
                _atomic_write(
                    raw_path,
                    {
                        "schema_version": "scenario_pipeline_raw_v1",
                        "candidate_uid": record.candidate_uid,
                        "source_path": str(record.source_path.resolve()),
                        "source_sha256": entry.source_sha256,
                        "case": record.case.model_dump(mode="json"),
                    },
                )
                entry.stage_paths["raw"] = str(raw_path)
                kernel = extract_scenario_kernel(
                    record,
                    source_sha256=entry.source_sha256,
                )
                verify_kernel_hash(kernel)
                _atomic_write(kernel_path, kernel)
                entry.stage_paths["kernel"] = str(kernel_path)
                entry.kernel_id = kernel.kernel_id
                entry.kernel_sha256 = kernel.content_sha256

                review = reviews_by_uid.get(entry.candidate_uid)
                if review is None:
                    raise PipelineConversionError(
                        f"candidate {entry.candidate_uid} is absent from audit scope"
                    )
                review_payload = {
                    "schema_version": "scenario_kernel_review_v1",
                    "candidate_uid": entry.candidate_uid,
                    "kernel_id": kernel.kernel_id,
                    "kernel_sha256": kernel.content_sha256,
                    "deterministic_review": _review_payload(review),
                    "duplicate_evidence": duplicate_by_uid.get(entry.candidate_uid, []),
                    "reviewed_at": _now(),
                }
                review_path = _path_for(self.output, "kernel_reviews", key)
                _atomic_write(review_path, review_payload)
                entry.stage_paths["kernel_review"] = str(review_path)

                effect = extract_effect_spec(record, kernel)
                verify_effect_spec_hash(effect)
                _atomic_write(effect_path, effect)
                entry.stage_paths["effect_spec"] = str(effect_path)
                entry.effect_id = effect.effect_id
                entry.effect_sha256 = effect.content_sha256
                entry.effect_status = effect.status

                error_codes = {
                    finding.code
                    for finding in review.findings
                    if finding.severity == "ERROR"
                }
                actionable_codes = error_codes | {
                    finding.code
                    for finding in review.findings
                    if finding.severity == "WARNING"
                    and finding.code in _ACTIONABLE_WARNING_CODES
                }
                if actionable_codes:
                    entry.status = _status_from_review(actionable_codes)
                else:
                    entry.status = "ADAPTED_PENDING_REVIEW"
                if effect.notes:
                    for note in effect.notes:
                        _append_error(
                            entry,
                            PipelineError(
                                stage="effect_spec",
                                code="EFFECT_SPEC_DRAFT",
                                message=note,
                                retryable=False,
                            ),
                        )
                entry.terminal_reason = (
                    "；".join(sorted(actionable_codes))
                    if actionable_codes
                    else "已提取内核和效果草案，等待语义修复/审核"
                )
                entry.updated_at = _now()
            except Exception as exc:
                entry.status = "QUARANTINED"
                _append_error(
                    entry,
                    PipelineError(
                        stage="extract",
                        code=type(exc).__name__.upper(),
                        message=str(exc)[:4000],
                        retryable=False,
                    ),
                )
                entry.terminal_reason = str(exc)[:4000]
                quarantine_path = _path_for(self.output, "quarantine", key)
                _atomic_write(
                    quarantine_path,
                    {
                        "schema_version": "scenario_pipeline_quarantine_v1",
                        "candidate_uid": entry.candidate_uid,
                        "source_path": entry.source_path,
                        "status": entry.status,
                        "errors": [error.model_dump(mode="json") for error in entry.errors],
                    },
                )
                entry.stage_paths["quarantine"] = str(quarantine_path)
                entry.updated_at = _now()
            self._save_manifest(manifest)
        manifest.summary = self._summary(manifest, duplicates=duplicates)
        self._save_manifest(manifest)
        return manifest

    def prepare_repairs(
        self,
        *,
        candidate_uids: set[str] | None = None,
        response_dir: str | Path | None = None,
        allow_live_api: bool = False,
        model_id: str = "gpt-5.6-sol",
        reasoning_effort: str | None = None,
        workers: int = 1,
        max_calls: int | None = None,
        force: bool = False,
    ) -> PipelineManifest:
        """Create and optionally apply one semantic repair job per candidate.

        With the default offline settings this stage only creates durable
        repair prompts and a queue.  A response can later be placed in
        ``response_dir/<candidate-key>.json`` (or supplied by the live API
        worker).  Only a locally validated ``EffectSpecDraft`` is promoted to
        the compile input; the extracted draft remains as immutable evidence.
        """

        manifest = self._load_manifest()
        if manifest is None:
            raise ValueError("请先运行 extract 阶段建立 pipeline_manifest.json")
        if workers < 1:
            raise ValueError("workers must be positive")
        if max_calls is not None and max_calls < 1:
            raise ValueError("max_calls must be positive")
        if allow_live_api and not model_id.strip():
            raise ValueError("model_id must not be empty in live mode")

        external_responses = (
            Path(response_dir).expanduser().resolve() if response_dir else None
        )
        jobs: list[dict[str, Any]] = []
        queue_lines: list[str] = []

        for entry in manifest.entries:
            if candidate_uids is not None and entry.candidate_uid not in candidate_uids:
                continue
            key = _candidate_key(entry.candidate_uid)
            kernel_value = entry.stage_paths.get("kernel")
            effect_value = entry.stage_paths.get("effect_spec")
            if not kernel_value or not effect_value:
                _append_error(
                    entry,
                    PipelineError(
                        stage="repair",
                        code="MISSING_INTERMEDIATE",
                        message="缺少 ScenarioKernel 或 EffectSpec 草案，无法建立修复任务。",
                    ),
                )
                entry.repair_status = "FAILED"
                entry.status = "QUARANTINED"
                entry.terminal_reason = "缺少修复阶段输入"
                continue

            repaired_value = entry.stage_paths.get("repaired_effect_spec")
            if (
                not force
                and entry.repair_status == "READY_FOR_COMPILE"
                and repaired_value
                and Path(repaired_value).is_file()
            ):
                continue
            kernel_path = Path(kernel_value)
            effect_path = Path(effect_value)
            try:
                kernel = ScenarioKernel.model_validate_json(
                    kernel_path.read_text(encoding="utf-8")
                )
                effect = EffectSpec.model_validate_json(
                    effect_path.read_text(encoding="utf-8")
                )
                review_payload = (
                    _read_json(Path(entry.stage_paths["kernel_review"]))
                    if entry.stage_paths.get("kernel_review")
                    else {}
                )
                duplicate_evidence = review_payload.get("duplicate_evidence", [])
                if not isinstance(duplicate_evidence, list):
                    duplicate_evidence = []
                repair_plan = build_repair_plan(
                    entry,
                    kernel,
                    effect,
                    review_payload,
                    duplicate_evidence,
                )
                plan_path = _path_for(self.output, "repair_requests", key)
                prompt_path = _path_for(
                    self.output, "repair_requests", key, ".prompt.txt"
                )
                input_path = _path_for(
                    self.output, "repair_requests", key, ".input.json"
                )
                _atomic_write(plan_path, repair_plan)
                _write_text_atomic(
                    prompt_path,
                    render_repair_prompt(kernel, effect, repair_plan),
                )
                _atomic_write(
                    input_path,
                    {
                        "schema_version": "scenario_repair_input_v1",
                        "plan": repair_plan,
                        "kernel": kernel,
                        "effect_spec_draft": effect,
                    },
                )
                entry.stage_paths["repair_plan"] = str(plan_path)
                entry.stage_paths["repair_prompt"] = str(prompt_path)
                entry.stage_paths["repair_input"] = str(input_path)
                entry.repair_status = "PENDING"
                entry.status = (
                    repair_plan.decision
                    if repair_plan.decision in {"REVISE_REQUIRED", "REWRITE_REQUIRED"}
                    else "REPAIR_PENDING"
                )
                entry.terminal_reason = "已建立语义修复任务，等待模型修复或人工确认"
                entry.updated_at = _now()
                job = {
                    "key": key,
                    "entry": entry,
                    "kernel": kernel,
                    "effect": effect,
                    "prompt": render_repair_prompt(kernel, effect, repair_plan),
                    "plan": repair_plan,
                }
                jobs.append(job)
                queue_lines.append(
                    json.dumps(
                        {
                            "candidate_uid": entry.candidate_uid,
                            "key": key,
                            "plan_path": str(plan_path),
                            "prompt_path": str(prompt_path),
                            "input_path": str(input_path),
                            "response_hint": str(
                                (external_responses or (self.output / "repair_responses"))
                                / f"{key}.json"
                            ),
                            "decision": repair_plan.decision,
                        },
                        ensure_ascii=False,
                    )
                )
            except Exception as exc:
                entry.repair_status = "FAILED"
                entry.status = "QUARANTINED"
                entry.terminal_reason = str(exc)[:4000]
                _append_error(
                    entry,
                    PipelineError(
                        stage="repair",
                        code=type(exc).__name__.upper(),
                        message=str(exc)[:4000],
                    ),
                )
            self._save_manifest(manifest)

        queue_path = self.output / "repair_queue.jsonl"
        _write_text_atomic(queue_path, "\n".join(queue_lines) + ("\n" if queue_lines else ""))

        def _response_candidates(job: dict[str, Any]) -> list[Path]:
            key = str(job["key"])
            candidates: list[Path] = []
            if external_responses is not None:
                candidates.extend(
                    [
                        external_responses / f"{key}.json",
                        external_responses / f"{key}.response.json",
                        external_responses / key / "effect_spec.json",
                    ]
                )
            candidates.append(self.output / "repair_responses" / key / "effect_spec.json")
            return candidates

        def _run_live(job: dict[str, Any]) -> tuple[dict[str, Any], EffectSpec | None, str | None]:
            from .pipeline_api import PipelineAPI, StageCallConfig

            response_dir_for_job = self.output / "repair_responses" / str(job["key"])
            config = StageCallConfig(
                model_id=model_id,
                reasoning_effort=reasoning_effort,
                temperature=0.1,
                max_completion_tokens=16384,
                timeout=600,
                retry_count=2,
            )
            try:
                effect_value = PipelineAPI().generate_effect(
                    kernel=job["kernel"],
                    prompt=job["prompt"],
                    config=config,
                    output_dir=response_dir_for_job,
                    allow_live_api=True,
                )
                return job, effect_value, None
            except Exception as exc:  # evidence is written by PipelineAPI
                return job, None, f"{type(exc).__name__}: {exc}"

        results: list[dict[str, Any]] = []
        live_jobs = jobs if allow_live_api else []
        if max_calls is not None:
            live_jobs = live_jobs[:max_calls]
        live_results: dict[str, tuple[EffectSpec | None, str | None]] = {}
        if live_jobs:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_run_live, job) for job in live_jobs]
                for future in as_completed(futures):
                    job, effect_value, error = future.result()
                    live_results[str(job["key"])] = (effect_value, error)

        for job in jobs:
            entry = job["entry"]
            key = str(job["key"])
            response_path: Path | None = next(
                (path for path in _response_candidates(job) if path.is_file()), None
            )
            effect_value: EffectSpec | None = live_results.get(key, (None, None))[0]
            live_error = live_results.get(key, (None, None))[1]
            application: RepairApplicationResult
            if effect_value is None and response_path is None and live_error is None:
                application = RepairApplicationResult(
                    candidate_uid=entry.candidate_uid,
                    repair_status="PENDING",
                )
                entry.repair_status = "PENDING"
            else:
                entry.repair_attempts += 1
                try:
                    if effect_value is None:
                        assert response_path is not None
                        effect_value = apply_effect_repair(
                            _read_json(response_path),
                            kernel=job["kernel"],
                        )
                    repaired_path = _path_for(
                        self.output, "repaired_effect_specs", key
                    )
                    _atomic_write(repaired_path, effect_value)
                    entry.stage_paths["effect_spec_draft"] = entry.stage_paths.get(
                        "effect_spec", ""
                    )
                    entry.stage_paths["effect_spec"] = str(repaired_path)
                    entry.stage_paths["repaired_effect_spec"] = str(repaired_path)
                    entry.effect_id = effect_value.effect_id
                    entry.effect_sha256 = effect_value.content_sha256
                    entry.effect_status = effect_value.status
                    entry.repair_status = "READY_FOR_COMPILE"
                    entry.status = "REPAIR_VALID"
                    entry.terminal_reason = "修复响应通过本地契约校验，等待编译和六路径验证"
                    application = RepairApplicationResult(
                        candidate_uid=entry.candidate_uid,
                        repair_status="READY_FOR_COMPILE",
                        response_path=str(response_path) if response_path else None,
                        repaired_effect_path=str(repaired_path),
                        effect_id=effect_value.effect_id,
                        effect_sha256=effect_value.content_sha256,
                        applied_operations=[
                            operation.operation_id
                            for operation in job["plan"].automatic_operations
                        ],
                    )
                except Exception as exc:
                    message = live_error or str(exc)
                    entry.repair_status = "FAILED"
                    entry.status = (
                        "REWRITE_REQUIRED"
                        if job["plan"].decision == "REWRITE_REQUIRED"
                        else "REVISE_REQUIRED"
                    )
                    entry.terminal_reason = message[:4000]
                    _append_error(
                        entry,
                        PipelineError(
                            stage="repair",
                            code="REPAIR_RESPONSE_INVALID",
                            message=message[:4000],
                            retryable=True,
                        ),
                    )
                    application = RepairApplicationResult(
                        candidate_uid=entry.candidate_uid,
                        repair_status="FAILED",
                        response_path=str(response_path) if response_path else None,
                        error_type=type(exc).__name__,
                        error=message[:4000],
                    )

            result_path = _path_for(self.output, "repair_results", key)
            _atomic_write(result_path, application)
            entry.stage_paths["repair_result"] = str(result_path)
            entry.updated_at = _now()
            results.append(application.model_dump(mode="json"))
            self._save_manifest(manifest)

        manifest.summary = self._summary(manifest)
        manifest.summary.update(
            {
                "repair_job_count": len(jobs),
                "repair_result_count": len(results),
                "repair_live_api": bool(allow_live_api),
                "repair_live_api_calls": len(live_jobs),
                "repair_queue": str(queue_path),
            }
        )
        self._save_manifest(manifest)
        _atomic_write(
            self.output / "repair_summary.json",
            {
                "schema_version": "scenario_repair_summary_v1",
                "candidate_count": len(manifest.entries),
                "job_count": len(jobs),
                "result_count": len(results),
                "result_status_counts": dict(
                    sorted(Counter(item["repair_status"] for item in results).items())
                ),
                "live_api": bool(allow_live_api),
                "live_api_calls": len(live_jobs),
                "queue_path": str(queue_path),
                "updated_at": _now(),
            },
        )
        return manifest

    def compile_ready(self, *, force: bool = False) -> PipelineManifest:
        manifest = self._load_manifest()
        if manifest is None:
            raise ValueError("请先运行 extract 阶段建立 pipeline_manifest.json")
        for entry in manifest.entries:
            key = _candidate_key(entry.candidate_uid)
            kernel_value = entry.stage_paths.get("kernel")
            effect_value = entry.stage_paths.get("effect_spec")
            if not kernel_value or not effect_value:
                continue
            kernel_path = Path(kernel_value)
            effect_path = Path(effect_value)
            if not kernel_path.is_file() or not effect_path.is_file():
                _append_error(
                    entry,
                    PipelineError(
                        stage="compile",
                        code="MISSING_INTERMEDIATE",
                        message="缺少 ScenarioKernel 或 EffectSpec 文件，无法编译。",
                    ),
                )
                entry.status = "QUARANTINED"
                entry.terminal_reason = "缺少中间产物"
                self._save_manifest(manifest)
                continue
            try:
                kernel = ScenarioKernel.model_validate_json(kernel_path.read_text(encoding="utf-8"))
                effect = EffectSpec.model_validate_json(effect_path.read_text(encoding="utf-8"))
                kernel_hash = verify_kernel_hash(kernel)
                effect_hash = verify_effect_spec_hash(effect)
                if entry.kernel_id and kernel.kernel_id != entry.kernel_id:
                    raise PipelineConversionError(
                        "manifest 中的 kernel_id 与实际 ScenarioKernel 不一致"
                    )
                if entry.kernel_sha256 and kernel_hash != entry.kernel_sha256:
                    raise PipelineConversionError(
                        "manifest 中的 kernel_sha256 与实际 ScenarioKernel 不一致"
                    )
                if entry.effect_id and effect.effect_id != entry.effect_id:
                    raise PipelineConversionError(
                        "manifest 中的 effect_id 与实际 EffectSpec 不一致"
                    )
                if entry.effect_sha256 and effect_hash != entry.effect_sha256:
                    raise PipelineConversionError(
                        "manifest 中的 effect_sha256 与实际 EffectSpec 不一致"
                    )
                verify_effect_kernel_binding(kernel, effect)
                # Drafts are expected for legacy candidates.  Leave their
                # status and evidence untouched; compile is a no-op until a
                # semantic repair explicitly marks the EffectSpec ready.
                if effect.status != "READY_FOR_COMPILE":
                    if entry.status in _COMPLETED_COMPILE_STATUSES:
                        entry.status = "REVISE_REQUIRED"
                        entry.repair_status = "HUMAN_REVIEW_REQUIRED"
                        _append_error(
                            entry,
                            PipelineError(
                                stage="compile",
                                code="EFFECT_NOT_READY",
                                message=(
                                    "已完成条目的 EffectSpec 被改回 DRAFT，"
                                    "原有编译/验证状态失效，需重新审核。"
                                ),
                                retryable=True,
                            ),
                        )
                    entry.terminal_reason = (
                        "EffectSpec 仍为 DRAFT，需语义修复后再编译"
                    )
                    entry.updated_at = _now()
                    self._save_manifest(manifest)
                    continue
                if not force and entry.status in _COMPLETED_COMPILE_STATUSES:
                    validation_value = entry.stage_paths.get("validation")
                    validation_ok = False
                    if validation_value and Path(validation_value).is_file():
                        try:
                            validation_payload = _read_json(Path(validation_value))
                            validation_ok = validation_payload.get("status") == "PASS"
                        except PipelineConversionError:
                            validation_ok = False
                    if validation_ok:
                        continue
                entry.attempts["compile"] = entry.attempts.get("compile", 0) + 1
                compiled = compile_kernel_effect(
                    kernel,
                    effect,
                    case_id=f"{_category_slug(entry.category)}-pipeline-{key[:12]}",
                    category=kernel.category,
                    provenance={
                        "pipeline_version": "scenario_pipeline_v2",
                        "source_candidate_uid": entry.candidate_uid,
                        "source_case_id": entry.source_case_id,
                    },
                )
                compiled_path = _path_for(self.output, "compiled", key, ".jsonl")
                compiled_text = (
                    json.dumps(
                        {
                            "schema_version": "business_case_spec_v1",
                            "case": compiled.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                _write_text_atomic(compiled_path, compiled_text)
                validation_path = _path_for(self.output, "validations", key)
                entry.stage_paths["compiled"] = str(compiled_path)
                entry.stage_paths["validation"] = str(validation_path)
                # Compilation and path validation are separate gates.  A
                # syntactically compiled case is recorded as COMPILED first;
                # only the independent deterministic six-path report can
                # promote it to SIX_PATH_VALID.
                entry.status = "COMPILED"
                entry.terminal_reason = "编译成功，正在执行独立六路径演算"
                self._save_manifest(manifest)
                oracle = oracle_from_effect(effect)
                validation = validate_six_paths(compiled, oracle)
                _atomic_write(validation_path, validation)
                entry.errors = [
                    error
                    for error in entry.errors
                    if error.stage != "compile"
                    and not (
                        error.stage == "validation"
                        and error.code == "SIX_PATH_VALIDATION_FAILED"
                    )
                ]
                if validation.status == "PASS":
                    entry.status = "SIX_PATH_VALID"
                    entry.repair_status = "READY_FOR_COMPILE"
                    entry.terminal_reason = "编译成功，六条标准路径及关键节点/完整链路矩阵均通过"
                else:
                    entry.status = "REVISE_REQUIRED"
                    entry.repair_status = "HUMAN_REVIEW_REQUIRED"
                    entry.terminal_reason = "六路径演算未通过，需根据验证报告定向修复"
                    _append_error(
                        entry,
                        PipelineError(
                            stage="validation",
                            code="SIX_PATH_VALIDATION_FAILED",
                            message="；".join(validation.errors[:20])
                            or "六路径报告存在失败项",
                            retryable=True,
                        ),
                    )
            except Exception as exc:
                entry.status = "REVISE_REQUIRED"
                _append_error(
                    entry,
                    PipelineError(
                        stage="compile",
                        code=type(exc).__name__.upper(),
                        message=str(exc)[:4000],
                        retryable=False,
                    ),
                )
                entry.terminal_reason = str(exc)[:4000]
                _atomic_write(
                    _path_for(self.output, "validations", key),
                    {
                        "status": "FAILED",
                        "candidate_uid": entry.candidate_uid,
                        "error": str(exc)[:4000],
                    },
                )
            entry.updated_at = _now()
            self._save_manifest(manifest)
        manifest.summary = self._summary(manifest)
        self._save_manifest(manifest)
        return manifest

    def _quality_record_path(self, stage: str, key: str) -> Path:
        """Return a non-overwriting path for a runtime/review evidence file."""

        directory = self.output / stage
        directory.mkdir(parents=True, exist_ok=True)
        base = directory / f"{key}.json"
        if not base.exists():
            return base
        attempt = 2
        while True:
            candidate = directory / f"{key}.attempt-{attempt:03d}.json"
            if not candidate.exists():
                return candidate
            attempt += 1

    def _entry_for_uid(self, manifest: PipelineManifest, candidate_uid: str) -> PipelineManifestEntry:
        entry = next(
            (item for item in manifest.entries if item.candidate_uid == candidate_uid),
            None,
        )
        if entry is None:
            raise ValueError(f"unknown candidate_uid: {candidate_uid}")
        return entry

    def record_runtime_check(
        self,
        record: RuntimeCheckRecord | dict[str, Any],
    ) -> PipelineManifest:
        """Persist an offline runtime result and advance the manifest gate."""

        parsed = record if isinstance(record, RuntimeCheckRecord) else RuntimeCheckRecord.model_validate(record)
        manifest = self._load_manifest()
        if manifest is None:
            raise ValueError("请先运行 extract 阶段建立 pipeline_manifest.json")
        entry = self._entry_for_uid(manifest, parsed.candidate_uid)
        path = self._quality_record_path("runtime_checks", _candidate_key(parsed.candidate_uid))
        _atomic_write(path, parsed)
        entry.stage_paths["runtime_check"] = str(path)
        if parsed.status == "PASS":
            if entry.status in {
                "SIX_PATH_VALID",
                "RUNTIME_VALID",
                "SEMANTIC_ACCEPTED",
                "HUMAN_ACCEPTED",
                "FORMAL_ACCEPTED",
            }:
                if entry.status == "SIX_PATH_VALID":
                    entry.status = "RUNTIME_VALID"
                entry.terminal_reason = "离线运行检查通过"
                entry.errors = [
                    error
                    for error in entry.errors
                    if not (
                        error.stage == "runtime"
                        and error.code == "RUNTIME_CHECK_FAILED"
                    )
                ]
            else:
                _append_error(
                    entry,
                    PipelineError(
                        stage="runtime",
                        code="RUNTIME_GATE_ORDER",
                        message="运行检查通过，但候选尚未通过六路径验证，不能推进状态。",
                    ),
                )
        elif parsed.status == "FAIL":
            entry.status = "REVISE_REQUIRED"
            entry.repair_status = "HUMAN_REVIEW_REQUIRED"
            entry.terminal_reason = "离线运行检查失败，需根据运行证据修复"
            _append_error(
                entry,
                PipelineError(
                    stage="runtime",
                    code="RUNTIME_CHECK_FAILED",
                    message="；".join(parsed.errors[:20]) or parsed.summary,
                    retryable=True,
                ),
            )
        entry.updated_at = _now()
        manifest.summary = self._summary(manifest)
        self._save_manifest(manifest)
        return manifest

    def record_semantic_review(
        self,
        record: SemanticReviewRecord | dict[str, Any],
    ) -> PipelineManifest:
        """Persist one independent semantic review without calling a model."""

        parsed = record if isinstance(record, SemanticReviewRecord) else SemanticReviewRecord.model_validate(record)
        manifest = self._load_manifest()
        if manifest is None:
            raise ValueError("请先运行 extract 阶段建立 pipeline_manifest.json")
        entry = self._entry_for_uid(manifest, parsed.candidate_uid)
        path = self._quality_record_path("semantic_reviews", _candidate_key(parsed.candidate_uid))
        _atomic_write(path, parsed)
        entry.stage_paths["semantic_review"] = str(path)
        if parsed.decision == "ACCEPT":
            if entry.status in {
                "RUNTIME_VALID",
                "SEMANTIC_ACCEPTED",
                "HUMAN_ACCEPTED",
                "FORMAL_ACCEPTED",
            }:
                if entry.status == "RUNTIME_VALID":
                    entry.status = "SEMANTIC_ACCEPTED"
                entry.terminal_reason = "语义审核通过，等待人工终审"
            else:
                _append_error(
                    entry,
                    PipelineError(
                        stage="semantic_review",
                        code="SEMANTIC_GATE_ORDER",
                        message="语义审核通过，但候选尚未通过运行检查，不能推进状态。",
                    ),
                )
        elif parsed.decision == "REVISE":
            entry.status = "REVISE_REQUIRED"
            entry.repair_status = "HUMAN_REVIEW_REQUIRED"
            entry.terminal_reason = "语义审核提出修改要求"
            _append_error(
                entry,
                PipelineError(
                    stage="semantic_review",
                    code="SEMANTIC_REVIEW_REVISE",
                    message="；".join(parsed.key_issues or parsed.recommendations),
                    retryable=True,
                ),
            )
        else:
            entry.status = "QUARANTINED"
            entry.terminal_reason = "语义审核拒绝候选"
            _append_error(
                entry,
                PipelineError(
                    stage="semantic_review",
                    code="SEMANTIC_REVIEW_REJECT",
                    message="；".join(parsed.key_issues or parsed.recommendations),
                    retryable=False,
                ),
            )
        entry.updated_at = _now()
        manifest.summary = self._summary(manifest)
        self._save_manifest(manifest)
        return manifest

    def record_human_decision(
        self,
        record: HumanDecisionRecord | dict[str, Any],
    ) -> PipelineManifest:
        """Persist the final human decision and optionally mark a release."""

        parsed = record if isinstance(record, HumanDecisionRecord) else HumanDecisionRecord.model_validate(record)
        manifest = self._load_manifest()
        if manifest is None:
            raise ValueError("请先运行 extract 阶段建立 pipeline_manifest.json")
        entry = self._entry_for_uid(manifest, parsed.candidate_uid)
        path = self._quality_record_path("accepted", _candidate_key(parsed.candidate_uid))
        _atomic_write(path, parsed)
        entry.stage_paths["human_decision"] = str(path)
        if parsed.decision == "ACCEPT":
            if entry.status in {
                "SEMANTIC_ACCEPTED",
                "HUMAN_ACCEPTED",
                "FORMAL_ACCEPTED",
            }:
                entry.status = "FORMAL_ACCEPTED" if parsed.release_membership else "HUMAN_ACCEPTED"
                entry.terminal_reason = "人工终审通过"
                entry.repair_status = "READY_FOR_COMPILE"
            else:
                _append_error(
                    entry,
                    PipelineError(
                        stage="human_review",
                        code="HUMAN_GATE_ORDER",
                        message="人工终审通过，但候选尚未完成语义审核，不能入库。",
                    ),
                )
        elif parsed.decision == "REVISE":
            entry.status = "REVISE_REQUIRED"
            entry.repair_status = "HUMAN_REVIEW_REQUIRED"
            entry.terminal_reason = "人工终审要求修改"
            _append_error(
                entry,
                PipelineError(
                    stage="human_review",
                    code="HUMAN_REVIEW_REVISE",
                    message=parsed.reason,
                    retryable=True,
                ),
            )
        else:
            entry.status = "QUARANTINED"
            entry.terminal_reason = "人工终审拒绝候选"
            _append_error(
                entry,
                PipelineError(
                    stage="human_review",
                    code="HUMAN_REVIEW_REJECT",
                    message=parsed.reason,
                    retryable=False,
                ),
            )
        if parsed.release_membership:
            entry.updated_at = _now()
            # Release membership is represented in the legacy manifest's
            # metadata so older readers remain compatible.
            entry.attempts["release_membership_count"] = len(parsed.release_membership)
        entry.updated_at = _now()
        manifest.summary = self._summary(manifest)
        self._save_manifest(manifest)
        return manifest

    def _summary(
        self,
        manifest: PipelineManifest,
        *,
        duplicates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        statuses = Counter(entry.status for entry in manifest.entries)
        categories = Counter(entry.category for entry in manifest.entries)
        items = Counter(entry.evaluation_item or entry.category for entry in manifest.entries)
        generators = Counter(entry.generator_model_id for entry in manifest.entries)
        repair_statuses = Counter(
            entry.repair_status or "NOT_STARTED" for entry in manifest.entries
        )
        error_codes = Counter(
            error.code
            for entry in manifest.entries
            for error in entry.errors
        )
        return {
            "candidate_count": len(manifest.entries),
            "status_counts": dict(sorted(statuses.items())),
            "category_counts": dict(sorted(categories.items())),
            "evaluation_item_counts": dict(sorted(items.items())),
            "generator_model_counts": dict(sorted(generators.items())),
            "kernel_count": sum(bool(entry.kernel_id) for entry in manifest.entries),
            "effect_spec_count": sum(bool(entry.effect_id) for entry in manifest.entries),
            "effect_spec_ready_count": sum(
                entry.effect_status == "READY_FOR_COMPILE"
                for entry in manifest.entries
            ),
            "repair_status_counts": dict(sorted(repair_statuses.items())),
            "repair_pending_count": sum(
                entry.repair_status in {"PENDING", "MODEL_REPAIR_REQUIRED"}
                for entry in manifest.entries
            ),
            "source_hash_match_count": sum(
                entry.source_hash_verified
                for entry in manifest.entries
            ),
            "compiled_count": sum(
                entry.status in {"COMPILED", "SIX_PATH_VALID", "RUNTIME_VALID", "SEMANTIC_ACCEPTED", "HUMAN_ACCEPTED", "FORMAL_ACCEPTED"}
                for entry in manifest.entries
            ),
            "compiled_artifact_count": sum(
                bool(entry.stage_paths.get("compiled"))
                and Path(entry.stage_paths["compiled"]).is_file()
                for entry in manifest.entries
            ),
            "six_path_validation_pass_count": sum(
                entry.status in {"SIX_PATH_VALID", "RUNTIME_VALID", "SEMANTIC_ACCEPTED", "HUMAN_ACCEPTED", "FORMAL_ACCEPTED"}
                for entry in manifest.entries
            ),
            "six_path_validation_fail_count": sum(
                any(
                    error.stage == "validation"
                    and error.code == "SIX_PATH_VALIDATION_FAILED"
                    for error in entry.errors
                )
                for entry in manifest.entries
            ),
            "runtime_check_count": sum(
                bool(entry.stage_paths.get("runtime_check"))
                for entry in manifest.entries
            ),
            "semantic_review_count": sum(
                bool(entry.stage_paths.get("semantic_review"))
                for entry in manifest.entries
            ),
            "human_decision_count": sum(
                bool(entry.stage_paths.get("human_decision"))
                for entry in manifest.entries
            ),
            "duplicate_group_count": len(duplicates)
            if duplicates is not None
            else int(manifest.summary.get("duplicate_group_count", 0)),
            "error_code_counts": dict(sorted(error_codes.items())),
            "unprocessed_count": sum(entry.status == "RAW" for entry in manifest.entries),
            # Preserve repair-stage counters when a later compile pass
            # refreshes the common summary.
            "repair_job_count": int(manifest.summary.get("repair_job_count", 0)),
            "repair_result_count": int(manifest.summary.get("repair_result_count", 0)),
            "repair_live_api": bool(manifest.summary.get("repair_live_api", False)),
            "repair_live_api_calls": int(
                manifest.summary.get("repair_live_api_calls", 0)
            ),
            "repair_queue": manifest.summary.get("repair_queue"),
            "updated_at": _now(),
        }


__all__ = ["DEFAULT_PIPELINE_DIRNAME", "ScenarioPipeline"]
