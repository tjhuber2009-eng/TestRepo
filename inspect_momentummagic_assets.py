import re, json, requests
from urllib.parse import urljoin

BASE='https://www.sergeybuzz.com/'
CANDIDATES=[
    BASE,
    urljoin(BASE,'momentummagic-indicator'),
    urljoin(BASE,'momentummagic-strategy'),
    urljoin(BASE,'momentummagic-trading-system-free-setup-guide'),
    urljoin(BASE,'posts'),
    urljoin(BASE,'sitemap.xml'),
]
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})
out=[]
for url in CANDIDATES:
    try:
        r=s.get(url,timeout=30,allow_redirects=True); text=r.text
        hrefs=re.findall(r'''href=["']([^"']+)["']''',text,re.I)
        all_urls=[urljoin(r.url,h.replace('\\/','/')) for h in hrefs]
        mom=[u for u in all_urls if 'momentum' in u.lower() or 'magic' in u.lower()]
        assets=[]
        # Surface public asset references without submitting forms or accessing paid content.
        for u in re.findall(r'https?[^"\'<> ]+',text):
            u=u.replace('\\/','/').replace('\\u0026','&')
            if any(k in u.lower() for k in ['cloudfront','momentum','download','.pine','.txt','.pdf','.zip']): assets.append(u[:1000])
        snippets=[]
        patterns=['MomentumMagic','41,213,609','60.27','73 trades','MACD','RSI','preset','BTCUSD','Bitcoin','fast','slow','signal length','trend','moving average','long signal','exit','download','Pine Script','source code']
        for pat in patterns:
            for m in list(re.finditer(re.escape(pat),text,re.I))[:12]:
                a=max(0,m.start()-450); b=min(len(text),m.end()+1200)
                snippets.append({'pattern':pat,'snippet':re.sub(r'\s+',' ',text[a:b])[:1800]})
        out.append({'requested':url,'status':r.status_code,'final_url':r.url,'length':len(text),'momentum_links':sorted(set(mom))[:100],'public_asset_refs':sorted(set(assets))[:150],'snippets':snippets[:220]})
    except Exception as e: out.append({'requested':url,'error':repr(e)})
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:120000])
