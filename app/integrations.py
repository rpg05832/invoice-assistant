"""Explicit, versioned export profiles and opt-in external accounting calls."""
import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx
from fastapi import Body,File,UploadFile,HTTPException
from fastapi.responses import Response
from .domain import EXPORT_FIELDS,safe_cell,money
from .cloud import secret_cipher,settings


def fail(message,status=400):raise HTTPException(status,message)
def encode(v):return json.dumps(v,ensure_ascii=False)
def now():return datetime.now(timezone.utc).isoformat()


def profile_options(payload):
    format=payload.get('format','csv')
    if format not in ('csv','xlsx','fixed','json','api'):fail('פורמט פרופיל לא נתמך')
    filename=payload.get('filename','documents.csv' if format=='csv' else 'documents.txt')
    if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',filename) or filename.startswith('.'):fail('שם קובץ לא תקין')
    check_policy=payload.get('check_policy','join');check_mapping=payload.get('check_mapping','reference')
    if check_policy not in ('join','single','first') or check_mapping not in ('reference','notes','payment'):fail('מיפוי צ׳קים לא תקין')
    if format=='fixed' and any(not col.get('max_length') for col in payload['columns']):fail('טקסט באורך קבוע מחייב אורך לכל עמודה')
    companion=payload.get('companion',{})
    if not isinstance(companion,dict) or len(companion)>10:fail('קבצי פרמטרים לא תקינים')
    for name,value in companion.items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',name) or name.startswith('.') or not isinstance(value,str) or len(value)>50000:fail('קובץ פרמטרים לא תקין')
    return {'format':format,'filename':filename,'check_policy':check_policy,'check_mapping':check_mapping,'companion':companion,'version':uuid.uuid4().hex}


