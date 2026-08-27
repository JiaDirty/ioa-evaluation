import json
import unittest

from src.evaluation.agent_model.evidence_builder import (
    EvidenceBuilder,
    parse_evidence_ref,
)


class AgentModelEvidenceBuilderTest(unittest.TestCase):
    def test_every_reference_can_be_parsed_and_resolved(self):
        builder = EvidenceBuilder()
        ref_id = builder.record_agent_call(
            "suite:key-node:run-1", "INC-01", "agent", 1, "in", "out"
        )

        self.assertEqual(
            parse_evidence_ref(ref_id),
            {
                "run_id": "suite:key-node:run-1",
                "kind": "agent",
                "index": 0,
            },
        )
        self.assertEqual(builder.get_by_ref(ref_id)["ref_id"], ref_id)
        with self.assertRaisesRegex(ValueError, "invalid evidence reference"):
            parse_evidence_ref("ev-unstructured")

    def test_duplicate_model_response_is_referenced_not_resent(self):
        builder = EvidenceBuilder()
        action = {
            "type": "final",
            "business_output": {"answer": "controlled answer"},
        }
        builder.record_agent_call(
            run_id="run-1",
            case_id="INC-01",
            role_id="agent",
            round_index=1,
            input_summary="input",
            output_summary="output",
            raw_output={"step_output": action},
        )

        ref = builder.record_runtime_event("run-1", {
            "event_id": "event-1",
            "event_type": "model_call",
            "payload": {
                "request": {"messages": [{"content": "ordinary request"}]},
                "response": {"raw": json.dumps(action), "error": None},
            },
        })

        self.assertEqual(ref, "")
        self.assertEqual(len(builder.get_all()), 1)

    def test_distinct_original_response_is_preserved_for_format_audit(self):
        builder = EvidenceBuilder()
        builder.record_agent_call(
            run_id="run-1",
            case_id="INC-01",
            role_id="agent",
            round_index=1,
            input_summary="input",
            output_summary="output",
            raw_output={"step_output": {"type": "final"}},
        )

        builder.record_runtime_event("run-1", {
            "event_id": "event-1",
            "event_type": "model_call",
            "payload": {
                "request": {"messages": [{"content": "ordinary request"}]},
                "response": {"raw": "unstructured original answer", "error": None},
            },
        })

        self.assertEqual(
            builder.get_all()[-1]["payload"]["response"]["raw"],
            "unstructured original answer",
        )

    def test_clear_removes_deduplication_state(self):
        builder = EvidenceBuilder()
        builder.record_agent_call(
            "run-1", "INC-01", "agent", 1, "input", "output",
            raw_output={"type": "final"},
        )

        builder.clear()

        self.assertEqual(builder.get_all(), [])
        self.assertEqual(builder._agent_output_fingerprints, set())

    def test_repeated_reward_history_is_joined_to_reward_events(self):
        builder = EvidenceBuilder()
        builder.record_agent_call(
            run_id="run-1",
            case_id="INC-01",
            role_id="agent",
            round_index=3,
            input_summary="input",
            output_summary="output",
            raw_input={
                "public_state": {
                    "round": 3,
                    "recent_reward_history": [
                        {"round": 1, "reward": 0.1, "output_length": 500},
                        {"round": 2, "reward": 0.2, "output_length": 600},
                    ],
                }
            },
        )

        raw_input = builder.get_all()[0]["raw_input"]
        state = raw_input["public_state"]
        self.assertNotIn("recent_reward_history", state)
        self.assertEqual(state["recent_reward_history_rounds"], [1, 2])
        self.assertEqual(state["recent_reward_history_count"], 2)
        self.assertIn("reward runtime events", state["recent_reward_history_evidence"])
