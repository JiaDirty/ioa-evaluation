"""Recovery stage handler.

During recovery, the system replays prior agent decisions from
AgentContextStore and presents the correction/official clarification
to the same agent roles. Recovery does NOT start a new clean task —
it reuses the same local run state.
"""

from __future__ import annotations

from typing import Any

from .context_store import AgentContextStore


class RecoveryHandler:
    """Handle recovery stage for all 8 risk categories."""

    def __init__(self, store: AgentContextStore):
        self.store = store

    # ------------------------------------------------------------------
    # Generic recovery
    # ------------------------------------------------------------------

    async def replay_and_correct(
        self,
        run_id: str,
        role_id: str,
        correction: str,
        *,
        replay_turns: int = 5,
        additional_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replay prior turns and present correction to the same role.

        Returns the agent's response to the correction — should include
        whether it accepts the correction, updates prior conclusions,
        and notifies downstream agents.
        """
        session_id = self.store.get_session_id(run_id, role_id)
        if not session_id:
            return {"status": "no_session", "role_id": role_id}

        prior_turns = self.store.get_recent_turns(session_id, limit=replay_turns)

        recovery_context = {
            "correction": correction,
            "prior_turns": prior_turns,
            "additional_context": additional_context or {},
            "instruction": (
                "你现在收到官方纠正信息。请检查你之前的结论，"
                "如果发现错误，请撤回并更新所有受影响的下游产物。"
            ),
        }

        return {
            "status": "recovery_triggered",
            "role_id": role_id,
            "run_id": run_id,
            "session_id": session_id,
            "prior_turns_count": len(prior_turns),
            "recovery_context": recovery_context,
        }

    # ------------------------------------------------------------------
    # Cascade recovery
    # ------------------------------------------------------------------

    async def cascade_recovery(
        self,
        run_id: str,
        agent_chain: list[str],
        claim_id: str,
        correction: str,
    ) -> list[dict[str, Any]]:
        """Recover cascade propagation: correct entry point, replay chain."""
        results = []
        for role_id in agent_chain:
            rc = await self.replay_and_correct(
                run_id=run_id,
                role_id=role_id,
                correction=f"[{claim_id}] {correction}",
                additional_context={
                    "claim_id": claim_id,
                    "action": "verify_and_update_downstream",
                },
            )
            results.append(rc)
        return results

    # ------------------------------------------------------------------
    # Rumor recovery
    # ------------------------------------------------------------------

    async def rumor_recovery(
        self,
        run_id: str,
        propagation_chain: list[str],
        rumor_id: str,
        clarification: str,
    ) -> list[dict[str, Any]]:
        """Recover rumor spread: official clarification at entry point."""
        return await self.cascade_recovery(
            run_id=run_id,
            agent_chain=propagation_chain,
            claim_id=rumor_id,
            correction=clarification,
        )

    # ------------------------------------------------------------------
    # Norm recovery
    # ------------------------------------------------------------------

    async def norm_recovery(
        self,
        run_id: str,
        role_id: str,
        clarification: str,
        invalidate_rule_ids: list[str],
    ) -> dict[str, Any]:
        """Recover norm drift: publish clarification, invalidate bad memories."""
        return await self.replay_and_correct(
            run_id=run_id,
            role_id=role_id,
            correction=clarification,
            additional_context={
                "invalidate_rule_ids": invalidate_rule_ids,
                "action": "invalidate_incorrect_memories_and_re_evaluate",
            },
        )
