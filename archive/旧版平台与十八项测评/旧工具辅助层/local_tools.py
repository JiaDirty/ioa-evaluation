"""Register existing local demo tools behind ToolGateway."""

from __future__ import annotations

from pathlib import Path

from .config import load_tool_descriptors
from .gateway import ToolGateway
from .models import ToolDescriptor
from .registry import ToolRegistry


DEFAULT_LOCAL_TOOL_SPECS = [
    ("get_stock_price", ["read_market_data"], "low"),
    ("analyze_financial_report", ["read_market_data"], "medium"),
    ("lookup_drug_info", ["read_healthcare"], "medium"),
    ("check_clinical_trial", ["read_healthcare"], "medium"),
    ("search_flights", ["read_travel"], "low"),
    ("search_hotels", ["read_travel"], "low"),
    ("aggregate_news", ["aggregate_news"], "low"),
    ("fact_check", ["fact_check"], "medium"),
]


def _fallback_descriptors() -> list[ToolDescriptor]:
    return [
        ToolDescriptor(
            tool_id=tool_id,
            name=tool_id.replace("_", " ").title(),
            description=f"Local wrapped tool: {tool_id}",
            required_scopes=scopes,
            risk_level=risk,
            provider="local",
        )
        for tool_id, scopes, risk in DEFAULT_LOCAL_TOOL_SPECS
    ]


def build_default_tool_gateway(config_path: str | Path | None = None) -> ToolGateway:
    from ..agents import tools as local

    registry = ToolRegistry()
    handlers = {
        "get_stock_price": local.get_stock_price,
        "analyze_financial_report": local.analyze_financial_report,
        "lookup_drug_info": local.lookup_drug_info,
        "check_clinical_trial": local.check_clinical_trial,
        "search_flights": local.search_flights,
        "search_hotels": local.search_hotels,
        "aggregate_news": local.aggregate_news,
        "fact_check": local.fact_check,
    }
    descriptors = load_tool_descriptors(config_path) or _fallback_descriptors()
    for descriptor in descriptors:
        registry.register(descriptor, handlers.get(descriptor.tool_id))
    return ToolGateway(registry)
