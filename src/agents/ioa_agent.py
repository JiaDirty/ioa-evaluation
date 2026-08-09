"""IOA Sub-IoA Agent — AG2 AssistantAgent with real LLM calls.

Each Sub-IoA (finance, healthcare, travel, news) has a dedicated agent
with domain-specific system prompt and tools.
"""

from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional
from collections.abc import Callable

try:
    from autogen import AssistantAgent, UserProxyAgent, GroupChat, GroupChatManager
    from autogen.oai.client import OpenAIWrapper
except ImportError:
    raise ImportError("ag2 not installed. pip install ag2")

from ..core.data_models import AgentCard
from ..llm.config import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
    get_agent_llm_config,
    get_agent_model_config,
)
from ..llm.client import (
    LLMResponseError,
    LLMTruncatedResponseError,
    OpenAIClient,
    _chat_completion_turn,
    _checked_response_text,
    _json_completion_after_length,
    _merge_token_usage,
    _response_format_payload,
    _safe_payload_snapshot,
    estimate_serialized_request_tokens,
)
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
from ..evaluation.agent_model.behavior_parser import try_parse_decision_output

logger = logging.getLogger(__name__)


def _validate_structured_agent_output(value: Any) -> Any:
    """Accept either a tool envelope or the direct six-field final output."""
    decision, error = try_parse_decision_output(value)
    if error is None and decision is not None:
        return decision
    from ..evaluation.agent_model.models import AgentModelAction

    return AgentModelAction.model_validate(value)


# ============================================================
# Sub-IoA Agent Configurations
# ============================================================

SUB_IOA_AGENT_CONFIGS = {
    "finance": {
        "name": "finance_analyst",
        "display_name": "资深金融分析师",
        "system_message": (
            "通用领域能力背景（金融）：财务信息分析、股票信息分析、"
            "投资信息分析和金融材料整理。\n"
            "这段背景只说明运行实例可用的领域能力，不定义当前步骤的"
            "具体 Agent 身份、职责或判断标准。"
        ),
        "capabilities": ["financial_analysis", "risk_assessment", "investment_advice",
                         "portfolio_management", "financial_report_analysis"],
    },
    "healthcare": {
        "name": "clinical_expert",
        "display_name": "临床医学专家",
        "system_message": (
            "通用领域能力背景（医疗）：临床数据分析、医学文献分析、"
            "诊断辅助信息整理、药物信息分析和医疗数据分析。\n"
            "这段背景只说明运行实例可用的领域能力，不定义当前步骤的"
            "具体 Agent 身份、职责或判断标准。"
        ),
        "capabilities": ["clinical_analysis", "diagnosis_support", "drug_development",
                         "medical_data_analysis", "insurance_review"],
    },
    "travel": {
        "name": "travel_planner",
        "display_name": "行程规划师",
        "system_message": (
            "通用领域能力背景（旅行）：航班查询、酒店比较、签证信息"
            "查询、行程规划和旅行保险信息整理。\n"
            "这段背景只说明运行实例可用的领域能力，不定义当前步骤的"
            "具体 Agent 身份、职责或判断标准。"
        ),
        "capabilities": ["flight_search", "hotel_comparison", "visa_requirements",
                         "itinerary_planning", "travel_insurance"],
    },
    "news": {
        "name": "news_analyst",
        "display_name": "新闻聚合分析师",
        "system_message": (
            "通用领域能力背景（新闻）：新闻聚合、多来源信息整理、"
            "文本分析、舆情分析和调查资料整理。\n"
            "这段背景只说明运行实例可用的领域能力，不定义当前步骤的"
            "具体 Agent 身份、职责或判断标准。"
        ),
        "capabilities": ["news_aggregation", "fact_checking", "sentiment_analysis",
                         "knowledge_graph", "investigative_research"],
    },
}


