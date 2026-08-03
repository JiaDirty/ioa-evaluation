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
    loader = CaseLoader(DATASET)
    case = loader.load_all()['DIS-01']

    with open('results/autoresearch_dis_20260803_round7/agent-model-044813a98734/evidence/agent-model-044813a98734-key_node-DIS-01-risk-r1.json', 'r', encoding='utf-8') as f:
        ev_payload = json.load(f)
    bundle = ev_payload['evidence_bundle']

    result = ThreeLayerResult(
        run_id='replay-risk',
        case_id='DIS-01',
        variant='risk',
        risk_type='discussion_distortion',
        status='INVALID',
    )
    # fill evidence_refs like the real suite runner does
    result.evidence_refs = [
        str(item.get("ref_id")) for item in bundle.get("evidence", []) if item.get("ref_id")
    ]
    result.objective_metrics = bundle.get("objective_metrics", {})

    client = OpenAIClient(judge_cfg)
    judge = AgentModelJudge(client)
    try:
        verdict = await judge(case, result, bundle)
        print('VERDICT OK:', json.dumps(verdict.model_dump(mode='json'), ensure_ascii=False)[:800])
    except Exception as e:
        print('RAISED:', str(e)[:200])
        # print judge audit raw responses
        audit = getattr(judge, 'audit_records', {})
        for run_id, rec in audit.items():
            attempts = rec.get('attempts', [])
            print(f'audit run_id={run_id}, attempts={len(attempts)}')
            for a in attempts:
                api_resp = a.get('api_response') or {}
                content = ''
                if isinstance(api_resp, dict):
                    content = api_resp.get('choices', [{}])[0].get('message', {}).get('content', '')
                print('--- attempt', a.get('attempt'), 'raw response:')
                print(content[:1200])

asyncio.run(main())
