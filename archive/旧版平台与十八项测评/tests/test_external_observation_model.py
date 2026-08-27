import unittest
from datetime import datetime

from src.attacks.observation import ExternalObservationModel, NetworkObservationEvent
from src.core.data_models import AuditAction, AuditEntry


class ExternalObservationModelTest(unittest.TestCase):
    def test_observation_hides_internal_agent_ids_by_default(self):
        entry = AuditEntry(
            trace_id="trace-1",
            step_index=0,
            action=AuditAction.RELAY,
            agent_id="finance-gw",
            sub_ioa_id="finance",
            target_agent_id="secret-healthcare-agent",
            details={"target_sub_ioa": "healthcare"},
        )

        observations = ExternalObservationModel().from_audit_entries([entry])

        self.assertEqual(observations[0].target_domain_hint, "healthcare")
        self.assertNotIn("secret-healthcare-agent", str(observations[0]))

    def test_gateway_exposure_uses_external_observations_not_raw_audit(self):
        entries = [
            AuditEntry(
                trace_id=f"trace-{i}",
                step_index=i,
                action=AuditAction.RELAY,
                agent_id="finance-gw",
                sub_ioa_id="finance",
                details={"target_sub_ioa": "news"},
            )
            for i in range(4)
        ]

        model = ExternalObservationModel()
        observations = model.from_audit_entries(entries)
        result = model.infer_gateway_exposure(observations)

        self.assertTrue(result["exposed"])
        self.assertEqual(result["top_domain"], "finance")

    def test_external_model_accepts_network_events_without_internal_audit(self):
        events = [
            NetworkObservationEvent(
                timestamp=datetime.now(),
                trace_id=f"trace-{i}",
                source_domain="finance",
                target_domain_hint="healthcare",
                protocol="a2a",
                status_hint="observed",
            )
            for i in range(3)
        ]

        observations = ExternalObservationModel().from_network_events(events)
        result = ExternalObservationModel().infer_behavior_pattern(observations)

        self.assertEqual(observations[0].action, "network_post")
        self.assertTrue(result["inferable"])
        self.assertEqual(result["top_pair"], "finance->healthcare")


if __name__ == "__main__":
    unittest.main()
