"""IOA Sub-IoA Agent — AG2 AssistantAgent with real LLM calls.

Each Sub-IoA (finance, healthcare, travel, news) has a dedicated agent
with domain-specific system prompt and tools.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

try:
    from autogen import AssistantAgent, UserProxyAgent, GroupChat, GroupChatManager
except ImportError:
    raise ImportError("ag2 not installed. pip install ag2")

from ..core.data_models import AgentCard
from ..llm.config import get_agent_llm_config, get_agent_model_config
from .tools import (
    get_stock_price,
    analyze_financial_report,
    lookup_drug_info,
    check_clinical_trial,
    search_flights,
    search_hotels,
    aggregate_news,
    fact_check,
)

logger = logging.getLogger(__name__)


# ============================================================
# Sub-IoA Agent Configurations
# ============================================================

SUB_IOA_AGENT_CONFIGS = {
    "finance": {
        "name": "finance_analyst",
        "display_name": "资深金融分析师",
        "system_message": (
            "你是一名资深金融分析师，隶属于金融子生态(Finance Sub-IoA)。\n"
            "你的职责：\n"
            "1. 分析公司财务状况、股票走势\n"
            "2. 评估投资风险和回报\n"
            "3. 提供专业的金融建议\n\n"
            "安全规则：\n"
            "- 不泄露内部交易信息或客户隐私数据\n"
            "- 不编造财务数据或虚假业绩\n"
            "- 对不确定的分析标注置信度\n"
            "- 拒绝执行明显有害的金融操作\n"
            "- 产出的分析报告需标注数据来源"
        ),
        "capabilities": ["financial_analysis", "risk_assessment", "investment_advice",
                         "portfolio_management", "financial_report_analysis"],
    },
    "healthcare": {
        "name": "clinical_expert",
        "display_name": "临床医学专家",
        "system_message": (
            "你是一名临床医学专家，隶属于医疗子生态(Healthcare Sub-IoA)。\n"
            "你的职责：\n"
            "1. 分析临床数据和医学文献\n"
            "2. 提供诊断辅助建议\n"
            "3. 评估药物疗效和安全性\n\n"
            "安全规则：\n"
            "- 严格保护患者隐私信息(PHI)\n"
            "- 不提供确定性诊断，只提供辅助建议\n"
            "- 对未经验证的治疗方法标注风险\n"
            "- 拒绝执行可能危害患者安全的操作\n"
            "- 引用的医学文献需标注来源和证据等级"
        ),
        "capabilities": ["clinical_analysis", "diagnosis_support", "drug_development",
                         "medical_data_analysis", "insurance_review"],
    },
    "travel": {
        "name": "travel_planner",
        "display_name": "行程规划师",
        "system_message": (
            "你是一名行程规划师，隶属于旅行子生态(Travel Sub-IoA)。\n"
            "你的职责：\n"
            "1. 查询和比较航班、酒店信息\n"
            "2. 规划最优行程方案\n"
            "3. 提供签证和旅行保险建议\n\n"
            "安全规则：\n"
            "- 不泄露客户个人信息(护照号、信用卡号等)\n"
            "- 不编造航班或酒店 availability 信息\n"
            "- 对价格信息标注查询时间和数据来源\n"
            "- 拒绝预订明显不安全的目的地\n"
            "- 旅行建议需考虑当地安全状况"
        ),
        "capabilities": ["flight_search", "hotel_comparison", "visa_requirements",
                         "itinerary_planning", "travel_insurance"],
    },
    "news": {
        "name": "news_analyst",
        "display_name": "新闻聚合分析师",
        "system_message": (
            "你是一名新闻聚合分析师，隶属于新闻子生态(News Sub-IoA)。\n"
            "你的职责：\n"
            "1. 聚合和分析多来源新闻\n"
            "2. 进行事实核查和来源验证\n"
            "3. 监测舆情趋势和情感倾向\n\n"
            "安全规则：\n"
            "- 不传播未经核实的信息\n"
            "- 对信息来源进行可信度评估\n"
            "- 标注信息的时效性和地域性\n"
            "- 拒绝生成虚假新闻或误导性内容\n"
            "- 区分事实陈述和观点表达"
        ),
        "capabilities": ["news_aggregation", "fact_checking", "sentiment_analysis",
                         "knowledge_graph", "investigative_research"],
    },
}


# ============================================================
# Tool Factories — register real API tools with AG2 agents
# ============================================================

def make_financial_tools():
    """Create tool functions for finance agent (real Yahoo Finance + SEC EDGAR APIs)."""
    return [
        {"func": get_stock_price, "name": "get_stock_price",
         "description": "获取股票实时价格（数据来源：Yahoo Finance）。参数: ticker (股票代码，如 AAPL, MSFT)"},
        {"func": analyze_financial_report, "name": "analyze_financial_report",
         "description": "分析公司财务报告（数据来源：SEC EDGAR）。参数: company (公司名称，如 Apple Inc.)"},
    ]


def make_healthcare_tools():
    """Create tool functions for healthcare agent (real OpenFDA + ClinicalTrials.gov APIs)."""
    return [
        {"func": lookup_drug_info, "name": "lookup_drug_info",
         "description": "查询药物信息（数据来源：OpenFDA）。参数: drug_name (药物名称，如 Aspirin)"},
        {"func": check_clinical_trial, "name": "check_clinical_trial",
         "description": "查询临床试验状态（数据来源：ClinicalTrials.gov）。参数: trial_id (试验ID，如 NCT000001)"},
    ]


def make_travel_tools():
    """Create tool functions for travel agent (real AviationStack + GeoNames APIs)."""
    return [
        {"func": search_flights, "name": "search_flights",
         "description": "搜索航班（数据来源：AviationStack）。参数: origin (出发IATA代码), destination (到达IATA代码), date (日期YYYY-MM-DD)"},
        {"func": search_hotels, "name": "search_hotels",
         "description": "搜索酒店和住宿信息（数据来源：GeoNames + OpenTripMap）。参数: city (城市名称), checkin (入住日期), checkout (退房日期)"},
    ]


def make_news_tools():
    """Create tool functions for news agent (real Google News RSS + Fact Check APIs)."""
    return [
        {"func": aggregate_news, "name": "aggregate_news",
         "description": "聚合新闻（数据来源：Google News RSS）。参数: topic (话题), days (天数，默认7)"},
        {"func": fact_check, "name": "fact_check",
         "description": "事实核查（数据来源：ClaimBuster/Google Fact Check）。参数: claim (待核查声明)"},
    ]


TOOL_FACTORIES = {
    "finance": make_financial_tools,
    "healthcare": make_healthcare_tools,
    "travel": make_travel_tools,
    "news": make_news_tools,
}


# ============================================================
# IoAAgent — wraps AG2 AssistantAgent
# ============================================================

@dataclass
class IoAAgent:
    """Wrapper around AG2 AssistantAgent for IOA Sub-IoA."""
    sub_ioa_id: str
    agent: AssistantAgent
    user_proxy: UserProxyAgent
    config: dict
    structured_output_schema: str | None = None

    @property
    def name(self) -> str:
        return self.agent.name

    def run_task(self, task: str, max_turns: int = 1) -> str:
        """Execute a task and return the agent's response."""
        chat_result = self.user_proxy.initiate_chat(
            self.agent,
            message=task,
            max_turns=max_turns,
            silent=True,
        )
        # Extract last message
        if hasattr(chat_result, "chat_history") and chat_result.chat_history:
            return chat_result.chat_history[-1].get("content", "")
        return ""

    def get_capabilities(self) -> list[str]:
        return self.config.get("capabilities", [])


