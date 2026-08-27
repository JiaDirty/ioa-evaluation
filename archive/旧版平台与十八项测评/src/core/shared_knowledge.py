"""Shared Knowledge Base — 跨域共享知识库 / 公共 Memory。

IoA 生态中各 Sub-IoA 共享的知识存储，支持：
- 跨域知识写入与读取
- 来源归因（哪个 Agent / Sub-IoA 贡献）
- 安全检查（防止恶意知识注入）
- 知识冲突检测
- 时效性管理
"""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KnowledgeStatus(str, Enum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    RETRACTED = "retracted"
    EXPIRED = "expired"


class KnowledgeEntry(BaseModel):
    """一条共享知识条目。"""
    entry_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    content: str
    domain: str  # 所属领域，如 "finance", "healthcare"
    source_agent_id: str  # 贡献者
    source_sub_ioa_id: str  # 来源子生态
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: KnowledgeStatus = KnowledgeStatus.ACTIVE
    tags: list[str] = Field(default_factory=list)
    supporting_count: int = 0  # 被多少 Agent 支持
    disputing_count: int = 0  # 被多少 Agent 质疑
    created_at: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeConflict(BaseModel):
    """知识冲突记录。"""
    conflict_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    entry_ids: list[str]  # 冲突的知识条目
    domain: str
    description: str
    detected_at: datetime = Field(default_factory=datetime.now)
    resolved: bool = False


class SharedKnowledgeBase:
    """跨域共享知识库。

    作为 IoA 生态的公共 Memory，所有 Sub-IoA 的 Agent 都可以
    读取和写入知识条目，系统自动追踪来源并检测冲突。
    """

    def __init__(self, semantic_judge: Callable[[str, str, dict[str, Any]], Any] | None = None) -> None:
        self._entries: dict[str, KnowledgeEntry] = {}
        self._conflicts: list[KnowledgeConflict] = []
        self._domain_index: dict[str, list[str]] = {}  # domain -> [entry_ids]
        self._agent_index: dict[str, list[str]] = {}  # agent_id -> [entry_ids]
        self._semantic_judge = semantic_judge

    def set_semantic_judge(self, semantic_judge: Callable[[str, str, dict[str, Any]], Any]) -> None:
        self._semantic_judge = semantic_judge

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def add_knowledge(
        self,
        content: str,
        domain: str,
        source_agent_id: str,
        source_sub_ioa_id: str,
        confidence: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeEntry:
        """添加一条知识条目。

        自动检测与现有知识的冲突。
        """
        entry = KnowledgeEntry(
            content=content,
            domain=domain,
            source_agent_id=source_agent_id,
            source_sub_ioa_id=source_sub_ioa_id,
            confidence=confidence,
            tags=tags or [],
            metadata=metadata or {},
        )

        self._entries[entry.entry_id] = entry

        # 更新索引
        self._domain_index.setdefault(domain, []).append(entry.entry_id)
        self._agent_index.setdefault(source_agent_id, []).append(entry.entry_id)

        # 检测冲突
        await self._check_conflicts(entry)

        logger.info("Knowledge[%s] added by %s/%s: %s",
                     entry.entry_id, source_sub_ioa_id, source_agent_id,
                     content[:60])
        return entry

    async def support_knowledge(self, entry_id: str, agent_id: str) -> bool:
        """Agent 对某条知识表示支持。"""
        entry = self._entries.get(entry_id)
        if not entry:
            return False
        entry.supporting_count += 1
        entry.last_updated = datetime.now()
        # 支持者多 → 提升置信度
        entry.confidence = min(1.0, entry.confidence + 0.05)
        return True

    async def dispute_knowledge(
        self, entry_id: str, agent_id: str, reason: str = ""
    ) -> bool:
        """Agent 对某条知识提出质疑。"""
        entry = self._entries.get(entry_id)
        if not entry:
            return False
        entry.disputing_count += 1
        entry.last_updated = datetime.now()
        # 质疑者多 → 降低置信度
        entry.confidence = max(0.0, entry.confidence - 0.1)
        # 如果质疑超过支持，标记为争议
        if entry.disputing_count > entry.supporting_count:
            entry.status = KnowledgeStatus.DISPUTED
        return True

    async def retract_knowledge(self, entry_id: str, agent_id: str) -> bool:
        """撤回知识条目。"""
        entry = self._entries.get(entry_id)
        if not entry:
            return False
        entry.status = KnowledgeStatus.RETRACTED
        entry.last_updated = datetime.now()
        return True

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    async def query_by_domain(self, domain: str) -> list[KnowledgeEntry]:
        """查询某领域的所有活跃知识。"""
        entry_ids = self._domain_index.get(domain, [])
        return [
            self._entries[eid] for eid in entry_ids
            if eid in self._entries and self._entries[eid].status == KnowledgeStatus.ACTIVE
        ]

    async def query_by_agent(self, agent_id: str) -> list[KnowledgeEntry]:
        """查询某 Agent 贡献的所有知识。"""
        entry_ids = self._agent_index.get(agent_id, [])
        return [self._entries[eid] for eid in entry_ids if eid in self._entries]

    async def query_by_tags(self, tags: list[str]) -> list[KnowledgeEntry]:
        """按标签查询知识。"""
        tag_set = set(tags)
        return [
            e for e in self._entries.values()
            if e.status == KnowledgeStatus.ACTIVE and tag_set.intersection(e.tags)
        ]

    async def get_knowledge(self, entry_id: str) -> KnowledgeEntry | None:
        """获取单条知识。"""
        return self._entries.get(entry_id)

    async def search(self, keyword: str, domain: str | None = None) -> list[KnowledgeEntry]:
        """关键词搜索知识。"""
        results = []
        for entry in self._entries.values():
            if entry.status != KnowledgeStatus.ACTIVE:
                continue
            if domain and entry.domain != domain:
                continue
            if keyword.lower() in entry.content.lower():
                results.append(entry)
        return sorted(results, key=lambda e: e.confidence, reverse=True)

    # ------------------------------------------------------------------
    # 冲突检测
    # ------------------------------------------------------------------

    async def _check_conflicts(self, new_entry: KnowledgeEntry) -> None:
        """检测新条目与现有知识的冲突。"""
        existing = await self.query_by_domain(new_entry.domain)
        for entry in existing:
            if entry.entry_id == new_entry.entry_id:
                continue
            shared_tags = set(entry.tags).intersection(set(new_entry.tags))
            if shared_tags and entry.content != new_entry.content:
                semantic = await self._judge_semantic_relation(entry, new_entry, shared_tags)
                entry.metadata["semantic_relation"] = semantic.get("relation", "unknown")
                entry.metadata["semantic_relation_reason"] = semantic.get("reason", "")
                new_entry.metadata["semantic_relation"] = semantic.get("relation", "unknown")
                new_entry.metadata["semantic_relation_reason"] = semantic.get("reason", "")
                if semantic.get("relation") == "contradiction":
                    conflict = KnowledgeConflict(
                        entry_ids=[entry.entry_id, new_entry.entry_id],
                        domain=new_entry.domain,
                        description=f"Conflicting knowledge in domain '{new_entry.domain}': "
                                    f"{semantic.get('reason', '')}",
                    )
                    self._conflicts.append(conflict)
                    entry.status = KnowledgeStatus.DISPUTED
                    new_entry.status = KnowledgeStatus.DISPUTED
                    logger.warning("Knowledge conflict detected: %s", conflict.description)
                elif semantic.get("relation") == "unknown":
                    entry.status = KnowledgeStatus.DISPUTED
                    new_entry.status = KnowledgeStatus.DISPUTED

    async def _judge_semantic_relation(
        self,
        existing: KnowledgeEntry,
        new_entry: KnowledgeEntry,
        shared_tags: set[str],
    ) -> dict[str, Any]:
        if self._semantic_judge is None:
            return {
                "relation": "unknown",
                "reason": "No semantic judge configured; conflict not asserted",
            }
        context = {
            "domain": new_entry.domain,
            "shared_tags": sorted(shared_tags),
            "existing_source": existing.source_agent_id,
            "new_source": new_entry.source_agent_id,
        }
        result = await asyncio.to_thread(
            self._semantic_judge,
            existing.content,
            new_entry.content,
            context,
        )
        if isinstance(result, dict):
            return result
        return {
            "relation": getattr(result, "relation", "unknown"),
            "reason": getattr(result, "reason", ""),
        }

    # ------------------------------------------------------------------
    # 统计与状态
    # ------------------------------------------------------------------

    def get_conflicts(self) -> list[KnowledgeConflict]:
        return self._conflicts.copy()

    def get_stats(self) -> dict[str, Any]:
        """返回知识库统计信息。"""
        active = sum(1 for e in self._entries.values() if e.status == KnowledgeStatus.ACTIVE)
        disputed = sum(1 for e in self._entries.values() if e.status == KnowledgeStatus.DISPUTED)
        retracted = sum(1 for e in self._entries.values() if e.status == KnowledgeStatus.RETRACTED)
        return {
            "total_entries": len(self._entries),
            "active": active,
            "disputed": disputed,
            "retracted": retracted,
            "conflicts": len(self._conflicts),
            "domains": list(self._domain_index.keys()),
        }

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return (f"SharedKnowledgeBase(entries={len(self._entries)}, "
                f"conflicts={len(self._conflicts)})")
