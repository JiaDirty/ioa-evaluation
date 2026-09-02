#!/usr/bin/env python
"""Run the versioned ScenarioKernel/EffectSpec migration pipeline.

The command is deliberately offline: it reads existing candidates and writes
intermediate artifacts, but never calls a paid model API.  A later API stage
can consume the saved kernel/effect files explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.scenario_generation.pipeline_runner import (  # noqa: E402
    DEFAULT_PIPELINE_DIRNAME,
    ScenarioPipeline,
)


DEFAULT_SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"
DEFAULT_OUTPUT = ROOT / "data" / "scenario_pipeline" / DEFAULT_PIPELINE_DIRNAME


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source-kind",
        choices=("legacy", "generated", "manual"),
        default="legacy",
        help="输入来源类型；默认是历史候选 legacy",
    )
    parser.add_argument(
        "--stage",
        choices=("plan", "extract", "repair", "compile", "all"),
        default="all",
        help="plan 只预览；extract 提取；repair 建立/应用语义修复任务；compile 编译已修复项；all 依次执行",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="只处理指定 case_id；可重复传入",
    )
    parser.add_argument(
        "--sample-per-item",
        type=int,
        default=None,
        help="每个 11 个测评项稳定抽取几条（例如 2 会选 22 条）",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="在稳定排序后的候选列表中跳过多少条，用于分块续跑",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已有中间产物并重新处理所选候选",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="默认续跑已有 manifest；--no-resume 等价于对所选条目重新处理",
    )
    parser.add_argument(
        "--display-summary",
        action="store_true",
        help="额外打印易读的状态摘要",
    )
    parser.add_argument(
        "--repair-response-dir",
        type=Path,
        default=None,
        help="可选的模型修复响应目录；文件名使用候选稳定 key.json",
    )
    parser.add_argument(
        "--allow-live-api",
        action="store_true",
        help="显式允许 repair 阶段调用 AI Hub Mix；默认只生成离线修复队列",
    )
    parser.add_argument(
        "--repair-model",
        default="gpt-5.6-sol",
        help="repair 阶段使用的模型 ID",
    )
    parser.add_argument(
        "--repair-reasoning-effort",
        default=None,
        help="repair 阶段的 reasoning_effort，不填则不发送该字段",
    )
    parser.add_argument(
        "--repair-workers",
        type=int,
        default=1,
        help="live repair 的并行工作数",
    )
    parser.add_argument(
        "--max-repair-calls",
        type=int,
        default=None,
        help="live repair 最多调用次数；不填则处理全部候选",
    )
    return parser


def _print_result(result: dict[str, object], *, display_summary: bool) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not display_summary:
        return
    summary = result.get("summary")
    if not isinstance(summary, dict):
        return
    print("\n状态摘要：")
    print(f"候选总数：{summary.get('candidate_count', 0)}")
    print(f"内核文件：{summary.get('kernel_count', 0)}")
    print(f"效果规格文件：{summary.get('effect_spec_count', 0)}")
    print(f"修复任务：{summary.get('repair_job_count', 0)}")
    print(f"待修复：{summary.get('repair_pending_count', 0)}")
    print(f"已编译：{summary.get('compiled_count', 0)}")
    print(f"状态分布：{json.dumps(summary.get('status_counts', {}), ensure_ascii=False)}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    pipeline = ScenarioPipeline(source, output, source_kind=args.source_kind)

    try:
        all_records = pipeline.discover()
        selected = pipeline.select(
            all_records,
            candidate_uids=None,
            case_ids=set(args.case_id or []),
            sample_per_item=args.sample_per_item,
            limit=args.limit,
            offset=args.offset,
        )
        if not selected and args.stage != "compile":
            raise ValueError("筛选后没有候选数据")
        plan = pipeline.build_plan(all_records, selected=selected)
        plan.update(
            {
                "source": str(source),
                "output": str(output),
                "stage": args.stage,
                "source_kind": args.source_kind,
                "force": bool(args.force or not args.resume),
                "selected_candidate_uids": [item.candidate_uid for item in selected],
                "offset": args.offset,
                "will_call_live_api": bool(args.allow_live_api and args.stage in {"repair", "all"}),
                "repair_model": args.repair_model,
                "repair_workers": args.repair_workers,
                "max_repair_calls": args.max_repair_calls,
            }
        )
        _write_json(output / "pipeline_plan.json", plan)

        if args.stage == "plan":
            _print_result({"status": "PLAN_READY", "plan": plan}, display_summary=args.display_summary)
            return 0

        force = bool(args.force or not args.resume)
        manifest = None
        if args.stage in {"extract", "all"}:
            manifest = pipeline.extract(
                selected,
                audit_records=all_records,
                force=force,
            )
        if args.stage in {"repair", "all"}:
            manifest = pipeline.prepare_repairs(
                candidate_uids={item.candidate_uid for item in selected},
                response_dir=args.repair_response_dir,
                allow_live_api=bool(args.allow_live_api),
                model_id=args.repair_model,
                reasoning_effort=args.repair_reasoning_effort,
                workers=args.repair_workers,
                max_calls=args.max_repair_calls,
                force=force,
            )
        if args.stage in {"compile", "all"}:
            manifest = pipeline.compile_ready(force=force)
        if manifest is None:
            raise ValueError("没有执行任何流水线阶段")
        result = {
            "status": "PIPELINE_COMPLETED",
            "stage": args.stage,
            "manifest": str(pipeline.manifest_path),
            "selected_candidate_count": len(selected),
            "source_candidate_count": len(all_records),
            "summary": manifest.summary,
        }
        _print_result(result, display_summary=args.display_summary)
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "PIPELINE_FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
