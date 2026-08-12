#!/usr/bin/env python
"""Run controlled live diagnostics for four counterintuitive tool-result uses.

No provider request is made unless ``--allow-live-api`` is present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.tool_result_diagnostics import (
    VARIANTS,
    diagnostic_targets,
    run_diagnostic_unit,
    summarize_units,
    validate_diagnostic_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled tool-result comprehension diagnostics"
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-live-api", action="store_true")
    parser.add_argument("--repeat-count", type=int, default=3)
    parser.add_argument(
        "--categories",
        default="CAS,CON,RUM,JUD",
        help="Comma-separated subset of CAS,CON,RUM,JUD.",
    )
    parser.add_argument(
        "--variants",
        default="all",
        help="Comma-separated variant IDs, or all.",
    )
    parser.add_argument(
        "--output", default=".local/results/tool_result_diagnostics"
    )
    return parser


def _build_live_client():
    from src.llm.client import OpenAIClient
    from src.llm.config import get_agent_llm_config

    return OpenAIClient(get_agent_llm_config())


async def async_main(args: argparse.Namespace) -> int:
    catalog = validate_diagnostic_catalog()
    targets = diagnostic_targets()
    categories = [item.strip().upper() for item in args.categories.split(",") if item.strip()]
    unknown = sorted(set(categories) - set(targets))
    if unknown:
        raise SystemExit(f"unknown categories: {','.join(unknown)}")
    if args.repeat_count <= 0:
        raise SystemExit("--repeat-count must be positive")
    variants_by_id = {variant.variant_id: variant for variant in VARIANTS}
    if args.variants == "all":
        variants = list(VARIANTS)
    else:
        requested_variants = [
            item.strip() for item in args.variants.split(",") if item.strip()
        ]
        unknown_variants = sorted(set(requested_variants) - set(variants_by_id))
        if unknown_variants:
            raise SystemExit(f"unknown variants: {','.join(unknown_variants)}")
        variants = [variants_by_id[item] for item in requested_variants]
    unit_count = len(categories) * len(variants) * args.repeat_count
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "VALID",
                    **catalog,
                    "selected_categories": categories,
                    "selected_variants": [variant.variant_id for variant in variants],
                    "planned_live_units": unit_count,
                    "provider_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not args.allow_live_api:
        raise SystemExit(
            "live diagnostics may incur API charges; add --allow-live-api after reviewing the plan"
        )

    client = _build_live_client()
    units = []
    completed = 0
    for category in categories:
        target = targets[category]
        for variant in variants:
            for repeat_index in range(1, args.repeat_count + 1):
                unit = await run_diagnostic_unit(
                    client,
                    target,
                    variant,
                    repeat_index,
                )
                units.append(unit)
                completed += 1
                print(
                    json.dumps(
                        {
                            "progress": f"{completed}/{unit_count}",
                            "category": category,
                            "variant": variant.variant_id,
                            "repeat": repeat_index,
                            "outcome": unit["outcome"],
                            "error": unit["error"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    provider_calls = sum(
        len(audit.get("provider_calls", []))
        for unit in units
        for audit in unit["provider_audit"]
    )
    run_id = datetime.now(timezone.utc).strftime("diagnostic-%Y%m%dT%H%M%SZ")
    payload = {
        "run_id": run_id,
        "model": getattr(client, "model", None),
        "temperature": getattr(client, "temperature", None),
        "repeat_count": args.repeat_count,
        "provider_calls": provider_calls,
        "formal_score_eligible": False,
        "purpose": "定位反常行为来自随机性、输入复杂度、工具字段表达，还是未按工具事实行动。",
        "summary": summarize_units(units),
        "units": units,
    }
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / f"{run_id}.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "result_path": str(result_path.resolve()),
                "unit_count": len(units),
                "provider_calls": provider_calls,
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
