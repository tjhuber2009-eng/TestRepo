import re, json, html, requests
from urllib.parse import urljoin

URL='https://www.sergeybuzz.com/momentummagic-trading-system-free-setup-guide'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})
r=s.get(URL,timeout=30,allow_redirects=True); r.raise_for_status(); raw=r.text

# Visible-ish text extraction is only for public setup/rule recovery, not republication.
clean=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S)
clean=re.sub(r'<style\b[^>]*>.*?</style>',' ',clean,flags=re.I|re.S)
clean=re.sub(r'<[^>]+>',' ',clean)
clean=html.unescape(clean)
clean=re.sub(r'\\x3c[^>]*>',' ',clean)
clean=clean.replace('\\/','/').replace('\\u0026','&')
clean=re.sub(r'\s+',' ',clean).strip()

patterns=['41,213,609','60.27','73 trades','Profit Factor','Drawdown','BTCUSD','Bitcoin preset','Preset','MACD','Fast','Slow','Signal','RSI','Trend MA','Trend','Long Entry','Long Signal','Exit','source code','Pine Script','download']
ctx=[]
for pat in patterns:
    for m in list(re.finditer(re.escape(pat),clean,re.I))[:15]:
        a=max(0,m.start()-500); b=min(len(clean),m.end()+1200)
        ctx.append({'pattern':pat,'context':clean[a:b]})

# Public links/assets referenced by the guide page. No form submission, no paid endpoints.
hrefs=re.findall(r'''href=["']([^"']+)["']''',raw,re.I)
links=sorted(set(urljoin(r.url,h.replace('\\/','/')) for h in hrefs))
assets=[]
for u in re.findall(r'https?[^"\'<> ]+',raw):
    u=html.unescape(u.replace('\\/','/').replace('\\u0026','&'))
    if any(k in u.lower() for k in ['cloudfront','momentum','download','.pine','.txt','.pdf','.zip']): assets.append(u[:1500])

# Mine parameter-like public prose such as 'Fast Length: 12' or tables serialized in page text.
param_sentences=[]
for m in re.finditer(r'(?i)(?:BTCUSD|Bitcoin|MACD|RSI|trend|fast|slow|signal|length|period|threshold|preset)[^.!?]{0,220}[.!?]',clean):
    snt=m.group(0).strip()
    if re.search(r'\d',snt): param_sentences.append(snt)

out={'status':r.status_code,'final_url':r.url,'raw_length':len(raw),'public_links':[u for u in links if any(k in u.lower() for k in ['momentum','download','pine','cloudfront'])][:200],'public_asset_refs':sorted(set(assets))[:250],'parameter_sentences':param_sentences[:150],'contexts':ctx[:250]}
with open('momentummagic_guide_compact.json','w') as f: json.dump(out,f,indent=2)
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:120000])
