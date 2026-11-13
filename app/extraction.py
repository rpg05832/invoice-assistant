"""Conservative field proposals from labels, page position and amount consistency."""
import itertools
import re
from datetime import date
from decimal import Decimal, InvalidOperation

DATE=re.compile(r'(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})(?!\d)')
AMOUNT=re.compile(r'(?<![\d])[-+]?(?:\d{1,3}(?:[, .]\d{3})+|\d+)[.,]\d{2}(?!\d)')
IDENTIFIER=r'[A-Za-z]{0,6}[-/]?[0-9][A-Za-z0-9/\-]{0,29}'
TITLE=r'(?:חשבונית(?:\s+מס)?(?:\s*[/־-]?\s*קבלה)?(?:\s+מרכזת)?|קבלה|זיכוי|חשבון\s+עסקה|תעודת\s+משלוח|Invoice(?:\s+No\.?)?)'


def flat(text):return re.sub(r'[^a-zא-ת0-9]','',text.casefold())


def amounts(line):
    found=[]
    for match in AMOUNT.finditer(line):
        if line[match.end():].lstrip().startswith('%'):continue
        if 'מעמ' in flat(line) and '(' in line[:match.start()] and ')' in line[match.end():] and Decimal(match[0].replace(',','.'))<=100:continue
        value=match[0].replace(' ','');sign='-' if value.startswith('-') else '';value=value.lstrip('+-');whole,cents=value[:-3],value[-2:]
        whole=whole.replace(',','').replace('.','')
        try:
            number=Decimal(sign+whole+'.'+cents)
            if abs(number)<=Decimal('999999999999.99'):found.append(str(number.quantize(Decimal('.01'))))
        except InvalidOperation:pass
    return found


def dates(line):
    result=[]
    for match in DATE.finditer(line):
        day,month,year=map(int,match.groups())
        if year<100:year=2000+year if year<70 else 1900+year
        try:result.append(date(year,month,day).isoformat())
        except ValueError:pass
    return result


def kind_for(lines):
    for line in lines[:45]:
        label=flat(line)
        if any(v in label for v in ('ברקוד','פרטיתשלום','עבורחשבון','סכוםהחשבונית','תאריךחשבונית','מספרחשבונית')):continue
        if re.match(r'^(?:הדפסת\s+)?(?:חשבונית|קבלה|זיכוי|חשבון\s+עסקה|תעודת\s+משלוח|Invoice)',line,re.I):
            if 'זיכוי' in label:return 'credit'
            if 'תעודתמשלוח' in label:return 'delivery'
            if 'חשבוןעסקה' in label:return 'proforma'
            if 'חשבונית' in label and 'קבלה' in label:return 'tax_receipt'
            if label.startswith('קבלה'):return 'receipt'
            return 'invoice'
    return 'other'


def role(line):
    label=flat(line)
    if any(s in label for s in ('יתרה','חובכולל','ששולם','מחיריחידה','ליחידה','סכוםששולם')):return None,0
    if any(s in label for s in ('לפנימעמ','ללאמעמ','מחיראחריהנחה','subtotal','סהכבשורות')):return 'net',10
    if ('מעמ' in label or label in ('vat','vatamount','taxamount')) and not any(s in label for s in ('כוללמעמ','שחכולל','תיקבמעמ','עוסק','שחכוללמעמ')):
        return 'vat',8
    if any(s in label for s in ('סהכלתשלום','סהכמחיר','סהככוללמעמ','סךהכול','סךהכללחיוב','total')):
        if 'subtotal' in label:return 'net',10
        return 'total',12 if any(s in label for s in ('לתשלום','לחיוב')) else 8
    if label in ('סהכ','סךהכל') or label.startswith('סהכלקבלה'):return 'total',8
    return None,0


