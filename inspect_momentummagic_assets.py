import re, json, html, requests, subprocess
from PIL import Image, ImageOps, ImageEnhance

URL='https://www.sergeybuzz.com/momentummagic-trading-system-free-setup-guide'
IMG='https://d1yei2z3i6k35z.cloudfront.net/7386361/69feb0df605351.98982419_MomentumMagicSettingsPanel.png'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})
r=s.get(URL,timeout=30,allow_redirects=True); r.raise_for_status(); raw=r.text
clean=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S)
clean=re.sub(r'<style\b[^>]*>.*?</style>',' ',clean,flags=re.I|re.S)
clean=re.sub(r'<[^>]+>',' ',clean); clean=html.unescape(clean).replace('\\/','/').replace('\\u0026','&'); clean=re.sub(r'\s+',' ',clean).strip()
param_sentences=[]
for m in re.finditer(r'(?i)(?:BTCUSD|Bitcoin|MACD|RSI|trend|fast|slow|signal|length|period|threshold|preset)[^.!?]{0,220}[.!?]',clean):
    q=m.group(0).strip()
    if re.search(r'\d',q): param_sentences.append(q)
ir=s.get(IMG,timeout=30); ir.raise_for_status(); open('MomentumMagicSettingsPanel.png','wb').write(ir.content)
im=Image.open('MomentumMagicSettingsPanel.png').convert('RGB'); w,h=im.size
# TradingView settings modal occupies the center/right of this public screenshot. Test overlapping crops
# so values at the panel edge are not lost. Upscale + grayscale/contrast for one English OCR pass per crop.
crops={
 'right55':(int(w*.43),int(h*.02),int(w*.99),int(h*.99)),
 'center65':(int(w*.32),int(h*.02),int(w*.97),int(h*.99)),
 'right70':(int(w*.28),0,w,h),
}
ocr={}
for name,box in crops.items():
    c=im.crop(box); c=ImageOps.grayscale(c); c=ImageEnhance.Contrast(c).enhance(2.0); c=c.resize((c.width*3,c.height*3))
    fn=f'mm_{name}.png'; c.save(fn)
    vals=[]
    for psm in ['6','11']:
        p=subprocess.run(['tesseract',fn,'stdout','--psm',psm],capture_output=True,text=True,timeout=60)
        vals.append(p.stdout.strip())
    ocr[name]=vals
out={'status':r.status_code,'final_url':r.url,'settings_image_url':IMG,'settings_image_bytes':len(ir.content),'settings_image_size':[w,h],'settings_ocr_crops':ocr,'parameter_sentences':param_sentences[:150]}
with open('momentummagic_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:70000])
