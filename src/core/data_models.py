"""IoA 测评环境核心数据模型。

所有组件共享的数据结构定义，包括 Agent 注册信息、任务、审计日志、
协议消息等。基于 Pydantic v2 实现，支持序列化/反序列化和校验。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ============================================================
# 枚举类型
# ============================================================

class AgentStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class ProtocolType(str, Enum):
    A2A = "a2a"
    MCP = "mcp"
    PRIVATE_API = "private_api"


class TaskType(str, Enum):
    SINGLE_DOMAIN = "single_domain"
    CROSS_DOMAIN = "cross_domain"
    MULTI_HOP = "multi_hop"
    ARTIFACT_REUSE = "artifact_reuse"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditAction(str, Enum):
    DISCOVER = "discover"
    CALL = "call"
    RELAY = "relay"
    DELEGATE = "delegate"
    AGGREGATE = "aggregate"
    REGISTER = "register"
    AUTH_CHECK = "auth_check"
    PROTOCOL_NEGOTIATE = "protocol_negotiate"
    SECURITY_CHECK = "security_check"
    DECISION_AGENT = "decision_agent"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvaluationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"


class GatewayPipelineStage(str, Enum):
    TASK_INTAKE = "task_intake"
    TASK_UNDERSTANDING = "task_understanding"
    PERMISSION_ANALYSIS = "permission_analysis"
    POLICY_ENFORCEMENT = "policy_enforcement"
    LOCAL_DISCOVERY = "local_discovery"
    CROSS_DOMAIN_DISCOVERY = "cross_domain_discovery"
    CANDIDATE_RANKING = "candidate_ranking"
    CANDIDATE_VERIFICATION = "candidate_verification"
    PROTOCOL_SEMANTICS = "protocol_semantics"
    PROTOCOL_NEGOTIATION = "protocol_negotiation"
    PRE_DELIVERY_SECURITY = "pre_delivery_security"
    HTTP_DELIVERY = "http_delivery"
    POST_DELIVERY_SECURITY = "post_delivery_security"
    ARTIFACT_AGGREGATION = "artifact_aggregation"
    AUDIT_FINALIZATION = "audit_finalization"


class ActorType(str, Enum):
    TESTCASE = "testcase"
    MARKETPLACE = "marketplace"
    GATEWAY = "gateway"
    REGISTRY = "registry"
    DECISION_AGENT = "decision_agent"
    DOMAIN_AGENT = "domain_agent"
    POLICY_ENGINE = "policy_engine"
    PROTOCOL_ADAPTER = "protocol_adapter"
    JUDGE = "judge"


# ============================================================
# Agent 注册相关
# ============================================================

class EvidenceRef(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    evidence_type: str = ""
    uri: str = ""
    hash_value: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityClaim(BaseModel):
    capability_id: str
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    safety_profile: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    declared_by: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ProtocolSupport(BaseModel):
    protocol: ProtocolType
    version: str = "1.0"
    binding: str = "HTTP"
    security_level: Literal["low", "medium", "high"] = "medium"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndpointDescriptor(BaseModel):
    url: str
    protocol: ProtocolType = ProtocolType.A2A
    method: str = "POST"
    allowlisted: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthDescriptor(BaseModel):
    scheme: str = "bearer"
    required_scopes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CertificateDescriptor(BaseModel):
    certificate_id: str = ""
    issuer: str = ""
    fingerprint: str = ""
    valid: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SignatureDescriptor(BaseModel):
    signer: str
    algorithm: str = "testbed-hmac-sha256"
    value: str
    created_at: datetime = Field(default_factory=datetime.now)


class AgentCardProvenance(BaseModel):
    registered_by: str = ""
    registration_surface: str = "registry"
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCard(BaseModel):
    """Agent 注册卡片，存放于 Registry 中。"""
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    display_name: str
    provider: str
    sub_ioa_id: str

    # 能力声明
    declared_capabilities: list[str] = Field(default_factory=list)
    supported_protocols: list[ProtocolType] = Field(default_factory=lambda: [ProtocolType.A2A])
    endpoint: str = ""
    protocol_versions: dict[str, str] = Field(default_factory=dict)
    capability_claims: list[CapabilityClaim] = Field(default_factory=list)
    protocol_support: list[ProtocolSupport] = Field(default_factory=list)
    endpoint_descriptor: EndpointDescriptor | None = None
    auth: AuthDescriptor = Field(default_factory=AuthDescriptor)

    # 真实能力（用于测试对比，正常情况下不暴露）
    actual_capabilities: list[str] | None = None
    verified_capabilities: list[str] = Field(default_factory=list)
    trust_level: Literal["untrusted", "sandboxed", "verified", "privileged"] = "untrusted"

    # 信任体系
    certificate: str | None = None
    certificate_descriptor: CertificateDescriptor | None = None
    signatures: list[SignatureDescriptor] = Field(default_factory=list)
    provenance: AgentCardProvenance = Field(default_factory=AgentCardProvenance)
    reputation_score: float = Field(default=0.5, ge=0.0, le=1.0)
    permission_scope: list[str] = Field(default_factory=list)
    registration_time: datetime = Field(default_factory=datetime.now)
    last_active: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    status: AgentStatus = AgentStatus.ACTIVE


class DiscoveryQuery(BaseModel):
    """Agent 发现查询条件。"""
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_protocols: list[ProtocolType] = Field(default_factory=list)
    min_reputation: float = 0.0
    sub_ioa_id: str | None = None
    max_results: int = 10


class VerificationResult(BaseModel):
    """身份验证结果。"""
    agent_id: str
    verified: bool
    certificate_valid: bool = False
    capability_match: bool = False
    reputation_adequate: bool = False
    reason: str = ""


# ============================================================
# 任务相关
# ============================================================

class HumanApproval(BaseModel):
    required: bool = False
    granted: bool = False
    approval_token: str | None = None
    approver_id: str | None = None
    reason: str = ""
    checked_at: datetime | None = None


class TaskConstraints(BaseModel):
    min_protocol_security_level: Literal["high", "medium", "low"] = "medium"
    allowed_protocols: list[ProtocolType] = Field(
        default_factory=lambda: [ProtocolType.A2A, ProtocolType.MCP, ProtocolType.PRIVATE_API]
    )
    forbidden_protocols: list[str] = Field(default_factory=list)
    max_delegation_depth: int = 2
    human_approval_required: bool = False
    audit_required: bool = True
    allow_knowledge_write: bool = False
    allow_cross_domain_relay: bool = False
    forbidden_data_classes: list[str] = Field(default_factory=list)
    require_provenance: bool = True
    require_citations: bool = False


class Task(BaseModel):
    """任务定义。"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    test_case_id: str | None = None
    user_id: str = "user"
    origin_sub_ioa: str | None = None
    target_sub_ioas: list[str] = Field(default_factory=list)
    task_type: TaskType
    description: str
    user_goal: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    user_grants: list[str] = Field(default_factory=list)
    human_approval: HumanApproval | None = None
    prior_artifacts: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    trace_id: str = ""
    priority_factors: dict[str, float] = Field(
        default_factory=lambda: {"capability": 0.4, "reputation": 0.3, "cost": 0.2, "risk": 0.1}
    )
    max_hops: int = 3
    timeout: int = 300
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    # 任务负载（具体数据）
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskEnvelope(Task):
    """Design-doc compatible task envelope; extends the runtime Task model."""


