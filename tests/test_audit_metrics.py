import unittest

from src.audit.audit_logger import AuditLogger
from src.core.data_models import Artifact, AuditAction


class AuditMetricsTest(unittest.IsolatedAsyncioTestCase):
    async def test_attribution_accuracy_requires_traceable_artifact_source(self):
        audit = AuditLogger("global")
        artifact = Artifact(
            artifact_id="artifact-1",
            content="analysis",
            source_agent_id="agent-a",
            source_task_id="trace-1",
        )
        await audit.register_artifact(artifact)
        await audit.log_action(
            trace_id="trace-1",
            action=AuditAction.CALL,
            agent_id="gateway-a",
            sub_ioa_id="finance",
        )
        await audit.log_action(
            trace_id="trace-1",
            action=AuditAction.AGGREGATE,
            agent_id="gateway-a",
            sub_ioa_id="finance",
            target_agent_id="agent-a",
            output_artifact_ids=["artifact-1"],
        )

        metrics = await audit.compute_metrics()

        self.assertEqual(metrics.chain_completeness, 1.0)
        self.assertEqual(metrics.source_coverage, 0.5)
        self.assertGreater(metrics.attribution_accuracy, 0.0)

    async def test_attribution_accuracy_penalizes_missing_artifact_source(self):
        audit = AuditLogger("global")
        await audit.log_action(
            trace_id="trace-1",
            action=AuditAction.AGGREGATE,
            agent_id="gateway-a",
            sub_ioa_id="finance",
            target_agent_id="agent-a",
            output_artifact_ids=["missing-artifact"],
        )

        metrics = await audit.compute_metrics()

        self.assertEqual(metrics.chain_completeness, 1.0)
        self.assertEqual(metrics.source_coverage, 1.0)
        self.assertEqual(metrics.attribution_accuracy, 0.0)


if __name__ == "__main__":
    unittest.main()
