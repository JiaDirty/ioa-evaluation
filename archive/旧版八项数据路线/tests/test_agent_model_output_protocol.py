import json
import unittest
from pathlib import Path

from src.evaluation.agent_model.behavior_parser import (
    BehaviorParser,
    semantic_consistency_errors,
    try_parse_decision_output,
)
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.context_builder import AgentContextBuilder
from src.evaluation.agent_model.models import AgentModelAction
from src.evaluation.agent_model.prompt_policy import visible_action_schema
from src.runtime.ag2_runtime import _convert_agent_model_action


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "generated"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class AgentModelOutputProtocolTest(unittest.TestCase):
    def test_provider_schema_contains_no_open_ended_objects(self):
        schema_text = json.dumps(AgentModelAction.model_json_schema())

        self.assertNotIn('"additionalProperties": true', schema_text)

    def test_final_only_schema_omits_unreachable_tool_definitions(self):
        for category in ("CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE"):
            with self.subTest(category=category):
                definitions = visible_action_schema(category, []).get("$defs", {})
                self.assertNotIn("AgentModelToolArguments", definitions)
                self.assertNotIn("AgentModelToolCallRequest", definitions)

    def test_provider_nested_action_is_expanded_without_changing_intent(self):
        final = AgentModelAction.model_validate({
            "action": {
                "kind": "final",
                "business_output": {"answer": "keep"},
                "behavior_record": {},
                "reason": "done",
            },
        })
        tool = AgentModelAction.model_validate({
            "action": {
                "kind": "tool_call",
                "tool_call": {
                    "tool_id": "query_business_status",
                    "arguments": {"claim_id": "信息-02"},
                    "reason": "verify",
                },
            },
        })

        self.assertEqual(final.type, "final")
        self.assertEqual(final.business_output.answer, "keep")
        self.assertIsNone(final.tool_call)
        self.assertEqual(tool.type, "tool_call")
        self.assertEqual(tool.tool_call.tool_id, "query_business_status")
        self.assertEqual(tool.business_output.answer, "")

    def test_visible_final_schema_contains_only_the_six_business_fields(self):
        schema = visible_action_schema(
            "CAS",
            [],
            required_claim_id="信息-01",
            require_forward_decision=True,
        )
        self.assertEqual(set(schema["properties"]), {
            "status", "decision", "answer", "evidence_refs",
            "next_action", "handoff_message",
        })
        serialized = json.dumps(schema, ensure_ascii=False)
        for hidden in (
            "behavior_record", "claims", "forwarded", "used_for_action",
            "decision_code", "rules_used", "discussion_actions",
        ):
            self.assertNotIn(hidden, serialized)

        output, error = try_parse_decision_output({
            "status": "COMPLETED",
            "decision": "记录该信息。",
            "answer": "机场当前正常运行。",
            "evidence_refs": ["权威来源-01"],
            "next_action": "完成本步骤",
            "handoff_message": "",
        })
        self.assertIsNone(error)
        self.assertEqual(output.decision, "记录该信息。")

    def test_unused_strict_tool_arguments_are_not_sent_to_gateway(self):
        converted = _convert_agent_model_action({
            "type": "tool_call",
            "tool_call": {
                "tool_id": "query_business_status",
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
                "tool_id": "query_business_status",
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
                "tool_id": "query_business_status",
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

    def test_nested_final_action_must_not_embed_tool_call(self):
        raw = {
            "action": {
                "kind": "final",
                "business_output": {"answer": "keep"},
                "behavior_record": {},
                "reason": "done",
                "tool_call": {
                    "tool_id": "query_business_status",
                    "arguments": {"claim_id": "claim-1"},
                    "reason": "lookup",
                },
            },
        }

        action, error = BehaviorParser().try_parse_action(raw)

        self.assertIsNone(action)
        self.assertIn("must not include tool_call", error)

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

    def test_context_builder_uses_unified_six_field_final_schema(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        prompt = AgentContextBuilder(case).build_prompt(
            role_id="synthesis",
            task_text="controlled task",
        )

        self.assertIn('"handoff_message"', prompt)
        self.assertIn('"decision"', prompt)
        self.assertNotIn('"tool_call"', prompt)
        self.assertNotIn('"business_output"', prompt)
        self.assertNotIn('"behavior_record"', prompt)
        self.assertNotIn('"human_confirmation_required"', prompt)
        self.assertNotIn('"action_type"', prompt)

    def test_semantic_consistency_reports_only_internal_contradictions(self):
        action = {
            "type": "final",
            "business_output": {
                "answer": "接受该说法，继续传递，并用于最终业务结论；已经核验。",
                "decision": "传递",
                "confidence": 0.7,
                "limitations": [],
            },
            "reason": "把信息传给下一角色，并据此采取行动。",
            "behavior_record": {
                "claims": [{
                    "claim_id": "claim-1",
                    "statement": "待判断信息",
                    "disposition": "accepted",
                    "forwarded": False,
                    "used_for_action": False,
                    "verification_status": "unverified",
                }],
            },
        }

        errors = semantic_consistency_errors(action, "claim-1")

        self.assertEqual(len(errors), 3)
        self.assertTrue(any("传递决定" in item for item in errors))
        self.assertTrue(any("采用情况" in item for item in errors))
        self.assertTrue(any("核验状态" in item for item in errors))

    def test_semantic_consistency_does_not_prefer_a_safe_decision(self):
        action = {
            "type": "final",
            "business_output": {
                "answer": "不接受该说法，不传递，也不用于最终业务结论；尚未核验。",
                "decision": "不传递",
                "confidence": 0.7,
                "limitations": [],
            },
            "reason": "不把信息传给下一角色，不据此采取行动。",
            "behavior_record": {
                "claims": [{
                    "claim_id": "claim-1",
                    "statement": "待判断信息",
                    "disposition": "rejected",
                    "forwarded": False,
                    "used_for_action": False,
                    "verification_status": "unverified",
                }],
            },
        }

        self.assertEqual(semantic_consistency_errors(action, "claim-1"), [])
