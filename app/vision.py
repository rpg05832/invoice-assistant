"""Local geometry, OCR provenance and independent handwritten digit proposals."""
import csv
import io
import math
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from fastapi import HTTPException
from . import ocr
from .paths import assets


def preprocess(source, destination):
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        quarter=ocr.orientation(source)
        if quarter:image=image.rotate(-quarter,expand=True,fillcolor='white')
        array = np.array(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, None, fx=min(1,1600/gray.shape[1]), fy=min(1,1600/gray.shape[1]))
    edges = cv2.Canny(small, 60, 180)
    lines = cv2.HoughLinesP(edges, 1, np.pi/1800, threshold=80, minLineLength=max(50,small.shape[1]//8), maxLineGap=20)
    angles=[]
    if lines is not None:
        for x1,y1,x2,y2 in lines[:,0]:
            angle=math.degrees(math.atan2(y2-y1,x2-x1))
            if abs(angle)<12: angles.append(angle)
    angle=float(np.median(angles)) if len(angles)>=3 else 0
    corrected=image.rotate(angle,expand=True,fillcolor='white',resample=Image.Resampling.BICUBIC) if abs(angle)>.2 else image
    corrected=ImageOps.autocontrast(corrected.convert('RGB'))
    corrected.save(destination,'PNG')
    return {'quarter_rotation':quarter,'deskew_degrees':round(angle,2),'contrast':'autocontrast'}


def tokens(path):
    cap=ocr.capabilities()
    if not cap['available']: return []
    langs='+'.join(l for l in ('heb','eng') if l in cap['languages'])
    if not langs: return []
    try:
        result=ocr.run_tesseract([Path(path),'stdout','-l',langs,'--psm','3','tsv'],timeout=60)
        if result.returncode:return []
        with Image.open(path) as image:w,h=image.size
        output=[]
        for row in csv.DictReader(io.StringIO(result.stdout),delimiter='\t',quoting=csv.QUOTE_NONE):
            if row['level']!='5' or not row.get('text','').strip():continue
            output.append({'text':row['text'],'confidence':float(row['conf'])/100,'x':int(row['left'])/w,'y':int(row['top'])/h,'w':int(row['width'])/w,'h':int(row['height'])/h,'rotation':0,'line':f"{row['block_num']}:{row['par_num']}:{row['line_num']}"})
        return output
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):return []


def pdf_tokens(page):
    return [{'text':word[4],'confidence':1,'x':word[0]/page.rect.width,'y':word[1]/page.rect.height,'w':(word[2]-word[0])/page.rect.width,'h':(word[3]-word[1])/page.rect.height,'rotation':0,'line':f'{word[5]}:{word[6]}'} for word in page.get_text('words')]


def field_sources(fields, words):
    output={}
    for field,value in fields.items():
        if not value or field in ('kind','currency','purpose'):continue
        normalized=str(value).replace(',','').replace('-','').replace('/','').replace('.','')
        for word in words:
            candidate=word['text'].replace(',','').replace('-','').replace('/','').replace('.','').strip(':₪')
            if normalized==candidate or (len(normalized)>=4 and normalized in candidate):
                output[field]={k:word[k] for k in ('x','y','w','h','rotation')};break
    return output


def suggest_regions(path):
    """Propose disconnected document blocks; ambiguous pages still require review."""
    with Image.open(path) as image:
        image=ImageOps.grayscale(image);image.thumbnail((1200,1200));arr=np.array(image)
    ink=(arr<210).astype(np.uint8)
    # Join characters/lines into document areas, retaining large paper gutters.
    kernel=cv2.getStructuringElement(cv2.MORPH_RECT,(max(9,arr.shape[1]//35),max(9,arr.shape[0]//30)))
    connected=cv2.dilate(ink,kernel)
    n,_,stats,_=cv2.connectedComponentsWithStats(connected,8)
    regions=[];height,width=arr.shape
    for x,y,w,h,area in stats[1:]:
        if w<width*.12 or h<height*.07 or area<width*height*.008:continue
        pad=.015
        left=max(0,x/width-pad);top=max(0,y/height-pad)
        right=min(1,(x+w)/width+pad);bottom=min(1,(y+h)/height+pad)
        regions.append({'x':left,'y':top,'w':right-left,'h':bottom-top,'rotation':0})
    regions.sort(key=lambda r:(round(r['y']*8),r['x']))
    return {'regions':regions[:20] if len(regions)>1 else [],'requires_review':True,'warning':'המסגרות הן הצעה. בדקו גבולות, חפיפה וסיבוב מול המקור לפני אישור.'}


def digit_proposal(path):
    """Segment a numeral sequence and run a pinned MNIST ONNX classifier on CPU.

    Confidence is per glyph, not a guarantee for a complete check number.
    """
    model=Path(os.environ.get('INVOICE_DIGIT_MODEL',assets()/'models/mnist-12.onnx'))
    if not model.is_file():return {'engine':'mnist-onnx','available':False,'number':'','warning':'מודל הספרות לא מותקן'}
    import onnxruntime as ort
    with Image.open(path) as image:gray=np.array(ImageOps.grayscale(image))
    if gray.size>40_000_000:raise ValueError('Image too large')
    binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1]
    # Suppress long scanner ruling and isolated specks, preserving handwritten strokes.
    n,labels,stats,_=cv2.connectedComponentsWithStats(binary,8)
    candidates=[(x,y,w,h,a) for x,y,w,h,a in stats[1:] if a>=max(5,binary.size*.00004) and h>=binary.shape[0]*.15 and w<binary.shape[1]*.9]
    candidates.sort()
    if not candidates:return {'engine':'mnist-onnx','available':True,'number':'','confidence':0,'warning':'לא נמצאו ספרות מופרדות; אפשר להקליד או לסמן אזור מצומצם'}
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
    digits=[];confidence=[];boxes=[]
    for x,y,w,h,area in candidates[:30]:
        glyph=binary[y:y+h,x:x+w]
        scale=20/max(w,h);resized=cv2.resize(glyph,(max(1,round(w*scale)),max(1,round(h*scale))))
        canvas=np.zeros((28,28),np.float32);rh,rw=resized.shape
        canvas[(28-rh)//2:(28-rh)//2+rh,(28-rw)//2:(28-rw)//2+rw]=resized/255
        value=canvas.reshape(1,1,28,28)
        score=session.run(None,{session.get_inputs()[0].name:value})[0].reshape(-1)
        probability=np.exp(score-score.max());probability/=probability.sum()
        digits.append(str(int(score.argmax())));confidence.append(float(probability.max()))
        boxes.append({'x':int(x),'y':int(y),'w':int(w),'h':int(h)})
    return {'engine':'mnist-onnx','available':True,'number':''.join(digits),'confidence':min(confidence),'glyph_confidences':confidence,'boxes':boxes,'confirmed':False,'warning':'מודל ספרות בודדות עם הפרדת תווים. ספרות נוגעות או חופפות מחייבות תיקון; יש לאשר רצף מלא מול המקור.'}


def suggested_checks(path,excluded=(),words=None):
    words=tokens(path) if words is None else words
    candidates=[]
    regions=colored_digit_regions(path)
    import tempfile
    from .sequence_digits import recognize as sequence_recognize
    with Image.open(path) as image:
        for region in regions:
            x,y,w,h=[region[k] for k in ('x','y','w','h')]
            with tempfile.TemporaryDirectory() as temp:
                cropped=Path(temp)/'crop.png';image.crop((round(x*image.width),round(y*image.height),round((x+w)*image.width),round((y+h)*image.height))).save(cropped)
                proposals=[sequence_recognize(cropped,model) for model in ('paddle-v6-small','paddle-v5')]
            proposal=max((p for p in proposals if p.get('number')),key=lambda p:p.get('confidence',0),default={})
            candidates.append({'number':proposal.get('number',''),'region':{k:region[k] for k in ('x','y','w','h','rotation')},'engine':proposal.get('engine','colored-ink'),'proposals':proposals,'confirmed':False,'reason':region['reason']})
    for word in words:
        text=word['text'].strip('.,:;#')
        if not text.isascii() or not text.isdigit() or not 4<=len(text)<=12 or text in excluded:continue
        region={k:word[k] for k in ('x','y','w','h','rotation')}
        # Expand enough to retain handwritten ascenders and nearby leading zeros.
        x=max(0,region['x']-.008);y=max(0,region['y']-.008)
        region.update(x=x,y=y,w=min(1-x,region['w']+.016),h=min(1-y,region['h']+.016))
        candidates.append({'number':text,'region':region,'engine':'tesseract','confirmed':False,'reason':'רצף ספרות בעמוד המקורי. ייתכן שהוא אסמכתא אחרת; נדרש שיוך ידני.'})
    return candidates[:50]


def indra_recognize(path):
    model_dir=assets()/'models/indra'
    if not (model_dir/'rec_rtl.onnx').is_file():return {'available':False,'engine':'indra-rtl','text':'','warning':'מודל Indra RTL אינו מותקן'}
    try:
        import indra_ocr
        conv=indra_ocr.load(path=str(model_dir),threads=2,mode='ocr')
        result=[]
        for _,page in conv.convert(str(path)):result.append(page.text)
        return {'available':True,'engine':'indra-rtl','text':'\n'.join(result),'warning':'Indra כפוף לרישיון המודל OpenRAIL-M; בדקו התאמה לשימוש המסחרי שלכם'}
    except (ImportError,OSError,RuntimeError,ValueError) as exc:return {'available':False,'engine':'indra-rtl','text':'','warning':type(exc).__name__}


def pdf_items(page):
    aliases={'description':['description','תיאור','פריט'],'quantity':['quantity','qty','כמות'],'price':['price','מחיר','מחיר יחידה'],'discount':['discount','הנחה'],'total':['total','סכום','סהכ','סה״כ'],'item_code':['code','מק״ט','מקט']}
    try:tables=page.find_tables().tables
    except (ValueError,RuntimeError):return []
    from .advanced import validate_items
    output=[]
    for table in tables:
        rows=table.extract()
        if len(rows)<2:continue
        mapping={}
        for i,header in enumerate(rows[0]):
            title=(header or '').strip().casefold()
            for key,names in aliases.items():
                if title in names:mapping[key]=i
        if not {'description','quantity','price','total'}<=set(mapping):continue
        for row in rows[1:]:
            value={key:str(row[index] or '').replace(',','').strip() for key,index in mapping.items() if index<len(row)}
            value.setdefault('discount','0')
            try:output.extend(validate_items([value]))
            except HTTPException:continue
    return output


def colored_digit_regions(path):
    """Locate blue/purple annotations while retaining original-page coordinates."""
    with Image.open(path) as image:rgb=np.array(image.convert('RGB'))
    scale=min(1,1600/rgb.shape[1]);rgb=cv2.resize(rgb,None,fx=scale,fy=scale)
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    mask=cv2.inRange(hsv,np.array([85,35,35]),np.array([175,255,250]))
    n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
    height,width=mask.shape
    parts=[tuple(map(int,(x,y,w,h,area))) for x,y,w,h,area in stats[1:] if area>=5 and h>=3 and area<width*height*.05]
    groups=[]
    for x,y,w,h,area in sorted(parts,key=lambda p:p[0]):
        joined=False
        for group in groups:
            gx,gy,gw,gh=group
            overlap=min(y+h,gy+gh)-max(y,gy)
            if overlap>min(h,gh)*.2 and x-(gx+gw)<max(h,gh)*1.2 and gx-(x+w)<max(h,gh)*1.2:
                right=max(gx+gw,x+w);bottom=max(gy+gh,y+h);group[:]=[min(gx,x),min(gy,y),right-min(gx,x),bottom-min(gy,y)];joined=True;break
        if not joined:groups.append([x,y,w,h])
    results=[]
    for x,y,w,h in groups:
        if w<width*.045 or h<height*.008 or w/h<1.2 or h>height*.15:continue
        pad=max(5,round(h*.18));left=max(0,x-pad);top=max(0,y-pad);right=min(width,x+w+pad);bottom=min(height,y+h+pad)
        results.append({'x':left/width,'y':top/height,'w':(right-left)/width,'h':(bottom-top)/height,'rotation':0,'reason':'דיו כחול או סגול שנוסף לסריקה; נדרש לוודא שמדובר במספר צ׳ק'})
    return sorted(results,key=lambda r:(r['y'],r['x']))[:30]
