import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location("pilot_v4", Path(__file__).with_name("aihubmix_pilot_v4.py"))
_v4 = importlib.util.module_from_spec(_spec)
import sys
sys.modules["_v4"] = _v4
_spec.loader.exec_module(_v4)
from _v4 import *

# v5 uses the corrected prompt and disables provider-side hidden reasoning so
# that the content budget is available for the requested JSON payload.
OUT = ROOT / ".local" / "candidates" / "aihubmix_pilot_v5_20260828"
OUT.mkdir(parents=True, exist_ok=True)
SEED = 2026082806

def call(model: str):
    cfg = yaml.safe_load((ROOT / "config" / "agent_llm_config.yaml").read_text(encoding="utf8"))
    key = cfg.get("api_key") or os.getenv("AIHUBMIX_API_KEY")
    u = prompt(model)
    started = time.time()
    rec = {"model": model, "started_at": datetime.now(timezone.utc).isoformat(), "request_meta": {"model": model, "temperature": 0.3, "seed": SEED, "max_tokens": 24000, "reasoning_effort": "none", "response_format": "json_object"}, "prompt_sha256": hashlib.sha256((system + "\n" + u).encode()).hexdigest()}
    try:
        r = requests.post("https://aihubmix.com/v1/chat/completions", headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": u}], "temperature": 0.3, "seed": SEED, "max_tokens": 24000, "reasoning_effort": "none", "stream": False, "response_format": {"type": "json_object"}}, timeout=(20, 300))
        rec["http_status"] = r.status_code; rec["response_headers"] = {k:v for k,v in r.headers.items() if k.lower() in {"x-request-id","x-structured-output-degraded","x-json-repaired","content-type"}}; body = r.json(); rec["response_json"] = body
        if r.status_code != 200: raise RuntimeError("HTTP " + str(r.status_code))
        ch = body["choices"][0]; content = ch.get("message", {}).get("content") or ""; rec.update({"finish_reason": ch.get("finish_reason"), "content_chars": len(content), "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content})
        try:
            parsed = json.loads(content); rec["parsed"] = parsed
            try: b = CompactScenarioGenerationBatch.model_validate(parsed); rec.update({"compact_validation":"VALID", "case_count":len(b.cases)})
            except Exception as e: rec.update({"compact_validation":"INVALID", "validation_error":str(e)})
        except Exception as e: rec.update({"json_parse":"INVALID", "json_parse_error":str(e)})
    except Exception as e: rec["error"] = repr(e)
    rec["elapsed_seconds"] = round(time.time()-started,2); stem=model.replace('/','_'); (OUT/(stem+'.json')).write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf8')
    if 'content' in rec: (OUT/(stem+'.content.json')).write_text(rec['content'],encoding='utf8')
    return {k:rec.get(k) for k in ['model','http_status','finish_reason','json_parse','compact_validation','case_count','content_chars','elapsed_seconds','error']}

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex: results=list(ex.map(call, MODELS))
    (OUT/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8'); print(json.dumps(results,ensure_ascii=False,indent=2))
