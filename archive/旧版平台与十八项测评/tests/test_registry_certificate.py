import unittest

from src.core.data_models import AgentCard, ProtocolType
from src.registry.registry import Registry


class RegistryCertificateTest(unittest.IsolatedAsyncioTestCase):
    async def test_registry_issues_hmac_certificate_for_unsigned_agent(self):
        registry = Registry("finance-local")
        card = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate=None,
        )

        await registry.register(card)
        stored = await registry.get_agent("finance-agent-1")
        verification = await registry.verify_identity("finance-agent-1")

        self.assertIsNotNone(stored.certificate)
        self.assertTrue(stored.certificate.startswith("hmac:"))
        self.assertTrue(verification.verified)

    async def test_hmac_certificate_rejects_tampered_identity_fields(self):
        registry = Registry("finance-local")
        card = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate=None,
        )

        await registry.register(card)
        await registry.update("finance-agent-1", {"provider": "attacker-org"})
        verification = await registry.verify_identity("finance-agent-1")

        self.assertFalse(verification.verified)
        self.assertFalse(verification.certificate_valid)

    async def test_forged_certificate_is_rejected(self):
        registry = Registry("finance-local")
        card = AgentCard(
            agent_id="fake-agent",
            display_name="Fake Agent",
            provider="attacker",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            certificate="forged_cert_001",
        )

        await registry.register(card)
        verification = await registry.verify_identity("fake-agent")

        self.assertFalse(verification.verified)


if __name__ == "__main__":
    unittest.main()
