import json, sys
sys.path.insert(0, 'src')

from src.evaluation.agent_model.categories.discussion import _deterministic_manipulation_count, _SUPPORT_SAFETY_MARKERS, _DOUBT_SAFETY_MARKERS

base = 'results/autoresearch_dis_20260803_round10/agent-model-58fabea40dea'

board_events = []
controller_ids = [f'coord-01-{i}' for i in range(1, 9)]
with open(f'{base}/execution_trace.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        rec = json.loads(line)
        if rec.get('record_type') == 'agent_step' and rec.get('case_id') == 'DIS-01' and rec.get('variant') == 'risk':
            for ev in rec.get('events', []):
                if ev.get('event_type') == 'board_action':
                    board_events.append(ev)

print('board events:', len(board_events))
print('event top-level keys:', list(board_events[0].keys()))
print('event role_id:', repr(board_events[0].get('role_id')))

# per-event debug
matched = 0
for i, e in enumerate(board_events):
    payload = e.get('payload', {})
    role = e.get('role_id') or payload.get('author_role') or payload.get('by_role')
    in_controller = role in controller_ids
    action = str(payload.get('action', ''))
    content = str(payload.get('content') or payload.get('arguments', {}).get('content') or '')
    s = sum(m in content for m in _SUPPORT_SAFETY_MARKERS)
    d = sum(m in content for m in _DOUBT_SAFETY_MARKERS)
    if in_controller:
        matched += 1
        if i < 12 or s > d:
            print(f'  [{i}] role={role!r} in_ctrl={in_controller} action={action} s={s} d={d} counted={s>d} content={content[:80]}')
print('matched controller events:', matched)
print('final count:', _deterministic_manipulation_count(board_events, controller_ids))
