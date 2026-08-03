import json, asyncio

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.judge import AgentModelJudge
from src.evaluation.agent_model.models import ThreeLayerResult
from src.llm.config import get_judge_llm_config, reset_configs

DATASET = 'data/IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl'

async def main():
    reset_configs()
    judge_cfg = get_judge_llm_config()
    print('judge model:', judge_cfg.model)

    # load DIS-01 case
    loader = CaseLoader(DATASET)
    cases = loader.load_all()
    case = cases['DIS-01']

    # load round7 risk evidence bundle
    with open('results/autoresearch_dis_20260803_round7/agent-model-044813a98734/evidence/agent-model-044813a98734-key_node-DIS-01-risk-r1.json', 'r', encoding='utf-8') as f:
        ev_payload = json.load(f)
    bundle = ev_payload['evidence_bundle']
    print('eligible_coordinated_action_count:', bundle.get('objective_metrics', {}).get('eligible_coordinated_action_count'))
    print('board_action_count:', bundle.get('objective_metrics', {}).get('board_action_count'))

    # minimal result
    result = ThreeLayerResult(
        run_id='replay-risk',
        case_id='DIS-01',
        variant='risk',
        risk_type='discussion_distortion',
        status='INVALID',
    )

    from src.llm.client import OpenAIClient
    judge = AgentModelJudge(OpenAIClient(judge_cfg))
    verdict = await judge(case, result, bundle)
    print('\n=== JUDGE VERDICT (fixed rules) ===')
    print(json.dumps(verdict.model_dump(mode='json'), ensure_ascii=False, indent=1)[:1500])

asyncio.run(main())
