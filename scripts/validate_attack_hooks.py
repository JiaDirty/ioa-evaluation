from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audit.event_bus import EventBus
from src.attacks.registry import DEFAULT_ATTACK_ADAPTER_REGISTRY
from src.experiment.runner import IoAEnvironment
from src.experiment.scenario_loader import ScenarioLoader


async def main() -> None:
    bus = EventBus()
    seen = []

    def hook(event):
        seen.append(event.event_id)
        return event

    bus.add_hook(hook)
    event = bus.emit(
        task_id="task",
        trace_id="trace",
        stage="candidate_ranking",
        event_type="agentic_candidates_discovered",
        payload={"candidate_ids": ["forged-finance-analyst"]},
    )
    assert seen == [event.event_id]

    scenario = ScenarioLoader("data/seeds/seed_001_identity_spoofing.json").load()
    env = IoAEnvironment({
        "offline_deterministic": True,
        "execution_mode": "offline_deterministic",
        "auto_bind_deterministic_runtimes": True,
    })
    await env.setup_from_scenario(scenario)
    adapter = DEFAULT_ATTACK_ADAPTER_REGISTRY.create(scenario.attack.adapter)
    context = await adapter.prepare(env, scenario, {})
    assert context.prepared
    assert context.injection_applied
    trigger_event = env.event_bus.emit(
        task_id="task",
        trace_id="trace",
        stage="candidate_ranking",
        event_type="agentic_candidates_discovered",
        payload={"candidate_ids": ["forged-finance-analyst"]},
    )
    assert await adapter.should_trigger(trigger_event, context)
    await adapter.inject(trigger_event, context)
    assert any(log.target_event_id == trigger_event.event_id for log in context.attack_logs)
    print("validate_attack_hooks: OK")


if __name__ == "__main__":
    asyncio.run(main())
