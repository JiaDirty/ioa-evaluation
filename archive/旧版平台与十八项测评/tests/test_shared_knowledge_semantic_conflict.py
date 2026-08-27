import unittest

from src.core.shared_knowledge import KnowledgeStatus, SharedKnowledgeBase


class SharedKnowledgeSemanticConflictTest(unittest.IsolatedAsyncioTestCase):
    async def test_without_semantic_judge_does_not_assert_conflict(self):
        kb = SharedKnowledgeBase()
        first = await kb.add_knowledge(
            content="该公司FDA审批状态仍需确认",
            domain="healthcare",
            source_agent_id="a",
            source_sub_ioa_id="healthcare",
            tags=["pharma_claim"],
        )
        second = await kb.add_knowledge(
            content="该公司已经通过FDA审批",
            domain="healthcare",
            source_agent_id="b",
            source_sub_ioa_id="news",
            tags=["pharma_claim"],
        )

        self.assertEqual(len(kb.get_conflicts()), 0)
        self.assertEqual(first.status, KnowledgeStatus.DISPUTED)
        self.assertEqual(second.status, KnowledgeStatus.DISPUTED)
        self.assertEqual(second.metadata["semantic_relation"], "unknown")

    async def test_semantic_judge_detects_contradiction(self):
        def semantic_judge(existing, new, context):
            return {
                "relation": "contradiction",
                "reason": "one claim says unconfirmed while the other says approved",
            }

        kb = SharedKnowledgeBase(semantic_judge=semantic_judge)
        first = await kb.add_knowledge(
            content="该公司FDA审批状态仍需确认",
            domain="healthcare",
            source_agent_id="a",
            source_sub_ioa_id="healthcare",
            tags=["pharma_claim"],
        )
        second = await kb.add_knowledge(
            content="该公司已经通过FDA审批",
            domain="healthcare",
            source_agent_id="b",
            source_sub_ioa_id="news",
            tags=["pharma_claim"],
        )

        self.assertEqual(len(kb.get_conflicts()), 1)
        self.assertEqual(first.status, KnowledgeStatus.DISPUTED)
        self.assertEqual(second.status, KnowledgeStatus.DISPUTED)

    async def test_semantic_judge_supporting_claims_stay_active(self):
        def semantic_judge(existing, new, context):
            return {
                "relation": "support",
                "reason": "both claims say verification is required",
            }

        kb = SharedKnowledgeBase(semantic_judge=semantic_judge)
        first = await kb.add_knowledge(
            content="该公司FDA审批状态仍需官方来源确认",
            domain="healthcare",
            source_agent_id="a",
            source_sub_ioa_id="healthcare",
            tags=["pharma_claim"],
        )
        second = await kb.add_knowledge(
            content="该公司FDA审批状态尚未确认，需要等待官方公告",
            domain="healthcare",
            source_agent_id="b",
            source_sub_ioa_id="news",
            tags=["pharma_claim"],
        )

        self.assertEqual(len(kb.get_conflicts()), 0)
        self.assertEqual(first.status, KnowledgeStatus.ACTIVE)
        self.assertEqual(second.status, KnowledgeStatus.ACTIVE)
        self.assertEqual(second.metadata["semantic_relation"], "support")


if __name__ == "__main__":
    unittest.main()
