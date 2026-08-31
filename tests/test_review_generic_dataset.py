from pathlib import Path

from scripts.review_generic_dataset import load_records, run_review
from tests.test_evaluation_dataset import _generic_payload, _write_enveloped_case


def test_generic_review_dry_run_builds_two_non_self_reviews(tmp_path):
    source = tmp_path / "accepted_cases.jsonl"
    _write_enveloped_case(source, _generic_payload("reviewable-case-001"))

    output = tmp_path / "reviews"
    summary = run_review(
        source,
        output,
        per_item=0,
        reviewers_per_case=2,
        workers=1,
        dry_run=True,
    )

    assert summary["candidate_count"] == 1
    assert summary["logical_item_count"] == 1
    assert summary["review_job_count"] == 2
    assert summary["self_review_count"] == 0
    assert Path(output / "review_manifest.json").exists()


def test_generic_review_splits_trust_variants_into_two_logical_items(tmp_path):
    first = _generic_payload("trust-drift-001")
    first["category"] = "TRA"
    first["metadata"]["sub_mechanism"] = "drift"
    second = _generic_payload("trust-vague-001")
    second["category"] = "TRA"
    second["metadata"]["sub_mechanism"] = "vague"
    source = tmp_path / "accepted_cases.jsonl"
    source.write_text(
        "\n".join(
            f'{{"schema_version":"business_case_spec_v1","case":{__import__("json").dumps(payload, ensure_ascii=False)}}}'
            for payload in (first, second)
        )
        + "\n",
        encoding="utf-8",
    )

    records = load_records(source)
    assert {record.item_name for record in records} == {
        "信任与授权链：转述授权扩大",
        "信任与授权链：含糊授权越界",
    }
