from __future__ import annotations

from pathlib import Path
import unittest

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.categories.discussion import _visible_discussion_evidence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMBINED_DATASET = (
    PROJECT_ROOT
    / "data"
    / "generated"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)
DIS_DATASET = (
    PROJECT_ROOT / "data" / "agent_model_cases" / "DIS_discussion_distortion.jsonl"
)
FORBIDDEN_CUES = ("正确", "安全", "应拒绝", "应优先", "评分", "测评")


def _load_dis_rows(path: Path) -> dict[str, dict]:
    rows = CaseLoader(path).expanded_dicts()
    return {
        row["case_id"]: row
        for row in rows
        if row.get("category_code") == "DIS"
    }


class AgentModelDISEvidenceContentTest(unittest.TestCase):
    def test_all_twenty_cases_have_three_distinct_readable_evidence_bodies(self):
        rows = _load_dis_rows(DIS_DATASET)
        self.assertEqual(set(rows), {f"DIS-{index:02d}" for index in range(1, 21)})

        all_contents: list[str] = []
        for case_id, row in rows.items():
            initial = row["scenario"]["initial_evidence"]
            independent = initial["independent_sources"]
            self.assertEqual(len(independent), 2, case_id)
            contents = [
                initial["coordinated_common_source"].get("content"),
                independent[0].get("content"),
                independent[1].get("content"),
            ]
            self.assertTrue(
                all(isinstance(item, str) and item.strip() for item in contents),
                case_id,
            )
            self.assertEqual(len(set(contents)), 3, case_id)
            self.assertNotIn(row["scenario"]["manipulation_goal"], contents, case_id)
            for content in contents:
                for cue in FORBIDDEN_CUES:
                    self.assertNotIn(cue, content, f"{case_id}: {cue}")
            all_contents.extend(contents)

        self.assertEqual(len(all_contents), 60)
        self.assertEqual(len(set(all_contents)), 60)

    def test_combined_and_category_datasets_have_identical_dis_rows(self):
        self.assertEqual(_load_dis_rows(COMBINED_DATASET), _load_dis_rows(DIS_DATASET))

    def test_case_loader_preserves_the_new_evidence_bodies(self):
        loader = CaseLoader(DIS_DATASET)
        cases = loader.load_all()
        self.assertEqual(loader.errors, [])
        self.assertEqual(len(cases), 20)
        for case_id, case in cases.items():
            initial = case.scenario.initial_evidence
            self.assertIsNotNone(initial, case_id)
            assert initial is not None
            self.assertTrue(initial.coordinated_common_source["content"].strip())
            self.assertTrue(
                all(item["content"].strip() for item in initial.independent_sources)
            )

    def test_visible_evidence_uses_explicit_content_not_the_hidden_support_flag(self):
        visible = _visible_discussion_evidence(
            {
                "source_id": "source-1",
                "verification_status": "unverified",
                "supports_manipulation_goal": True,
                "content": "材料中实际记录的正文。",
            },
            {"source-1": "证据-01"},
        )
        self.assertEqual(
            visible,
            {
                "evidence_id": "证据-01",
                "content": "材料中实际记录的正文。",
            },
        )

        without_content = _visible_discussion_evidence(
            {
                "source_id": "source-2",
                "verification_status": "verified",
                "supports_manipulation_goal": False,
            },
            {"source-2": "证据-02"},
        )
        self.assertNotIn("content", without_content)


if __name__ == "__main__":
    unittest.main()
