import unittest
from datetime import datetime

from risk_tests.realism import CORE_REQUIRED_DECISION_AGENTS
from src.core.data_models import Artifact, RiskLevel, TaskResult, TaskStatus
from src.experiment.runner import IoAEnvironment
from src.attacks.observation import NetworkObservationEvent
from risk_tests.interconnection.structure_exposure import BehaviorInferenceTest


class ReportMetricsTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _task_result_with_decisions(task_id: str) -> TaskResult:
        return TaskResult(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            artifacts=[
                Artifact(
                    content="ok",
                    metadata={
                        "decision_agents": {
                            agent_name: {"agent_name": agent_name}
                            for agent_name in CORE_REQUIRED_DECISION_AGENTS
                        }
                    },
                )
            ],
        )

    async def test_report_utility_uses_marketplace_task_results_when_runner_has_none(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.marketplace._results["ok"] = TaskResult(
            task_id="ok",
            status=TaskStatus.COMPLETED,
        )
        env.marketplace._results["failed"] = TaskResult(
            task_id="failed",
            status=TaskStatus.FAILED,
            error="expected failure",
        )

        report = await env.metrics_engine.generate_report([], [])

        self.assertEqual(report["summary"]["utility"], 0.5)
        self.assertEqual(len(report["task_results"]), 2)

    async def test_behavior_inference_failure_is_not_low_risk(self):
        class _Audit:
            async def query_by_sub_ioa(self, sid):
                return []

        class _Env:
            audit_logger = _Audit()

            async def submit_task(self, task):
                return ReportMetricsTest._task_result_with_decisions(task.task_id)

            def get_sub_ioa_ids(self):
                return ["finance"]

            def get_network_observations(self):
                return [
                    NetworkObservationEvent(
                        timestamp=datetime.now(),
                        trace_id=f"trace-{i}",
                        source_domain="finance",
                        target_domain_hint="healthcare",
                        protocol="a2a",
                    )
                    for i in range(4)
                ]

        test = BehaviorInferenceTest()
        result = await test.run(_Env())

        self.assertFalse(result.passed)
        self.assertEqual(result.risk_level, RiskLevel.MEDIUM)
