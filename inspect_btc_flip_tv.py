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
rr=s.get(url,timeout=30); rr.raise_for_status(); src=rr.json().get('source',''); lines=src.splitlines()
print('PUB_ID',pub); print('SOURCE_SHA256',hashlib.sha256(src.encode()).hexdigest()); print('SOURCE_LEN',len(src),'LINES',len(lines))
keys=(
 'strategy(', 'input.', 'request.security', 'ta.ema', 'ta.rsi', 'ta.macd', 'ta.atr', 'ta.sma',
 'engulf', 'volume', 'longSignal', 'shortSignal', 'strategy.entry', 'strategy.exit', 'strategy.close', 'strategy.order',
 'stop=', 'limit=', 'qty=', 'qty_percent', 'pyramid', 'flip', 'cooldown', 'drawdown', 'ddHalt', 'highest', 'lowest',
 'risk', 'slDist', 'partial', 'takeProfit', 'bar_index', 'position_size', 'leverage', 'commission', 'slippage',
 'stopLong', 'stopShort', 'currentEquity', 'canGoLong', 'canGoShort', 'dailyBull', 'dailyBear', 'h4Conf', 'highVol',
 'pendingFlipSide', 'pendingFlipTime', 'entryPx', 'initSlDist', 'partialTaken', 'pyramided', 'peakEquity', 'haltUntil'
)
semantic=[]
for i,line in enumerate(lines,1):
    compact=line.strip()
    if compact and any(k.lower() in compact.lower() for k in keys):
        semantic.append({'line':i,'text':compact[:1200]}); print(f'SEMANTIC {i}: {compact[:1200]}')
# Focused 3-line contexts for execution variables; enough to independently reconstruct without republishing the script.
ids=['dailyBull','dailyBear','h4ConfBull','h4ConfBear','highVol','stopLong','stopShort','currentEquity','peakEquity','haltUntil','canGoLong','canGoShort','pendingFlipSide','pendingFlipTime','flipSL','flipQty','entryPx','initSlDist','partialTaken','partialTrigger','pyramidTrigger','pyramidQty','pyrSL','strategy.close','strategy.exit']
contexts={}
for ident in ids:
    hits=[]
    for i,line in enumerate(lines):
        if ident.lower() in line.lower():
            a=max(0,i-2); b=min(len(lines),i+3)
            hits.append({'line':i+1,'context':[{'line':j+1,'text':lines[j].strip()[:1200]} for j in range(a,b)]})
    contexts[ident]=hits[:20]
out={'pub_id':pub,'sha256':hashlib.sha256(src.encode()).hexdigest(),'source_len':len(src),'lines':len(lines),'semantic':semantic,'contexts':contexts}
with open('btc_flip_tv_semantics.json','w') as f: json.dump(out,f,indent=2)
open('btc_flip_v3_open_source.pine','w').write(src)
