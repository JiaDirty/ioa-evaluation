#!/usr/bin/env python
"""Recompile saved authoring or blueprint responses locally."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_candidate_batch import required_case_id_for  # noqa: E402
from src.evaluation.business_protocol.loader import load_business_cases_from_paths  # noqa: E402
from src.evaluation.scenario_generation import (  # noqa: E402
    AuthoringScenarioResponse,
    BlueprintScenarioResponse,
    compile_authoring_response,
    compile_blueprint_response,
)


def _request_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)} = (.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _load_context(batch_path: Path) -> dict[str, object]:
    context_path = batch_path.parent / "generation_context.json"
    if context_path.exists():
        return json.loads(context_path.read_text(encoding="utf-8"))
    request_path = batch_path.parent / "request_raw.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    user_message = next(
        item["content"]
        for item in reversed(payload.get("messages", []))
        if item.get("role") == "user"
    )
    category = _request_value(user_message, "TARGET_CATEGORY")
    batch_id = _request_value(user_message, "BATCH_ID")
    model_id = _request_value(user_message, "GENERATOR_MODEL_ID") or payload.get("model")
    seed_text = _request_value(user_message, "GENERATION_SEED")
    seed = None if seed_text in {None, "null"} else int(str(seed_text))
    variant = None
    if batch_id and "__drift__" in batch_id:
        variant = "drift"
    elif batch_id and "__vague__" in batch_id:
        variant = "vague"
    if not category or not batch_id or not model_id:
        raise ValueError("saved request is missing category, batch or model identity")
    case_id = required_case_id_for(
        target_category=category,
        batch_id=batch_id,
        model_id=str(model_id),
        seed=seed,
        variant=variant,
    )
    return {
        "target_category": category,
        "batch_id": batch_id,
        "generator_model_id": model_id,
        "generation_seed": seed,
        "required_case_id": case_id,
        "variant": variant,
    }


def recompile(source: Path, *, overwrite: bool = False) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for batch_path in sorted(source.rglob("candidate_batch.json")):
        if "repair_attempts" in batch_path.relative_to(source).parts:
            continue
        expanded_path = batch_path.parent / "expanded_cases.jsonl"
        if expanded_path.exists() and not overwrite:
            results.append({"status": "SKIPPED_EXISTING", "path": str(batch_path)})
            continue
        try:
            raw = json.loads(batch_path.read_text(encoding="utf-8"))
            version = raw.get("prompt_version")
            if version not in {
                "ioa_scenario_generation_v7_authoring",
                "ioa_scenario_generation_v8_blueprint",
                "ioa_scenario_generation_v9_blueprint_sequences",
            }:
                results.append({"status": "SKIPPED_OTHER_VERSION", "path": str(batch_path)})
                continue
            response = (
                AuthoringScenarioResponse.model_validate(raw)
                if version == "ioa_scenario_generation_v7_authoring"
                else BlueprintScenarioResponse.model_validate(raw)
            )
            if response.generation_status != "COMPLETED":
                results.append({"status": "SKIPPED_QUALITY_GATE", "path": str(batch_path)})
                continue
            context = _load_context(batch_path)
            provenance = {
                "generator_id": "aihubmix",
                "generator_model_id": context["generator_model_id"],
                "generation_seed": context["generation_seed"],
                "batch_id": context["batch_id"],
                "prompt_version": response.prompt_version,
            }
            compiler = (
                compile_authoring_response
                if version == "ioa_scenario_generation_v7_authoring"
                else compile_blueprint_response
            )
            case = compiler(
                response,
                case_id=str(context["required_case_id"]),
                category=str(context["target_category"]),
                provenance=provenance,
            )
            expanded_path.write_text(
                json.dumps(
                    {
                        "schema_version": "business_case_spec_v1",
                        "case": case.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            load_business_cases_from_paths([expanded_path])
            result = {
                "status": "RECOMPILED",
                "path": str(batch_path),
                "case_id": case.case_id,
            }
        except Exception as exc:
            result = {
                "status": "REJECTED",
                "path": str(batch_path),
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            }
        (batch_path.parent / "recompile_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append(result)
    counts = Counter(str(item["status"]) for item in results)
    summary = {
        "source": str(source),
        "candidate_batch_count": len(results),
        "status_counts": dict(sorted(counts.items())),
        "results": results,
    }
    (source / "recompile_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        summary = recompile(args.source, overwrite=args.overwrite)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "candidate_batch_count": summary["candidate_batch_count"],
                "status_counts": summary["status_counts"],
                "summary": str(args.source / "recompile_summary.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
