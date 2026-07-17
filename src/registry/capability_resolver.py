"""Deterministic semantic capability matching for offline agentic runs."""

from __future__ import annotations

import re
from typing import Iterable

from ..core.data_models import AgentCard, CapabilityRequirement


_ALIASES: dict[str, set[str]] = {
    "itinerary_planning": {"itinerary", "travel plan", "trip", "logistics", "booking", "hotel", "行程", "出差", "差旅", "预订", "酒店"},
    "logistics": {"itinerary", "routing", "travel", "logistics", "booking", "行程", "差旅", "预订"},
    "hotel_comparison": {"hotel", "booking", "travel", "itinerary", "酒店", "预订", "差旅"},
    "public_health": {"health", "medical", "public health", "vaccine", "clinical", "clinical_analysis", "epidemiology", "健康", "医疗", "临床", "疫苗"},
    "clinical_analysis": {"health", "medical", "clinical", "diagnosis", "public_health", "clinical trial", "trial", "drug", "医疗", "健康", "临床", "试验", "药企"},
    "drug_development": {"clinical", "clinical trial", "trial", "drug", "pharma", "biotech", "临床", "试验", "药企", "制药", "生物科技"},
    "clinical_trial": {"clinical", "clinical trial", "trial", "drug", "临床", "试验"},
    "travel_insurance": {"insurance", "coverage", "travel insurance", "risk", "保险", "保障"},
    "financial_analysis": {"finance", "financial", "investment", "market", "portfolio", "investment_advice", "risk_assessment", "财务", "金融", "投资", "市场", "资产", "并购"},
    "investment_advice": {"investment", "advice", "portfolio", "allocation", "financial", "market", "financial_analysis", "投资", "建议", "资产配置", "理财", "操作方案"},
    "portfolio_management": {"portfolio", "allocation", "investment", "financial", "investment_advice", "资产配置", "投资", "理财"},
    "payment_review": {"payment", "pay", "approval", "permission", "risk", "付款", "支付", "审核", "权限"},
    "risk_assessment": {"risk", "assessment", "safety", "financial_analysis", "public_health", "payment_review", "风险", "审核"},
    "news_aggregation": {"news", "media", "aggregation", "fact_checking", "source_verification", "sentiment_analysis", "trend_detection", "新闻", "舆情", "事实", "核查", "监控", "观点", "摘要"},
    "fact_checking": {"fact", "verify", "source", "provenance", "news", "news_aggregation", "source_verification", "核查", "来源", "事实", "引用", "新闻"},
    "source_verification": {"fact", "verify", "source", "provenance", "fact_checking", "news_aggregation", "核查", "来源", "事实"},
    "sentiment_analysis": {"sentiment", "public opinion", "opinion", "news", "news_aggregation", "trend_detection", "舆情", "情绪", "监控", "观点", "讨论"},
    "trend_detection": {"trend", "sentiment", "news", "news_aggregation", "market", "趋势", "舆情", "新闻"},
    "general_analysis": {"analysis", "report", "summary", "process", "workflow", "advice", "itinerary_planning", "investment_advice", "news_aggregation", "分析", "报告", "摘要", "流程", "建议"},
}


_TRUST_ORDER = {
    "untrusted": 0,
    "sandboxed": 1,
    "verified": 2,
    "privileged": 3,
}


def trust_satisfies(actual: str, minimum: str) -> bool:
    return _TRUST_ORDER.get(actual, 0) >= _TRUST_ORDER.get(minimum, 0)


def requirement_to_terms(requirement: CapabilityRequirement | str) -> set[str]:
    if isinstance(requirement, str):
        capability = requirement
        description = requirement
    else:
        capability = requirement.capability
        description = " ".join([
            requirement.capability,
            requirement.semantic_description,
            requirement.expected_output,
            " ".join(requirement.input_requirements),
        ])
    terms = {_normalize_token(capability)}
    terms.update(_ALIASES.get(_normalize_token(capability), set()))
    terms.update(_tokenize(description))
    return {term for term in terms if term}


def agent_capability_terms(agent: AgentCard) -> set[str]:
    text_parts: list[str] = []
    text_parts.extend(agent.declared_capabilities)
    for claim in agent.capability_claims:
        text_parts.extend([claim.capability_id, claim.name, claim.description])
    terms: set[str] = set()
    for part in text_parts:
        normalized = _normalize_token(part)
        terms.add(normalized)
        terms.update(_ALIASES.get(normalized, set()))
        terms.update(_tokenize(part))
    return {term for term in terms if term}


def agent_exact_capabilities(agent: AgentCard) -> set[str]:
    terms = {_normalize_token(capability) for capability in agent.declared_capabilities}
    for claim in agent.capability_claims:
        terms.add(_normalize_token(claim.capability_id))
    return {term for term in terms if term}


def capability_fit(agent: AgentCard, requirements: Iterable[CapabilityRequirement | str]) -> float:
    requirements = list(requirements)
    if not requirements:
        return 1.0
    agent_terms = agent_capability_terms(agent)
    exact_capabilities = agent_exact_capabilities(agent)
    scores: list[float] = []
    for requirement in requirements:
        capability = requirement if isinstance(requirement, str) else requirement.capability
        if _normalize_token(capability) == "general_analysis" and agent_terms:
            scores.append(0.35)
            continue
        main_terms = {_normalize_token(capability)}
        required_terms = requirement_to_terms(requirement)
        if not required_terms:
            scores.append(0.0)
            continue
        direct = 1.0 if any(term in exact_capabilities for term in main_terms) else 0.0
        overlap = len(agent_terms & required_terms) / max(1, len(required_terms))
        scores.append(max(direct, min(0.65, overlap * 2)))
    return sum(scores) / len(scores)


def _normalize_token(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _tokenize(value: str) -> set[str]:
    raw = str(value or "").lower()
    ascii_tokens = re.findall(r"[a-z][a-z0-9_]+", raw)
    cjk_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
    return set(ascii_tokens + cjk_tokens)
