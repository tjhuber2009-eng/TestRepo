import re, json, html, requests, subprocess
from PIL import Image, ImageOps, ImageEnhance

URL='https://www.sergeybuzz.com/momentummagic-trading-system-free-setup-guide'
IMG='https://d1yei2z3i6k35z.cloudfront.net/7386361/69feb0df605351.98982419_MomentumMagicSettingsPanel.png'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})
r=s.get(URL,timeout=30,allow_redirects=True); r.raise_for_status(); raw=r.text
clean=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S)
clean=re.sub(r'<style\b[^>]*>.*?</style>',' ',clean,flags=re.I|re.S)
clean=re.sub(r'<[^>]+>',' ',clean); clean=html.unescape(clean).replace('\\/','/').replace('\\u0026','&'); clean=re.sub(r'\s+',' ',clean).strip()

# Preserve substantial public prose surrounding parameter definitions so the audit can recover
# rule semantics without copying the downloadable Pine source.
contexts={}
for pat in [
    'BTC/ETH default: 6','default: 6','Trend MA','Trend MA Period','Trend MA Type',
    'RSI Period','RSI Trend','MACD Signal Period','Signal Smooth','Signal Line Type',
    'Entry Filters','Exit Filters','positions close','opposite signal','Parameter Explanation',
    'Troubleshooting Common Issues'
]:
    vals=[]
    for m in list(re.finditer(re.escape(pat),clean,re.I))[:20]:
        vals.append(clean[max(0,m.start()-900):min(len(clean),m.end()+2200)])
    contexts[pat]=vals

# Also isolate the public Parameter Explanation section if headings survived text extraction.
section=''
a=clean.lower().find('parameter explanation')
b=clean.lower().find('troubleshooting common issues',a+1) if a>=0 else -1
if a>=0:
    if b<0: b=min(len(clean),a+12000)
    section=clean[a:b][:12000]

ir=s.get(IMG,timeout=30); ir.raise_for_status(); open('MomentumMagicSettingsPanel.png','wb').write(ir.content)
im=Image.open('MomentumMagicSettingsPanel.png').convert('RGB'); w,h=im.size
crops={'right55':(int(w*.43),int(h*.02),int(w*.99),int(h*.99)),'center65':(int(w*.32),int(h*.02),int(w*.97),int(h*.99)),'right70':(int(w*.28),0,w,h)}
ocr={}
for name,box in crops.items():
    c=ImageEnhance.Contrast(ImageOps.grayscale(im.crop(box))).enhance(2.0); c=c.resize((c.width*3,c.height*3)); fn=f'mm_{name}.png'; c.save(fn)
    vals=[]
    for psm in ['6','11']:
        p=subprocess.run(['tesseract',fn,'stdout','--psm',psm],capture_output=True,text=True,timeout=60); vals.append(p.stdout.strip())
    ocr[name]=vals
out={'status':r.status_code,'final_url':r.url,'settings_image_url':IMG,'settings_image_size':[w,h],'parameter_section':section,'parameter_contexts':contexts,'settings_ocr_crops':ocr}
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps({'parameter_section':section,'parameter_contexts':contexts,'settings_ocr_crops':ocr},indent=2)[:90000])
