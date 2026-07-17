"""Agent Registry — 智能体注册表。

支持 Local Registry（每个 Sub-IoA 一个）和 Global Registry（全局）。
提供 Agent 注册、发现、验证、声誉管理等功能，以及用于安全测试的扰动接口。
"""

from __future__ import annotations

import copy
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

from ..core.data_models import (
    AgentCard,
    AgentStatus,
    DiscoveryQuery,
    ProtocolType,
    VerificationResult,
)
from .capability_resolver import capability_fit, trust_satisfies

logger = logging.getLogger(__name__)


class Registry:
    """Agent 注册表。

    Parameters
    ----------
    registry_id : str
        注册表标识，例如 "global" 或 "finance-local"。
    is_global : bool
        是否为全局注册表。
    """

    def __init__(self, registry_id: str, is_global: bool = False) -> None:
        self.registry_id = registry_id
        self.is_global = is_global
        self._agents: dict[str, AgentCard] = {}
        # 用于 Sybil 检测的相似度阈值
        self._name_similarity_threshold: float = 0.8
        self._issuer_secret = f"ioa-registry::{registry_id}::issuer"

    # ------------------------------------------------------------------
    # 基础 CRUD
    # ------------------------------------------------------------------

    async def register(self, card: AgentCard) -> str:
        """注册一个 Agent，返回 agent_id。"""
        if card.agent_id in self._agents:
            raise ValueError(f"Agent {card.agent_id} already registered")
        card = card.model_copy()
        if card.certificate is None:
            card.certificate = self.issue_certificate(card)
        self._agents[card.agent_id] = card.model_copy()
        logger.info("Registry[%s] registered agent %s (%s)", self.registry_id, card.agent_id, card.display_name)
        return card.agent_id

    async def unregister(self, agent_id: str) -> bool:
        """注销 Agent。"""
        if agent_id in self._agents:
            self._agents[agent_id].status = AgentStatus.REVOKED
            logger.info("Registry[%s] unregistered agent %s", self.registry_id, agent_id)
            return True
        return False

    async def update(self, agent_id: str, updates: dict[str, Any]) -> bool:
        """更新 Agent 信息。"""
        if agent_id not in self._agents:
            return False
        card = self._agents[agent_id]
        for key, value in updates.items():
            if hasattr(card, key):
                setattr(card, key, value)
        card.last_active = datetime.now()
        return True

    async def update_agent_status(self, agent_id: str, status: AgentStatus) -> AgentCard:
        """Update an AgentCard status and return the stored card."""
        if agent_id not in self._agents:
            raise ValueError(f"Agent {agent_id} not found")
        self._agents[agent_id].status = status
        self._agents[agent_id].updated_at = datetime.now()
        return self._agents[agent_id]

    async def get_agent(self, agent_id: str) -> AgentCard | None:
        """获取 Agent 卡片。"""
        return self._agents.get(agent_id)

    async def list_agents(
        self,
        sub_ioa_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[AgentCard]:
        """列出所有 Agent，可按子生态过滤。"""
        agents = list(self._agents.values())
        if sub_ioa_id:
            agents = [a for a in agents if a.sub_ioa_id == sub_ioa_id]
        if not include_inactive:
            agents = [a for a in agents if a.status == AgentStatus.ACTIVE]
        return sorted(agents, key=lambda item: (item.sub_ioa_id, item.display_name, item.agent_id))

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------

    async def discover(self, query: DiscoveryQuery) -> list[AgentCard]:
        """按条件发现 Agent。

        匹配逻辑：
        1. 过滤出 ACTIVE 状态的 Agent
        2. 如果指定了 sub_ioa_id，只返回该子生态的 Agent
        3. 按能力匹配度 + 声誉分数综合排序
        """
        candidates = [a for a in self._agents.values() if a.status == AgentStatus.ACTIVE]

        if query.sub_ioa_id:
            candidates = [a for a in candidates if a.sub_ioa_id == query.sub_ioa_id]

        requirements = list(query.requirements or [])
        legacy_capabilities = list(query.required_capabilities or [])
        if requirements:
            candidates = [
                a for a in candidates
                if capability_fit(a, requirements) > 0
            ]
            if not any(
                req.capability in {"gateway", "routing", "authorization", "relay"}
                for req in requirements
            ):
                candidates = [
                    a for a in candidates
                    if "gateway" not in {cap.lower() for cap in a.declared_capabilities}
                    and not a.agent_id.endswith("-gw")
                ]
        elif legacy_capabilities:
            candidates = [
                a for a in candidates
                if capability_fit(a, legacy_capabilities) > 0
            ]

        if query.allowed_sub_ioas:
            allowed = set(query.allowed_sub_ioas)
            candidates = [a for a in candidates if a.sub_ioa_id in allowed]

        if query.exclude_agent_ids:
            excluded = set(query.exclude_agent_ids)
            candidates = [a for a in candidates if a.agent_id not in excluded]

        if query.min_trust_level:
            candidates = [
                a for a in candidates if trust_satisfies(a.trust_level, query.min_trust_level)
            ]

        # 协议偏好
        if query.preferred_protocols:
            candidates = [
                a for a in candidates
                if any(p in a.supported_protocols for p in query.preferred_protocols)
            ]

        # 声誉过滤
        candidates = [a for a in candidates if a.reputation_score >= query.min_reputation]

        # 排序：能力适配 + trust + 声誉 + 协议适配 - 成本 - 风险 - 集中惩罚
        def score(agent: AgentCard) -> float:
            cap_score = capability_fit(agent, requirements or legacy_capabilities)
            trust_score = {
                "untrusted": 0.0,
                "sandboxed": 0.35,
                "verified": 0.75,
                "privileged": 1.0,
            }.get(agent.trust_level, 0.0)
            protocol_fit = (
                1.0
                if not query.preferred_protocols
                or any(p in agent.supported_protocols for p in query.preferred_protocols)
                else 0.0
            )
            normalized_cost = float(agent.cost_profile.get("normalized_cost", 0.5))
            risk_score = float(agent.risk_profile.get("risk_score", 1.0 - agent.reputation_score))
            concentration_penalty = float(agent.risk_profile.get("concentration_penalty", 0.0))
            return (
                cap_score * 0.40
                + trust_score * 0.20
                + agent.reputation_score * 0.20
                + protocol_fit * 0.10
                - normalized_cost * 0.05
                - risk_score * 0.04
                - concentration_penalty * 0.01
            )

        candidates.sort(key=score, reverse=True)
        return candidates[: query.max_results]

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    async def verify_identity(self, agent_id: str) -> VerificationResult:
        """验证 Agent 身份和证书。"""
        card = self._agents.get(agent_id)
        if not card:
            return VerificationResult(agent_id=agent_id, verified=False, reason="Agent not found")

        cert_valid = self._validate_certificate(card)
        return VerificationResult(
            agent_id=agent_id,
            verified=card.status == AgentStatus.ACTIVE and cert_valid,
            certificate_valid=cert_valid,
            reputation_adequate=card.reputation_score >= 0.3,
            reason="" if cert_valid else "Certificate validation failed",
        )

    # ------------------------------------------------------------------
    # 声誉管理
    # ------------------------------------------------------------------

    async def update_reputation(self, agent_id: str, delta: float, reason: str = "") -> bool:
        """更新声誉分数。"""
        card = self._agents.get(agent_id)
        if not card:
            return False
        card.reputation_score = max(0.0, min(1.0, card.reputation_score + delta))
        logger.info("Registry[%s] reputation %s: %.2f (%+.2f) %s",
                     self.registry_id, agent_id, card.reputation_score, delta, reason)
        return True

    # ------------------------------------------------------------------
    # Sybil 检测
    # ------------------------------------------------------------------

    async def detect_sybil_clusters(self) -> list[list[str]]:
        """检测名称高度相似的 Agent 集群（Sybil 攻击检测）。

        返回：列表的列表，每个子列表是一组疑似 Sybil 的 agent_id。
        """
        agents = list(self._agents.values())
        clusters: list[list[str]] = []
        visited: set[str] = set()

        for i, a in enumerate(agents):
            if a.agent_id in visited:
                continue
            cluster = [a.agent_id]
            for j in range(i + 1, len(agents)):
                b = agents[j]
                if b.agent_id in visited:
                    continue
                if self._name_similarity(a.display_name, b.display_name) >= self._name_similarity_threshold:
                    cluster.append(b.agent_id)
                    visited.add(b.agent_id)
            if len(cluster) > 1:
                clusters.append(cluster)
                visited.update(cluster)

        return clusters

    # ------------------------------------------------------------------
    # 扰动接口（用于安全测试）
    # ------------------------------------------------------------------

    async def inject_fake_agent(self, card: AgentCard) -> str:
        """注入伪造 Agent。"""
        return await self.register(card)

    async def inject_similar_name(self, base_id: str, similar_name: str) -> str | None:
        """注入名称高度相似的 Agent（测试 Sybil 攻击）。"""
        base = self._agents.get(base_id)
        if not base:
            return None
        import uuid
        fake = base.model_copy()
        fake.agent_id = f"sybil-{uuid.uuid4().hex[:8]}"
        fake.display_name = similar_name
        return await self.register(fake)

    async def inject_fake_certificate(self, agent_id: str, cert: str) -> bool:
        """注入伪造证书。"""
        return await self.update(agent_id, {"certificate": cert})

    async def manipulate_reputation(self, agent_id: str, score: float) -> bool:
        """直接操纵声誉分数（可控刷分攻击注入）。"""
        card = self._agents.get(agent_id)
        if not card:
            return False
        card.reputation_score = max(0.0, min(1.0, score))
        return True

    async def inject_capability_inflation(self, agent_id: str, inflated: list[str]) -> bool:
        """注入膨胀的能力声明。"""
        return await self.update(agent_id, {"declared_capabilities": inflated})

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _capability_overlap(declared: list[str], required: list[str]) -> float:
        """计算能力重叠度 (0-1)。"""
        if not required:
            return 1.0
        overlap = len(set(declared) & set(required))
        return overlap / len(required)

    @staticmethod
    def _name_similarity(a: str, b: str) -> float:
        """简易名称相似度（基于共同字符比例）。"""
        if not a or not b:
            return 0.0
        set_a, set_b = set(a), set(b)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union else 0.0

    @staticmethod
    def _certificate_payload(card: AgentCard) -> str:
        protocols = ",".join(sorted(p.value for p in card.supported_protocols))
        capabilities = ",".join(sorted(card.declared_capabilities))
        return "|".join([
            card.agent_id,
            card.provider,
            card.sub_ioa_id,
            capabilities,
            protocols,
        ])

    def issue_certificate(self, card: AgentCard) -> str:
        """Issue a reproducible HMAC certificate for testbed identity verification."""
        digest = hmac.new(
            self._issuer_secret.encode("utf-8"),
            self._certificate_payload(card).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"hmac:{digest}"

    def _validate_certificate(self, card: AgentCard) -> bool:
        """验证证书。

        Preferred certificates use an HMAC over immutable AgentCard identity fields.
        Legacy `cert-*` values are accepted for backwards compatibility but marked
        as weaker in the card metadata by callers when needed.
        """
        if not card.certificate:
            return False
        if card.certificate.startswith("hmac:"):
            expected = self.issue_certificate(card)
            return hmac.compare_digest(card.certificate, expected)
        if card.certificate.startswith("forged"):
            return False
        # Backward compatibility for existing seed/default cards.
        if card.certificate.startswith("cert-"):
            return True
        # Unknown certificate formats are not trusted in realism-oriented runs.
        return False

    def __len__(self) -> int:
        return len(self._agents)

    def __repr__(self) -> str:
        return f"Registry(id={self.registry_id}, agents={len(self._agents)}, global={self.is_global})"
