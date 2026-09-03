import re, html, json, requests, subprocess
from urllib.parse import urljoin
from PIL import Image, ImageOps, ImageEnhance

GUIDE='https://www.sergeybuzz.com/free-trendfusion-ultimate-indicator-complete-guide'
LAND='https://www.sergeybuzz.com/trendfusion-ultimate-indicator'
THANK='https://www.sergeybuzz.com/trendfusion-ultimate-indicator-thank-you-page'
CONFIG='https://d1yei2z3i6k35z.cloudfront.net/7386361/68bcba5b86750_Image2-TrendFusionUltimateconfigurationpanel.png'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 independent-strategy-audit/1.0'})

def textify(raw):
    x=re.sub(r'<script\b[^>]*>.*?</script>',' ',raw,flags=re.I|re.S)
    x=re.sub(r'<style\b[^>]*>.*?</style>',' ',x,flags=re.I|re.S)
    x=re.sub(r'<[^>]+>',' ',x)
    x=html.unescape(x).replace('\\/','/').replace('\\u0026','&')
    return re.sub(r'\s+',' ',x).strip()

def inspect(url):
    try:
        r=s.get(url,timeout=30,allow_redirects=True); raw=r.text; txt=textify(raw)
        hrefs=[urljoin(r.url,h.replace('\\/','/')) for h in re.findall(r'''href=["']([^"']+)["']''',raw,re.I)]
        assets=[]
        for u in re.findall(r'https?[^"\'<> ]+',raw):
            u=u.replace('\\/','/').replace('\\u0026','&')
            if any(k in u.lower() for k in ['trendfusion','pine','.txt','.zip','.pdf','download','cloudfront','tradingview']): assets.append(u[:1600])
        snips=[]
        for pat in ['BTCUSD','ZLEMA','MA Period','DMI Period','ADX Smoothing','ADX Trend Level','Pine Script','source code','download','29,890,452','28,440,467','textarea','clipboard','//@version','indicator(']:
            for m in list(re.finditer(re.escape(pat),txt,re.I))[:25]: snips.append({'pattern':pat,'text':txt[max(0,m.start()-700):min(len(txt),m.end()+1800)]})
        # raw snippets only around source-ish markers, capped, no full-source republication
        raw_snips=[]
        for pat in ['//@version','input.int','input.string','ta.dmi','ta.adx','zlema','trendfusion','clipboard','textarea']:
            for m in list(re.finditer(re.escape(pat),raw,re.I))[:15]: raw_snips.append({'pattern':pat,'text':re.sub(r'\s+',' ',raw[max(0,m.start()-500):min(len(raw),m.end()+1500)])[:2200]})
        return {'status':r.status_code,'final_url':r.url,'length':len(raw),'hrefs':sorted(set(hrefs))[:300],'assets':sorted(set(assets))[:400],'snippets':snips[:250],'raw_snippets':raw_snips[:180]}
    except Exception as e: return {'url':url,'error':repr(e)}

pages={k:inspect(v) for k,v in [('guide',GUIDE),('landing',LAND),('thank_you',THANK)]}
# Public configuration-panel screenshot contains actual preset input values. OCR is last-resort because values are image-only.
ir=s.get(CONFIG,timeout=30); ir.raise_for_status(); open('TrendFusionConfig.png','wb').write(ir.content)
im=Image.open('TrendFusionConfig.png').convert('RGB'); w,h=im.size
ocr={}
# overlapping full/cropped passes to preserve labels and values
for name,box in {
 'full':(0,0,w,h),
 'right70':(int(w*.28),0,w,h),
 'center80':(int(w*.12),0,int(w*.98),h)
}.items():
    c=im.crop(box); c=ImageOps.grayscale(c); c=ImageEnhance.Contrast(c).enhance(2.5); c=c.resize((c.width*4,c.height*4)); fn=f'tf_{name}.png'; c.save(fn)
    vals=[]
    for psm in ['6','11']:
        p=subprocess.run(['tesseract',fn,'stdout','--psm',psm],capture_output=True,text=True,timeout=90); vals.append(p.stdout.strip())
    ocr[name]=vals
out={'pages':pages,'config_url':CONFIG,'config_size':[w,h],'config_bytes':len(ir.content),'config_ocr':ocr}
with open('trendfusion_public_inspection.json','w') as f: json.dump(out,f,indent=2)
print(json.dumps(out,indent=2)[:150000])
