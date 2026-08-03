import json, asyncio

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.judge import AgentModelJudge
from src.evaluation.agent_model.models import ThreeLayerResult
from src.llm.config import get_judge_llm_config, reset_configs
from src.llm.client import OpenAIClient

DATASET = 'data/IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl'

async def main():
    reset_configs()
    judge_cfg = get_judge_llm_config()
    case = CaseLoader(DATASET).load_all()['DIS-01']

    with open('results/autoresearch_dis_20260803_round7/agent-model-044813a98734/evidence/agent-model-044813a98734-key_node-DIS-01-risk-r1.json', 'r', encoding='utf-8') as f:
        bundle = json.load(f)['evidence_bundle']

    result = ThreeLayerResult(
        run_id='replay-risk', case_id='DIS-01', variant='risk',
        risk_type='discussion_distortion', status='INVALID',
    )
    result.evidence_refs = [str(i.get("ref_id")) for i in bundle.get("evidence", []) if i.get("ref_id")]
    result.objective_metrics = bundle.get("objective_metrics", {})

    judge = AgentModelJudge(OpenAIClient(judge_cfg))
    try:
        v = await judge(case, result, bundle)
        print('OK:', json.dumps(v.model_dump(mode='json'), ensure_ascii=False)[:600])
    except Exception as e:
        print('RAISED:', str(e)[:150])
        audit = getattr(judge, 'audit_records', {})
        for rid, rec in audit.items():
            for a in rec.get('attempts', []):
                api_resp = a.get('api_response') or {}
                content = ''
                if isinstance(api_resp, dict):
                    content = api_resp.get('choices', [{}])[0].get('message', {}).get('content', '')
                # print the semantic_metrics part
                idx = content.find('semantic_metrics')
                print(f'--- attempt {a.get("attempt")} ---')
                if idx >= 0:
                    print(content[idx-100:idx+400])
                else:
                    print('(no semantic_metrics key)')
                    print(content[-400:])

asyncio.run(main())
