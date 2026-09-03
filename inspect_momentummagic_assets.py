import re, json, requests
from urllib.parse import urljoin

BASE='https://www.sergeybuzz.com/'
CANDIDATES=[
    BASE,
    urljoin(BASE,'momentummagic-indicator'),
    urljoin(BASE,'momentummagic-trading-system'),
    urljoin(BASE,'momentummagic-trading-system-41m-bitcoin-returns-free-setup-guide'),
    urljoin(BASE,'posts'),
    urljoin(BASE,'sitemap.xml'),
]
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})
out=[]
for url in CANDIDATES:
    try:
        r=s.get(url,timeout=30,allow_redirects=True)
        text=r.text
        hrefs=re.findall(r'''href=["']([^"']+)["']''',text,re.I)
        mom=[urljoin(r.url,h) for h in hrefs if 'momentum' in h.lower() or 'magic' in h.lower()]
        # Keep only short public snippets around diagnostic keywords; do not republish page/code wholesale.
        snippets=[]
        for pat in ['MomentumMagic','41,213,609','60.27','73 trades','MACD','RSI','preset','fast','slow','signal','trend','download','pine']:
            for m in list(re.finditer(re.escape(pat),text,re.I))[:5]:
                a=max(0,m.start()-300); b=min(len(text),m.end()+700)
                sn=re.sub(r'\s+',' ',text[a:b])
                snippets.append({'pattern':pat,'snippet':sn[:1200]})
        out.append({'requested':url,'status':r.status_code,'final_url':r.url,'length':len(text),'momentum_links':sorted(set(mom))[:100],'snippets':snippets[:80]})
    except Exception as e:
        out.append({'requested':url,'error':repr(e)})
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:60000])
