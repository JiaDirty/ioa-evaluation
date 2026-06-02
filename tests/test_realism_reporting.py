import unittest

from risk_tests.realism import get_realism_profile
from risk_tests.registry import ALL_TESTS, list_all_tests
from run_experiment import _select_sybil_base_agent_id
from src.core.data_models import AgentCard, RiskLevel, TestResult
from src.audit.audit_logger import AuditLogger
from src.experiment.runner import MetricsEngine
from src.registry.registry import Registry


class RealismReportingTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_registered_risk_test_carries_realism_profile(self):
        levels = {"mechanism_real", "hybrid_controlled", "concept_probe"}

        for test in ALL_TESTS:
            result = test.make_result(passed=True, risk_level=RiskLevel.LOW)

            self.assertIsNotNone(getattr(result, "realism", None), test.test_id)
            self.assertIn(result.realism["level"], levels, test.test_id)
            self.assertIsInstance(result.realism["agent_in_loop"], bool, test.test_id)
            self.assertIsInstance(result.realism["communication_chain"], list, test.test_id)
            self.assertTrue(result.realism["limitations"], test.test_id)
            self.assertTrue(result.realism["required_decision_agents"], test.test_id)

    async def test_report_summarizes_realism_strength(self):
        metrics_engine = MetricsEngine(AuditLogger("global"))
        result = TestResult(
            test_id="t1",
            test_name="realism smoke",
            category="trust_authorization",
            passed=True,
            risk_level=RiskLevel.LOW,
            realism={
                "level": "mechanism_real",
                "agent_in_loop": True,
                "communication_chain": ["task", "gateway", "agent_endpoint", "audit"],
                "infrastructure_components": ["Registry", "Gateway", "AuditLogger"],
                "evidence": ["gateway_dispatch"],
                "limitations": ["single-machine testbed"],
            },
        )

        report = await metrics_engine.generate_report([result], [])

        realism = report["summary"]["realism"]
        self.assertEqual(realism["level_counts"]["mechanism_real"], 1)
        self.assertEqual(realism["agent_in_loop_tests"], 1)
        self.assertEqual(realism["gateway_mediated_tests"], 1)
        self.assertIn("single-machine testbed", realism["limitations"])

    async def test_list_all_tests_exposes_realism_level_for_frontend_and_docs(self):
        listed = list_all_tests()

        self.assertEqual(len(listed), 18)
        self.assertTrue(all("realism_level" in item for item in listed))
        self.assertTrue(all(item["realism_level"] for item in listed))

    async def test_all_registered_tests_meet_high_agent_integration_floor(self):
        for test in ALL_TESTS:
            profile = get_realism_profile(test.test_id)
            chain = [str(item).lower() for item in profile.get("communication_chain", [])]
            components = [str(item).lower() for item in profile.get("infrastructure_components", [])]
            combined = chain + components

            self.assertNotEqual(profile["level"], "concept_probe", test.test_id)
            self.assertTrue(profile["agent_in_loop"], test.test_id)
            self.assertTrue(any("gateway" in item for item in combined), test.test_id)
            self.assertTrue(
                any("task" in item or "marketplace" in item for item in combined),
                test.test_id,
            )
            self.assertGreaterEqual(len(profile.get("evidence", [])), 2, test.test_id)

    async def test_report_exposes_high_integration_gate(self):
        metrics_engine = MetricsEngine(AuditLogger("global"))
        high = TestResult(
            test_id="high",
            test_name="high",
            category="trust_authorization",
            passed=True,
            risk_level=RiskLevel.LOW,
            realism={
                "level": "mechanism_real",
                "agent_in_loop": True,
                "communication_chain": ["task", "marketplace", "gateway", "agent_endpoint", "audit"],
                "infrastructure_components": ["TaskMarketplace", "Gateway"],
                "evidence": ["gateway_dispatch", "agent_response"],
                "limitations": ["single-machine testbed"],
            },
        )
        low = TestResult(
            test_id="low",
            test_name="low",
            category="human_agency",
            passed=True,
            risk_level=RiskLevel.LOW,
            realism={
                "level": "concept_probe",
                "agent_in_loop": False,
                "communication_chain": ["controlled_state"],
                "infrastructure_components": ["LLMJudge"],
                "evidence": ["semantic probe"],
                "limitations": ["not agent integrated"],
            },
        )

        report = await metrics_engine.generate_report([high, low], [])

        realism = report["summary"]["realism"]
        self.assertEqual(realism["high_integration_tests"], 1)
        self.assertEqual(realism["low_integration_tests"], ["low"])
        self.assertFalse(realism["meets_high_integration_floor"])

    async def test_sybil_scenario_injection_uses_existing_agent_as_base(self):
        registry = Registry("finance-local")
        await registry.register(AgentCard(
            agent_id="finance-base-agent",
            display_name="Base Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=["financial_analysis"],
            certificate="cert-finance-base-agent",
            permission_scope=["read", "execute"],
        ))
        await registry.register(AgentCard(
            agent_id="finance-gw",
            display_name="Finance Gateway",
            provider="finance-infra",
            sub_ioa_id="finance",
            declared_capabilities=["gateway", "routing"],
            actual_capabilities=["gateway", "routing"],
            certificate="cert-finance-gw",
            permission_scope=["read", "execute", "relay"],
        ))

        class _Env:
            def get_local_registry(self, sub_ioa_id):
                return registry if sub_ioa_id == "finance" else None

        base_id = await _select_sybil_base_agent_id(_Env(), "finance")

        self.assertEqual(base_id, "finance-base-agent")


if __name__ == "__main__":
    unittest.main()
