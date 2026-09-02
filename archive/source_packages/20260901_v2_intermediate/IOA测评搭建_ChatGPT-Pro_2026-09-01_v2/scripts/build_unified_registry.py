#!/usr/bin/env python
"""Build the unified offline registry for the scenario pipeline.

This command only reads the existing pipeline artifacts and writes task cards,
relative-path registry records, transition events and a migration report.  It
never creates a model client and never makes a network request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.scenario_generation.unified_pipeline import (  # noqa: E402
    build_unified_registry,
    validate_unified_registry,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pipeline-output",
        type=Path,
        default=ROOT / "data" / "scenario_pipeline" / "场景生产流水线-第01轮",
        help="已有 pipeline_manifest.json 所在目录",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="写入相对路径时使用的项目根目录",
    )
    parser.add_argument("--display-summary", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_unified_registry(
            args.pipeline_output.expanduser().resolve(),
            repo_root=args.repo_root.expanduser().resolve(),
        )
        validation = validate_unified_registry(
            result.registry_dir,
            repo_root=args.repo_root.expanduser().resolve(),
        )
        payload = {
            "status": "REGISTRY_BUILT",
            **result.model_dump(mode="json"),
            "validation": validation,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.display_summary:
            print("\n统一登记摘要：")
            print(f"候选：{result.candidate_count}")
            print(f"任务卡：{result.task_card_count}")
            print(f"状态事件：{result.event_count}")
            print(f"全部登记：{'是' if result.all_candidates_processed else '否'}")
            print(f"流水线状态：{json.dumps(result.stage_counts, ensure_ascii=False)}")
            print(f"质量状态：{json.dumps(result.quality_status_counts, ensure_ascii=False)}")
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "REGISTRY_FAILED", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
