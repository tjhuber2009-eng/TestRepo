import hashlib, json, re, requests, urllib.parse

SLUG='GoUoySJs-BTC-MTF-Engulfing-Flip-Pyramid-Strategy-1H-2X'
PAGE=f'https://www.tradingview.com/script/{SLUG}/'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
s=requests.Session(); s.headers.update({'User-Agent':UA,'Origin':'https://www.tradingview.com','Referer':'https://www.tradingview.com/'})
r=s.get(PAGE,timeout=30); r.raise_for_status(); txt=r.text
m=re.search(r'"script_id_part"\s*:\s*"(PUB;[A-Za-z0-9]+)"',txt) or re.search(r'"scriptIdPart"\s*:\s*"(PUB;[A-Za-z0-9]+)"',txt)
if not m: raise RuntimeError('PUB id not found')
pub=m.group(1)
url='https://pine-facade.tradingview.com/pine-facade/get/'+urllib.parse.quote(pub,safe='')+'/last'
rr=s.get(url,timeout=30); print('FACADE_STATUS',rr.status_code); rr.raise_for_status(); obj=rr.json(); src=obj.get('source','')
print('PUB_ID',pub); print('SOURCE_SHA256',hashlib.sha256(src.encode()).hexdigest()); print('SOURCE_LEN',len(src),'LINES',len(src.splitlines()))
# Do not publish the full source. Extract compact semantic lines sufficient for independent reconstruction.
keys=(
 'strategy(', 'input.', 'request.security', 'ta.ema', 'ta.rsi', 'ta.macd', 'ta.atr', 'ta.sma',
 'engulf', 'volume', 'longSignal', 'shortSignal', 'longCond', 'shortCond', 'strategy.entry', 'strategy.exit',
 'strategy.close', 'strategy.order', 'stop=', 'limit=', 'qty=', 'qty_percent', 'pyramid', 'flip', 'cooldown',
 'drawdown', 'ddHalt', 'highest', 'lowest', 'risk', 'slDist', 'stopLoss', 'partial', 'takeProfit', 'tp', 'bar_index',
 'time', 'hour', 'position_size', 'entryPrice', 'entry_price', 'leverage', 'commission', 'slippage'
)
semantic=[]
for i,line in enumerate(src.splitlines(),1):
    compact=line.strip()
    if compact and any(k.lower() in compact.lower() for k in keys):
        semantic.append({'line':i,'text':compact[:1000]})
        print(f'SEMANTIC {i}: {compact[:1000]}')
with open('btc_flip_tv_semantics.json','w') as f:
    json.dump({'pub_id':pub,'sha256':hashlib.sha256(src.encode()).hexdigest(),'source_len':len(src),'lines':len(src.splitlines()),'semantic':semantic},f,indent=2)
# Keep exact source only as ephemeral workflow artifact for audit execution; never commit it.
open('btc_flip_v3_open_source.pine','w').write(src)
