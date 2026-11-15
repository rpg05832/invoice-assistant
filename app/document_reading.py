"""Local focused reading of small invoice text; source values are never calculated."""
import re
import tempfile
from decimal import Decimal,InvalidOperation
from pathlib import Path
from PIL import Image,ImageOps
from . import ocr,vision
from .domain import extract
from .extraction import amounts,flat,role,word_rows,IDENTIFIER


def read_region(source,region,quality='fast'):
    with tempfile.TemporaryDirectory(prefix='invoice-field-') as directory:
        target=Path(directory)/'field.png'
        with Image.open(source) as image:
            x,y,w,h=[region[k] for k in ('x','y','w','h')]
            crop=image.crop((round(x*image.width),round(y*image.height),round((x+w)*image.width),round((y+h)*image.height))).rotate(-region.get('rotation',0),expand=True,fillcolor='white')
            scale=max(1,min(3,1800/max(1,crop.width),1800/max(1,crop.height)))
            crop=ImageOps.autocontrast(crop.convert('RGB')).resize((max(1,round(crop.width*scale)),max(1,round(crop.height*scale))),Image.Resampling.LANCZOS)
            crop.save(target)
        text,engine,warning=ocr.recognize(target,quality=quality,psm=6)
        words=vision.tokens(target,quality=quality,psm=6)
    return text,engine,warning,words


def refine(source,text,words,quality='fast'):
    """Retry a suspicious summary and missing header fields before creating a proposal."""
    fields=extract(text,words);regions=[];info=[]
    label=flat(text)
    try:bad_amounts=not fields['vat'] or Decimal(fields['vat'])==0 or abs(Decimal(fields['net'])+Decimal(fields['vat'])-Decimal(fields['total']))>Decimal('.01')
    except InvalidOperation:bad_amounts=True
    if 'מעמ' in label and bad_amounts:
        rows=[row for value,row in word_rows(words) if role(value)[0] and .25<min(w['y'] for w in row)<.9]
        if rows:
            last=max(min(w['y'] for w in row) for row in rows)
            summary=[w for row in rows if min(w['y'] for w in row)>last-.16 for w in row]
            left=max(0,min(w['x'] for w in summary)-.12);right=min(1,max(w['x']+w['w'] for w in summary)+.12)
            top=max(.2,min(w['y'] for w in summary)-.025);bottom=min(.95,max(w['y']+w['h'] for w in summary)+.075)
            regions.append(('amount-summary',{'x':left,'y':top,'w':right-left,'h':bottom-top,'rotation':0}))
    if not fields['number'] or not fields['supplier_tax_id'] or not fields['supplier_name']:
        regions.append(('header',{'x':0,'y':0,'w':1,'h':.30,'rotation':0}))
    for purpose,region in regions:
        extra,engine,warning,new_words=read_region(source,region,quality)
        if engine!='tesseract' or not extra:continue
        text+='\n'+extra
        # The automatic regions are unrotated, so map coordinates back to source.
        for word in new_words:
            words.append({**word,'x':region['x']+word['x']*region['w'],'y':region['y']+word['y']*region['h'],'w':word['w']*region['w'],'h':word['h']*region['h']})
        info.append({'purpose':purpose,'region':region,'engine':engine})
    return text,words,info


def field_proposals(field,text):
    parsed=extract(text);values=[]
    if field in ('net','vat','total'):values=([parsed[field]] if parsed[field] else [])+amounts(text)
    elif field=='number':
        values=[parsed['number']] if parsed['number'] else re.findall(rf'(?<![\w/])({IDENTIFIER})(?![\w/])',text)
    elif field=='supplier_tax_id':values=re.findall(r'(?<!\d)\d{9}(?!\d)',text)
    elif field=='supplier_name':values=[parsed['supplier_name'] or text.strip()]
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))[:20]


def amount_review_reason(fields,text):
    try:
        if Decimal(fields.get('vat') or '-1')!=0:return ''
    except InvalidOperation:return ''
    label=flat(text).replace('מעם','מעמ')
    explicit_rate=any(any(k in flat(line).replace('מעם','מעמ') for k in ('מעמ','vat')) and any(Decimal(v.replace(',','.'))>0 for v in re.findall(r'(\d{1,2}(?:[.,]\d{1,2})?)\s*%',line)) for line in text.splitlines())
    if explicit_rate or ('כוללמעמ' in label and any(v in label for v in ('ללאמעמ','לפנימעמ','בלימעמ','לאמעמ'))):
        return 'הפענוח הציע מע״מ אפס, אך במקור מופיעים סכומים עם ובלי מע״מ או שיעור מע״מ חיובי. בדקו את טבלת הסיכום ואשרו את הסכומים במפורש.'
    return ''


def original_region(document,region):
    """Map a field in a split/rotated image back to its original page."""
    base=document.get('region')
    if not base:return {**region,'source_document_id':document['id']}
    angle=base.get('rotation',0);points=[]
    for x,y in [(region['x'],region['y']),(region['x']+region['w'],region['y']),(region['x'],region['y']+region['h']),(region['x']+region['w'],region['y']+region['h'])]:
        u,v=(y,1-x) if angle==90 else (1-x,1-y) if angle==180 else (1-y,x) if angle==270 else (x,y)
        points.append((base['x']+u*base['w'],base['y']+v*base['h']))
    left=min(p[0] for p in points);top=min(p[1] for p in points)
    return {'x':left,'y':top,'w':max(p[0] for p in points)-left,'h':max(p[1] for p in points)-top,'rotation':angle,'source_document_id':document['id']}
