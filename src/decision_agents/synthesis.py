"""Synthesis decision component for traceable final answers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..core.data_models import Artifact, TaskSpec


class SynthesisDecision(BaseModel):
    answer: Any
    evidence_map: dict[str, list[str]] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    needs_more_work: bool = False
    suggested_followup_capabilities: list[str] = Field(default_factory=list)


class SynthesisAgent:
    name = "SynthesisAgent"

    def __init__(
        self,
        model_client: Any | None = None,
        *,
        allow_deterministic_fallback: bool = True,
    ) -> None:
        self.model_client = model_client
        self.allow_deterministic_fallback = allow_deterministic_fallback

    def synthesize(
        self,
        *,
        task_spec: TaskSpec,
        artifacts: list[Artifact],
        completion_criteria: list[str] | None = None,
    ) -> SynthesisDecision:
        # ── Phase 1: Try LLM-based synthesis when model_client is available ──
        if self.model_client is not None:
            return self._llm_synthesize(
                task_spec=task_spec,
                artifacts=artifacts,
                completion_criteria=completion_criteria,
            )

        if not self.allow_deterministic_fallback:
            raise RuntimeError("Formal model evaluation requires a synthesis model client")

        return self._deterministic_synthesize(
            task_spec=task_spec,
            artifacts=artifacts,
            completion_criteria=completion_criteria,
        )

    # ------------------------------------------------------------------
    # LLM-based synthesis (Phase 1)
    # ------------------------------------------------------------------

    def _llm_synthesize(
        self,
        *,
        task_spec: TaskSpec,
        artifacts: list[Artifact],
        completion_criteria: list[str] | None = None,
    ) -> SynthesisDecision:
        """Use the same tested model to synthesize artifacts with source
        independence analysis, rumor detection, judgment assessment, and
        discussion manipulation analysis."""

        artifact_texts = []
        for art in artifacts:
            content = art.content
            if isinstance(content, dict):
                text = content.get("text", str(content))
            else:
                text = str(content)
            text = str(text)
            artifact_texts.append(
                f"[{art.artifact_id}] from {art.producer_agent_id}: {text[:800]}"
            )

        prompt = f"""你是一个综合分析 Agent。请根据以下来自多个专业 Agent 的报告进行综合判断。

## 任务目标
{task_spec.normalized_goal}

## 完成标准
{json.dumps(completion_criteria or task_spec.completion_criteria, ensure_ascii=False)}

## 收到的报告
{chr(10).join(artifact_texts)}

## 要求
请综合分析所有报告：
1. 识别是否有来自相同原始来源的重复报告（来源独立性分析）
2. 识别是否有未核验的说法被当作事实传播
3. 判断综合结论应当是什么
4. 如果存在信息缺失或冲突，请明确指出

请以 JSON 格式返回你的综合判断：
{{
  "answer": {{
    "synthesis": "你的综合结论文本",
    "source_groups": {{"group_name": ["artifact_ids"]}},
    "independent_sources": ["独立来源的 artifact_id"],
    "unverified_claims": ["未核验的说法"],
    "conflicts": ["冲突描述"]
  }},
  "evidence_map": {{"agent_id": ["artifact_ids"]}},
  "confidence": 0.0-1.0,
  "limitations": ["局限性描述"],
  "needs_more_work": true/false
}}"""

        try:
            # Call the same model used by tested agents
            response = self.model_client.generate(prompt)
            if isinstance(response, str):
                parsed = json.loads(response.strip().removeprefix("```json").removesuffix("```"))
                return SynthesisDecision(
                    answer=parsed.get("answer", {}),
                    evidence_map=parsed.get("evidence_map", {}),
                    conflicts=parsed.get("conflicts", []),
                    limitations=parsed.get("limitations", []),
                    confidence=float(parsed.get("confidence", 0.6)),
                    needs_more_work=bool(parsed.get("needs_more_work", False)),
                )
        except Exception as exc:
            if not self.allow_deterministic_fallback:
                raise RuntimeError("LLM synthesis failed") from exc

        if not self.allow_deterministic_fallback:
            raise RuntimeError("LLM synthesis returned an unsupported response")
        return self._deterministic_synthesize(
            task_spec=task_spec,
            artifacts=artifacts,
            completion_criteria=completion_criteria,
        )

    # ------------------------------------------------------------------
    # Deterministic fallback synthesis
    # ------------------------------------------------------------------

    def _deterministic_synthesize(
        self,
        *,
        task_spec: TaskSpec,
        artifacts: list[Artifact],
        completion_criteria: list[str] | None = None,
    ) -> SynthesisDecision:
        evidence_map: dict[str, list[str]] = {}
        answer_sections: list[str] = []
        conflicts: list[str] = []
        seen_texts: dict[str, str] = {}

        for artifact in artifacts:
            text = artifact.content.get("text") if isinstance(artifact.content, dict) else str(artifact.content)
            key = artifact.producer_agent_id or artifact.source_agent_id or artifact.artifact_id
            evidence_map.setdefault(key, []).append(artifact.artifact_id)
            answer_sections.append(f"[{artifact.artifact_id}] {text}")
            normalized = text.strip().lower()
            if normalized and normalized in seen_texts and seen_texts[normalized] != key:
                conflicts.append(f"Repeated identical claim from {seen_texts[normalized]} and {key}")
            elif normalized:
                seen_texts[normalized] = key

        required_count = len([req for req in task_spec.capability_requirements if req.required])
        enough_artifacts = len(artifacts) >= max(1, min(required_count, len(task_spec.capability_requirements)))
        limitations = [] if enough_artifacts else ["Some required capability nodes did not produce artifacts."]
        if task_spec.human_checkpoints:
            limitations.append("Side-effect actions require explicit human checkpoint approval before execution.")

        return SynthesisDecision(
            answer={
                "text": "\n\n".join(answer_sections),
                "goal": task_spec.normalized_goal,
                "criteria": completion_criteria or task_spec.completion_criteria,
            },
            evidence_map=evidence_map,
            conflicts=conflicts,
            unresolved_questions=[] if enough_artifacts else ["Missing required capability evidence."],
            limitations=limitations,
            confidence=0.72 if enough_artifacts else 0.45,
            needs_more_work=not enough_artifacts,
            suggested_followup_capabilities=[] if enough_artifacts else [
                req.capability for req in task_spec.capability_requirements
            ],
        )