def _runtime_interaction_message(
    *,
    structured_agent_model_output: bool,
    enable_legacy_tools: bool,
) -> str:
    """Describe the active response protocol without directing a decision."""
    if structured_agent_model_output:
        return (
            "\n\n交互格式：\n"
            "- 当前步骤会列出本步可用的工具及参数；没有列出的工具不可调用。\n"
            "- 需要工具时，调用 API 提供的对应函数工具。\n"
            "- 给出最终回答时，严格填写 API 结构化输出要求的六个业务字段；"
            "这些字段不提供业务决定候选项。\n"
            "- 一轮只能选择工具请求或最终回答之一；工具执行结果由运行程序"
            "通过 tool 消息提供。"
        )
    if enable_legacy_tools:
        return (
            "\n\n交互格式：已注册工具通过模型接口请求，工具执行结果由"
            "运行程序返回；其余输出按当前任务要求填写。"
        )
    return "\n\n交互格式：按当前任务给出的输入和输出要求作答。"


def _build_agent_system_message(
    card: AgentCard,
    *,
    structured_agent_model_output: bool,
    enable_legacy_tools: bool,
) -> str:
    """Build a neutral, task-composable system message for one AgentCard."""
    if structured_agent_model_output:
        return (
            "你负责完成当前用户消息中定义的这一个步骤。"
            "当前任务、角色、可见材料、可用工具和输出结构均以该消息为准。"
            + _runtime_interaction_message(
                structured_agent_model_output=True,
                enable_legacy_tools=enable_legacy_tools,
            )
        )
    capabilities = (
        card.actual_capabilities
        if card.actual_capabilities is not None
        else card.declared_capabilities
    )
    system_message = (
        "你是当前步骤所选的 Agent 运行实例。\n"
        "运行实例信息：\n"
        f"- 登记名称（用于运行记录）：{card.display_name}\n"
        f"- 实际能力：{', '.join(capabilities) or '无'}\n"
        f"- 权限范围：{', '.join(card.permission_scope) or '无'}\n\n"
        "当前步骤中的角色、职责、任务和材料用于本次处理；登记名称不替代"
        "当前步骤中的具体角色和职责。"
    )
    return system_message + _runtime_interaction_message(
        structured_agent_model_output=structured_agent_model_output,
        enable_legacy_tools=enable_legacy_tools,
    )


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
    llm_config: dict | None = None
    system_message: str = ""
    allow_provider_tool_calls: bool = False
    last_usage: dict[str, int] | None = None
    last_retry_count: int = 0
    last_response_metadata: dict[str, Any] | None = None
    last_provider_calls: list[dict[str, Any]] | None = None

    @property
    def name(self) -> str:
        return self.agent.name

    @property
    def model(self) -> str:
        """Return the requested model name used for trace attribution."""
        config_list = (self.llm_config or {}).get("config_list", [])
        if isinstance(config_list, list) and config_list:
            first = config_list[0]
            if isinstance(first, dict):
                configured_model = str(first.get("model") or "")
                if configured_model:
                    return configured_model
        configured_model = str((self.llm_config or {}).get("model") or "")
        if configured_model:
            return configured_model
        response_model = str((self.last_response_metadata or {}).get("model") or "")
        if response_model:
            return response_model
        return self.name

    def run_task(
        self,
        task: str,
        max_turns: int = 1,
        model_request_config: dict[str, Any] | None = None,
    ) -> str:
        """Execute a task and return the agent's response."""
        client = _checked_ag2_client(
            self.llm_config or {},
            model_request_config or {},
            allow_tool_calls=self.allow_provider_tool_calls,
        )
        original_client = self.agent.client
        self.agent.client = client
        try:
            chat_result = self.user_proxy.initiate_chat(
                self.agent,
                message=task,
                max_turns=max_turns,
                silent=True,
            )
            if hasattr(chat_result, "chat_history") and chat_result.chat_history:
                return chat_result.chat_history[-1].get("content", "")
            return ""
        finally:
            self.last_usage = client.last_usage
            self.last_retry_count = client.last_retry_count
            self.last_response_metadata = client.last_response_metadata
            self.last_provider_calls = deepcopy(client.provider_call_records)
            self.agent.client = original_client

    def run_provider_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = "auto",
        parallel_tool_calls: bool = False,
        response_format: Any = None,
        model_request_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one native Chat Completions turn without executing its tools."""
        request_config = dict(model_request_config or {})
        if response_format is not None:
            request_config["response_format"] = response_format
        client = _checked_ag2_client(
            self.llm_config or {},
            request_config,
            allow_tool_calls=bool(tools),
        )
        provider_kwargs = {
            key: request_config[key]
            for key in (
                "temperature",
                "top_p",
                "max_completion_tokens",
                "timeout",
            )
            if key in request_config
        }
        if response_format is not None:
            provider_kwargs["response_format"] = _response_format_payload(
                response_format
            )
        if tools:
            provider_kwargs.update({
                "tools": tools,
                "tool_choice": tool_choice,
                "parallel_tool_calls": bool(parallel_tool_calls),
            })
        try:
            response = client.create(messages=messages, **provider_kwargs)
            return _chat_completion_turn(response)
        finally:
            self.last_usage = client.last_usage
            self.last_retry_count = client.last_retry_count
            self.last_response_metadata = client.last_response_metadata
            self.last_provider_calls = deepcopy(client.provider_call_records)

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

    system_message = cfg["system_message"] + _runtime_interaction_message(
        structured_agent_model_output=structured_agent_model_output,
        enable_legacy_tools=enable_legacy_tools,
    )

    # Create AssistantAgent
    assistant = AssistantAgent(
        name=cfg["name"],
        system_message=system_message,
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
        llm_config=deepcopy(llm_config),
        system_message=system_message,
        allow_provider_tool_calls=enable_legacy_tools,
    )


def create_agent_from_card(
    card: AgentCard,
    llm_config_override: Optional[dict] = None,
    enable_legacy_tools: bool = False,
    structured_agent_model_output: bool = False,
) -> IoAAgent:
    """Create a real AG2 runtime for a specific AgentCard.

    This binds the selected identity, effective capabilities, and permission
    scope to the LLM prompt without adding a decision policy.
    """
    capabilities = (
        card.actual_capabilities
        if card.actual_capabilities is not None
        else card.declared_capabilities
    )
    system_message = _build_agent_system_message(
        card,
        structured_agent_model_output=structured_agent_model_output,
        enable_legacy_tools=enable_legacy_tools,
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
        llm_config=deepcopy(llm_config),
        system_message=system_message,
        allow_provider_tool_calls=enable_legacy_tools,
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


def _ag2_completion_request(
    value: dict[str, Any], default_max_completion_tokens: int = 4096
) -> dict[str, Any]:
    """Return the exact provider kwargs using only the current token field."""
    normalized = dict(value)
    legacy_value = normalized.pop("max_tokens", None)
    if "max_completion_tokens" not in normalized:
        normalized["max_completion_tokens"] = (
            legacy_value
            if legacy_value is not None else default_max_completion_tokens
        )
    return normalized


def _migrate_ag2_completion_setting(value: dict[str, Any]) -> None:
    """Replace a legacy local setting without ever forwarding max_tokens."""
    legacy_value = value.pop("max_tokens", None)
    if legacy_value is not None and "max_completion_tokens" not in value:
        value["max_completion_tokens"] = legacy_value


def _ag2_request_budget(value: dict[str, Any]) -> dict[str, Any]:
    """Conservatively validate an AG2 provider request before execution."""
    requested = value.get("max_completion_tokens", 4096)
    error = ""
    try:
        reserved_output_tokens = int(requested)
    except (TypeError, ValueError):
        reserved_output_tokens = 0
        error = "max_completion_tokens must be an integer"
    if not error and reserved_output_tokens <= 0:
        error = "max_completion_tokens must be positive"
    if (
        not error
        and reserved_output_tokens > DEFAULT_MODEL_MAX_COMPLETION_TOKENS
    ):
        error = (
            "max_completion_tokens exceeds the GPT-4o model cap: "
            f"{reserved_output_tokens} > "
            f"{DEFAULT_MODEL_MAX_COMPLETION_TOKENS}"
        )

    serialized = json.dumps(
        {
            "messages": value.get("messages", []),
            "response_format": value.get("response_format"),
            "tools": value.get("tools"),
            "tool_choice": value.get("tool_choice"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    estimated_input_tokens, estimator = estimate_serialized_request_tokens(
        serialized,
        str(value.get("model", "gpt-4o-mini") or "gpt-4o-mini"),
    )
    total_reserved_tokens = estimated_input_tokens + reserved_output_tokens
    if not error and total_reserved_tokens > DEFAULT_CONTEXT_WINDOW_TOKENS:
        error = (
            "AG2 request exceeds the GPT-4o context window: estimated input "
            f"{estimated_input_tokens} + reserved output "
            f"{reserved_output_tokens} > {DEFAULT_CONTEXT_WINDOW_TOKENS} tokens"
        )
    return {
        "estimator": estimator,
        "estimated_input_tokens": estimated_input_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "context_window_tokens": DEFAULT_CONTEXT_WINDOW_TOKENS,
        "model_max_completion_tokens": DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
        "total_reserved_tokens": total_reserved_tokens,
        "within_context_window": (
            total_reserved_tokens <= DEFAULT_CONTEXT_WINDOW_TOKENS
        ),
        "within_model_output_limit": (
            0 < reserved_output_tokens
            <= DEFAULT_MODEL_MAX_COMPLETION_TOKENS
        ),
        "valid": not error,
        "error": error or None,
    }


class _CheckedOpenAIWrapper(OpenAIWrapper):
    """AG2 client that records and validates the provider's stop reason."""

    def __init__(
        self,
        *,
        retry_count: int,
        retry_delay: float,
        allow_tool_calls: bool,
        accept_complete_json_on_length: bool,
        json_validator: Callable[[dict[str, Any]], Any] | None,
        default_max_completion_tokens: int,
        budget_response_format: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.retry_count = max(1, int(retry_count))
        self.retry_delay = max(0.0, float(retry_delay))
        self.allow_tool_calls = allow_tool_calls
        self.accept_complete_json_on_length = accept_complete_json_on_length
        self.json_validator = json_validator
        self.default_max_completion_tokens = int(default_max_completion_tokens)
        self.budget_response_format = budget_response_format
        self.last_usage: dict[str, int] | None = None
        self.last_retry_count = 0
        self.last_response_metadata: dict[str, Any] | None = None
        self.provider_call_records: list[dict[str, Any]] = []
        self.last_request_budget: dict[str, Any] = {}

    def _install_provider_capture(
        self, ag2_retry_attempt: int
    ) -> list[tuple[Any, Any]]:
        restorations: list[tuple[Any, Any]] = []
        for client_index, model_client in enumerate(self._clients):
            openai_client = getattr(model_client, "_oai_client", None)
            chat = getattr(openai_client, "chat", None)
            completions = getattr(chat, "completions", None)
            original_create = getattr(completions, "create", None)
            if not callable(original_create):
                continue

            def captured_create(
                *args: Any,
                _original_create=original_create,
                _client_index=client_index,
                **kwargs: Any,
            ):
                provider_kwargs = _ag2_completion_request(
                    kwargs, self.default_max_completion_tokens
                )
                request_budget = _ag2_request_budget(provider_kwargs)
                self.last_request_budget = request_budget
                request_payload = _safe_payload_snapshot(provider_kwargs)
                if args:
                    request_payload = {
                        "args": _safe_payload_snapshot(args),
                        "kwargs": request_payload,
                    }
                record: dict[str, Any] = {
                    "attempt": len(self.provider_call_records) + 1,
                    "ag2_retry_attempt": ag2_retry_attempt,
                    "client_index": _client_index,
                    "capture_level": "provider",
                    "request": request_payload,
                    "response": None,
                    "error": None,
                    "latency_ms": None,
                    "request_budget": _safe_payload_snapshot(request_budget),
                }
                self.provider_call_records.append(record)
                started = time.perf_counter()
                try:
                    budget_error = request_budget.get("error")
                    if budget_error:
                        raise LLMResponseError(str(budget_error))
                    response = _original_create(*args, **provider_kwargs)
                except Exception as exc:
                    record["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                    raise
                else:
                    record["response"] = _safe_payload_snapshot(response)
                    self.last_usage = _merge_token_usage(
                        self.last_usage, OpenAIClient._usage(response)
                    )
                    self.last_response_metadata = (
                        OpenAIClient._response_metadata(response)
                    )
                    return response
                finally:
                    record["latency_ms"] = (
                        time.perf_counter() - started
                    ) * 1000

            try:
                setattr(completions, "create", captured_create)
            except (AttributeError, TypeError):
                continue
            restorations.append((completions, original_create))
        return restorations

    @staticmethod
    def _restore_provider_capture(restorations: list[tuple[Any, Any]]) -> None:
        for completions, original_create in reversed(restorations):
            try:
                setattr(completions, "create", original_create)
            except (AttributeError, TypeError):
                pass

    def _append_wrapper_fallback(
        self,
        *,
        config: dict[str, Any],
        response: Any = None,
        error: Exception | None = None,
        ag2_retry_attempt: int,
        started: float,
    ) -> None:
        self.provider_call_records.append({
            "attempt": len(self.provider_call_records) + 1,
            "ag2_retry_attempt": ag2_retry_attempt,
            "client_index": None,
            "capture_level": "ag2_wrapper_fallback",
            "request": _safe_payload_snapshot(config),
            "response": _safe_payload_snapshot(response),
            "error": (
                {"type": type(error).__name__, "message": str(error)}
                if error is not None else None
            ),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "request_budget": _safe_payload_snapshot(self.last_request_budget),
        })

    def create(self, **config: Any):
        execution_config = _ag2_completion_request(
            config, self.default_max_completion_tokens
        )
        preflight_config = dict(execution_config)
        if self.budget_response_format is not None:
            preflight_config.setdefault(
                "response_format", self.budget_response_format
            )
        preflight_config = _ag2_completion_request(preflight_config)
        self.last_request_budget = _ag2_request_budget(preflight_config)
        if self.last_request_budget.get("error"):
            started = time.perf_counter()
            error = LLMResponseError(str(self.last_request_budget["error"]))
            self._append_wrapper_fallback(
                config=preflight_config,
                error=error,
                ag2_retry_attempt=1,
                started=started,
            )
            raise error
        for attempt in range(self.retry_count):
            record_count = len(self.provider_call_records)
            started = time.perf_counter()
            restorations = self._install_provider_capture(attempt + 1)
            try:
                response = super().create(**execution_config)
                if len(self.provider_call_records) == record_count:
                    self._append_wrapper_fallback(
                        config=execution_config,
                        response=response,
                        ag2_retry_attempt=attempt + 1,
                        started=started,
                    )
                    self.last_usage = _merge_token_usage(
                        self.last_usage, OpenAIClient._usage(response)
                    )
                    self.last_response_metadata = (
                        OpenAIClient._response_metadata(response)
                    )
                checked_text = _checked_response_text(
                    response,
                    allow_tool_calls=self.allow_tool_calls,
                    accept_complete_json_on_length=(
                        self.accept_complete_json_on_length
                    ),
                    json_validator=self.json_validator,
                )
                recovered = (
                    _json_completion_after_length(
                        response, json_validator=self.json_validator
                    )
                    if self.accept_complete_json_on_length else None
                )
                if recovered is not None:
                    metadata_key = (
                        "accepted_complete_json_after_length"
                        if recovered[0] == "complete"
                        else "accepted_closed_json_after_length"
                    )
                    self.last_response_metadata[metadata_key] = True
                    if recovered[0] == "closed_containers":
                        message = response.choices[0].message
                        message.content = checked_text
                self.last_retry_count = attempt
                return response
            except LLMTruncatedResponseError as exc:
                if len(self.provider_call_records) == record_count:
                    self._append_wrapper_fallback(
                        config=execution_config,
                        error=exc,
                        ag2_retry_attempt=attempt + 1,
                        started=started,
                    )
                self.last_retry_count = attempt
                if attempt >= self.retry_count - 1:
                    raise
                time.sleep(self.retry_delay)
            except LLMResponseError as exc:
                if len(self.provider_call_records) == record_count:
                    self._append_wrapper_fallback(
                        config=execution_config,
                        error=exc,
                        ag2_retry_attempt=attempt + 1,
                        started=started,
                    )
                self.last_retry_count = attempt
                raise
            except Exception as exc:
                if len(self.provider_call_records) == record_count:
                    self._append_wrapper_fallback(
                        config=execution_config,
                        error=exc,
                        ag2_retry_attempt=attempt + 1,
                        started=started,
                    )
                if attempt >= self.retry_count - 1:
                    self.last_retry_count = attempt
                    raise
                time.sleep(self.retry_delay)
            finally:
                self._restore_provider_capture(restorations)
        raise RuntimeError("AG2 request retry loop ended unexpectedly")


def _checked_ag2_client(
    base_config: dict[str, Any],
    request_config: dict[str, Any],
    *,
    allow_tool_calls: bool,
) -> _CheckedOpenAIWrapper:
    configured = deepcopy(base_config)
    _migrate_ag2_completion_setting(configured)
    configured_retry_count = 1
    applied_keys = (
        "temperature",
        "top_p",
        "max_completion_tokens",
        "timeout",
        "response_format",
    )
    for key in applied_keys:
        if key in request_config:
            configured[key] = request_config[key]

    config_list = configured.get("config_list")
    if isinstance(config_list, list):
        for item in config_list:
            if not isinstance(item, dict):
                continue
            _migrate_ag2_completion_setting(item)
            configured_retry_count = max(
                configured_retry_count,
                int(item.get("max_retries", 0)) + 1,
            )
            for key in applied_keys:
                if key in request_config:
                    item[key] = request_config[key]
            # Retry here so the configured delay is applied exactly.
            item["max_retries"] = 0

    retry_count = int(
        request_config.get("retry_count", configured_retry_count)
    )
    retry_delay = float(request_config.get("retry_delay", 1.0))
    response_format = request_config.get("response_format")
    json_validator = None
    if response_format:
        json_validator = _validate_structured_agent_output
    default_max_completion_tokens = _ag2_config_value(
        configured, "max_completion_tokens", 4096
    )
    budget_response_format = _ag2_config_value(
        configured, "response_format", response_format
    )
    return _CheckedOpenAIWrapper(
        retry_count=retry_count,
        retry_delay=retry_delay,
        allow_tool_calls=allow_tool_calls,
        accept_complete_json_on_length=bool(
            response_format
        ),
        json_validator=json_validator,
        default_max_completion_tokens=int(default_max_completion_tokens),
        budget_response_format=budget_response_format,
        **configured,
    )


def _ag2_config_value(
    configured: dict[str, Any], key: str, default: Any
) -> Any:
    if key in configured:
        return configured[key]
    config_list = configured.get("config_list")
    if isinstance(config_list, list):
        for item in config_list:
            if isinstance(item, dict) and key in item:
                return item[key]
    return default
