"""Prompt-to-TaskSpec decision component for agentic execution."""

from __future__ import annotations

import json
from typing import Any

from ..core.data_models import (
    CapabilityRequirement,
    DeliverableSpec,
    HumanCheckpointSpec,
    TaskConstraints,
    TaskSpec,
)


class TaskSpecificationAgent:
    """Create a structured TaskSpec from a natural language prompt.

    Live runs may inject a model client. Offline deterministic runs use the same
    output schema and a reproducible semantic extractor so tests still exercise
    the agentic state machine instead of scripted routes.
    """

    name = "TaskSpecificationAgent"

    def __init__(
        self,
        model_client: Any | None = None,
        *,
        max_repair_attempts: int = 2,
        require_model: bool = False,
    ) -> None:
        self.model_client = model_client
        self.max_repair_attempts = max_repair_attempts
        self.require_model = require_model

    def specify(
        self,
        *,
        prompt: str,
        constraints: TaskConstraints | dict[str, Any] | None = None,
        user_goal: str = "",
        available_capabilities: list[str] | None = None,
    ) -> TaskSpec:
        constraint_model = (
            constraints
            if isinstance(constraints, TaskConstraints)
            else TaskConstraints.model_validate(constraints or {})
        )
        if self.model_client is not None:
            parsed = self._try_model(
                prompt,
                constraint_model,
                user_goal,
                available_capabilities=available_capabilities or [],
            )
            if parsed is not None:
                return parsed
        if self.require_model:
            raise RuntimeError(
                "Live TaskSpecificationAgent did not produce a valid model-backed TaskSpec"
            )
        return self._deterministic_spec(prompt, constraint_model, user_goal)

    def _try_model(
        self,
        prompt: str,
        constraints: TaskConstraints,
        user_goal: str,
        *,
        available_capabilities: list[str],
    ) -> TaskSpec | None:
        system = (
            "You are TaskSpecificationAgent. Return strict JSON matching TaskSpec. "
            "Do not choose Agent IDs, endpoints, target_sub_ioas, or hop_chain. "
            "Every capability_requirements[].capability must exactly match one item "
            "from available_capabilities. Keep the complete plan within max_plan_nodes, "
            "including policy precheck, human checkpoints, and synthesis."
        )
        previous_error = ""
        for _ in range(self.max_repair_attempts + 1):
            user = json.dumps(
                {
                    "prompt": prompt,
                    "user_goal": user_goal,
                    "constraints": constraints.model_dump(mode="json"),
                    "available_capabilities": available_capabilities,
                    "previous_validation_error": previous_error,
                    "schema": TaskSpec.model_json_schema(),
                },
                ensure_ascii=False,
            )
            try:
                if hasattr(self.model_client, "generate_json"):
                    payload = self.model_client.generate_json(system, user)
                else:
                    raw = self.model_client.generate_with_system(system, user)
                    payload = json.loads(str(raw).strip())
                spec = TaskSpec.model_validate(payload)
                self._reject_route_leakage(spec)
                self._validate_capabilities(spec, available_capabilities)
                if not self._human_checkpoint_allowed(prompt, constraints):
                    spec.human_checkpoints = []
                spec.capability_requirements = self._trim_requirements(
                    spec.capability_requirements,
                    constraints,
                    len(spec.human_checkpoints),
                )
                return spec
            except Exception as exc:
                previous_error = str(exc)
                continue
        return None

    @staticmethod
    def _human_checkpoint_allowed(prompt: str, constraints: TaskConstraints) -> bool:
        if constraints.human_approval_required:
            return True
        if not constraints.human_approval_for_side_effects:
            return False
        text = prompt.lower()
        side_effect_markers = [
            "execute payment",
            "make payment",
            "place order",
            "purchase now",
            "book and pay",
            "wire transfer",
            "执行付款",
            "进行付款",
            "立即付款",
            "执行购买",
            "立即购买",
            "预订并支付",
            "执行交易",
            "进行交易",
            "下单",
            "转账",
        ]
        return any(marker in text for marker in side_effect_markers)

    @staticmethod
    def _validate_capabilities(spec: TaskSpec, available_capabilities: list[str]) -> None:
        if not available_capabilities:
            return
        allowed = set(available_capabilities)
        unknown = sorted({
            requirement.capability
            for requirement in spec.capability_requirements
            if requirement.capability not in allowed
        })
        if unknown:
            raise ValueError(
                f"TaskSpec used capabilities not present in the live registry: {unknown}"
            )

    def _deterministic_spec(
        self, prompt: str, constraints: TaskConstraints, user_goal: str
    ) -> TaskSpec:
        text = prompt.lower()
        requirements: list[CapabilityRequirement] = []

        def has_any(tokens: list[str]) -> bool:
            return any(token in text for token in tokens)

        def add(capability: str, description: str, expected: str = "") -> None:
            if capability in {req.capability for req in requirements}:
                return
            requirements.append(
                CapabilityRequirement(
                    capability=capability,
                    semantic_description=description,
                    expected_output=expected or description,
                )
            )

        if has_any(["travel", "trip", "itinerary", "flight", "hotel", "booking", "差旅", "出差", "行程", "航班", "酒店", "预订"]):
            add("itinerary_planning", "Plan a safe and practical trip itinerary.", "travel plan")
            add("logistics", "Coordinate travel logistics and constraints.", "logistics notes")
        if has_any(["health risk", "public health", "vaccine", "malaria", "健康风险", "公共卫生", "疫苗", "疟疾", "肯尼亚"]):
            add("public_health", "Assess destination health and public health risks.", "health risk assessment")
        if has_any(["health", "medical", "clinical", "trial", "biotech", "pharma", "医疗", "临床", "试验", "制药", "生物科技", "医疗技术"]):
            add("clinical_analysis", "Assess clinical, medical, or trial evidence relevant to the task.", "clinical evidence assessment")
        if has_any(["insurance", "coverage", "保险"]):
            add("travel_insurance", "Compare travel insurance options and coverage.", "insurance comparison")
        if has_any(["investment", "financial", "finance", "market", "merger", "m&a", "投资", "财务", "金融", "市场", "并购风险", "收购", "兼并", "资产"]):
            add("financial_analysis", "Analyze financial and market risk.", "financial risk analysis")
        if has_any(["advice", "recommend", "portfolio", "allocation", "trade", "操作方案", "投资建议", "推荐", "资产配置", "理财", "调仓"]):
            add("investment_advice", "Compare investment options and produce decision-support recommendations.", "investment recommendation")
        if has_any(["payment", "pay", "permission", "approval", "付款", "支付", "权限", "审核"]):
            add("payment_review", "Review payment, booking, or approval authority before side effects.", "payment and authority review")
        if has_any(["risk", "风险"]):
            add("risk_assessment", "Assess risk factors and mitigation options.", "risk assessment")
        if has_any(["news", "rumor", "sentiment", "public opinion", "opinion", "policy", "舆情", "新闻", "谣言", "观点", "政策", "讨论", "摘要"]):
            add("news_aggregation", "Gather and summarize relevant news signals.", "news summary")
        if has_any(["sentiment", "public opinion", "monitor", "舆情", "监控", "观点", "讨论"]):
            add("sentiment_analysis", "Assess sentiment, discussion dynamics, and public-opinion signals.", "sentiment and discussion summary")
        if has_any(["fact", "verify", "source", "provenance", "citation", "核查", "来源", "引用", "事实"]):
            add("fact_checking", "Verify claims and source reliability.", "source verification")
        if has_any(["workflow", "process", "automation", "跨域", "自动化", "流程", "操作"]):
            add("general_analysis", "Analyze the cross-domain process request and produce a practical plan.", "process analysis")

        checkpoints: list[HumanCheckpointSpec] = []
        side_effect_or_high_impact = has_any([
            "buy", "purchase", "book", "trade", "payment", "pay",
            "购买", "预订", "下单", "交易", "付款", "支付", "操作方案", "资产配置", "自动",
        ])
        if constraints.human_approval_for_side_effects and side_effect_or_high_impact:
            checkpoints.append(
                HumanCheckpointSpec(
                    checkpoint_id="approval-side-effect",
                    trigger="before_side_effect",
                    reason="Prompt or constraints require user confirmation before side effects.",
                )
            )
        elif constraints.human_approval_required:
            checkpoints.append(
                HumanCheckpointSpec(
                    checkpoint_id="approval-required",
                    trigger="before_high_impact_decision",
                    reason="Task constraints require explicit human approval.",
                )
            )

        requirements = self._trim_requirements(requirements, constraints, len(checkpoints))
        if not requirements:
            add("general_analysis", "Analyze the request and produce a useful answer.", "analysis")

        complexity = (
            "multi_stage"
            if len(requirements) >= 3 or checkpoints
            else ("multi_agent" if len(requirements) > 1 else "single_agent")
        )
        return TaskSpec(
            normalized_goal=user_goal or prompt,
            intent="agentic_task_execution",
            deliverables=[
                DeliverableSpec(
                    deliverable_id="final-answer",
                    description="Final answer with source mapping, limitations, and safety notes.",
                )
            ],
            capability_requirements=requirements,
            constraints=constraints,
            completion_criteria=[
                "All required capability nodes either complete with traceable artifacts or fail closed.",
                "Final answer cites source artifact IDs and preserves human approval boundaries.",
            ],
            human_checkpoints=checkpoints,
            estimated_complexity=complexity,
            rationale="Offline deterministic TaskSpec extraction from the natural language prompt.",
            confidence=0.68,
        )

    @staticmethod
    def _reject_route_leakage(spec: TaskSpec) -> None:
        raw = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False).lower()
        forbidden = ["hop_chain", "target_sub_ioas", "endpoint", "agent_id"]
        leaked = [item for item in forbidden if item in raw]
        if leaked:
            raise ValueError(f"TaskSpec leaks scripted routing fields: {leaked}")

    @staticmethod
    def _trim_requirements(
        requirements: list[CapabilityRequirement],
        constraints: TaskConstraints,
        checkpoint_count: int,
    ) -> list[CapabilityRequirement]:
        max_requirements = max(1, constraints.max_plan_nodes - 2 - checkpoint_count)
        if len(requirements) <= max_requirements:
            return requirements

        priority = {
            "clinical_analysis": 100,
            "public_health": 96,
            "itinerary_planning": 94,
            "travel_insurance": 92,
            "financial_analysis": 90,
            "investment_advice": 88,
            "payment_review": 86,
            "fact_checking": 84,
            "news_aggregation": 80,
            "sentiment_analysis": 78,
            "logistics": 55,
            "risk_assessment": 45,
            "general_analysis": 10,
        }
        indexed = list(enumerate(requirements))
        selected = sorted(
            indexed,
            key=lambda item: (priority.get(item[1].capability, 50), -item[0]),
            reverse=True,
        )[:max_requirements]
        return [requirement for _, requirement in sorted(selected, key=lambda item: item[0])]
