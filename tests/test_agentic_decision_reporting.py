import unittest

from src.audit.audit_logger import AuditLogger
from src.core.data_models import Artifact, RiskLevel, TaskResult, TaskStatus, TestResult
from src.experiment.runner import MetricsEngine


class AgenticDecisionReportingTest(unittest.IsolatedAsyncioTestCase):
    async def test_report_summarizes_agentic_decision_coverage(self):
        metrics_engine = MetricsEngine(AuditLogger("global"))
        required_agents = [
            "TaskUnderstandingAgent",
            "PermissionAnalysisAgent",
            "ContentSecurityAgent",
            "DelegationDriftAgent",
        ]
        test_result = TestResult(
            test_id="ioa_identity_spoofing",
            test_name="identity spoofing",
            category="trust_authorization",
            passed=True,
            risk_level=RiskLevel.LOW,
            realism={"required_decision_agents": required_agents},
            details={
                "decision_agents": {
                    "DelegationDriftAgent": {
                        "agent_name": "DelegationDriftAgent",
                        "fallback_used": False,
                    }
                }
            },
        )
        artifact = Artifact(
            content="ok",
            source_agent_id="finance-agent-1",
            source_task_id="task-1",
            safe=True,
            metadata={
                "decision_agents": {
                    "task_understanding": {"agent_name": "TaskUnderstandingAgent"},
                    "permission_analysis": {"agent_name": "PermissionAnalysisAgent"},
                    "content_security": {
                        "agent_name": "ContentSecurityAgent",
                        "fallback_used": True,
                    },
                },
                "security_check": {"keyword_hits": ["inject"]},
            },
        )
        task_result = TaskResult(
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            artifacts=[artifact],
        )

        report = await metrics_engine.generate_report([test_result], [task_result])

        summary = report["summary"]["agentic_decisions"]
        self.assertEqual(summary["decision_agent_tasks"], 1)
        self.assertEqual(summary["decision_agent_event_count"], 4)
        self.assertEqual(summary["agentic_decision_coverage"], 1.0)
        self.assertEqual(summary["keyword_match_usage_count"], 1)
        self.assertEqual(summary["semantic_rule_fallback_count"], 1)
        self.assertEqual(summary["required_decision_agents"], required_agents)
        self.assertEqual(summary["missing_required_decision_agents"], [])


if __name__ == "__main__":
    unittest.main()
