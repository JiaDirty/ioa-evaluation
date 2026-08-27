"""Evidence builder — constructs traceable evidence packages for Judge evaluation.

Each evidence item is keyed by (case_id, run_id, artifact_id, claim_id, etc.)
and can be independently verified. No LLM is used for evidence building.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from copy import deepcopy
from typing import Any

from .models import ThreeLayerResult, VARIANT


_EVIDENCE_REF_PATTERN = re.compile(
    r"^ev:(?P<run_id>.+):(?P<kind>[a-z][a-z-]*):(?P<index>\d{4,})$"
)


def parse_evidence_ref(ref_id: str) -> dict[str, Any]:
    match = _EVIDENCE_REF_PATTERN.fullmatch(str(ref_id))
    if match is None:
        raise ValueError(f"invalid evidence reference: {ref_id}")
    return {
        "run_id": match.group("run_id"),
        "kind": match.group("kind"),
        "index": int(match.group("index")),
    }


class EvidenceBuilder:
    """Build evidence packages from raw evaluation data."""

    def __init__(self):
        self._evidence: list[dict[str, Any]] = []
        self._agent_output_fingerprints: set[str] = set()

    def _ref_id(self, run_id: str, kind: str) -> str:
        return f"ev:{run_id}:{kind}:{len(self._evidence):04d}"

    # ------------------------------------------------------------------
    # Evidence recording
    # ------------------------------------------------------------------

    def record_agent_call(
        self,
        run_id: str,
        case_id: str,
        role_id: str,
        round_index: int,
        input_summary: str,
        output_summary: str,
        tool_calls: list[dict[str, Any]] | None = None,
        parent_artifact_id: str | None = None,
        raw_input: dict[str, Any] | None = None,
        raw_output: Any = None,
        artifact_ids: list[str] | None = None,
    ) -> str:
        """Record an agent call with input/output summary."""
        ref = {
            "type": "agent_call",
            "ref_id": self._ref_id(run_id, "agent"),
            "run_id": run_id,
            "case_id": case_id,
            "role_id": role_id,
            "round_index": round_index,
            "input_summary": input_summary[:500],
            "output_summary": output_summary[:500],
            "tool_calls": tool_calls or [],
            "parent_artifact_id": parent_artifact_id,
            "raw_input": self._compact_agent_input(raw_input or {}),
            "raw_input_location": "context.db agent_turns and execution trace",
            "raw_output": raw_output,
            "artifact_ids": artifact_ids or [],
            "recorded_at": datetime.now().isoformat(),
        }
        self._evidence.append(ref)
        self._remember_agent_output(raw_output)
        return ref["ref_id"]

    def record_propagation(
        self,
        run_id: str,
        claim_id: str,
        source_role: str,
        receiver_role: str,
        accepted: bool | None,
        forwarded: bool | None,
        confidence: float | None,
        verification_requested: bool = False,
        *,
        statement: str = "",
        seen: bool | None = None,
        supported_by_ground_truth: bool | None = None,
    ) -> str:
        """Record a claim propagation edge."""
        ref = {
            "type": "propagation",
            "ref_id": self._ref_id(run_id, "propagation"),
            "run_id": run_id,
            "claim_id": claim_id,
            "statement": statement,
            "source_role": source_role,
            "receiver_role": receiver_role,
            "seen": seen,
            "accepted": accepted,
            "forwarded": forwarded,
            "confidence": confidence,
            "verification_requested": verification_requested,
            "supported_by_ground_truth": supported_by_ground_truth,
            "recorded_at": datetime.now().isoformat(),
        }
        self._evidence.append(ref)
        return ref["ref_id"]

    def record_tool_call(
        self,
        run_id: str,
        tool_id: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        caller_role: str,
    ) -> str:
        """Record a tool call with result."""
        ref = {
            "type": "tool_call",
            "ref_id": self._ref_id(run_id, "tool"),
            "run_id": run_id,
            "tool_id": tool_id,
            "arguments": arguments,
            "result": result,
            "caller_role": caller_role,
            "recorded_at": datetime.now().isoformat(),
        }
        self._evidence.append(ref)
        return ref["ref_id"]

    def record_runtime_event(
        self,
        run_id: str,
        event: dict[str, Any],
    ) -> str:
        """Record one immutable local runtime event for Judge auditability."""
        payload = event.get("payload", {})
        if event.get("event_type") == "model_call" and isinstance(payload, dict):
            request = payload.get("request", {})
            messages = request.get("messages", []) if isinstance(request, dict) else []
            request_text = "\n".join(
                str(message.get("content", ""))
                for message in messages if isinstance(message, dict)
            )
            response = payload.get("response", {})
            if not isinstance(response, dict):
                response = {"raw": response}
            format_correction = (
                "Format-only correction" in request_text
                or "仅纠正格式" in request_text
            )
            raw_response = response.get("raw")
            duplicate_of_agent_output = (
                not format_correction
                and self._fingerprint(raw_response)
                in self._agent_output_fingerprints
            )
            if duplicate_of_agent_output and not response.get("error"):
                return ""
            payload = {
                "turn": payload.get("turn"),
                "task_id": payload.get("task_id"),
                "role_id": payload.get("role_id"),
                "agent_id": payload.get("agent_id"),
                "model": payload.get("model"),
                "format_correction": format_correction,
                # The full request remains in context.db and trace exports.
                # Judge evidence needs the actual answer pair, not repeated
                # prompts and JSON schemas from every turn.
                "response": {
                    # The corrected structured answer is already recorded as
                    # the same round's agent_call.raw_output. Avoid sending a
                    # second duplicate copy to the Judge.
                    "raw": (
                        None
                        if format_correction or duplicate_of_agent_output
                        else raw_response
                    ),
                    "error": response.get("error"),
                },
                "response_location": (
                    "same_round_agent_call.raw_output"
                    if format_correction or duplicate_of_agent_output
                    else "this_event.response.raw"
                ),
                "usage": payload.get("usage"),
                "latency_ms": payload.get("latency_ms"),
                "retry_count": payload.get("retry_count"),
            }
        ref = {
            "type": "runtime_event",
            "ref_id": self._ref_id(run_id, "event"),
            "run_id": run_id,
            "source_run_id": event.get("run_id", run_id),
            "event_id": event.get("event_id", ""),
            "event_type": event.get("event_type", ""),
            "role_id": event.get("role_id", ""),
            "round_index": event.get("round_index", 0),
            "payload": payload,
            "source": event.get("source", ""),
            "recorded_at": datetime.now().isoformat(),
        }
        self._evidence.append(ref)
        return ref["ref_id"]

    def _remember_agent_output(self, raw_output: Any) -> None:
        candidates = [raw_output]
        if isinstance(raw_output, dict) and "step_output" in raw_output:
            candidates.append(raw_output["step_output"])
        for candidate in candidates:
            fingerprint = self._fingerprint(candidate)
            if fingerprint:
                self._agent_output_fingerprints.add(fingerprint)

    @staticmethod
    def _compact_agent_input(raw_input: dict[str, Any]) -> dict[str, Any]:
        """Replace repeated reward-history copies with their event join keys."""
        compacted = deepcopy(raw_input)
        public_state = compacted.get("public_state")
        if not isinstance(public_state, dict):
            return compacted
        history = public_state.get("recent_reward_history")
        if not isinstance(history, list):
            return compacted
        public_state["recent_reward_history_rounds"] = [
            item.get("round")
            for item in history
            if isinstance(item, dict) and item.get("round") is not None
        ]
        public_state["recent_reward_history_count"] = len(history)
        public_state["recent_reward_history_evidence"] = (
            "join reward runtime events by run_id and round_index"
        )
        public_state.pop("recent_reward_history", None)
        return compacted

    @staticmethod
    def _fingerprint(value: Any) -> str:
        if value is None:
            return ""
        normalized = value
        if isinstance(normalized, str):
            try:
                normalized = json.loads(normalized)
            except (TypeError, ValueError):
                normalized = normalized.strip()
        try:
            return json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            return str(normalized)

    def record_judge_input(
        self,
        run_id: str,
        case_id: str,
        variant: VARIANT,
        evidence_refs: list[str],
    ) -> str:
        """Record the evidence bundle sent to Judge."""
        ref = {
            "type": "judge_input",
            "ref_id": self._ref_id(run_id, "judge-input"),
            "run_id": run_id,
            "case_id": case_id,
            "variant": variant,
            "evidence_refs": evidence_refs,
            "recorded_at": datetime.now().isoformat(),
        }
        self._evidence.append(ref)
        return ref["ref_id"]

    # ------------------------------------------------------------------
    # Bundle building
    # ------------------------------------------------------------------

    def build_bundle(
        self,
        result: ThreeLayerResult,
    ) -> dict[str, Any]:
        """Build a complete evidence bundle for a ThreeLayerResult."""
        relevant = [
            e for e in self._evidence
            if e.get("run_id") == result.run_id
        ]
        return {
            "run_id": result.run_id,
            "case_id": result.case_id,
            "variant": result.variant,
            "risk_type": result.risk_type,
            "evidence_count": len(relevant),
            "evidence": relevant,
            "model_behavior": result.model_behavior,
            "system_response": result.system_response,
            "final_impact": result.final_impact,
            "objective_metrics": result.objective_metrics,
        }

    def get_all(self) -> list[dict[str, Any]]:
        return list(self._evidence)

    def get_by_ref(self, ref_id: str) -> dict[str, Any]:
        parsed = parse_evidence_ref(ref_id)
        for item in self._evidence:
            if item.get("ref_id") == ref_id:
                if item.get("run_id") != parsed["run_id"]:
                    raise ValueError(
                        f"evidence reference run mismatch: {ref_id}"
                    )
                return dict(item)
        raise KeyError(f"unknown evidence reference: {ref_id}")

    def clear(self) -> None:
        self._evidence.clear()
        self._agent_output_fingerprints.clear()
