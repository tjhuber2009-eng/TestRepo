import hashlib, html as htmlmod, json, re, requests, urllib.parse

SLUG='5zYcH3Gx-open-open-1-BUY-else-SELL'
PAGE=f'https://www.tradingview.com/script/{SLUG}/'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Origin':'https://www.tradingview.com','Referer':'https://www.tradingview.com/'})
r=s.get(PAGE,timeout=30); r.raise_for_status(); txt=r.text
m=re.search(r'"script_id_part"\s*:\s*"(PUB;[A-Za-z0-9]+)"',txt) or re.search(r'"scriptIdPart"\s*:\s*"(PUB;[A-Za-z0-9]+)"',txt)
if not m: raise RuntimeError('PUB id not found')
pub=m.group(1)
url='https://pine-facade.tradingview.com/pine-facade/get/'+urllib.parse.quote(pub,safe='')+'/last'
rr=s.get(url,timeout=30); print('FACADE_STATUS',rr.status_code); rr.raise_for_status()
obj=rr.json(); src=obj.get('source','')
print('PUB_ID',pub)
print('SOURCE_SHA256',hashlib.sha256(src.encode()).hexdigest())
print('SOURCE_LEN',len(src),'LINES',len(src.splitlines()))
# Print only compact semantic lines, avoiding redistribution of comments/boilerplate.
keys=('strategy(', 'input', 'strategy.entry', 'strategy.exit', 'strategy.close', 'strategy.order', 'trail_', 'commission', 'pyramiding', 'qty', 'open[1]', 'when=', 'if ')
for i,line in enumerate(src.splitlines(),1):
    compact=line.strip()
    if any(k in compact for k in keys):
        print(f'SEMANTIC {i}: {compact[:500]}')
