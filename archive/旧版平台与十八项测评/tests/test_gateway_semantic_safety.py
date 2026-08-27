import unittest
from dataclasses import dataclass

from src.audit.audit_logger import AuditLogger
from src.core.data_models import Artifact
from src.gateway.gateway import Gateway
from src.registry.registry import Registry


@dataclass
class _Verdict:
    has_risk: bool
    reason: str


class GatewaySemanticSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_llm_safety_judge_can_mark_semantic_risk_unsafe(self):
        def safety_judge(content, context):
            return _Verdict(has_risk=True, reason="semantic financial manipulation")

        gateway = Gateway(
            gateway_id="finance-gw",
            sub_ioa_id="finance",
            local_registry=Registry("finance-local"),
            global_registry=Registry("global", is_global=True),
            audit_logger=AuditLogger("global"),
            safety_judge=safety_judge,
        )
        artifact = Artifact(
            content="This recommendation pressures the user into a high-risk investment.",
            source_agent_id="finance-agent",
            source_task_id="task-1",
            safe=True,
        )

        checked = await gateway._security_check(artifact)

        self.assertFalse(checked.safe)
        self.assertIn("content_security", checked.metadata["decision_agents"])
        self.assertIn(
            "safety_labels",
            checked.metadata["decision_agents"]["content_security"]["output"],
        )
        self.assertEqual(checked.metadata["security_check"]["keyword_hits"], [])
        self.assertTrue(checked.metadata["security_check"]["llm_judge_used"])
        self.assertTrue(checked.metadata["security_check"]["llm_judge_has_risk"])
        self.assertEqual(
            checked.metadata["security_check"]["llm_judge_reason"],
            "semantic financial manipulation",
        )


if __name__ == "__main__":
    unittest.main()
