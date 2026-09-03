import re, html, requests

URL='https://www.sergeybuzz.com/free-trendfusion-ultimate-indicator-complete-guide'
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0'})
r=s.get(URL,timeout=30); r.raise_for_status(); text=r.text
print('PAGE_BYTES',len(text))
# print candidate assets/links only, no full copyrighted source.
patterns=[r'https?://[^"\'<> ]+', r'(?:src|href)=["\']([^"\']+)["\']']
vals=[]
for pat in patterns:
    for m in re.finditer(pat,text,re.I):
        v=m.group(1) if m.lastindex else m.group(0)
        v=html.unescape(v)
        if any(k.lower() in v.lower() for k in ['trendfusion','pine','download','image2','image3','cloudfront','systeme']):
            vals.append(v)
for v in sorted(set(vals)):
    print('ASSET',v[:1000])
# nearby HTML around configuration panel / free indicator / form action
for needle in ['configuration panel','GET FREE INDICATOR','TrendFusion Ultimate', 'form']:
    pos=text.lower().find(needle.lower())
    if pos>=0:
        snippet=re.sub(r'\s+',' ',text[max(0,pos-1500):pos+2500])
        # redact input values/emails if any
        snippet=re.sub(r'value=["\'][^"\']*["\']','value="[redacted]"',snippet,flags=re.I)
        print('\nNEAR',needle, snippet[:4000])