class TaskResult(BaseModel):
    """任务执行结果。"""
    task_id: str
    status: TaskStatus
    output: Any = None
    artifacts: list[Artifact] = Field(default_factory=list)
    error: str | None = None
    execution_time: float = 0.0
    participating_agents: list[str] = Field(default_factory=list)


# ============================================================
# 产物（Artifact）相关
# ============================================================

class Artifact(BaseModel):
    """任务产出的产物，支持来源追踪。"""
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    task_id: str = ""
    producer_agent_id: str = ""
    protocol: str = ""
    content: Any
    content_type: str = "text"
    source_agent_id: str = ""
    source_task_id: str = ""
    hash_value: str = ""
    safe: bool = False  # 是否经过安全净化
    provenance: list[EvidenceRef] = Field(default_factory=list)
    safety_labels: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    def model_post_init(self, __context: Any) -> None:
        if not self.task_id and self.source_task_id:
            self.task_id = self.source_task_id
        if not self.producer_agent_id and self.source_agent_id:
            self.producer_agent_id = self.source_agent_id
        if not self.hash_value:
            import hashlib
            content_str = str(self.content)
            self.hash_value = hashlib.sha256(content_str.encode()).hexdigest()[:16]


# ============================================================
# 审计日志相关
# ============================================================

class AuditEntry(BaseModel):
    """审计日志条目。"""
    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    trace_id: str  # 任务调用链 ID
    task_id: str | None = None
    step_index: int
    timestamp: datetime = Field(default_factory=datetime.now)
    action: AuditAction

    # 主体信息
    agent_id: str
    sub_ioa_id: str
    gateway_id: str | None = None
    actor_type: ActorType | None = None
    actor_id: str = ""

    # 授权与协议
    auth_scope: list[str] = Field(default_factory=list)
    protocol_type: ProtocolType | None = None

    # 产物
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)

    # 关联
    parent_trace_id: str | None = None
    target_agent_id: str | None = None

    # 额外信息
    input_hash: str | None = None
    output_hash: str | None = None
    decision_summary: str | None = None
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.task_id is None:
            self.task_id = self.trace_id
        if not self.actor_id:
            self.actor_id = self.agent_id
        if not self.structured_payload and self.details:
            self.structured_payload = self.details


