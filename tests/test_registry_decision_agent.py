import unittest

from src.attacks.registry_attack_surface import RegistryAttackSurface, RegistryMutationRequest
from src.audit.audit_logger import AuditLogger
from src.core.data_models import AgentCard, AuditAction
from src.registry.registry import Registry


class RegistryDecisionAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_registry_attack_surface_records_registry_risk_decision(self):
        local = Registry("finance-local")
        global_reg = Registry("global", is_global=True)
        audit = AuditLogger("global")
        surface = RegistryAttackSurface(local, global_reg, audit, "finance")
        fake = AgentCard(
            agent_id="fake-finance",
            display_name="Fake Finance",
            provider="attacker",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            certificate="forged-cert",
        )

        outcome = await surface.submit(RegistryMutationRequest(
            operation="register_agent",
            actor_id="external-attacker",
            sub_ioa_id="finance",
            card=fake,
        ))

        self.assertTrue(outcome.applied)
        entries = [
            entry for entry in await audit.query_chain("registry-register_agent-fake-finance")
            if entry.action == AuditAction.DECISION_AGENT
        ]
        self.assertEqual(entries[0].details["decision_agent"], "RegistryRiskAgent")
        self.assertEqual(
            outcome.details["registry_risk_decision"]["recommended_action"],
            "quarantine",
        )


if __name__ == "__main__":
    unittest.main()
