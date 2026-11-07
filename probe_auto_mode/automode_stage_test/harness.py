"""mitmproxy addon: observe classifier requests, save templates, never alter live permissions."""
import json, hashlib, pathlib, time
from mitmproxy import http
ROOT = pathlib.Path(__file__).resolve().parent
BETA = 'auto-mode-classifier-2026-07-16'

def classify(body):
    if not isinstance(body, dict): return None
    chunks = [b.get('text','') for m in body.get('messages',[]) if isinstance(m,dict)
              for b in (m.get('content',[]) if isinstance(m.get('content'),list) else []) if isinstance(b,dict)]
    txt = '\n'.join(chunks)
    if '<transcript>' not in txt or '</transcript>' not in txt: return None
    if 'Stage 1 does NOT apply user intent' in txt: return 'stage1'
    if 'Review the classification process' in txt and 'Use <thinking>' in txt: return 'stage2'
    return 'unknown'

def request(flow: http.HTTPFlow):
    r=flow.request
    if r.method!='POST' or r.path.split('?')[0]!='/v1/messages': return
    if BETA not in [x.strip() for x in r.headers.get('anthropic-beta','').split(',')]: return
    try: body=json.loads(r.get_text(strict=False))
    except (ValueError,UnicodeError): return
    stage=classify(body)
    if not stage: return
    flow.metadata['automode_stage']=stage
    flow.metadata['automode_sha']=hashlib.sha256(r.raw_content or b'').hexdigest()
    # Save request JSON but exclude request headers, auth tokens, and metadata identifiers.
    body.pop('metadata',None)
    for block in body.get('system',[]):
        if isinstance(block,dict) and block.get('text','').startswith('x-anthropic-billing-header:'):
            block['text']='[billing header removed]'
    path=ROOT/'captured_templates'; path.mkdir(exist_ok=True)
    name=f'{int(time.time()*1000)}_{stage}_{flow.id[:8]}.json'
    (path/name).write_text(json.dumps(body,indent=2),encoding='utf-8')
    print(f'[CLASSIFIER] {stage} saved {name}; original SHA256={flow.metadata["automode_sha"][:16]}')

def response(flow: http.HTTPFlow):
    stage=flow.metadata.get('automode_stage')
    if not stage or not flow.response: return
    raw=flow.response.get_text(strict=False)
    import re
    sev=re.search(r'<severity>\s*(\d+)',raw)
    cat=re.search(r'<category>(.*?)</category>',raw,re.S)
    print(f'[CLASSIFIER RESPONSE] {stage} severity={sev.group(1) if sev else "not found"} category={cat.group(1) if cat else "none"} status={flow.response.status_code}')