class AuditEvent(AuditEntry):
    """Design-doc compatible audit event alias for append-only evidence."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    event_type: str = ""

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if not self.event_type:
            self.event_type = self.action.value


class AuditMetrics(BaseModel):
    """审计指标。"""
    chain_completeness: float = 0.0  # 调用链完整率
    attribution_accuracy: float = 0.0  # 归因准确率
    source_coverage: float = 0.0  # 来源覆盖率
    total_entries: int = 0
    total_traces: int = 0


# ============================================================
# 协议消息相关
# ============================================================

class ProtocolMessage(BaseModel):
    """跨协议通信消息。"""
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    source_protocol: ProtocolType
    target_protocol: ProtocolType
    source_agent_id: str
    target_agent_id: str
    trace_id: str = ""

    # 消息内容
    method: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None

    # 协议特有字段
    headers: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NegotiationResult(BaseModel):
    """协议协商结果。"""
    success: bool
    agreed_protocol: ProtocolType | None = None
    protocol_version: str = ""
    fallback_used: bool = False
    downgrade_detected: bool = False
    reason: str = ""


# ============================================================
# 认证与授权相关
# ============================================================

class AuthResult(BaseModel):
    """授权结果。"""
    authorized: bool
    granted_scope: list[str] = Field(default_factory=list)
    reason: str = ""
    delegation_depth: int = 0
    scope_expansion_detected: bool = False  # 是否检测到越权


class PolicyTicket(BaseModel):
    ticket_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    task_id: str
    allowed: bool
    reason: str = ""
    granted_scopes: list[str] = Field(default_factory=list)
    denied_scopes: list[str] = Field(default_factory=list)
    effective_scopes: list[str] = Field(default_factory=list)
    human_approval_checked: bool = False
    expires_at: datetime = Field(default_factory=datetime.now)
    verifier_version: str = "ioa-policy-v1"


# ============================================================
# 测试相关
# ============================================================

class TestResult(BaseModel):
    """风险测试结果。"""
    test_id: str
    test_name: str
    category: str
    passed: bool
    status: EvaluationStatus = EvaluationStatus.VALID
    risk_level: RiskLevel
    confidence: float = 0.0
    explanation: str = ""
    metrics: dict[str, float] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    realism: dict[str, Any] = Field(default_factory=dict)
    execution_time: float = 0.0
