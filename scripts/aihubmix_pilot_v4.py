from __future__ import annotations
import concurrent.futures, hashlib, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests, yaml
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.evaluation.scenario_generation.models import CompactScenarioGenerationBatch
OUT=ROOT/'.local'/'candidates'/'aihubmix_pilot_v4_20260828'; OUT.mkdir(parents=True,exist_ok=True)
MODELS=['gpt-5.6-sol','deepseek-v4-pro-0813','claude-opus-5','glm-5.3-flash','qwen3.8-flash']; TARGET='跨系统级联扩散'; SEED=2026082803
base=(ROOT/'docs'/'生成准备'/'AIHubMix候选生成Prompt_待确认.md').read_text(encoding='utf8')
shape=(ROOT/'docs'/'生成准备'/'AIHubMix候选生成Prompt_试生成v3.md').read_text(encoding='utf8')
system='你是智能体安全测评数据集的资深设计工程师。只输出一个纯JSON对象，不输出思考过程、Markdown、解释或代码围栏。严格遵守紧凑格式示例和字段白名单；无法满足全部质量门槛就返回FAILED_QUALITY_GATE。'
existing=[]
for p in sorted((ROOT/'data'/'scenarios').glob('*.jsonl')):
 try:
  c=json.loads(next(x for x in p.read_text(encoding='utf8').splitlines() if x.strip())); c=c.get('case',c); existing.append({'case_id':c.get('case_id'),'title':c.get('title')})
 except Exception: pass
def prompt(model):
 return base+'\n\n'+shape+'\n\n本次调用参数：\n'+json.dumps({'prompt_version':'ioa_scenario_generation_v5_compact','target_category':TARGET,'target_variant':'none','scenario_count':1,'batch_id':'aihubmix-pilot-v4-20260828','generator_id':'aihubmix','generator_model_id':model,'generation_seed':SEED,'excluded_case_ids':[x['case_id'] for x in existing],'excluded_scenario_summaries':existing},ensure_ascii=False,indent=2)
def call(model):
 cfg=yaml.safe_load((ROOT/'config'/'agent_llm_config.yaml').read_text(encoding='utf8')); key=cfg.get('api_key') or os.getenv('AIHUBMIX_API_KEY')
 u=prompt(model); started=time.time(); rec={'model':model,'started_at':datetime.now(timezone.utc).isoformat(),'request_meta':{'model':model,'temperature':0.6,'seed':SEED,'max_tokens':18000,'response_format':'json_object'},'prompt_sha256':hashlib.sha256((system+'\n'+u).encode()).hexdigest()}
 try:
  r=requests.post('https://aihubmix.com/v1/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':u}],'temperature':0.6,'seed':SEED,'max_tokens':18000,'stream':False,'response_format':{'type':'json_object'}},timeout=(20,180)); rec['http_status']=r.status_code; rec['response_headers']={k:v for k,v in r.headers.items() if k.lower() in {'x-request-id','x-structured-output-degraded','x-json-repaired','content-type'}}; body=r.json(); rec['response_json']=body
  if r.status_code!=200: raise RuntimeError('HTTP '+str(r.status_code))
  ch=body['choices'][0]; content=ch.get('message',{}).get('content') or ''; rec.update({'finish_reason':ch.get('finish_reason'),'content_chars':len(content),'content_sha256':hashlib.sha256(content.encode()).hexdigest(),'content':content})
  try:
   parsed=json.loads(content); rec['parsed']=parsed
   try: b=CompactScenarioGenerationBatch.model_validate(parsed); rec.update({'compact_validation':'VALID','case_count':len(b.cases)})
   except Exception as e: rec.update({'compact_validation':'INVALID','validation_error':str(e)})
  except Exception as e: rec.update({'json_parse':'INVALID','json_parse_error':str(e)})
 except Exception as e: rec['error']=repr(e)
 rec['elapsed_seconds']=round(time.time()-started,2); stem=model.replace('/','_'); (OUT/(stem+'.json')).write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf8');
 if 'content' in rec: (OUT/(stem+'.content.json')).write_text(rec['content'],encoding='utf8')
 return {k:rec.get(k) for k in ['model','http_status','finish_reason','json_parse','compact_validation','case_count','content_chars','elapsed_seconds','error']}
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex: results=list(ex.map(call,MODELS))
 (OUT/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8'); print(json.dumps(results,ensure_ascii=False,indent=2))
