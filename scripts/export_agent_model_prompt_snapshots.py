#!/usr/bin/env python
"""Generate all deterministic first-call prompt snapshots without an LLM."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.data_models import Artifact, TaskResult, TaskStatus
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.prompt_snapshot import (
    build_prompt_snapshot,
    export_prompt_snapshots,
)
from src.evaluation.agent_model.suite_runner import AgentModelSuiteRunner
from src.tools.gateway import ToolGateway


DATASET = PROJECT_ROOT / "data" / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"


class PromptCaptureEnvironment:
    """Capture submitted tasks and return protocol-valid inert outputs."""

    def __init__(self, cases: dict[str, Any]) -> None:
        self.cases = cases
        self.tool_gateway = ToolGateway()
        self.snapshots: list[dict[str, Any]] = []
        self._active_information: dict[str, dict[str, str]] = {}

    async def submit_task(self, task: Any) -> TaskResult:
        payload = task.payload
        case = self.cases[str(payload["case_id"])]
        self.snapshots.append(build_prompt_snapshot(
            task, case, tool_gateway=self.tool_gateway,
        ))

        run_id = str(payload["run_id"])
        public_state = payload.get("public_state", {})
        visible = public_state.get("active_information", {})
        required = public_state.get("required_claim_assessment", {})
        if visible:
            self._active_information[run_id] = {
                "claim_id": str(visible["claim_id"]),
                "statement": str(visible["statement"]),
            }
        elif required and run_id in self._active_information:
            self._active_information[run_id]["claim_id"] = str(required["claim_id"])

        claims: list[dict[str, Any]] = []
        active = self._active_information.get(run_id)
        if case.category_code in {"CAS", "RUM"} and active:
            claims.append({
                "claim_id": active["claim_id"],
                "statement": active["statement"],
                "disposition": "uncertain",
                # Exercise the maximal deterministic chain so every intended
                # role prompt is available for pre-run review.
                "forwarded": True,
                "used_for_action": False,
                "confidence": 0.5,
                "source_label": "",
                "verification_status": "unknown",
            })
        elif case.category_code == "CON":
            claims.append({
                "claim_id": "说法-01",
                "statement": "受控共识命题",
                "disposition": "uncertain",
                "forwarded": False,
                "used_for_action": False,
                "confidence": 0.5,
                "source_label": "",
                "verification_status": "unknown",
            })
        output = {
            "type": "final",
            "business_output": {
                "answer": "本地输入审计占位结果",
                "decision": "no_op",
                "confidence": 0.5,
                "limitations": ["该结果仅用于生成输入快照，不属于模型测评结果。"],
            },
            "behavior_record": {
                "claims": claims,
                "verification_requested": False,
            },
            "reason": "prompt_snapshot_only",
        }
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="prompt-snapshot-capture",
            content=output,
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=output,
            artifacts=[artifact],
            participating_agents=["prompt-snapshot-capture"],
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export model-visible inputs without calling a model",
    )
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "results" / "agent_model_prompt_review",
    )
    parser.add_argument("--case-id", action="append", default=[])
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    loader = CaseLoader(args.dataset)
    all_cases = loader.load_all()
    if loader.errors:
        raise RuntimeError("dataset validation failed: " + "; ".join(map(str, loader.errors)))
    selected_ids = args.case_id or sorted(all_cases)
    unknown = sorted(set(selected_ids) - set(all_cases))
    if unknown:
        raise ValueError(f"unknown case ids: {unknown}")
    cases = {case_id: all_cases[case_id] for case_id in selected_ids}
    environment = PromptCaptureEnvironment(all_cases)
    runner = AgentModelSuiteRunner(
        db_path=":memory:",
        environment=environment,
        fake_model=True,
        judge_callback=lambda *_args: {
            "status": "SAFE_BEHAVIOR",
            "reason": "prompt snapshot generation only",
        },
        suite_run_id="prompt-snapshot",
    )
    await runner.open()
    try:
        for case in cases.values():
            await runner.run_case(case, repeat_count=1)
    finally:
        await runner.close()

    observed = {
        (item["audit"]["case_id"], item["audit"]["variant"])
        for item in environment.snapshots
    }
    expected = {
        (case_id, variant)
        for case_id in cases
        for variant in ("baseline", "risk", "recovery")
    }
    missing = sorted(expected - observed)
    if missing:
        raise RuntimeError(f"prompt coverage incomplete: {missing}")
    return export_prompt_snapshots(environment.snapshots, args.output)


def main() -> int:
    result = asyncio.run(_run(_parser().parse_args()))
    print(
        f"exported {result['record_count']} snapshots for "
        f"{result['case_count']} cases to {result['files']['jsonl']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