def parse(text,fields,words=None,company_tax_id=''):
    text=re.sub('[\u200e\u200f\u202a-\u202e\u2066-\u2069]','',text).replace('−','-')
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    result=dict.fromkeys(fields,'');result.update(currency='ILS',kind=kind_for(lines))
    result['purpose']='supplier_invoice' if result['kind'] in ('invoice','tax_receipt','credit') else 'reconciliation'
    numbers=[]
    for index,line in enumerate(lines):
        patterns=[(rf'^\s*{TITLE}\s*(?:מספר|מס[׳\"\'.]?|No\.?)?\s*[:#-]?\s*({IDENTIFIER})',12),(rf'^\s*מספר\s*[:#]\s*({IDENTIFIER})',9),(rf'(?:מספר\s+חשבונית|ברקוד\s+מספר\s+חשבונית)\s*[:#]?\s*[*\"\s]*({IDENTIFIER})',11),(rf'^מספר\s+תעודה\s*[:#]?\s*({IDENTIFIER})',5),(rf'^אסמכתא\s+לתשלום\s+מס\s*[:#]?\s*({IDENTIFIER})',8)]
        for pattern,score in patterns:
            match=re.search(pattern,line,re.I)
            if match and not DATE.fullmatch(match[1]) and any(c.isdigit() for c in match[1]):numbers.append((score-index/10000,match[1]))
    if numbers:result['number']=max(numbers)[1]
    date_candidates=[];due=[]
    for index,line in enumerate(lines):
        label=flat(line);values=dates(line)
        if not values and any(k in label for k in ('תאריך','date')) and index+1<len(lines):values=dates(lines[index+1])
        for value in values:
            if any(k in label for k in ('לתשלוםעד','תאריךפרעון','תאריךפירעון','duedate')):due.append((index,value));continue
            if any(k in label for k in ('הדפסה','הופקב','פרעון','פירעון')):continue
            score=15 if any(k in label for k in ('תאריךחשבונית','תאריךמסמך','invoicedate')) else 10 if label.startswith(('תאריך','date')) else 3-index/len(lines or [0])
            date_candidates.append((score-index/10000,value))
    if date_candidates:result['date']=max(date_candidates)[1]
    if due:result['due_date']=min(due)[1]
    id_candidates=[]
    for index,line in enumerate(lines[:70]):
        label=flat(line);ids=re.findall(r'(?<!\d)[0-9]{9}(?!\d)',line)
        if not ids and any(k in label for k in ('עוסקמורשה','מספרעוסק','חפ','vatid','עמותרשומה')) and index+1<len(lines):ids=re.findall(r'(?<!\d)[0-9]{9}(?!\d)',lines[index+1])
        for ident in ids:
            if ident==company_tax_id:result['recipient_tax_id']=ident;continue
            if any(k in label for k in ('חברהלקוח','תיקמעמלקוח','חברה')) and not any(k in label for k in ('עוסק','עמותה')):
                if not result['recipient_tax_id']:result['recipient_tax_id']=ident
                continue
            score=10 if any(k in label for k in ('עוסק','חפ','vatid','עמותרשומה','תיקמסהכנסה')) else 1
            id_candidates.append((score-index/100,ident))
    if id_candidates:
        ranked_ids=sorted(set(id_candidates),reverse=True)
        if len({ident for score,ident in ranked_ids if score>=ranked_ids[0][0]-.6})==1:result['supplier_tax_id']=ranked_ids[0][1]
    allocation=re.search(r'(?:מספר\s+הקצאה|הקצאה|הקצא)\s*[:#]?\s*([0-9]{5,30})',text)
    if allocation:result['allocation']=allocation[1]
    # Keep several candidates: a table item must never win merely by appearing first.
    candidates={key:{} for key in ('net','vat','total')}
    def add(key,value,score):candidates[key][value]=max(score,candidates[key].get(value,-1000))
    for index,line in enumerate(lines):
        key,priority=role(line)
        if not key:continue
        if key=='vat' and flat(line)=='מעמ' and any('כולל' in flat(previous) for previous in lines[max(0,index-2):index]):continue
        for offset in (0,1,-1,2,3,4):
            other=index+offset
            if not 0<=other<len(lines):continue
            if offset and role(lines[other])[0] not in (None,key):continue
            for value in amounts(lines[other]):
                add(key,value,priority-abs(offset)*1.5+index/max(1,len(lines))*2)
    # Geometry associates label and amount columns even when OCR reading order differs.
    if words:
        for word in words:
            key,priority=role(word['text'])
            if not key:continue
            center=word['y']+word['h']/2
            for other in words:
                distance=abs(other['y']+other['h']/2-center)
                if distance>max(.009,word['h']*.65,other['h']*.65):continue
                for value in amounts(other['text']):add(key,value,priority+5-distance*100+word['y']*2)
    observed={value for line in lines for value in amounts(line)}
    # Recover a missing component only when that exact amount was actually read.
    for net,total in itertools.product(list(candidates['net']),list(candidates['total'])):
        difference=Decimal(total)-Decimal(net)
        value=str(difference.quantize(Decimal('.01')))
        if value in observed and abs(difference)<=abs(Decimal(total))*Decimal('.4'):add('vat',value,9)
    for vat,total in itertools.product(list(candidates['vat']),list(candidates['total'])):
        difference=Decimal(total)-Decimal(vat);value=str(difference.quantize(Decimal('.01')))
        if value in observed and abs(Decimal(vat))<=abs(Decimal(total))*Decimal('.4'):add('net',value,9)
    ranked={key:sorted(values,key=values.get,reverse=True)[:12] for key,values in candidates.items()}
    triples=[]
    for net,vat,total in itertools.product(ranked['net'],ranked['vat'],ranked['total']):
        if abs(Decimal(net)+Decimal(vat)-Decimal(total))<=Decimal('.01'):
            score=sum(candidates[k][v] for k,v in [('net',net),('vat',vat),('total',total)])
            triples.append((score,net,vat,total))
    if triples:
        _,result['net'],result['vat'],result['total']=max(triples)
    else:
        for key,values in ranked.items():
            if values:result[key]=values[0]
    return result
