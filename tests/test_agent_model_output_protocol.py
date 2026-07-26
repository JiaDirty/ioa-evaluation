import json
import unittest
from pathlib import Path

from src.evaluation.agent_model.behavior_parser import BehaviorParser
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.context_builder import AgentContextBuilder
from src.evaluation.agent_model.models import AgentModelAction
from src.runtime.ag2_runtime import _convert_agent_model_action


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class AgentModelOutputProtocolTest(unittest.TestCase):
    def test_provider_schema_contains_no_open_ended_objects(self):
        schema_text = json.dumps(AgentModelAction.model_json_schema())

        self.assertNotIn('"additionalProperties": true', schema_text)

    def test_unused_strict_tool_arguments_are_not_sent_to_gateway(self):
        converted = _convert_agent_model_action({
            "type": "tool_call",
            "tool_call": {
                "tool_id": "authoritative_fact_lookup",
                "arguments": {
                    "claim_id": "claim-1",
                    "query": "verify",
                    "message_id": None,
                },
            },
        })

        self.assertEqual(
            converted["arguments"],
            {"claim_id": "claim-1", "query": "verify"},
        )

    def test_converter_rejects_final_with_tool_call(self):
        converted = _convert_agent_model_action({
            "type": "final",
            "business_output": {"answer": "需要核验"},
            "behavior_record": {"claims": []},
            "tool_call": {
                "tool_id": "authoritative_fact_lookup",
                "arguments": {"claim_id": "claim-1"},
            },
        })

        self.assertIsNone(converted)

    def test_strict_agent_model_action_parses(self):
        raw = {
            "type": "final",
            "business_output": {
                "answer": "业务结论",
                "decision": "needs_verification",
                "confidence": 0.7,
                "limitations": [],
            },
            "behavior_record": {
                "claims": [],
                "sources": [],
                "verification_requested": True,
                "rules_used": [],
                "exceptions_used": [],
                "alternatives_presented": [],
                "human_confirmation_required": False,
                "permission_expansion_requested": False,
                "discussion_actions": [],
                "notes": [],
            },
        }

        action, error = BehaviorParser().try_parse_action(raw)

        self.assertIsNone(error)
        self.assertEqual(action.type, "final")
        self.assertTrue(action.behavior_record.verification_requested)

    def test_plain_text_is_protocol_parse_failure(self):
        action, error = BehaviorParser().try_parse_action("plain answer")

        self.assertIsNone(action)
        self.assertIn("invalid AgentModelAction JSON", error)

    def test_final_action_must_not_embed_tool_call(self):
        raw = {
            "type": "final",
            "business_output": {"answer": "需要核验"},
            "behavior_record": {"claims": [], "sources": []},
            "tool_call": {
                "tool_id": "authoritative_fact_lookup",
                "arguments": {"claim_id": "claim-1"},
                "reason": "verify",
            },
        }

        action, error = BehaviorParser().try_parse_action(raw)
        record, record_error = BehaviorParser().try_parse(raw)

        self.assertIsNone(action)
        self.assertIn("must not include tool_call", error)
        self.assertIsNotNone(record_error)
        self.assertEqual(record.claims, [])

    def test_tool_call_action_requires_tool_call_object(self):
        raw = {
            "type": "tool_call",
            "business_output": {"answer": ""},
            "behavior_record": {"claims": [], "sources": []},
            "tool_call": None,
        }

        action, error = BehaviorParser().try_parse_action(raw)

        self.assertIsNone(action)
        self.assertIn("requires tool_call", error)

    def test_step_output_transport_wrapper_is_unwrapped_strictly(self):
        raw = {
            "step_output": {
                "type": "final",
                "business_output": {"answer": "纠正信息"},
                "behavior_record": {
                    "claims": [{
                        "claim_id": "claim-cas-01:recovery",
                        "statement": "机场官方运行状态恢复接口返回正常",
                        "disposition": "accepted",
                        "forwarded": True,
                        "corrects_claim_id": "claim-cas-01",
                    }],
                },
            },
            "status": "",
            "error": "",
        }

        action, error = BehaviorParser().try_parse_action(raw)

        self.assertIsNone(error)
        self.assertEqual(
            action.behavior_record.claims[0].corrects_claim_id,
            "claim-cas-01",
        )

    def test_context_builder_uses_agent_model_action_schema(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        prompt = AgentContextBuilder(case).build_prompt(
            role_id="synthesis",
            task_text="controlled task",
        )

        self.assertIn("AgentModelAction", prompt)
        self.assertIn('"business_output"', prompt)
        self.assertIn('"tool_call"', prompt)
        self.assertNotIn('"action_type"', prompt)
