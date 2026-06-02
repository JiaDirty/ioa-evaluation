"""IoA 测评环境核心数据模型。

所有组件共享的数据结构定义，包括 Agent 注册信息、任务、审计日志、
协议消息等。基于 Pydantic v2 实现，支持序列化/反序列化和校验。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

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


# ============================================================
# Agent 注册相关
# ============================================================

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

    # 真实能力（用于测试对比，正常情况下不暴露）
    actual_capabilities: list[str] | None = None

    # 信任体系
    certificate: str | None = None
    reputation_score: float = Field(default=0.5, ge=0.0, le=1.0)
    permission_scope: list[str] = Field(default_factory=list)
    registration_time: datetime = Field(default_factory=datetime.now)
    last_active: datetime = Field(default_factory=datetime.now)
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

class Task(BaseModel):
    """任务定义。"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    task_type: TaskType
    description: str
    required_capabilities: list[str] = Field(default_factory=list)
    priority_factors: dict[str, float] = Field(
        default_factory=lambda: {"capability": 0.4, "reputation": 0.3, "cost": 0.2, "risk": 0.1}
    )
    max_hops: int = 3
    timeout: int = 300
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    # 任务负载（具体数据）
    payload: dict[str, Any] = Field(default_factory=dict)


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
    content: Any
    content_type: str = "text"
    source_agent_id: str = ""
    source_task_id: str = ""
    hash_value: str = ""
    safe: bool = False  # 是否经过安全净化
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    def model_post_init(self, __context: Any) -> None:
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
    step_index: int
    timestamp: datetime = Field(default_factory=datetime.now)
    action: AuditAction

    # 主体信息
    agent_id: str
    sub_ioa_id: str
    gateway_id: str | None = None

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
    details: dict[str, Any] = Field(default_factory=dict)


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
