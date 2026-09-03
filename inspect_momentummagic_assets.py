import re, json, html, requests, subprocess, base64
from urllib.parse import urljoin
from PIL import Image, ImageOps, ImageEnhance

GUIDE='https://www.sergeybuzz.com/momentummagic-trading-system-free-setup-guide'
INDICATOR='https://www.sergeybuzz.com/momentummagic-indicator'
IMG='https://d1yei2z3i6k35z.cloudfront.net/7386361/69feb0df605351.98982419_MomentumMagicSettingsPanel.png'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})

def textify(raw):
    clean=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S)
    clean=re.sub(r'<style\b[^>]*>.*?</style>',' ',clean,flags=re.I|re.S)
    clean=re.sub(r'<[^>]+>',' ',clean)
    clean=html.unescape(clean).replace('\\/','/').replace('\\u0026','&')
    return re.sub(r'\s+',' ',clean).strip()

r=s.get(GUIDE,timeout=30,allow_redirects=True); r.raise_for_status(); clean=textify(r.text)
contexts={}
for pat in ['BTC/ETH default: 6','Trend MA Period','Trend MA Type','RSI Period','MACD Signal Period','Signal Smooth','Signal Line Type','positions close','opposite signal']:
    vals=[]
    for m in list(re.finditer(re.escape(pat),clean,re.I))[:20]: vals.append(clean[max(0,m.start()-900):min(len(clean),m.end()+2200)])
    contexts[pat]=vals

a=clean.lower().find('parameter explanation'); b=clean.lower().find('troubleshooting common issues',a+1) if a>=0 else -1
section=clean[a:(b if b>=0 else min(len(clean),a+12000))][:12000] if a>=0 else ''

# Inspect only publicly reachable indicator/download page assets. Do not submit forms or access paid endpoints.
page=s.get(INDICATOR,timeout=30,allow_redirects=True)
raw2=page.text
hrefs=[urljoin(page.url,x.replace('\\/','/')) for x in re.findall(r'''href=["']([^"']+)["']''',raw2,re.I)]
urls=[]
for u in re.findall(r'https?[^"\'<> ]+',raw2):
    u=u.replace('\\/','/').replace('\\u0026','&')
    if any(k in u.lower() for k in ['momentum','pine','.txt','.zip','.pdf','download','cloudfront']): urls.append(u[:1200])
page_text=textify(raw2)
page_snips=[]
for pat in ['source code','pine script','download','BTCUSD','MACD Fast','MACD Slow','Trend MA','RSI Period','41,213,609']:
    for m in list(re.finditer(re.escape(pat),page_text,re.I))[:15]: page_snips.append({'pattern':pat,'text':page_text[max(0,m.start()-600):min(len(page_text),m.end()+1400)]})

ir=s.get(IMG,timeout=30); ir.raise_for_status(); open('MomentumMagicSettingsPanel.png','wb').write(ir.content)
im=Image.open('MomentumMagicSettingsPanel.png').convert('RGB'); w,h=im.size
panel=im.crop((int(w*.43),0,w,h)); panel.save('MomentumMagicSettingsPanel_crop.png')
with open('MomentumMagicSettingsPanel_crop.png','rb') as f, open('MomentumMagicSettingsPanel_crop.b64','w') as o:
    o.write(base64.b64encode(f.read()).decode('ascii'))

# General OCR plus narrow digit-only passes on rows containing Fast/Slow/Signal values.
crops={'right55':(int(w*.43),int(h*.02),int(w*.99),int(h*.99)),'center65':(int(w*.32),int(h*.02),int(w*.97),int(h*.99))}
ocr={}
for name,box in crops.items():
    c=ImageEnhance.Contrast(ImageOps.grayscale(im.crop(box))).enhance(2.2); c=c.resize((c.width*4,c.height*4)); fn=f'mm_{name}.png'; c.save(fn)
    vals=[]
    for psm in ['6','11']:
        p=subprocess.run(['tesseract',fn,'stdout','--psm',psm],capture_output=True,text=True,timeout=60); vals.append(p.stdout.strip())
    ocr[name]=vals

# Values appear in lower-right settings pane. Sweep narrow horizontal bands and keep digit OCR.
digit_rows=[]
x0=int(w*.72); x1=int(w*.985)
for y0 in range(int(h*.54), int(h*.94), 12):
    y1=min(h,y0+34)
    c=im.crop((x0,y0,x1,y1)); c=ImageOps.grayscale(c); c=ImageEnhance.Contrast(c).enhance(3.0); c=c.resize((c.width*5,c.height*5))
    fn='digitrow.png'; c.save(fn)
    p=subprocess.run(['tesseract',fn,'stdout','--psm','7','-c','tessedit_char_whitelist=0123456789'],capture_output=True,text=True,timeout=30)
    t=p.stdout.strip()
    if t: digit_rows.append({'y0':y0,'y1':y1,'digits':t})

out={
 'status':r.status_code,'final_url':r.url,'parameter_section':section,'parameter_contexts':contexts,
 'indicator_page':{'status':page.status_code,'final_url':page.url,'hrefs':sorted(set(hrefs))[:200],'asset_urls':sorted(set(urls))[:250],'snippets':page_snips[:160]},
 'settings_image_url':IMG,'settings_image_size':[w,h],'settings_ocr_crops':ocr,'digit_rows':digit_rows
}
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:120000])
