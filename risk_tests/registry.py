"""IoA 风险测试注册表。

收集所有 18 个 IoA 风险测试，提供统一的访问接口。
"""

from __future__ import annotations

from .base_test import BaseIoARiskTest

# C1: 信任与授权失灵
from .trust_authorization.identity_spoofing import IdentitySpoofingTest
from .trust_authorization.registry_distortion import RegistryDistortionTest
from .trust_authorization.delegation_drift import DelegationDriftTest

# C2: 协议互操作失配
from .protocol_interop.negotiation_pollution import NegotiationPollutionTest
from .protocol_interop.interop_mismatch import InteropMismatchTest
from .protocol_interop.accountability_break import AccountabilityBreakTest

# C3: 互联扩散与可推断性
from .interconnection.cascade_propagation import CascadePropagationTest
from .interconnection.structure_exposure import StructureExposureTest, BehaviorInferenceTest

# C4: 公共知识失真
from .public_knowledge.ecosystem_consensus import (
    EcosystemConsensusTest, RumorSpreadTest, NormDriftTest,
)

# C5: 生态权力失衡
from .power_imbalance.reputation_monopoly import (
    ReputationMonopolyTest, IncentiveMismatchTest, NodeManipulationTest,
)

# C6: 人机能动性侵蚀
from .human_agency.judgment_surrender import (
    JudgmentSurrenderTest, DiscussionDistortionTest, AgencyErosionTest,
)


# ============================================================
# 测试注册表
# ============================================================

ALL_TESTS: list[BaseIoARiskTest] = [
    # C1: 信任与授权失灵
    IdentitySpoofingTest(),
    RegistryDistortionTest(),
    DelegationDriftTest(),
    # C2: 协议互操作失配
    NegotiationPollutionTest(),
    InteropMismatchTest(),
    AccountabilityBreakTest(),
    # C3: 互联扩散与可推断性
    CascadePropagationTest(),
    StructureExposureTest(),
    BehaviorInferenceTest(),
    # C4: 公共知识失真
    EcosystemConsensusTest(),
    RumorSpreadTest(),
    NormDriftTest(),
    # C5: 生态权力失衡
    ReputationMonopolyTest(),
    IncentiveMismatchTest(),
    NodeManipulationTest(),
    # C6: 人机能动性侵蚀
    JudgmentSurrenderTest(),
    DiscussionDistortionTest(),
    AgencyErosionTest(),
]

# 按类别索引
TESTS_BY_CATEGORY: dict[str, list[BaseIoARiskTest]] = {}
for test in ALL_TESTS:
    TESTS_BY_CATEGORY.setdefault(test.category, []).append(test)

# 按 ID 索引
TESTS_BY_ID: dict[str, BaseIoARiskTest] = {t.test_id: t for t in ALL_TESTS}


def get_test(test_id: str) -> BaseIoARiskTest | None:
    return TESTS_BY_ID.get(test_id)


def get_tests_by_category(category: str) -> list[BaseIoARiskTest]:
    return TESTS_BY_CATEGORY.get(category, [])


def list_all_tests() -> list[dict[str, str]]:
    return [
        {"test_id": t.test_id, "test_name": t.test_name, "category": t.category}
        for t in ALL_TESTS
    ]
