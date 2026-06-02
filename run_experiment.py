"""IoA 测评环境 — 完整实验运行脚本。

用法：
    python run_experiment.py                  # 运行所有 18 个风险测试
    python run_experiment.py --test C1        # 运行 C1 类别的测试
    python run_experiment.py --test ioa_identity_spoofing  # 运行单个测试
    python run_experiment.py --demo           # 运行演示场景
    python run_experiment.py --scenario data/seeds/seed_001_identity_spoofing.json  # 从场景文件加载并运行
    python run_experiment.py --scenario-dir data/seeds/  # 运行目录下所有场景
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent))

from src.experiment.runner import ExperimentRunner, IoAEnvironment
from src.experiment.scenario_loader import ScenarioLoader, load_all_seeds, Scenario
from src.core.data_models import Task, TaskType
from risk_tests.registry import ALL_TESTS, TESTS_BY_CATEGORY, TESTS_BY_ID, get_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ioa-experiment")


# ============================================================
# 环境初始化
# ============================================================

async def setup_environment() -> IoAEnvironment:
    """初始化完整的 IoA 测试环境。"""
    logger.info("=" * 60)
    logger.info("Initializing IoA Evaluation Environment")
    logger.info("=" * 60)

    env = IoAEnvironment()

    # 添加 4 个 Sub-IoA
    for sub_ioa_id in ["finance", "healthcare", "travel", "news"]:
        env.add_sub_ioa(sub_ioa_id)

    # 注册默认 Agent
    await env.setup_default_agents()

    # 设置全连接拓扑
    await env.setup_default_topology("full_mesh")

    logger.info(env.describe())
    return env


# ============================================================
# 运行风险测试
# ============================================================

async def run_all_tests(env: IoAEnvironment) -> dict:
    """运行所有 18 个 IoA 风险测试。"""
    runner = ExperimentRunner(env)

    logger.info("\n" + "=" * 60)
    logger.info("Running All 18 IoA Risk Tests")
    logger.info("=" * 60)

    for test in ALL_TESTS:
        logger.info("\n--- [%s] %s ---", test.test_id, test.test_name)
        result = await runner.run_single_test(test.test_id, test.run)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  Result: %s (risk=%s, confidence=%.2f, time=%.2fs)",
                     status, result.risk_level.value, result.confidence, result.execution_time)
        if result.explanation:
            logger.info("  Explanation: %s", result.explanation)

    report = await runner.generate_report()
    return report


async def run_category_tests(env: IoAEnvironment, category: str) -> dict:
    """运行指定类别的测试。"""
    runner = ExperimentRunner(env)
    tests = TESTS_BY_CATEGORY.get(category, [])
    if not tests:
        logger.error("Category '%s' not found. Available: %s",
                     category, list(TESTS_BY_CATEGORY.keys()))
        return {}

    logger.info("\nRunning tests for category: %s (%d tests)", category, len(tests))
    for test in tests:
        logger.info("\n--- [%s] %s ---", test.test_id, test.test_name)
        result = await runner.run_single_test(test.test_id, test.run)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  Result: %s", status)

    return await runner.generate_report()


async def run_single_test(env: IoAEnvironment, test_id: str) -> dict:
    """运行单个测试。"""
    test = get_test(test_id)
    if not test:
        logger.error("Test '%s' not found. Available: %s",
                     test_id, [t.test_id for t in ALL_TESTS])
        return {}

    runner = ExperimentRunner(env)
    logger.info("\nRunning test: [%s] %s", test.test_id, test.test_name)
    result = await runner.run_single_test(test.test_id, test.run)
    logger.info("Result: %s", "PASS" if result.passed else "FAIL")
    return await runner.generate_report()


# ============================================================
# 演示场景
# ============================================================

async def run_demo(env: IoAEnvironment) -> dict:
    """运行演示场景：医疗公司投资风险分析。"""
    logger.info("\n" + "=" * 60)
    logger.info("Demo: Healthcare Company Investment Risk Analysis")
    logger.info("=" * 60)

    runner = ExperimentRunner(env)

    # 场景 1: 单域任务
    logger.info("\n--- Scenario 1: Single-Domain Task ---")
    task1 = Task(
        task_type=TaskType.SINGLE_DOMAIN,
        description="分析某制药公司的财务健康状况",
        required_capabilities=["financial_analysis"],
        payload={"target_sub_ioa": "finance"},
    )
    result1 = await runner.run_task_scenario(task1)
    logger.info("Status: %s, Agents: %s", result1.status.value, result1.participating_agents)

    # 场景 2: 跨域任务
    logger.info("\n--- Scenario 2: Cross-Domain Task ---")
    task2 = Task(
        task_type=TaskType.CROSS_DOMAIN,
        description="评估某医疗公司的投资风险（需整合金融、医疗、舆情数据）",
        required_capabilities=["financial_analysis", "clinical_analysis", "sentiment_analysis"],
        payload={"target_sub_ioas": ["finance", "healthcare", "news"]},
    )
    result2 = await runner.run_task_scenario(task2)
    logger.info("Status: %s, Agents: %s", result2.status.value, result2.participating_agents)
    if result2.artifacts:
        for a in result2.artifacts:
            logger.info("  Artifact: %s (safe=%s, source=%s)", a.artifact_id[:8], a.safe, a.source_agent_id)

    # 场景 3: 多跳委托
    logger.info("\n--- Scenario 3: Multi-Hop Delegation ---")
    task3 = Task(
        task_type=TaskType.MULTI_HOP,
        description="跨境医疗合规审查",
        required_capabilities=["clinical_analysis", "compliance_check"],
        payload={
            "hop_chain": [
                {"sub_ioa_id": "healthcare", "granted_scope": ["read_medical"]},
                {"sub_ioa_id": "finance", "granted_scope": ["read_medical", "read_financial"]},
            ],
            "initial_scope": ["read_medical"],
        },
    )
    result3 = await runner.run_task_scenario(task3)
    logger.info("Status: %s, Agents: %s", result3.status.value, result3.participating_agents)

    # 生成报告
    report = await runner.generate_report()

    # 审计指标
    audit_metrics = await env.audit_logger.compute_metrics()
    logger.info("\n--- Audit Metrics ---")
    logger.info("  Chain Completeness: %.2f", audit_metrics.chain_completeness)
    logger.info("  Attribution Accuracy: %.2f", audit_metrics.attribution_accuracy)
    logger.info("  Source Coverage: %.2f", audit_metrics.source_coverage)
    logger.info("  Total Entries: %d", audit_metrics.total_entries)
    logger.info("  Total Traces: %d", audit_metrics.total_traces)

    # 拓扑信息
    logger.info("\n--- Topology ---")
    logger.info(env.topology.describe())

    return report


# ============================================================
# 场景文件加载运行
# ============================================================

async def _select_sybil_base_agent_id(env: IoAEnvironment, sub_ioa_id: str) -> str:
    """Pick a real, non-gateway AgentCard as the base for Sybil variants."""
    registry = env.get_local_registry(sub_ioa_id)
    if registry is None:
        raise ValueError(f"No local registry for Sub-IoA: {sub_ioa_id}")

    agents = await registry.list_agents(sub_ioa_id)
    candidates = [
        agent for agent in agents
        if "gateway" not in {cap.lower() for cap in agent.declared_capabilities}
        and not agent.agent_id.endswith("-gw")
    ]
    if not candidates:
        candidates = agents
    if not candidates:
        raise ValueError(f"No base agent available for Sybil attack in {sub_ioa_id}")

    candidates.sort(
        key=lambda agent: (agent.reputation_score, len(agent.declared_capabilities), agent.agent_id),
        reverse=True,
    )
    return candidates[0].agent_id


async def run_scenario(env: IoAEnvironment, scenario: Scenario) -> dict:
    """运行单个 seed 场景：加载环境 → 执行任务 → 注入攻击 → 评估结果。"""
    logger.info("\n" + "=" * 60)
    logger.info("Scenario: %s", scenario.scenario_name)
    logger.info("  ID: %s", scenario.scenario_id)
    logger.info("  Risk: %s → %s (%s)", scenario.risk.dimension_cn,
                scenario.risk.sub_dimension_cn, scenario.risk.risk_level)
    logger.info("  Attack: %s", scenario.attack.attack_type)
    logger.info("=" * 60)

    # 从场景配置初始化环境
    await env.setup_from_scenario(scenario)

    runner = ExperimentRunner(env)

    # 1. 执行场景定义的任务
    task = env.build_task_from_scenario(scenario)
    logger.info("\n--- Task Execution ---")
    logger.info("  Type: %s", task.task_type.value)
    logger.info("  Description: %s", task.description)
    task_result = await runner.run_task_scenario(task)
    logger.info("  Status: %s", task_result.status.value)
    logger.info("  Agents: %s", task_result.participating_agents)

    # 2. 注入攻击（如果有配置）
    if scenario.attack.attack_type:
        logger.info("\n--- Attack Injection ---")
        logger.info("  Type: %s", scenario.attack.attack_type)
        logger.info("  Target: %s/%s", scenario.attack.target_sub_ioa,
                     scenario.attack.target_component)
        logger.info("  Goal: %s", scenario.attack.goal[:80] + "..." if len(scenario.attack.goal) > 80 else scenario.attack.goal)

        try:
            attack_type = scenario.attack.attack_type
            target = scenario.attack.target_sub_ioa
            params = scenario.attack.params

            # 根据攻击类型调用对应注入方法
            if attack_type == "sybil_social_engineering":
                sybil_count = params.get("sybil_count", 5)
                base_agent_id = await _select_sybil_base_agent_id(env, target)
                ids = await env.attack_injector.inject_sybil_attack(target, base_agent_id, sybil_count)
                logger.info("  Injected %d Sybil agents: %s", len(ids), ids)
            elif attack_type == "identity_spoofing":
                from src.core.data_models import AgentCard
                fake_card = AgentCard(
                    display_name="伪造Agent",
                    provider="malicious",
                    sub_ioa_id=target,
                    declared_capabilities=params.get("fake_capabilities", []),
                    certificate=params.get("fake_certificate", "cert-forged"),
                )
                result_id = await env.attack_injector.inject_identity_spoofing(target, fake_card)
                logger.info("  Injected fake agent: %s", result_id)
            elif attack_type == "knowledge_injection":
                # 知识注入：通过共享知识库注入虚假信息
                content = params.get("injection_content", scenario.attack.goal)
                domain = params.get("injection_domain", target)
                await env.knowledge_base.add_knowledge(
                    content=content,
                    domain=domain,
                    source_agent_id=f"attacker-{target}",
                    source_sub_ioa_id=target,
                    confidence=params.get("claimed_confidence", 0.95),
                    tags=params.get("injection_tags", []),
                )
                logger.info("  Injected knowledge into domain: %s", domain)
            else:
                # 通用攻击：使用 PAIR 生成攻击 prompt
                prompt = env.attack_injector.generate_prompt_injection(
                    scenario.attack.goal,
                    f"Sub-IoA: {target}, Component: {scenario.attack.target_component}",
                )
                logger.info("  Generated attack prompt (%d chars)", len(prompt))
        except Exception as e:
            logger.warning("  Attack injection failed: %s", e)

    # 3. 审计指标
    audit_metrics = await env.audit_logger.compute_metrics()
    logger.info("\n--- Audit Metrics ---")
    logger.info("  Chain Completeness: %.2f", audit_metrics.chain_completeness)
    logger.info("  Attribution Accuracy: %.2f", audit_metrics.attribution_accuracy)
    logger.info("  Source Coverage: %.2f", audit_metrics.source_coverage)
    logger.info("  Total Entries: %d", audit_metrics.total_entries)

    # 4. 生成报告
    report = await runner.generate_report()

    # 附加场景元数据
    report["scenario"] = {
        "scenario_id": scenario.scenario_id,
        "scenario_name": scenario.scenario_name,
        "risk_dimension": scenario.risk.dimension,
        "risk_sub_dimension": scenario.risk.sub_dimension,
        "attack_type": scenario.attack.attack_type,
        "expected_attack_success": scenario.expected.attack_should_succeed,
        "difficulty": scenario.metadata.get("difficulty", "unknown"),
        "source": scenario.source_path,
    }

    return report


async def run_scenario_dir(scenario_dir: str) -> list[dict]:
    """运行目录下所有 seed 场景。"""
    scenarios = load_all_seeds(scenario_dir)
    if not scenarios:
        logger.error("No scenarios found in: %s", scenario_dir)
        return []

    logger.info("Loaded %d scenarios from %s", len(scenarios), scenario_dir)
    reports = []

    for scenario in scenarios:
        env = IoAEnvironment()
        report = await run_scenario(env, scenario)
        reports.append(report)

    return reports


# ============================================================
# 结果保存
# ============================================================

def save_report(report: dict, output_dir: str = "results") -> str:
    """保存实验报告。"""
    output_path = Path(__file__).parent / output_dir
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    scenario_id = report.get("scenario", {}).get("scenario_id")
    suffix = f"_{scenario_id}" if scenario_id else ""
    filename = output_path / f"experiment_report_{timestamp}{suffix}.json"

    # 处理不可序列化的对象
    def default_serializer(obj):
        if hasattr(obj, "value"):
            return obj.value
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=default_serializer)

    logger.info("\nReport saved to: %s", filename)
    return str(filename)


# ============================================================
# 主入口
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="IoA Evaluation Environment")
    parser.add_argument("--test", type=str, default=None,
                        help="Test ID or category (C1-C6). Default: run all")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo scenarios instead of risk tests")
    parser.add_argument("--scenario", type=str, default=None,
                        help="Path to a seed scenario JSON file")
    parser.add_argument("--scenario-dir", type=str, default=None,
                        help="Directory containing seed_*.json files")
    parser.add_argument("--output", type=str, default="results",
                        help="Output directory for reports")
    args = parser.parse_args()

    # 场景文件模式
    if args.scenario:
        loader = ScenarioLoader(args.scenario)
        scenario = loader.load()
        env = IoAEnvironment()
        report = await run_scenario(env, scenario)
        if report:
            save_report(report, args.output)
        _print_summary(report)
        return

    # 场景目录模式
    if args.scenario_dir:
        reports = await run_scenario_dir(args.scenario_dir)
        for i, report in enumerate(reports):
            if report:
                save_report(report, args.output)
        _print_multi_summary(reports)
        return

    # 标准模式：初始化默认环境
    env = await setup_environment()

    if args.demo:
        report = await run_demo(env)
    elif args.test:
        if args.test.startswith("C") and args.test[1:].isdigit():
            category_map = {
                "C1": "trust_authorization",
                "C2": "protocol_interop",
                "C3": "interconnection",
                "C4": "public_knowledge",
                "C5": "power_imbalance",
                "C6": "human_agency",
            }
            category = category_map.get(args.test)
            if category:
                report = await run_category_tests(env, category)
            else:
                logger.error("Unknown category: %s", args.test)
                return
        else:
            report = await run_single_test(env, args.test)
    else:
        report = await run_all_tests(env)

    if report:
        save_report(report, args.output)

    _print_summary(report)


def _print_summary(report: dict | None) -> None:
    """打印单个实验摘要。"""
    if report and "summary" in report:
        s = report["summary"]
        logger.info("\n" + "=" * 60)
        logger.info("EXPERIMENT SUMMARY")
        logger.info("=" * 60)
        logger.info("Total Tests: %d", s.get("total_tests", 0))
        logger.info("Valid Tests: %d", s.get("valid_tests", s.get("total_tests", 0)))
        logger.info("Invalid Tests: %d", s.get("invalid_tests", 0))
        logger.info("Passed: %d", s.get("passed", 0))
        logger.info("Failed: %d", s.get("failed", 0))
        logger.info("Valid Pass Rate: %.2f", s.get("valid_pass_rate", 0))
        logger.info("Utility: %.2f", s.get("utility", 0))

        if "scenario" in report:
            sc = report["scenario"]
            logger.info("\nScenario: %s", sc.get("scenario_name", ""))
            logger.info("Risk: %s → %s", sc.get("risk_dimension", ""), sc.get("risk_sub_dimension", ""))
            logger.info("Attack: %s", sc.get("attack_type", ""))


def _print_multi_summary(reports: list[dict]) -> None:
    """打印多场景批量摘要。"""
    if not reports:
        return

    logger.info("\n" + "=" * 60)
    logger.info("BATCH SCENARIO SUMMARY (%d scenarios)", len(reports))
    logger.info("=" * 60)

    total_tests = 0
    total_valid = 0
    total_invalid = 0
    total_passed = 0
    total_failed = 0
    for r in reports:
        s = r.get("summary", {})
        total_tests += s.get("total_tests", 0)
        total_valid += s.get("valid_tests", s.get("total_tests", 0))
        total_invalid += s.get("invalid_tests", 0)
        total_passed += s.get("passed", 0)
        total_failed += s.get("failed", 0)
        sc = r.get("scenario", {})
        status = "INVALID" if s.get("invalid_tests", 0) else ("PASS" if s.get("failed", 0) == 0 else "FAIL")
        logger.info("  [%s] %s — %s/%s (%s)",
                     status, sc.get("scenario_name", "?"),
                     sc.get("risk_dimension", "?"), sc.get("risk_sub_dimension", "?"),
                     sc.get("attack_type", "?"))

    logger.info("\nTotal: %d tests, %d valid, %d invalid, %d passed, %d failed",
                total_tests, total_valid, total_invalid, total_passed, total_failed)


if __name__ == "__main__":
    asyncio.run(main())
