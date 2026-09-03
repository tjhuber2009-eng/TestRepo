import re, json, html, requests, subprocess, os
from urllib.parse import urljoin

URL='https://www.sergeybuzz.com/momentummagic-trading-system-free-setup-guide'
IMG='https://d1yei2z3i6k35z.cloudfront.net/7386361/69feb0df605351.98982419_MomentumMagicSettingsPanel.png'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})
r=s.get(URL,timeout=30,allow_redirects=True); r.raise_for_status(); raw=r.text
clean=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S)
clean=re.sub(r'<style\b[^>]*>.*?</style>',' ',clean,flags=re.I|re.S)
clean=re.sub(r'<[^>]+>',' ',clean); clean=html.unescape(clean).replace('\\/','/').replace('\\u0026','&'); clean=re.sub(r'\s+',' ',clean).strip()
patterns=['41,213,609','60.27','73 trades','Profit Factor','Drawdown','BTCUSD','Bitcoin preset','Preset','MACD','Fast','Slow','Signal','RSI','Trend MA','Long Entry','Long Signal','Exit','source code','Pine Script','download']
ctx=[]
for pat in patterns:
    for m in list(re.finditer(re.escape(pat),clean,re.I))[:15]:
        ctx.append({'pattern':pat,'context':clean[max(0,m.start()-500):min(len(clean),m.end()+1200)]})
param_sentences=[]
for m in re.finditer(r'(?i)(?:BTCUSD|Bitcoin|MACD|RSI|trend|fast|slow|signal|length|period|threshold|preset)[^.!?]{0,220}[.!?]',clean):
    q=m.group(0).strip()
    if re.search(r'\d',q): param_sentences.append(q)
# Last resort: OCR the author's publicly hosted settings screenshot. No form submission or paid material.
ir=s.get(IMG,timeout=30); ir.raise_for_status(); open('MomentumMagicSettingsPanel.png','wb').write(ir.content)
ocr=''
try:
    p=subprocess.run(['tesseract','MomentumMagicSettingsPanel.png','stdout','--psm','6'],capture_output=True,text=True,timeout=60)
    ocr=p.stdout.strip()
except Exception as e:
    ocr='OCR_ERROR '+repr(e)
out={'status':r.status_code,'final_url':r.url,'settings_image_url':IMG,'settings_image_bytes':len(ir.content),'settings_ocr':ocr,'parameter_sentences':param_sentences[:150],'contexts':ctx[:220]}
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps({'status':out['status'],'settings_image_bytes':out['settings_image_bytes'],'settings_ocr':ocr,'parameter_sentences':out['parameter_sentences']},indent=2)[:40000])
