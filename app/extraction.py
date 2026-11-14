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
    label=re.sub('[0-9]','',flat(line)).replace('מעם','מעמ')
    if any(s in label for s in ('יתרה','חובכולל','ששולם','מחיריחידה','ליחידה','סכוםששולם')):return None,0
    if any(s in label for s in ('לפנימעמ','ללאמעמ','בלימעמ','לאמעמ','לואמעמ','מחיראחריהנחה','subtotal','סהכבשורות')):return 'net',10
    if ('מעמ' in label or label in ('vat','vatamount','taxamount')) and not any(s in label for s in ('כוללמעמ','שחכולל','תיקבמעמ','עוסק','שחכוללמעמ')):
        return 'vat',8
    if any(s in label for s in ('סהכלתשלום','סהכמחיר','סהככוללמעמ','סךהכול','סךהכללחיוב','total')):
        if 'subtotal' in label:return 'net',10
        return 'total',12 if any(s in label for s in ('לתשלום','לחיוב')) else 8
    if label in ('סהכ','סךהכל') or label.startswith('סהכלקבלה'):return 'total',8
    return None,0


def word_rows(words):
    """Rebuild spatial rows before classifying multiword Hebrew amount labels."""
    rows=[]
    for word in sorted(words,key=lambda w:w['y']+w['h']/2):
        center=word['y']+word['h']/2
        row=next((r for r in reversed(rows) if abs(r[0]-center)<=max(.0035,min(r[1],word['h'])*.55)),None)
        if row:row[2].append(word)
        else:rows.append([center,word['h'],[word]])
    for _,_,row in rows:
        hebrew=any(re.search('[א-ת]',w['text']) for w in row)
        yield ' '.join(w['text'] for w in sorted(row,key=lambda w:w['x'],reverse=hebrew)),row


def supplier_name(lines):
    explicit=re.search(r'(?:שם\s+(?:הספק|ספק)|supplier\s*name)\s*[:：]\s*(.+)','\n'.join(lines),re.I)
    if explicit:return explicit[1].strip()
    candidates=[]
    for index,line in enumerate(lines[:12]):
        if re.search(TITLE,line,re.I) or flat(line).startswith(('לכבוד','billto','לקוח')):break
        name=re.split(r'(?:עוסק\s*מורשה|עוסק\s*פטור|מספר\s*עוסק|ח[.״"\']?פ[.״"\']?\s*[:：]|VAT\s*ID)',line,flags=re.I)[0].strip(' |:')
        if re.search(r'טלפון|פקס|כתובת|רחוב|תאריך|מספר|subtotal|total|vat\b',name,re.I) or re.search(r'\d',name):continue
        if len(re.findall(r'[א-תA-Za-z]{2,}',name))>=2:candidates.append(name)
    if candidates:return candidates[0]
    for index,line in enumerate(lines[:-1]):
        if flat(line).startswith('מפיקהמסמך'):
            candidate=lines[index+1].strip(' .|')
            if len(re.findall('[א-תA-Za-z]{2,}',candidate))>=2 and not re.search(r'\d|חתימה|תאריך|שם המקבל|מסמך זה',candidate):return candidate
    return ''


def currency_for(lines):
    aliases={'USD':'USD','EUR':'EUR','GBP':'GBP','ILS':'ILS','דולר':'USD','דולרים':'USD','אירו':'EUR','יורו':'EUR','שקל':'ILS','שקלים':'ILS'}
    explicit=[]
    for line in lines:
        match=re.search(r'(?:currency|מטבע)\s*[:=-]?\s*([A-Za-z]{3}|דולרים|דולר|אירו|יורו|שקלים|שקל)\b',line,re.I)
        if match:explicit.append(aliases.get(match[1].upper(),aliases.get(match[1],match[1].upper())))
    if explicit:return explicit[0] if len(set(explicit))==1 else ''
    detected=set(re.findall(r'\b(?:USD|EUR|GBP|ILS|CAD|AUD|CHF|JPY|CNY)\b','\n'.join(lines),re.I));detected={v.upper() for v in detected}
    text='\n'.join(lines)
    if '€' in text:detected.add('EUR')
    if '£' in text:detected.add('GBP')
    if '₪' in text or re.search(r'ש[״"\']ח',text):detected.add('ILS')
    if len(detected)==1:return next(iter(detected))
    if detected or '$' in text:return ''
    return 'ILS'


def parse(text,fields,words=None,company_tax_id=''):
    text=re.sub('[\u200e\u200f\u202a-\u202e\u2066-\u2069]','',text).replace('−','-')
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    result=dict.fromkeys(fields,'');result.update(currency=currency_for(lines),kind=kind_for(lines))
    result['purpose']='supplier_invoice' if result['kind'] in ('invoice','tax_receipt','credit') else 'reconciliation'
    result['supplier_name']=supplier_name(lines)
    numbers=[]
    for index,line in enumerate(lines):
        patterns=[(rf'^\s*{TITLE}\s*(?:מספר|מס[׳\"\'.]?|No\.?)?\s*[:#-]?\s*({IDENTIFIER})',12),(rf'(?:^|\s)מספר\s*[:#]\s*({IDENTIFIER})',9 if 'חשבונית' in line or line.startswith('מספר') else 0),(rf'(?:מספר\s+חשבונית|ברקוד\s+מספר\s+חשבונית)\s*[:#]?\s*[*\"\s]*({IDENTIFIER})',11),(rf'^מספר\s+תעודה\s*[:#]?\s*({IDENTIFIER})',5),(rf'^אסמכתא\s+לתשלום\s+מס\s*[:#]?\s*({IDENTIFIER})',8)]
        for pattern,score in patterns:
            match=re.search(pattern,line,re.I)
            if score and match and not DATE.fullmatch(match[1]) and any(c.isdigit() for c in match[1]):numbers.append((score-index/10000,match[1]))
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
        for row_text,row in word_rows(words):
            key,priority=role(row_text)
            if not key:continue
            for other in row:
                for value in amounts(other['text']):add(key,value,priority+5+other['y']*2)
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
