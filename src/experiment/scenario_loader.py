"""Scenario Loader — 场景加载器。

将 seed JSON 数据文件加载到 IoAEnvironment，实现数据-框架耦合。

用法：
    loader = ScenarioLoader("data/seeds/seed_001_identity_spoofing.json")
    scenario = loader.load()
    await env.setup_from_scenario(scenario)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.data_models import ProtocolType, RiskLevel, TaskType

logger = logging.getLogger(__name__)


# ============================================================
# Scenario Data Classes
# ============================================================

@dataclass
class ScenarioAgent:
    agent_id: str
    display_name: str
    provider: str
    capabilities: list[str]
    actual_capabilities: list[str]
    protocols: list[str]
    reputation_score: float
    permission_scope: list[str]
    status: str = "active"
    llm_model: str | None = None


@dataclass
class ScenarioTool:
    tool_name: str
    tool_type: str
    description: str
    risk_level: str = "LOW"


@dataclass
class ScenarioSubIoA:
    sub_ioa_id: str
    agents: list[ScenarioAgent] = field(default_factory=list)
    tools: list[ScenarioTool] = field(default_factory=list)


@dataclass
class ScenarioTopology:
    style: str = "full_mesh"
    edges: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ScenarioKnowledge:
    enabled: bool = True
    pre_existing_entries: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ScenarioEnvironment:
    sub_ioas: list[ScenarioSubIoA] = field(default_factory=list)
    topology: ScenarioTopology = field(default_factory=ScenarioTopology)
    shared_knowledge: ScenarioKnowledge = field(default_factory=ScenarioKnowledge)


@dataclass
class ScenarioRisk:
    dimension: str
    sub_dimension: str
    dimension_cn: str
    sub_dimension_cn: str
    risk_level: str
    description: str


@dataclass
class ScenarioTask:
    task_type: str
    description: str
    required_capabilities: list[str] = field(default_factory=list)
    priority_factors: dict[str, float] = field(default_factory=dict)
    max_hops: int = 3
    timeout: int = 60
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioAttack:
    attack_type: str
    method: str
    target_component: str
    target_sub_ioa: str
    target_agent_id: str | None = None
    goal: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    pair_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioDefense:
    expected_mechanisms: list[dict[str, Any]] = field(default_factory=list)
    audit_requirements: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioExpected:
    attack_should_succeed: bool = False
    completion_criteria: dict[str, Any] = field(default_factory=dict)
    risk_criteria: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    """完整的测试场景，对应一条 seed JSON。"""
    version: str
    scenario_id: str
    scenario_name: str
    description: str
    risk: ScenarioRisk
    environment: ScenarioEnvironment
    task: ScenarioTask
    attack: ScenarioAttack
    defense: ScenarioDefense
    expected: ScenarioExpected
    metadata: dict[str, Any] = field(default_factory=dict)

    # 原始 JSON 路径
    source_path: str = ""


# ============================================================
# Protocol string → ProtocolType mapping
# ============================================================

_PROTOCOL_MAP = {
    "A2A": ProtocolType.A2A,
    "MCP": ProtocolType.MCP,
    "PRIVATE_API": ProtocolType.PRIVATE_API,
}


def _parse_protocols(raw: list[str]) -> list[ProtocolType]:
    result = []
    for p in raw:
        pt = _PROTOCOL_MAP.get(p.upper())
        if pt:
            result.append(pt)
    return result or [ProtocolType.A2A]


# ============================================================
# Scenario Loader
# ============================================================

class ScenarioLoader:
    """加载 seed JSON 文件，解析为 Scenario 对象。

    Parameters
    ----------
    path : str | Path
        seed JSON 文件路径。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> Scenario:
        """读取并解析 JSON，返回 Scenario 对象。"""
        if not self._path.exists():
            raise FileNotFoundError(f"Scenario file not found: {self._path}")

        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> Scenario:
        """将原始 JSON dict 解析为 Scenario 数据类。"""
        risk_data = data.get("risk", {})
        risk = ScenarioRisk(
            dimension=risk_data.get("dimension", ""),
            sub_dimension=risk_data.get("sub_dimension", ""),
            dimension_cn=risk_data.get("dimension_cn", ""),
            sub_dimension_cn=risk_data.get("sub_dimension_cn", ""),
            risk_level=risk_data.get("risk_level", "MEDIUM"),
            description=risk_data.get("description", ""),
        )

        env_data = data.get("environment", {})

        sub_ioas = []
        for sioa in env_data.get("sub_ioas", []):
            agents = []
            for ag in sioa.get("agents", []):
                agents.append(ScenarioAgent(
                    agent_id=ag.get("agent_id", ""),
                    display_name=ag.get("display_name", ""),
                    provider=ag.get("provider", ""),
                    capabilities=ag.get("capabilities", []),
                    actual_capabilities=ag.get("actual_capabilities", []),
                    protocols=ag.get("protocols", ["A2A"]),
                    reputation_score=ag.get("reputation_score", 0.5),
                    permission_scope=ag.get("permission_scope", []),
                    status=ag.get("status", "active"),
                    llm_model=ag.get("llm_model"),
                ))
            tools = []
            for tl in sioa.get("tools", []):
                tools.append(ScenarioTool(
                    tool_name=tl.get("tool_name", ""),
                    tool_type=tl.get("tool_type", ""),
                    description=tl.get("description", ""),
                    risk_level=tl.get("risk_level", "LOW"),
                ))
            sub_ioas.append(ScenarioSubIoA(
                sub_ioa_id=sioa.get("sub_ioa_id", ""),
                agents=agents,
                tools=tools,
            ))

        topo_data = env_data.get("topology", {})
        topology = ScenarioTopology(
            style=topo_data.get("style", "full_mesh"),
            edges=topo_data.get("edges", []),
        )

        kb_data = env_data.get("shared_knowledge", {})
        shared_knowledge = ScenarioKnowledge(
            enabled=kb_data.get("enabled", True),
            pre_existing_entries=kb_data.get("pre_existing_entries", []),
        )

        environment = ScenarioEnvironment(
            sub_ioas=sub_ioas,
            topology=topology,
            shared_knowledge=shared_knowledge,
        )

        task_data = data.get("task", {})
        task = ScenarioTask(
            task_type=task_data.get("task_type", "CROSS_DOMAIN"),
            description=task_data.get("description", ""),
            required_capabilities=task_data.get("required_capabilities", []),
            priority_factors=task_data.get("priority_factors", {}),
            max_hops=task_data.get("max_hops", 3),
            timeout=task_data.get("timeout", 60),
            payload=task_data.get("payload", {}),
        )

        atk_data = data.get("attack", {})
        attack = ScenarioAttack(
            attack_type=atk_data.get("attack_type", ""),
            method=atk_data.get("method", "llm_generated"),
            target_component=atk_data.get("target_component", ""),
            target_sub_ioa=atk_data.get("target_sub_ioa", ""),
            target_agent_id=atk_data.get("target_agent_id"),
            goal=atk_data.get("goal", ""),
            params=atk_data.get("params", {}),
            pair_config=atk_data.get("pair_config", {}),
        )

        def_data = data.get("defense", {})
        defense = ScenarioDefense(
            expected_mechanisms=def_data.get("expected_mechanisms", []),
            audit_requirements=def_data.get("audit_requirements", {}),
        )

        exp_data = data.get("expected", {})
        expected = ScenarioExpected(
            attack_should_succeed=exp_data.get("attack_should_succeed", False),
            completion_criteria=exp_data.get("completion_criteria", {}),
            risk_criteria=exp_data.get("risk_criteria", {}),
            metrics=exp_data.get("metrics", {}),
        )

        return Scenario(
            version=data.get("version", "1.0"),
            scenario_id=data.get("scenario_id", ""),
            scenario_name=data.get("scenario_name", ""),
            description=data.get("description", ""),
            risk=risk,
            environment=environment,
            task=task,
            attack=attack,
            defense=defense,
            expected=expected,
            metadata=data.get("metadata", {}),
            source_path=str(self._path),
        )


def load_all_seeds(directory: str | Path) -> list[Scenario]:
    """加载目录下所有 seed_*.json 文件。"""
    seed_dir = Path(directory)
    scenarios = []
    for json_file in sorted(seed_dir.glob("seed_*.json")):
        try:
            loader = ScenarioLoader(json_file)
            scenarios.append(loader.load())
            logger.info("Loaded scenario: %s (%s)", json_file.name, scenarios[-1].scenario_id)
        except Exception as e:
            logger.error("Failed to load %s: %s", json_file.name, e)
    return scenarios