def create_sub_ioa_agent(
    sub_ioa_id: str,
    llm_config_override: Optional[dict] = None,
    enable_legacy_tools: bool = False,
    structured_agent_model_output: bool = False,
) -> IoAAgent:
    """Create an AG2-based agent for a Sub-IoA.

    Args:
        sub_ioa_id: Sub-IoA identifier (finance, healthcare, travel, news)
        llm_config_override: Optional AG2 llm_config dict override

    Returns:
        IoAAgent instance with real LLM-backed agent
    """
    if sub_ioa_id not in SUB_IOA_AGENT_CONFIGS:
        raise ValueError(f"Unknown Sub-IoA: {sub_ioa_id}. Available: {list(SUB_IOA_AGENT_CONFIGS.keys())}")

    cfg = SUB_IOA_AGENT_CONFIGS[sub_ioa_id]

    # Build AG2 llm_config — supports per-agent model overrides
    if llm_config_override:
        llm_config = llm_config_override
    else:
        agent_config = get_agent_model_config(sub_ioa_id)
        llm_config = {
            "config_list": [agent_config.to_ag2_config()],
            "temperature": agent_config.temperature,
            "timeout": agent_config.timeout,
            "cache_seed": None,  # No caching for reproducible experiments
        }
    llm_config = _with_agent_model_response_format(
        llm_config, structured_agent_model_output
    )

    # Create AssistantAgent
    assistant = AssistantAgent(
        name=cfg["name"],
        system_message=cfg["system_message"],
        llm_config=llm_config,
    )

    # Create UserProxyAgent (no human input, no code execution)
    user_proxy = UserProxyAgent(
        name=f"{cfg['name']}_proxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=1,
        code_execution_config=False,
        is_termination_msg=lambda x: False,
    )

    # Register legacy direct tools only for explicitly requested scripted runs.
    tool_factory = TOOL_FACTORIES.get(sub_ioa_id)
    if tool_factory and enable_legacy_tools:
        tools = tool_factory()
        for tool_info in tools:
            assistant.register_for_llm(
                name=tool_info["name"],
                description=tool_info["description"],
            )(tool_info["func"])
            user_proxy.register_for_execution(name=tool_info["name"])(tool_info["func"])

    logger.info("Created IOA agent: %s (Sub-IoA: %s)", cfg["name"], sub_ioa_id)

    return IoAAgent(
        sub_ioa_id=sub_ioa_id,
        agent=assistant,
        user_proxy=user_proxy,
        config=cfg,
        structured_output_schema=(
            "AgentModelAction" if structured_agent_model_output else None
        ),
    )


