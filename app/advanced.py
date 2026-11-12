"""Company-scoped lists, provenance, multi-page documents and acceptance evidence."""
import csv
import difflib
import io
import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from fastapi import Body, File, Form, HTTPException, UploadFile
from .domain import FIELDS, money

KINDS={'customers','employees','accounts','banks','cash','transaction_types','cost_centers','projects','items'}
PURPOSES={'journal','supplier_invoice','reconciliation','archive'}

def fail(message,status=400):raise HTTPException(status,message)
def encode(value):return json.dumps(value,ensure_ascii=False)
def now():return datetime.now(timezone.utc).isoformat()


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS company_meta(company_id TEXT PRIMARY KEY REFERENCES companies(id),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_meta(document_id TEXT PRIMARY KEY REFERENCES documents(id),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_links(primary_id TEXT NOT NULL REFERENCES documents(id),child_id TEXT PRIMARY KEY REFERENCES documents(id),CHECK(primary_id<>child_id));
    CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),kind TEXT NOT NULL,code TEXT NOT NULL,value TEXT NOT NULL,UNIQUE(company_id,kind,code));
    CREATE TABLE IF NOT EXISTS defaults(company_id TEXT NOT NULL REFERENCES companies(id),supplier_id TEXT NOT NULL REFERENCES suppliers(id),kind TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(company_id,supplier_id,kind));
    CREATE TABLE IF NOT EXISTS recognition_runs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),engine TEXT NOT NULL,result TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS evaluations(document_id TEXT PRIMARY KEY REFERENCES documents(id),truth TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cloud_settings(company_id TEXT PRIMARY KEY REFERENCES companies(id),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cloud_usage(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),month TEXT NOT NULL,reserved TEXT NOT NULL,actual TEXT,status TEXT NOT NULL,detail TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integration_settings(company_id TEXT PRIMARY KEY REFERENCES companies(id),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integration_attempts(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES batches(id),status TEXT NOT NULL,result TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS verification(company_id TEXT PRIMARY KEY REFERENCES companies(id),value TEXT NOT NULL);
    ''')


def company_meta(c,company_id):
    row=c.execute('SELECT value FROM company_meta WHERE company_id=?',(company_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_company_meta(c,company_id,value):
    c.execute('INSERT INTO company_meta VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET value=excluded.value',(company_id,encode(value)))


def meta(c,doc_id):
    row=c.execute('SELECT value FROM document_meta WHERE document_id=?',(doc_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_meta(c,doc_id,changes):
    value=meta(c,doc_id);value.update(changes)
    c.execute('INSERT INTO document_meta VALUES(?,?) ON CONFLICT(document_id) DO UPDATE SET value=excluded.value',(doc_id,encode(value)))
    return value


def enrich(c,d):
    value=meta(c,d['id'])
    d.update({key:value.get(key,default) for key,default in [('items',[]),('field_sources',{}),('field_engines',{}),('payment',None),('processing',{}),('suggested_checks',[])]})
    ids=[d['id']]+[r[0] for r in c.execute('SELECT child_id FROM document_links WHERE primary_id=?',(d['id'],))]
    d['pages']=[dict(r) for i in ids for r in c.execute('SELECT id,source_id,page,region,engine FROM documents WHERE id=?',(i,))]
    d['supplier_suggestions']=[]
    name=d['fields'].get('supplier_name','')
    if name and not d['supplier_id']:
        for row in c.execute('SELECT * FROM suppliers WHERE company_id=? AND active=1',(d['company_id'],)):
            score=difflib.SequenceMatcher(None,name.casefold(),row['name'].casefold()).ratio()
            if score>=.35:d['supplier_suggestions'].append({'id':row['id'],'code':row['code'],'name':row['name'],'score':round(score,2),'reason':'דמיון בשם בלבד; נדרש אישור'})
        d['supplier_suggestions'].sort(key=lambda r:r['score'],reverse=True);d['supplier_suggestions']=d['supplier_suggestions'][:5]


def defaults_for(c,company_id,supplier_id,kind):
    row=c.execute('SELECT value FROM defaults WHERE company_id=? AND supplier_id=? AND kind=?',(company_id,supplier_id,kind)).fetchone()
    return json.loads(row[0]) if row else {}


def validate_items(items):
    if not isinstance(items,list) or len(items)>1000:fail('עד 1,000 שורות פריטים למסמך')
    output=[]
    for item in items:
        if not isinstance(item,dict):fail('שורת פריט לא תקינה')
        description=str(item.get('description',''))[:1000]
        try:
            quantity=Decimal(str(item.get('quantity','1')));discount=Decimal(str(item.get('discount','0')))
            price=money(item.get('price',''));total=money(item.get('total',''))
            if not quantity.is_finite() or not discount.is_finite() or abs(quantity)>Decimal('1000000000') or not 0<=discount<=100:raise ValueError()
            calculated=(quantity*price*(1-discount/100)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            if calculated!=total:fail('כמות כפול מחיר לאחר הנחה אינה שווה לסכום השורה')
        except (ValueError,ArithmeticError):fail('נתונים לא תקינים בשורת פריט')
        output.append({'description':description,'quantity':str(quantity),'price':str(price),'discount':str(discount),'total':str(total),'item_code':str(item.get('item_code',''))[:200]})
    return output


def update_extra(c,d,payload,fields,supplier_id):
    changes={}
    if 'items' in payload:changes['items']=validate_items(payload['items'])
    if fields.get('purpose') not in PURPOSES:fail('יש לבחור יעד מסמך נתמך')
    engines=meta(c,d['id']).get('field_engines',{})
    for key,value in fields.items():
        if value!=d['fields'].get(key):engines[key]='manual'
    changes['field_engines']=engines
    save_meta(c,d['id'],changes)
    if payload.get('save_defaults'):
        if not supplier_id:fail('יש לבחור ספק לפני שמירת כללי רישום')
        defaults={k:fields.get(k,'') for k in ('expense_account','transaction_type','vat_account','cost_center','project','purpose')}
        c.execute('INSERT INTO defaults VALUES(?,?,?,?) ON CONFLICT(company_id,supplier_id,kind) DO UPDATE SET value=excluded.value',(d['company_id'],supplier_id,fields['kind'],encode(defaults)))


def extra_issues(c,d):
    value=meta(c,d['id']);out=[]
    if value.get('items'):
        try:
            if sum((money(i['total']) for i in value['items']),Decimal(0))!=money(d['fields'].get('net','')):out.append('סכומי שורות הפריטים אינם שווים לסכום לפני מע״מ')
        except ValueError:out.append('סכומי פריטים לא תקינים')
    for field,kind in [('expense_account','accounts'),('vat_account','accounts'),('transaction_type','transaction_types'),('cost_center','cost_centers'),('project','projects')]:
        code=d['fields'].get(field,'')
        if code and c.execute('SELECT 1 FROM records WHERE company_id=? AND kind=? LIMIT 1',(d['company_id'],kind)).fetchone():
            record=c.execute('SELECT value FROM records WHERE company_id=? AND kind=? AND code=?',(d['company_id'],kind,code)).fetchone()
            if not record or json.loads(record[0]).get('active') is False:out.append(f'קוד {code} אינו כרטיס פעיל ברשימת {kind}')
    return out


def source_document(c,d,doc_id=None):
    doc_id=doc_id or d['id']
    if doc_id!=d['id'] and not c.execute('SELECT 1 FROM document_links WHERE primary_id=? AND child_id=?',(d['id'],doc_id)).fetchone():fail('עמוד המקור אינו חלק מהחשבונית',404)
    row=c.execute('SELECT * FROM documents WHERE id=? AND company_id=?',(doc_id,d['company_id'])).fetchone()
    if not row:fail('עמוד לא נמצא',404)
    return dict(row)


def register(app,s):
    db=s['db'];company=s['company'];document=s['document'];describe=s['describe'];audit=s['audit'];editable=s['editable'];data=s['data'];crop=s['crop']

    @app.post('/api/companies/{company_id}/import-fixed-preview')
    def fixed_import_preview(company_id:str,file:UploadFile=File(...),widths:str=Form(...),encoding:str=Form('cp1255')):
        with db() as c:company(c,company_id)
        try:width_list=[int(x.strip()) for x in widths.split(',')]
        except ValueError:fail('אורכי עמודות לא תקינים')
        if not width_list or len(width_list)>50 or any(w<1 or w>4000 for w in width_list) or sum(width_list)>20000 or encoding not in ('utf-8-sig','cp1255'):fail('מבנה קובץ לא תקין')
        content=file.file.read(5_000_001)
        if len(content)>5_000_000:fail('הקובץ מוגבל ל־5MB')
        try:text=content.decode(encoding)
        except UnicodeDecodeError:fail('הקידוד אינו מתאים לקובץ')
        lines=text.splitlines()
        if len(lines)>1000:fail('עד 1,000 שורות בכל יבוא')
        rows=[[f'column_{i+1}' for i in range(len(width_list))]]
        for line in lines:
            if not line.strip():continue
            encoded=line.encode(encoding.replace('-sig',''))
            if len(encoded)!=sum(width_list):fail('אורך שורה אינו תואם למבנה שנבחר; בדקו את המפרט לפני יבוא')
            offset=0;row=[]
            for width in width_list:
                try:row.append(encoded[offset:offset+width].decode(encoding.replace('-sig','')).strip())
                except UnicodeDecodeError:fail('רוחב עמודה חוצה תו בקידוד שנבחר')
                offset+=width
            rows.append(row)
        return {'sheets':{'Fixed width':rows}}

    @app.get('/api/companies/{company_id}/records/{kind}')
    def records(company_id:str,kind:str):
        if kind not in KINDS:fail('סוג רשימה לא נתמך')
        with db() as c:
            company(c,company_id)
            return [{**json.loads(r['value']),'id':r['id']} for r in c.execute('SELECT * FROM records WHERE company_id=? AND kind=? ORDER BY code',(company_id,kind))]

    @app.post('/api/companies/{company_id}/records/{kind}/preview')
    def preview_records(company_id:str,kind:str,file:UploadFile=File(...)):
        if kind not in KINDS|{'companies'}:fail('סוג רשימה לא נתמך')
        return s['preview_suppliers'](company_id,file)

    @app.post('/api/companies/{company_id}/records/{kind}/import')
    def import_records(company_id:str,kind:str,payload:dict=Body(...)):
        if kind not in KINDS:fail('סוג רשימה לא נתמך')
        rows=payload.get('rows')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:fail('יש לבחור 1–1,000 שורות')
        seen=set();clean=[]
        for row in rows:
            if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k].strip() or len(row[k])>200 for k in ('code','name')):fail('חסרים קוד ושם ברשומה')
            if row['code'] in seen:fail('קוד כפול ברשימה')
            seen.add(row['code'])
            clean.append({k:str(v)[:2000] for k,v in row.items() if k!='id'})
            clean[-1]['active']=str(row.get('active','true')).strip().casefold() not in ('false','0','לא','inactive','no')
        with db() as c:
            company(c,company_id)
            for row in clean:
                c.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(company_id,kind,code) DO UPDATE SET value=excluded.value',(uuid.uuid4().hex,company_id,kind,row['code'],encode(row)))
            audit(c,company_id,None,'records_imported',{'kind':kind,'count':len(clean)})
        return {'count':len(clean)}

    @app.post('/api/companies/import')
    def import_companies(payload:dict=Body(...)):
        rows=payload.get('rows')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:fail('רשימת חברות לא תקינה')
        with db() as c:
            result=[]
            for p in rows:
                if not isinstance(p,dict) or not isinstance(p.get('name'),str) or not 1<=len(p['name'].strip())<=150:fail('שם חברה לא תקין')
                tax=str(p.get('tax_id',''))
                if tax and not re.fullmatch('[0-9]{9}',tax):fail('מספר עוסק לא תקין')
                target=p.get('target',s['TARGETS'][0])
                if target not in s['TARGETS']:fail('תוכנת יעד לא נתמכת')
                existing=c.execute('SELECT id FROM companies WHERE tax_id=? AND name=?',(tax,p['name'].strip())).fetchone()
                if existing:result.append(existing[0]);continue
                ident=uuid.uuid4().hex;c.execute('INSERT INTO companies VALUES(?,?,?,?,?)',(ident,p['name'].strip(),tax,target,now()));save_company_meta(c,ident,{'code':str(p.get('code',''))[:100]});result.append(ident)
            return {'ids':result}

    @app.post('/api/documents/{doc_id}/join')
    def join(doc_id:str,payload:dict=Body(...)):
        ids=payload.get('ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=49 or len(set(ids))!=len(ids) or doc_id in ids:fail('בחרו עד 49 עמודי המשך שונים')
        with db() as c:
            primary=document(c,doc_id);editable(primary)
            if c.execute('SELECT 1 FROM document_links WHERE child_id=?',(doc_id,)).fetchone():fail('מסמך זה כבר צורף כעמוד המשך')
            checks=list(primary['checks'])
            for ident in ids:
                child=document(c,ident,primary['company_id']);editable(child)
                if c.execute('SELECT 1 FROM document_links WHERE child_id=? OR primary_id=?',(ident,ident)).fetchone():fail('עמוד כבר משויך. יש לפרק את החיבור הקודם תחילה',409)
                c.execute('INSERT INTO document_links VALUES(?,?)',(doc_id,ident))
                c.execute("UPDATE documents SET status='archived',revision=revision+1 WHERE id=?",(ident,))
                checks.extend([{**check,'source_document_id':ident} for check in child['checks']])
            c.execute("UPDATE documents SET status='review',checks=?,revision=revision+1 WHERE id=?",(encode(checks),doc_id))
            audit(c,primary['company_id'],doc_id,'joined',{'pages':ids})
            return describe(c,document(c,doc_id))

    @app.post('/api/documents/{doc_id}/unjoin')
    def unjoin(doc_id:str):
        with db() as c:
            d=document(c,doc_id);editable(d)
            ids=[r[0] for r in c.execute('SELECT child_id FROM document_links WHERE primary_id=?',(doc_id,))]
            for ident in ids:c.execute("UPDATE documents SET status='review',revision=revision+1 WHERE id=?",(ident,))
            c.execute('DELETE FROM document_links WHERE primary_id=?',(doc_id,))
            checks=[ch for ch in d['checks'] if ch.get('source_document_id',doc_id)==doc_id]
            c.execute("UPDATE documents SET status='review',checks=?,revision=revision+1 WHERE id=?",(encode(checks),doc_id))
            audit(c,d['company_id'],doc_id,'unjoined',{'pages':ids})
        return {'ids':ids}

    @app.post('/api/documents/{doc_id}/split-suggestions')
    def suggest_split(doc_id:str):
        from .vision import suggest_regions
        with db() as c:d=document(c,doc_id)
        return suggest_regions(data/d['image'])

    @app.post('/api/documents/{doc_id}/check-suggestions')
    def check_suggestions(doc_id:str):
        from .vision import suggested_checks
        with db() as c:
            d=document(c,doc_id);editable(d)
            source=data/'files'/d['source_id']/f"page-{d['page']}.png"
            fields=d['fields'];excluded=[fields.get(key,'') for key in ('supplier_tax_id','recipient_tax_id','number','allocation')]
            results=suggested_checks(source,excluded)
            for result in results:result['source_document_id']=doc_id
            related=[]
            for row in c.execute("SELECT id,checks FROM documents WHERE company_id=? AND id<>? AND status<>'archived'",(d['company_id'],doc_id)):
                for check in json.loads(row['checks']):
                    if check['number'] in {r['number'] for r in results}:related.append({'document_id':row['id'],'number':check['number']})
            return {'candidates':results,'related':related,'requires_assignment':True}

    @app.post('/api/documents/{doc_id}/recognize-indra')
    def recognize_indra(doc_id:str,payload:dict=Body(...)):
        from .vision import indra_recognize
        from .domain import extract
        if payload.get('license_accepted') is not True:fail('יש לאשר את התאמת רישיון המודל לשימוש לפני הפעלת Indra')
        with db() as c:
            d=document(c,doc_id);editable(d)
            result=indra_recognize(data/d['image'])
            if not result['available']:fail(result['warning'],409)
            proposal={**result,'fields':extract(result['text']),'checks':[],'items':[],'confirmed':False}
            run=uuid.uuid4().hex;c.execute('INSERT INTO recognition_runs VALUES(?,?,?,?,?)',(run,doc_id,'indra-rtl',encode(proposal),now()))
            audit(c,d['company_id'],doc_id,'recognition_proposed',{'run_id':run,'engine':'indra-rtl'})
            return {**proposal,'id':run,'conflicts':{key:{'current':d['fields'].get(key,''),'proposal':value} for key,value in proposal['fields'].items() if value and d['fields'].get(key) and value!=d['fields'][key]}}

    @app.get('/api/documents/{doc_id}/recognitions')
    def recognitions(doc_id:str):
        with db() as c:
            document(c,doc_id)
            return [{**dict(r),'result':json.loads(r['result'])} for r in c.execute('SELECT * FROM recognition_runs WHERE document_id=? ORDER BY created',(doc_id,))]

    @app.post('/api/documents/{doc_id}/evaluate')
    def evaluate(doc_id:str,payload:dict=Body(...)):
        allowed={'number','supplier_tax_id','total','checks'}
        truth=payload.get('truth')
        if not isinstance(truth,dict) or not truth or any(k not in allowed or not isinstance(v,str) or len(v)>1000 for k,v in truth.items()):fail('ערכי אמת לא תקינים')
        with db() as c:
            d=document(c,doc_id)
            c.execute('INSERT INTO evaluations VALUES(?,?,?) ON CONFLICT(document_id) DO UPDATE SET truth=excluded.truth,created=excluded.created',(doc_id,encode(truth),now()))
            audit(c,d['company_id'],doc_id,'evaluated',{'fields':list(truth)})
        return {'ok':True}

    @app.get('/api/companies/{company_id}/metrics')
    def metrics(company_id:str):
        import math
        def bucket():return {'correct':0,'tested':0}
        def score(value):
            n=value['tested'];k=value['correct'];rate=k/n if n else None
            lower=None
            if n:
                z=1.96;lower=(rate+z*z/(2*n)-z*math.sqrt(rate*(1-rate)/n+z*z/(4*n*n)))/(1+z*z/n)
            return {**value,'rate':rate,'lower_95_percent_bound':lower}
        with db() as c:
            company(c,company_id);rows=c.execute("SELECT d.*,e.truth FROM documents d LEFT JOIN evaluations e ON e.document_id=d.id WHERE d.company_id=? AND d.status<>'archived'",(company_id,)).fetchall()
            totals={key:bucket() for key in ('number','supplier_tax_id','total','checks')};finals={key:bucket() for key in totals};whole=bucket();whole_final=bucket();corrected=0;durations=[];engines={}
            for row in rows:
                fields=json.loads(row['fields']);original=json.loads(row['original_fields']);checks=';'.join(ch['number'] for ch in json.loads(row['checks']))
                edits=c.execute("SELECT detail FROM audit WHERE document_id=? AND action='edited'",(row['id'],)).fetchall()
                corrected+=any(json.loads(e[0]).get('before')!=json.loads(e[0]).get('after') or json.loads(e[0]).get('checks_before')!=json.loads(e[0]).get('checks_after') for e in edits)
                if row['truth']:
                    truth=json.loads(row['truth']);initial={**original,'checks':meta(c,row['id']).get('initial_checks','')};final={**fields,'checks':checks}
                    for key,value in truth.items():
                        totals[key]['tested']+=1;totals[key]['correct']+=initial.get(key,'')==value
                        if row['status'] in ('approved','exported','received'):
                            finals[key]['tested']+=1;finals[key]['correct']+=final.get(key,'')==value
                    if set(truth)==set(totals):
                        whole['tested']+=1;whole['correct']+=all(initial.get(k,'')==v for k,v in truth.items())
                        if row['status'] in ('approved','exported','received'):
                            whole_final['tested']+=1;whole_final['correct']+=all(final.get(k,'')==v for k,v in truth.items())
                    for run in c.execute('SELECT engine,result FROM recognition_runs WHERE document_id=?',(row['id'],)):
                        proposal=json.loads(run['result']);values={**proposal.get('fields',{}),'checks':';'.join(proposal.get('checks',[]))};engine=engines.setdefault(run['engine'],{key:bucket() for key in truth})
                        for key,value in truth.items():
                            part=engine.setdefault(key,bucket());part['tested']+=1;part['correct']+=values.get(key,'')==value
                for r in c.execute("SELECT action,detail,created FROM audit WHERE document_id=? AND action IN ('receipt_manual','receipt_report','receipt_api') ORDER BY id",(row['id'],)):
                    if r['action']=='receipt_api' or json.loads(r['detail']).get('status')=='received':durations.append((datetime.fromisoformat(r['created'])-datetime.fromisoformat(row['created'])).total_seconds());break
            whole_score=score(whole_final)
            return {'documents':len(rows),'corrected_documents':corrected,'correction_rate':corrected/len(rows) if rows else None,'exact_accuracy':{k:score(v) for k,v in totals.items()},'after_approval_accuracy':{k:score(v) for k,v in finals.items()},'whole_document_accuracy':score(whole),'whole_document_after_approval':whole_score,'engine_accuracy':{engine:{k:score(v) for k,v in fields.items()} for engine,fields in engines.items()},'target_99_demonstrated':bool(whole_score['lower_95_percent_bound'] is not None and whole_score['lower_95_percent_bound']>=.99),'mean_seconds_upload_to_received':sum(durations)/len(durations) if durations else None,'measured_received':len(durations)}

    @app.post('/api/documents/{doc_id}/payment')
    def payment(doc_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('יצירת תנועת תשלום דורשת אישור מפורש')
        try:
            amount=money(payload.get('amount',''));datetime.strptime(payload.get('date',''),'%Y-%m-%d')
        except (ValueError,TypeError):fail('סכום או תאריך תשלום לא תקינים')
        if amount<=0 or not isinstance(payload.get('bank_account'),str) or not payload['bank_account'].strip():fail('נדרשים סכום חיובי וכרטיס בנק')
        with db() as c:
            d=document(c,doc_id);editable(d)
            if not d['supplier_id'] or not d['checks'] or any(not ch['confirmed'] for ch in d['checks']):fail('יש לאשר ספק ומספרי צ׳קים לפני תנועת תשלום')
            value={'amount':str(amount),'date':payload['date'],'bank_account':payload['bank_account'][:200],'confirmed':True}
            save_meta(c,doc_id,{'payment':value});c.execute("UPDATE documents SET status='review',revision=revision+1 WHERE id=?",(doc_id,))
            audit(c,d['company_id'],doc_id,'payment_prepared',value)
        return value

    from . import cloud, integrations
    app.state.additional_recognize=cloud.register(app,s)
    integrations.register(app,s)
