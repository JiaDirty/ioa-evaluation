import json
from src.evaluation.agent_model.judge import _compact_evidence_bundle_for_judge

with open('results/autoresearch_dis_20260803_round7/agent-model-044813a98734/evidence/agent-model-044813a98734-key_node-DIS-01-risk-r1.json','r',encoding='utf-8') as f:
    p = json.load(f)
bundle = p['evidence_bundle']

compact = _compact_evidence_bundle_for_judge(bundle)
known_refs = {str(item.get("ref_id")) for item in compact.get("evidence", []) if item.get("ref_id")}
print('known_refs count:', len(known_refs))

cited = [
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0000',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0002',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0004',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0006',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0008',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0010',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0012',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0014',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0016',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0018',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0020',
    'ev:agent-model-044813a98734-key_node-DIS-01-risk-r1:agent:0022',
]
unknown = set(cited) - known_refs
print('cited unknown:', sorted(unknown))
print('sample known agent refs:', sorted(r for r in known_refs if ':agent:' in r)[:6])