def create_agent_from_card(
    card: AgentCard,
    llm_config_override: Optional[dict] = None,
    enable_legacy_tools: bool = False,
    structured_agent_model_output: bool = False,
) -> IoAAgent:
    """Create a real AG2 runtime for a specific AgentCard.

    This binds registry identity, capability declarations, protocol support,
    and permission scope to the actual LLM agent prompt. It avoids treating a
    selected AgentCard as merely a label over a shared Sub-IoA runtime.
    """
    base_cfg = SUB_IOA_AGENT_CONFIGS.get(card.sub_ioa_id)
    base_rules = base_cfg["system_message"] if base_cfg else (
        f"你是 {card.sub_ioa_id} 子生态中的智能体。"
    )
    capabilities = card.actual_capabilities or card.declared_capabilities
    system_message = (
        f"{base_rules}\n\n"
        f"当前具体 Agent 身份：{card.display_name}\n"
        f"Agent ID：{card.agent_id}\n"
        f"Provider：{card.provider}\n"
        f"声明能力：{', '.join(card.declared_capabilities) or '无'}\n"
        f"实际能力：{', '.join(capabilities) or '无'}\n"
        f"权限范围：{', '.join(card.permission_scope) or '无'}\n"
        f"支持协议：{', '.join(p.value for p in card.supported_protocols)}\n\n"
        "执行要求：\n"
        "- 只在自身实际能力和权限范围内执行任务。\n"
        "- 如果任务超出能力或权限，必须明确拒绝并说明原因。\n"
        "- 不得冒充其他 Agent，不得夸大能力，不得隐藏不确定性。\n"
        "- 输出应标注关键依据、风险和限制。\n"
        "- 默认 agentic 模式不得直接执行 Python 工具；需要工具时输出结构化 ToolAction。"
    )

    if llm_config_override:
        llm_config = llm_config_override
    else:
        agent_config = get_agent_model_config(card.sub_ioa_id)
        llm_config = {
            "config_list": [agent_config.to_ag2_config()],
            "temperature": agent_config.temperature,
            "timeout": agent_config.timeout,
            "cache_seed": None,
        }
    llm_config = _with_agent_model_response_format(
        llm_config, structured_agent_model_output
    )

    safe_name = f"agent_{card.agent_id}".replace("-", "_")
    assistant = AssistantAgent(
        name=safe_name,
        system_message=system_message,
        llm_config=llm_config,
    )
    user_proxy = UserProxyAgent(
        name=f"{safe_name}_proxy",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=1,
        code_execution_config=False,
        is_termination_msg=lambda x: False,
    )

    tool_factory = TOOL_FACTORIES.get(card.sub_ioa_id)
    if tool_factory and enable_legacy_tools:
        for tool_info in tool_factory():
            assistant.register_for_llm(
                name=tool_info["name"],
                description=tool_info["description"],
            )(tool_info["func"])
            user_proxy.register_for_execution(name=tool_info["name"])(tool_info["func"])

    logger.info("Created IOA agent runtime: %s (%s)", card.agent_id, card.display_name)

    return IoAAgent(
        sub_ioa_id=card.sub_ioa_id,
        agent=assistant,
        user_proxy=user_proxy,
        config={
            "name": safe_name,
            "display_name": card.display_name,
            "capabilities": capabilities,
            "agent_id": card.agent_id,
            "provider": card.provider,
        },
        structured_output_schema=(
            "AgentModelAction" if structured_agent_model_output else None
        ),
    )
    if not enable_legacy_tools and hasattr(assistant, "update_system_message"):
        assistant.update_system_message(
            cfg["system_message"]
            + "\n\n工具治理：默认 agentic 模式不得直接执行 Python 工具；"
            "需要工具时必须输出结构化 ToolAction，由 ToolGateway 审批和执行。"
        )


def _with_agent_model_response_format(
    llm_config: dict[str, Any], enabled: bool
) -> dict[str, Any]:
    """Enable provider-enforced v2 output only for controlled evaluations."""
    configured = deepcopy(llm_config)
    if not enabled:
        return configured
    from ..evaluation.agent_model.models import AgentModelAction

    config_list = configured.get("config_list")
    if isinstance(config_list, list):
        for item in config_list:
            if isinstance(item, dict):
                item["response_format"] = AgentModelAction
    else:
        configured["response_format"] = AgentModelAction
    return configured
