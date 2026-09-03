import re, json, html, requests, subprocess, base64
from urllib.parse import urljoin
from PIL import Image, ImageOps, ImageEnhance

GUIDE='https://www.sergeybuzz.com/momentummagic-trading-system-free-setup-guide'
INDICATOR='https://www.sergeybuzz.com/momentummagic-indicator'
THANK='https://www.sergeybuzz.com/momentummagic-indicator-thank-you-page'
IMG='https://d1yei2z3i6k35z.cloudfront.net/7386361/69feb0df605351.98982419_MomentumMagicSettingsPanel.png'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})

def textify(raw):
    clean=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S); clean=re.sub(r'<style\b[^>]*>.*?</style>',' ',clean,flags=re.I|re.S); clean=re.sub(r'<[^>]+>',' ',clean)
    clean=html.unescape(clean).replace('\\/','/').replace('\\u0026','&'); return re.sub(r'\s+',' ',clean).strip()

def inspect_page(url):
    p=s.get(url,timeout=30,allow_redirects=True); raw=p.text; txt=textify(raw)
    hrefs=[urljoin(p.url,x.replace('\\/','/')) for x in re.findall(r'''href=["']([^"']+)["']''',raw,re.I)]
    assets=[]
    for u in re.findall(r'https?[^"\'<> ]+',raw):
        u=u.replace('\\/','/').replace('\\u0026','&')
        if any(k in u.lower() for k in ['momentum','pine','.txt','.zip','.pdf','download','cloudfront','tradingview']): assets.append(u[:1400])
    snips=[]
    for pat in ['source code','pine script','download','BTCUSD','MACD Fast','MACD Slow','Trend MA','RSI Period','41,213,609','copy','code']:
        for m in list(re.finditer(re.escape(pat),txt,re.I))[:20]: snips.append({'pattern':pat,'text':txt[max(0,m.start()-800):min(len(txt),m.end()+1800)]})
    return {'status':p.status_code,'final_url':p.url,'hrefs':sorted(set(hrefs))[:250],'asset_urls':sorted(set(assets))[:350],'snippets':snips[:220]}

r=s.get(GUIDE,timeout=30,allow_redirects=True); r.raise_for_status(); clean=textify(r.text)
contexts={}
for pat in ['BTC/ETH default: 6','Trend MA Period','Trend MA Type','RSI Period','MACD Signal Period','Signal Smooth','Signal Line Type','positions close','opposite signal']:
    contexts[pat]=[clean[max(0,m.start()-900):min(len(clean),m.end()+2200)] for m in list(re.finditer(re.escape(pat),clean,re.I))[:20]]
a=clean.lower().find('parameter explanation'); b=clean.lower().find('troubleshooting common issues',a+1) if a>=0 else -1; section=clean[a:(b if b>=0 else min(len(clean),a+12000))][:12000] if a>=0 else ''

ir=s.get(IMG,timeout=30); ir.raise_for_status(); open('MomentumMagicSettingsPanel.png','wb').write(ir.content); im=Image.open('MomentumMagicSettingsPanel.png').convert('RGB'); w,h=im.size
panel=im.crop((int(w*.43),0,w,h)); panel.save('MomentumMagicSettingsPanel_crop.png')
with open('MomentumMagicSettingsPanel_crop.png','rb') as f, open('MomentumMagicSettingsPanel_crop.b64','w') as o: o.write(base64.b64encode(f.read()).decode('ascii'))
ocr={}
for name,box in {'right55':(int(w*.43),int(h*.02),int(w*.99),int(h*.99)),'center65':(int(w*.32),int(h*.02),int(w*.97),int(h*.99))}.items():
    c=ImageEnhance.Contrast(ImageOps.grayscale(im.crop(box))).enhance(2.2); c=c.resize((c.width*4,c.height*4)); fn=f'mm_{name}.png'; c.save(fn); vals=[]
    for psm in ['6','11']:
        p=subprocess.run(['tesseract',fn,'stdout','--psm',psm],capture_output=True,text=True,timeout=60); vals.append(p.stdout.strip())
    ocr[name]=vals
out={'status':r.status_code,'final_url':r.url,'parameter_section':section,'parameter_contexts':contexts,'indicator_page':inspect_page(INDICATOR),'thank_you_page':inspect_page(THANK),'settings_image_url':IMG,'settings_image_size':[w,h],'settings_ocr_crops':ocr}
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:150000])