def profile_fingerprint(profile):
    clean={k:v for k,v in profile.items() if k not in ('version','verification')}
    return hashlib.sha256(json.dumps(clean,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def fixed_bytes(snapshot):
    profile=snapshot['profile'];rows=[]
    for item in snapshot['items']:
        result=[]
        for col in profile['columns']:
            value=str(item['row'].get(col['field'],''))
            if col['field'] in ('date','due_date') and value:value=datetime.strptime(value,'%Y-%m-%d').strftime(profile['date_format'])
            length=col.get('max_length',0)
            if length<1 or len(value)>length:fail('אורך שדה אינו מתאים לפרופיל הקבוע')
            # Byte lengths, rather than Unicode codepoint lengths, define fixed text files.
            try:encoded=value.encode(profile['encoding'].replace('-sig',''))
            except UnicodeEncodeError:fail('תו אינו נתמך בקידוד הפרופיל')
            if len(encoded)>length:fail('שדה חורג מאורך הבתים בפרופיל הקבוע')
            result.append(encoded.ljust(length,b' '))
        rows.append(b''.join(result))
    return b'\r\n'.join(rows)+b'\r\n'


def archive_bytes(snapshot,csv_function):
    output=io.BytesIO();profile=snapshot['profile']
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        primary=fixed_bytes(snapshot) if profile.get('format')=='fixed' else encode([i['row'] for i in snapshot['items']]).encode() if profile.get('format') in ('json','api') else csv_function(snapshot)
        z.writestr(profile.get('filename','documents.csv'),primary)
        for name,value in profile.get('companion',{}).items():z.writestr(name,value.encode(profile['encoding']))
        def table(name,columns,rows):
            out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(columns);writer.writerows([[safe_cell(v) for v in row] for row in rows]);z.writestr(name,out.getvalue().encode('utf-8-sig'))
        table('checks.csv',['document_id','number','source_document_id','region'],[[i['document_id'],ch['number'],ch.get('source_document_id',i['document_id']),encode(ch.get('region'))] for i in snapshot['items'] for ch in i['checks']])
        table('lines.csv',['document_id','description','quantity','price','discount','total','item_code'],[[i['document_id']]+[line.get(k,'') for k in ('description','quantity','price','discount','total','item_code')] for i in snapshot['items'] for line in i.get('items',[])])
        table('journal.csv',['document_id','account','debit','credit','reference','reference2'],[[i['document_id']]+[r.get(k,'') for k in ('account','debit','credit','reference','reference2')] for i in snapshot['items'] for r in i.get('journal',[])])
        table('exceptions.csv',['note'],[['Target import is unverified unless the company profile has recorded acceptance evidence.']])
        z.writestr('manifest.json',encode({'profile':profile,'documents':len(snapshot['items']),'totals_by_currency':{currency:str(sum((money(i['row']['total']) for i in snapshot['items'] if i['row']['currency']==currency),Decimal(0))) for currency in {i['row']['currency'] for i in snapshot['items']}},'items':snapshot['items']}))
    return output.getvalue()


def journal(row,payment=None):
    entries=[]
    if row.get('purpose')=='journal':
        def entry(account,amount):
            return {'account':account,'debit':str(max(amount,Decimal(0))),'credit':str(max(-amount,Decimal(0))),'reference':row.get('checks',''),'reference2':row['number']}
        entries=[entry(row['expense_account'],money(row['net'])),entry(row['supplier_code'],-money(row['total']))]
        if money(row['vat']):entries.append(entry(row.get('vat_account',''),money(row['vat'])))
    if payment:
        amount=money(payment['amount'])
        entries += [{'account':row['supplier_code'],'debit':str(amount),'credit':'0.00','reference':row.get('checks',''),'reference2':row['number']},{'account':payment['bank_account'],'debit':'0.00','credit':str(amount),'reference':row.get('checks',''),'reference2':row['number']}]
    if sum((money(r['debit'])-money(r['credit']) for r in entries),Decimal(0))!=0:fail('פקודת היומן אינה מאוזנת')
    return entries


def register(app,s):
    from .advanced import meta
    db=s['db'];company=s['company'];document=s['document'];audit=s['audit'];data=s['data']

    @app.post('/api/companies/{company_id}/profile/template')
    def template(company_id:str,file:UploadFile=File(...)):
        result=s['preview_suppliers'](company_id,file)
        sheets=result['sheets'];headers=next(iter(sheets.values()),[[]])[0]
        return {'headers':headers,'sheets':sheets,'suggested_columns':[{'title':h,'field':h if h in EXPORT_FIELDS else '', 'max_length':0} for h in headers],'verified':False}

    @app.post('/api/companies/{company_id}/profile/clone')
    def clone(company_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('העתקת הגדרות בין חברות דורשת בחירה מפורשת')
        with db() as c:
            company(c,company_id);source=s['profile'](c,payload.get('source_company_id',''));source={**source,'version':uuid.uuid4().hex}
            c.execute('INSERT INTO profiles VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET settings=excluded.settings',(company_id,encode(source)))
            c.execute('DELETE FROM verification WHERE company_id=?',(company_id,));audit(c,company_id,None,'profile_cloned',{'source_company_id':payload['source_company_id']})
        return source

    @app.get('/api/companies/{company_id}/integration')
    def get_integration(company_id:str):
        with db() as c:
            company(c,company_id);value=settings(c,company_id,'integration_settings')
            verification=c.execute('SELECT value FROM verification WHERE company_id=?',(company_id,)).fetchone()
            return {**{k:v for k,v in value.items() if k!='encrypted_key'},'has_key':bool(value.get('encrypted_key')),'verification':json.loads(verification[0]) if verification else None}

    @app.put('/api/companies/{company_id}/integration')
    def put_integration(company_id:str,payload:dict=Body(...)):
        mode=payload.get('mode','off');endpoint=payload.get('endpoint','')
        if mode not in ('off','manual') or not isinstance(endpoint,str) or len(endpoint)>2000:fail('הגדרת חיבור לא תקינה')
        if mode!='off':
            url=urlsplit(endpoint)
            if url.scheme!='https' or not url.hostname or url.username or url.password:fail('יש להגדיר כתובת HTTPS מלאה ללא סיסמה בכתובת')
        mapping=payload.get('mapping',{})
        if not isinstance(mapping,dict) or any(v not in EXPORT_FIELDS for v in mapping.values()) or len(mapping)>100:fail('מיפוי שדות API לא תקין')
        method=payload.get('method','POST')
        if method not in ('POST','PUT'):fail('שיטת API לא נתמכת')
        success=payload.get('success',{})
        if not isinstance(success,dict) or not isinstance(success.get('path',''),str):fail('מיפוי תוצאת API לא תקין')
        with db() as c:
            company(c,company_id);old=settings(c,company_id,'integration_settings')
            value={'mode':mode,'endpoint':endpoint,'method':method,'mapping':mapping,'success':success,'reference_path':str(payload.get('reference_path',''))[:200],'encrypted_key':old.get('encrypted_key',''),'auth_header':str(payload.get('auth_header','Authorization'))[:100],'auth_prefix':str(payload.get('auth_prefix','Bearer '))[:100]}
            value.update(read_endpoint=str(payload.get('read_endpoint',''))[:2000],read_kind=str(payload.get('read_kind','suppliers')),read_items_path=str(payload.get('read_items_path',''))[:200],read_mapping=payload.get('read_mapping',{}))
            if value['read_endpoint'] and urlsplit(value['read_endpoint']).scheme!='https':fail('כתובת קריאת רשימות חייבת להיות HTTPS')
            if not isinstance(value['read_mapping'],dict) or len(value['read_mapping'])>50 or any(not isinstance(v,str) or len(v)>200 for v in value['read_mapping'].values()):fail('מיפוי קריאת רשימות לא תקין')
            if payload.get('clear_key'):value['encrypted_key']=''
            if payload.get('api_key'):value['encrypted_key']=secret_cipher(data).encrypt(str(payload['api_key']).encode()).decode()
            c.execute('INSERT INTO integration_settings VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET value=excluded.value',(company_id,encode(value)))
            c.execute('DELETE FROM verification WHERE company_id=?',(company_id,));audit(c,company_id,None,'integration_settings_saved',{'mode':mode})
        return {'ok':True}

    @app.post('/api/companies/{company_id}/integration/read')
    def read_index(company_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('קריאת נתונים מהמערכת דורשת פעולה מפורשת')
        with db() as c:
            company(c,company_id);config=settings(c,company_id,'integration_settings')
            endpoint=config.get('read_endpoint')
            if config.get('mode')!='manual' or not endpoint:fail('לא הוגדר חיבור לקריאת רשימות')
            key=secret_cipher(data).decrypt(config['encrypted_key'].encode()).decode() if config.get('encrypted_key') else ''
        headers={}
        if key:headers[config['auth_header']]=config['auth_prefix']+key
        try:
            with httpx.Client(timeout=60,follow_redirects=False) as client:
                response=client.get(endpoint,headers=headers);response.raise_for_status();value=response.json()
        except (httpx.HTTPError,ValueError):fail('קריאת הרשימות לא הושלמה',502)
        def at(value,path):
            if not path:return value
            for part in path.split('.'):
                if not isinstance(value,dict) or part not in value:return None
                value=value[part]
            return value
        rows=at(value,config.get('read_items_path',''))
        if not isinstance(rows,list) or len(rows)>1000:fail('יש להגדיר מסלול לרשימה ועד 1,000 רשומות בכל קריאה; השתמשו בעמודים או ביצוא קובץ')
        output=[]
        for row in rows:
            if not isinstance(row,dict):fail('רשומה חיצונית לא תקינה')
            item={field:str(at(row,path) if at(row,path) is not None else '')[:2000] for field,path in config['read_mapping'].items()}
            if not item.get('code') or not item.get('name'):fail('מיפוי החיבור חייב להחזיר קוד ושם לכל רשומה')
            output.append(item)
        with db() as c:audit(c,company_id,None,'index_api_preview',{'kind':config['read_kind'],'count':len(output)})
        return {'kind':config['read_kind'],'rows':output,'saved':False,'requires_confirmation':True}

    @app.post('/api/batches/{batch_id}/send')
    def send_batch(batch_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('שליחה לתוכנת הנהלת חשבונות דורשת אישור מפורש')
        with db() as c:
            batch=s['batch_details'](c,batch_id);config=settings(c,batch['company_id'],'integration_settings')
            if config.get('mode')!='manual':fail('חיבור API אינו מופעל')
            if c.execute('SELECT 1 FROM integration_attempts WHERE batch_id=?',(batch_id,)).fetchone():fail('המנה כבר נשלחה או שנעשה ניסיון שתוצאתו לא ברורה. בדקו את התוצאה לפני ניסיון נוסף.',409)
            if any(i['status']!='exported' for i in batch['items']):fail('הקליטה כבר תועדה לחלק מהמנה',409)
            attempt=uuid.uuid4().hex;c.execute('INSERT INTO integration_attempts VALUES(?,?,?,?,?)',(attempt,batch_id,'pending','{}',now()))
            key=secret_cipher(data).decrypt(config['encrypted_key'].encode()).decode() if config.get('encrypted_key') else ''
        rows=[{'document_id':i['document_id'],**{target:i['row'].get(source,'') for target,source in config['mapping'].items()},'items':i.get('items',[]),'journal':i.get('journal',[])} for i in batch['snapshot']['items']]
        headers={'Idempotency-Key':batch_id}
        if key:headers[config['auth_header']]=config['auth_prefix']+key
        result=None;status='unknown'
        try:
            with httpx.Client(timeout=60,follow_redirects=False) as client:
                response=client.request(config['method'],config['endpoint'],headers=headers,json={'batch_id':batch_id,'documents':rows})
                response.raise_for_status();result=response.json();status='responded'
        except (httpx.HTTPError,ValueError):pass
        def at(value,path):
            for part in path.split('.'):
                if not isinstance(value,dict) or part not in value:return None
                value=value[part]
            return value
        accepted=bool(result is not None and config['success'].get('path') and at(result,config['success']['path'])==config['success'].get('value') and config.get('reference_path') and at(result,config['reference_path']))
        with db() as c:
            c.execute('UPDATE integration_attempts SET status=?,result=? WHERE id=?',('accepted' if accepted else status,encode({'reference':str(at(result,config.get('reference_path','')))[:200] if result else '', 'http_response_received':result is not None}),attempt))
            if accepted:
                reason='API: '+str(at(result,config['reference_path']))[:500]
                for item in batch['items']:
                    c.execute("UPDATE batch_items SET status='received',reason=? WHERE batch_id=? AND document_id=?",(reason,batch_id,item['document_id']))
                    c.execute("UPDATE documents SET status='received',revision=revision+1 WHERE id=?",(item['document_id'],));audit(c,batch['company_id'],item['document_id'],'receipt_api',{'batch_id':batch_id,'reference':reason})
            audit(c,batch['company_id'],None,'api_attempt',{'batch_id':batch_id,'status':'accepted' if accepted else status})
        return {'status':'accepted' if accepted else status,'receipt_verified':accepted,'message':'התקבלה אסמכתא לפי מיפוי התשובה שהוגדר' if accepted else 'התגובה אינה מאמתת קליטה. יש לבדוק בתוכנת היעד או לייבא דוח תוצאות. לא תתבצע שליחה חוזרת אוטומטית.'}

    @app.post('/api/batches/{batch_id}/receipt-report')
    def receipt_report(batch_id:str,payload:dict=Body(...)):
        rows=payload.get('rows')
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:fail('דוח תוצאות לא תקין')
        with db() as c:
            batch=s['batch_details'](c,batch_id);valid=[];seen=set()
            for row in rows:
                if not isinstance(row,dict) or row.get('status') not in ('received','rejected') or not isinstance(row.get('reason'),str) or not row['reason'].strip():fail('כל תוצאה דורשת מזהה מסמך, מצב וסיבת תוצאה')
                ident=row.get('document_id')
                if ident in seen:fail('תוצאה כפולה למסמך')
                seen.add(ident)
                item=next((i for i in batch['items'] if i['document_id']==ident),None)
                if not item or item['status']!='exported' or document(c,ident)['status']!='exported':fail('מסמך לא שייך למנה פעילה או שכבר נקלט',409)
                valid.append(row)
            if payload.get('confirmed') is not True:return {'preview':valid,'count':len(valid)}
            for row in valid:
                c.execute('UPDATE batch_items SET status=?,reason=? WHERE batch_id=? AND document_id=?',(row['status'],row['reason'][:2000],batch_id,row['document_id']))
                c.execute('UPDATE documents SET status=?,revision=revision+1 WHERE id=?',(row['status'],row['document_id']));audit(c,batch['company_id'],row['document_id'],'receipt_report',{'batch_id':batch_id,'status':row['status'],'reason':row['reason'][:2000]})
        return {'count':len(valid)}

    @app.post('/api/companies/{company_id}/verify-profile')
    def verify_profile(company_id:str,payload:dict=Body(...)):
        required={'supplier_code','number','checks','hebrew','net','vat','total'}
        if not isinstance(payload.get('field_checks'),dict) or any(payload['field_checks'].get(k) is not True for k in required) or not isinstance(payload.get('target_version'),str) or not payload['target_version'].strip() or not payload.get('evidence'):fail('האימות מחייב גרסת תוכנה, אסמכתא והצלבת כל שדות החובה')
        with db() as c:
            company(c,company_id);batch=s['batch_details'](c,payload.get('batch_id',''))
            if batch['company_id']!=company_id or any(i['status']!='received' for i in batch['items']):fail('יש לבחור מנה של החברה שכל מסמכיה נקלטו ונבדקו')
            profile=s['profile'](c,company_id)
            if profile_fingerprint(profile)!=profile_fingerprint(batch['snapshot']['profile']):fail('הפרופיל השתנה מאז יצירת המנה. נדרשת בדיקת קליטה חדשה')
            value={'target':company(c,company_id)['target'],'target_version':payload['target_version'][:200],'batch_id':batch['id'],'profile_fingerprint':profile_fingerprint(profile),'evidence':str(payload['evidence'])[:2000],'field_checks':payload['field_checks'],'created':now(),'method':'manual_test_company'}
            c.execute('INSERT INTO verification VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET value=excluded.value',(company_id,encode(value)));audit(c,company_id,None,'profile_verified',value)
        return value
