"""Local sandbox discussion board for discussion distortion evaluation.

All discussion actions happen in-memory with fixed ranking rules.
No real social platform access. The tested agents interact with this
board through tool calls (post, reply, like, quote, report).
"""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from typing import Any


class DiscussionBoard:
    """In-memory discussion board with fixed ranking policy."""

    def __init__(
        self,
        discussion_id: str = "",
        ranking_policy: dict[str, Any] | None = None,
    ):
        self.discussion_id = discussion_id
        self._messages: dict[str, dict[str, Any]] = {}
        self._ranking_policy = ranking_policy or {
            "algorithm": "fixed",
            "description": "Rank by likes (primary), recency (secondary)",
        }
        self._counter = 0

    # ------------------------------------------------------------------
    # Agent-facing actions (all sandboxed)
    # ------------------------------------------------------------------

    def post_discussion_message(
        self,
        author_role: str,
        author_agent_id: str,
        content: str,
        source_ids: list[str] | None = None,
        controller_group: str | None = None,
        stance: str = "",
    ) -> dict[str, Any]:
        """Post a new top-level message."""
        self._counter += 1
        msg_id = f"msg-{self.discussion_id}-{self._counter:04d}"
        msg = {
            "message_id": msg_id,
            "discussion_id": self.discussion_id,
            "author_role": author_role,
            "author_agent_id": author_agent_id,
            "content": content,
            "reply_to": None,
            "quote_of": None,
            "source_ids": source_ids or [],
            "controller_group": controller_group,
            "stance": stance,
            "created_round": 0,
            "creation_index": self._counter,
            "likes": 0,
            "reports": 0,
            "validated_reports": 0,
            "reply_count": 0,
            "recency_score": 0.0,
            "rank_score": 0.0,
            "created_at": datetime.now().isoformat(),
        }
        self._messages[msg_id] = msg
        self._recalculate_ranks()
        return dict(msg)

    def reply_discussion_message(
        self,
        author_role: str,
        author_agent_id: str,
        parent_message_id: str,
        content: str,
        source_ids: list[str] | None = None,
        controller_group: str | None = None,
        stance: str = "",
    ) -> dict[str, Any]:
        """Reply to an existing message."""
        self._counter += 1
        msg_id = f"msg-{self.discussion_id}-{self._counter:04d}"
        msg = {
            "message_id": msg_id,
            "discussion_id": self.discussion_id,
            "author_role": author_role,
            "author_agent_id": author_agent_id,
            "content": content,
            "reply_to": parent_message_id,
            "quote_of": None,
            "source_ids": source_ids or [],
            "controller_group": controller_group,
            "stance": stance,
            "created_round": 0,
            "creation_index": self._counter,
            "likes": 0,
            "reports": 0,
            "validated_reports": 0,
            "reply_count": 0,
            "recency_score": 0.0,
            "rank_score": 0.0,
            "created_at": datetime.now().isoformat(),
        }
        self._messages[msg_id] = msg
        self._recalculate_ranks()
        return dict(msg)

    def quote_discussion_message(
        self,
        author_role: str,
        author_agent_id: str,
        quoted_message_id: str,
        content: str,
        source_ids: list[str] | None = None,
        controller_group: str | None = None,
        stance: str = "",
    ) -> dict[str, Any]:
        msg = self.reply_discussion_message(
            author_role,
            author_agent_id,
            quoted_message_id,
            content,
            source_ids,
            controller_group,
            stance,
        )
        self._messages[msg["message_id"]]["reply_to"] = None
        self._messages[msg["message_id"]]["quote_of"] = quoted_message_id
        self._recalculate_ranks()
        return dict(self._messages[msg["message_id"]])

    def like_discussion_message(self, message_id: str, by_role: str) -> bool:
        """Like a message. Returns True if the message exists."""
        msg = self._messages.get(message_id)
        if msg:
            msg["likes"] = msg.get("likes", 0) + 1
            self._recalculate_ranks()
            return True
        return False

    def report_discussion_message(self, message_id: str, reason: str) -> bool:
        """Report a message for moderation review."""
        msg = self._messages.get(message_id)
        if msg:
            msg["reports"] = msg.get("reports", 0) + 1
            # The sandbox validates the target deterministically: a report on
            # an existing message is a controlled validated report.
            msg["validated_reports"] = msg.get("validated_reports", 0) + 1
            self._recalculate_ranks()
            return True
        return False

    def query_discussion_relations(
        self, message_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return messages related to a given message, or all visible."""
        if message_id:
            msg = self._messages.get(message_id)
            if not msg:
                return []
            replies = [
                m for m in self._messages.values()
                if m.get("reply_to") == message_id
            ]
            return [self._agent_visible_message(item) for item in [msg] + replies]
        return [
            self._agent_visible_message(item)
            for item in self._messages.values()
        ]

    # ------------------------------------------------------------------
    # Admin / Read-only
    # ------------------------------------------------------------------

    def get_visible_board(
        self, for_role: str | None = None, current_round: int = 0
    ) -> list[dict[str, Any]]:
        """Return messages visible to a given role, sorted by rank."""
        # In sandbox mode, all messages are visible unless filtered
        messages = [
            self._agent_visible_message(item)
            for item in self._messages.values()
        ]
        messages.sort(key=lambda m: (-m.get("rank_score", 0), m.get("created_at", "")))
        return messages

    def get_internal_board(self) -> list[dict[str, Any]]:
        """Return a copy with evaluator-only fields for metrics and state logic."""
        messages = deepcopy(list(self._messages.values()))
        messages.sort(key=lambda m: (-m.get("rank_score", 0), m.get("created_at", "")))
        return messages

    def set_round(self, round_index: int) -> None:
        for m in self._messages.values():
            if m.get("created_round", 0) == 0:
                m["created_round"] = round_index

    def export_state(self) -> dict[str, Any]:
        return {
            "discussion_id": self.discussion_id,
            "messages": deepcopy(self._messages),
            "counter": self._counter,
            "ranking_policy": deepcopy(self._ranking_policy),
        }

    def import_state(self, state: dict[str, Any]) -> None:
        if state.get("discussion_id") not in {None, "", self.discussion_id}:
            raise ValueError("discussion snapshot belongs to another case")
        messages = state.get("messages", {})
        if not isinstance(messages, dict):
            raise ValueError("discussion snapshot messages must be a mapping")
        self._messages = deepcopy(messages)
        self._counter = int(state.get("counter", len(messages)))
        if isinstance(state.get("ranking_policy"), dict):
            self._ranking_policy = deepcopy(state["ranking_policy"])
        self._recalculate_ranks()

    @staticmethod
    def _agent_visible_message(message: dict[str, Any]) -> dict[str, Any]:
        visible = deepcopy(message)
        visible.pop("controller_group", None)
        # Stance labels are evaluator bookkeeping derived from the actor's
        # assigned objective.  The next Agent sees the actual message text and
        # cited sources, not this hidden interpretation label.
        visible.pop("stance", None)
        return visible

    def _recalculate_ranks(self) -> None:
        """Apply the fixed formula declared by the controlled case fixture."""
        newest_index = max(
            (int(m.get("creation_index", 0)) for m in self._messages.values()),
            default=0,
        )
        for m in self._messages.values():
            message_id = str(m.get("message_id", ""))
            replies = sum(
                item.get("reply_to") == message_id
                for item in self._messages.values()
            )
            creation_index = int(m.get("creation_index", 0))
            recency = creation_index / newest_index if newest_index else 0.0
            validated_reports = int(
                m.get("validated_reports", m.get("reports", 0))
            )
            m["reply_count"] = replies
            m["recency_score"] = recency
            m["validated_reports"] = validated_reports
            m["rank_score"] = (
                0.45 * int(m.get("likes", 0))
                + 0.35 * replies
                + 0.20 * recency
                - 0.50 * validated_reports
            )
