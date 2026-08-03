import json

base = 'results/autoresearch_dis_20260803_round10/agent-model-58fabea40dea'

with open(f'{base}/execution_trace.jsonl', 'r', encoding='utf-8') as f:
    records = [json.loads(l) for l in f if l.strip()]

# find DIS risk coordinated posts
print('=== risk coordinated posts ===')
seen = set()
for rec in records:
    if rec.get('record_type') == 'agent_step' and rec.get('case_id') == 'DIS-01' and rec.get('variant') == 'risk':
        role = rec.get('role_id')
        if not str(role).startswith('coord'):
            continue
        for ev in rec.get('events', []):
            if ev.get('event_type') == 'tool_call':
                pl = ev.get('payload', {})
                args = pl.get('arguments', {})
                content = str(args.get('content', ''))[:150]
                key = (role, content[:40])
                if key in seen:
                    continue
                seen.add(key)
                print(f'  {role}: [{args.get("source_ids")}] {content}')
