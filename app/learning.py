"""Private per-installation learning, supplier decisions and local training jobs."""
import hashlib
import io
import json
import math
import re
import tempfile
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
from fastapi import Body,HTTPException,UploadFile,File
from fastapi.responses import Response
from PIL import Image,ImageOps

REASONS={'corrected_value':'תיקון הקריאה','extra_stroke':'קו או קישוט שנקרא כספרה','missing_digit':'ספרה חסרה','joined_digits':'ספרות מחוברות','leading_zero':'אפסים מובילים','wrong_region':'אזור קריאה','wrong_supplier':'שיוך ספק','layout':'תבנית מסמך','not_check':'אינו מספר צ׳ק','other':'הסבר אחר'}
RULE_FIELDS=('expense_account','vat_account','transaction_type','cost_center','project','purpose','currency')


def now():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex
def encode(value):return json.dumps(value,ensure_ascii=False)
def fail(message,status=400):raise HTTPException(status,message)


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS learning_installation(id TEXT PRIMARY KEY,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_settings(company_id TEXT PRIMARY KEY REFERENCES companies(id),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_writers(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),name TEXT NOT NULL,created TEXT NOT NULL,UNIQUE(company_id,name));
    CREATE TABLE IF NOT EXISTS learning_suppliers(supplier_id TEXT PRIMARY KEY REFERENCES suppliers(id),company_id TEXT NOT NULL REFERENCES companies(id),fingerprint TEXT NOT NULL,answer TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_events(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),document_id TEXT REFERENCES documents(id),revision INTEGER NOT NULL,field TEXT NOT NULL,before_value TEXT NOT NULL,after_value TEXT NOT NULL,context TEXT NOT NULL,approved INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_examples(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),document_id TEXT NOT NULL REFERENCES documents(id),source_document_id TEXT NOT NULL REFERENCES documents(id),source_group TEXT NOT NULL,crop_sha TEXT NOT NULL,image TEXT NOT NULL,label TEXT NOT NULL,writer_id TEXT REFERENCES learning_writers(id),reason TEXT NOT NULL,explanation TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'auto',approved INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,UNIQUE(company_id,crop_sha));
    CREATE TABLE IF NOT EXISTS learning_templates(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),supplier_id TEXT NOT NULL REFERENCES suppliers(id),kind TEXT NOT NULL,field TEXT NOT NULL,region TEXT NOT NULL,source_document_id TEXT NOT NULL REFERENCES documents(id),created TEXT NOT NULL,UNIQUE(company_id,supplier_id,kind,field));
    CREATE TABLE IF NOT EXISTS learning_aliases(company_id TEXT NOT NULL REFERENCES companies(id),alias TEXT NOT NULL,supplier_id TEXT NOT NULL REFERENCES suppliers(id),PRIMARY KEY(company_id,alias));
    CREATE TABLE IF NOT EXISTS learning_alias_evidence(company_id TEXT NOT NULL REFERENCES companies(id),alias TEXT NOT NULL,supplier_id TEXT NOT NULL REFERENCES suppliers(id),PRIMARY KEY(company_id,alias,supplier_id));
    CREATE TABLE IF NOT EXISTS learning_checks(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),number TEXT NOT NULL,supplier_id TEXT REFERENCES suppliers(id),value TEXT NOT NULL,UNIQUE(company_id,number));
    CREATE TABLE IF NOT EXISTS learning_jobs(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),state TEXT NOT NULL,progress INTEGER NOT NULL,value TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_models(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),writer_id TEXT REFERENCES learning_writers(id),path TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 0,report TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_engine_observations(example_id TEXT NOT NULL REFERENCES learning_examples(id),engine TEXT NOT NULL,number TEXT NOT NULL,PRIMARY KEY(example_id,engine));
    CREATE TABLE IF NOT EXISTS learning_shared_examples(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),source_group TEXT NOT NULL,invoice_key TEXT NOT NULL,crop_sha TEXT NOT NULL,image TEXT NOT NULL,label TEXT NOT NULL,writer_id TEXT REFERENCES learning_writers(id),reason TEXT NOT NULL,explanation TEXT NOT NULL,role TEXT NOT NULL,approved INTEGER NOT NULL,origin TEXT NOT NULL,created TEXT NOT NULL,UNIQUE(company_id,crop_sha));
    CREATE TABLE IF NOT EXISTS learning_shared_templates(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),supplier_id TEXT NOT NULL REFERENCES suppliers(id),kind TEXT NOT NULL,field TEXT NOT NULL,region TEXT NOT NULL,origin TEXT NOT NULL,UNIQUE(company_id,supplier_id,kind,field));
    CREATE TABLE IF NOT EXISTS learning_supplier_proposals(supplier_id TEXT PRIMARY KEY REFERENCES suppliers(id),company_id TEXT NOT NULL REFERENCES companies(id),value TEXT NOT NULL,origin TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS learning_writer_origins(company_id TEXT NOT NULL REFERENCES companies(id),origin_installation TEXT NOT NULL,origin_id TEXT NOT NULL,writer_id TEXT NOT NULL REFERENCES learning_writers(id),PRIMARY KEY(company_id,origin_installation,origin_id));
    ''')
    if not c.execute('SELECT 1 FROM learning_installation').fetchone():c.execute('INSERT INTO learning_installation VALUES(?,?)',(uid(),now()))
    c.execute("UPDATE learning_jobs SET state='interrupted' WHERE state IN ('queued','running')")


def settings(c,company_id):
    row=c.execute('SELECT value FROM learning_settings WHERE company_id=?',(company_id,)).fetchone()
    return {'auto_train':True,'multi_pass':True,'minimum_new_examples':8,**(json.loads(row[0]) if row else {})}


def supplier_signature(row,c):
    value={key:row[key] for key in ('id','company_id','code','name','tax_id','active','expense_account')}
    extra=c.execute('SELECT value FROM supplier_meta WHERE supplier_id=?',(row['id'],)).fetchone()
    value['metadata']=json.loads(extra[0]) if extra else {}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def supplier_review(c,supplier_id,company_id):
    supplier=c.execute('SELECT * FROM suppliers WHERE id=? AND company_id=?',(supplier_id,company_id)).fetchone()
    if not supplier:return None
    row=c.execute('SELECT * FROM learning_suppliers WHERE supplier_id=? AND company_id=?',(supplier_id,company_id)).fetchone()
    return json.loads(row['answer']) if row and row['fingerprint']==supplier_signature(supplier,c) else None


def approval_issues(c,d):
    if d.get('supplier_id') and not supplier_review(c,d['supplier_id'],d['company_id']):return ['יש להשלים את שאלת האישור לכרטיס הספק לפני אישור מסמכים שלו']
    return []


def defaults(c,company_id,supplier_id):
    answer=supplier_review(c,supplier_id,company_id)
    return {key:value for key,value in answer.get('rules',{}).items() if key in RULE_FIELDS and value} if answer else {}


def match_alias(c,company_id,name):
    if c.execute('SELECT COUNT(DISTINCT supplier_id) FROM learning_alias_evidence WHERE company_id=? AND alias=?',(company_id,name.strip().casefold())).fetchone()[0]>1:return None
    row=c.execute('SELECT a.supplier_id FROM learning_aliases a JOIN suppliers s ON s.id=a.supplier_id WHERE a.company_id=? AND a.alias=? AND s.active=1',(company_id,name.strip().casefold())).fetchone()
    return row[0] if row and supplier_review(c,row[0],company_id) else None


def check_writer(c,company_id,value):
    if value in (None,'','unknown'):return None
    if not isinstance(value,str) or not c.execute('SELECT 1 FROM learning_writers WHERE id=? AND company_id=?',(value,company_id)).fetchone():fail('סגנון הכתב אינו שייך לחברה')
    return value


def safe_path(data,relative):
    path=(data/relative).resolve()
    if not path.is_relative_to((data/'files/learning').resolve()):raise ValueError('נתיב למידה לא תקין')
    return path


def feedback(payload):
    value=payload.get('learning',{})
    if not isinstance(value,dict):fail('פרטי למידה לא תקינים')
    reason=value.get('reason','corrected_value')
    if reason not in REASONS:fail('סיבת תיקון לא נתמכת')
    explanation=value.get('explanation','')
    if not isinstance(explanation,str) or len(explanation)>2000:fail('הסבר התיקון מוגבל ל־2,000 תווים')
    regions=value.get('field_regions',{})
    if not isinstance(regions,dict) or len(regions)>40:fail('אזורי השדות לא תקינים')
    return {**value,'reason':reason,'explanation':explanation,'field_regions':regions}


def capture_example(c,d,check,data,crop,writer=None,reason='corrected_value',explanation=''):
    from .advanced import source_document
    if not check.get('confirmed') or not check.get('region') or not re.fullmatch('[0-9]{1,30}',check.get('number','')):return
    origin=source_document(c,d,check.get('source_document_id'))
    with tempfile.TemporaryDirectory() as temp:
        image=Path(temp)/'crop.png';crop(origin,check['region'],image);content=image.read_bytes()
    sha=hashlib.sha256(content).hexdigest();row=c.execute('SELECT * FROM learning_examples WHERE company_id=? AND crop_sha=?',(d['company_id'],sha)).fetchone()
    writer=check_writer(c,d['company_id'],check.get('writer_id') or writer)
    label=check['number'];ident=row['id'] if row else uid();relative=f"files/learning/{d['company_id']}/examples/{ident}.png";target=safe_path(data,relative);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(content)
    source=c.execute('SELECT sha FROM sources WHERE id=?',(origin['source_id'],)).fetchone()[0]
    shared=c.execute('SELECT label FROM learning_shared_examples WHERE company_id=? AND crop_sha=?',(d['company_id'],sha)).fetchone()
    if shared and shared['label']!=label:
        c.execute('UPDATE learning_shared_examples SET approved=0 WHERE company_id=? AND crop_sha=?',(d['company_id'],sha));c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(d['company_id'],))
    if row and (row['label']!=label or row['writer_id']!=writer or not row['approved']):
        c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(d['company_id'],))
    c.execute('INSERT INTO learning_examples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(company_id,crop_sha) DO UPDATE SET label=excluded.label,writer_id=excluded.writer_id,reason=excluded.reason,explanation=excluded.explanation,approved=1,created=excluded.created',(ident,d['company_id'],d['id'],origin['id'],source,sha,relative,label,writer,check.get('reason',reason),str(check.get('explanation',explanation))[:2000],'auto',1,now()))
    for proposal in check.get('proposals',[]):
        if not isinstance(proposal,dict) or not isinstance(proposal.get('number'),str):continue
        engine=str(proposal.get('engine',''))[:100]+'@'+str(proposal.get('variant','original'))[:30]
        c.execute('INSERT INTO learning_engine_observations VALUES(?,?,?) ON CONFLICT(example_id,engine) DO UPDATE SET number=excluded.number',(ident,engine,proposal['number'][:30]))


def record_edit(c,d,payload,fields,supplier_id,checks,data,crop,validate_region):
    value=feedback(payload);writer=check_writer(c,d['company_id'],value.get('writer_id'));regions=value['field_regions'];revision=d['revision']+1
    changes={key:(d['fields'].get(key,''),v) for key,v in fields.items() if d['fields'].get(key,'')!=v}
    if supplier_id!=d['supplier_id']:changes['supplier_id']=(d['supplier_id'] or '',supplier_id or '')
    for key,marked in regions.items():
        if key not in fields or not isinstance(marked,dict):fail('אזור שדה לא תקין')
        validate_region(marked.get('region'))
        from .advanced import source_document
        source_document(c,d,marked.get('source_document_id'))
        changes.setdefault(key,(d['fields'].get(key,''),fields[key]))
    from .advanced import meta
    sources=meta(c,d['id']).get('field_sources',{})
    for key,(before,after) in changes.items():
        context={'reason':value['reason'],'explanation':value['explanation'],'writer_id':writer,'supplier_id':supplier_id,'kind':fields.get('kind'),'region':regions.get(key),'engine':meta(c,d['id']).get('field_engines',{}).get(key,d['engine']),'source_region':sources.get(key)}
        c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),d['company_id'],d['id'],revision,key,before,after,encode(context),0,now()))
    # Keep the final teaching context for approval; unsaved changes never become training labels.
    from .advanced import save_meta
    save_meta(c,d['id'],{'learning_context':{'writer_id':writer,'reason':value['reason'],'explanation':value['explanation'],'field_regions':regions}})
    def identity(check):return (check.get('source_document_id') or d['id'],tuple(check.get('region',{}).get(k) for k in ('x','y','w','h','rotation')) if check.get('region') else None,check.get('number'))
    retained={identity(check) for check in checks if check.get('confirmed')}
    for old in d['checks']:
        if not old.get('confirmed') or not old.get('region') or identity(old) in retained:continue
        from .advanced import source_document
        source=source_document(c,d,old.get('source_document_id'))
        with tempfile.TemporaryDirectory() as temp:
            image=Path(temp)/'previous.png';crop(source,old['region'],image);sha=hashlib.sha256(image.read_bytes()).hexdigest()
        c.execute('UPDATE learning_examples SET approved=0 WHERE company_id=? AND crop_sha=?',(d['company_id'],sha))
        c.execute('DELETE FROM check_corrections WHERE company_id=? AND crop_sha=?',(d['company_id'],sha))
        c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(d['company_id'],))
    for index,check in enumerate(checks):
        old=d['checks'][index] if index<len(d['checks']) else {}
        if check.get('confirmed') and (check.get('number')!=old.get('number') or not old.get('confirmed')):
            context={'reason':check.get('reason',value['reason']),'explanation':check.get('explanation',value['explanation']),'writer_id':check.get('writer_id') or writer,'proposals':check.get('proposals',[]),'region':check.get('region'),'source_document_id':check.get('source_document_id')}
            c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),d['company_id'],d['id'],revision,'check',old.get('number',''),check['number'],encode(context),1,now()))
        capture_example(c,d,check,data,crop,writer,value['reason'],value['explanation'])
    if len(checks)<len(d['checks']):
        c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),d['company_id'],d['id'],revision,'checks_removed',encode(d['checks']),encode(checks),encode({'reason':value['reason'],'explanation':value['explanation']}),1,now()))


def on_approve(c,d):
    from .advanced import meta
    # Only the most recent answer per field in this document can teach a layout or alias.
    c.execute("UPDATE learning_events SET approved=1 WHERE document_id=? AND field<>'training_label'",(d['id'],))
    context=meta(c,d['id']).get('learning_context',{})
    if d.get('supplier_id'):
        import difflib
        official=c.execute('SELECT name FROM suppliers WHERE id=?',(d['supplier_id'],)).fetchone()[0]
        for index,name in enumerate((d['original_fields'].get('supplier_name',''),d['fields'].get('supplier_name',''))):
            if not name.strip() or (index==0 and difflib.SequenceMatcher(None,name.casefold(),official.casefold()).ratio()<.65):continue
            alias=name.strip().casefold()
            c.execute('INSERT OR IGNORE INTO learning_alias_evidence VALUES(?,?,?)',(d['company_id'],alias,d['supplier_id']))
            c.execute('INSERT INTO learning_aliases VALUES(?,?,?) ON CONFLICT(company_id,alias) DO UPDATE SET supplier_id=excluded.supplier_id',(d['company_id'],alias,d['supplier_id']))
        for field,value in context.get('field_regions',{}).items():
            ids=[d['id']]+[r[0] for r in c.execute('SELECT child_id FROM document_links WHERE primary_id=?',(d['id'],))]
            origin=value.get('source_document_id') or d['id'];region={**value['region'],'page_offset':ids.index(origin) if origin in ids else 0}
            c.execute('INSERT INTO learning_templates VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(company_id,supplier_id,kind,field) DO UPDATE SET region=excluded.region,source_document_id=excluded.source_document_id,created=excluded.created',(uid(),d['company_id'],d['supplier_id'],d['fields']['kind'],field,encode(region),origin,now()))


def evaluation_qualifies(result):
    return result['tested']>=2 and result['personal_correct']==result['tested'] and not result['regressions']


def backup_paths(c):
    return {row[0] for row in c.execute('SELECT image FROM learning_examples')}|{row[0] for row in c.execute('SELECT image FROM learning_shared_examples')}|{row[0] for row in c.execute('SELECT path FROM learning_models')}


def examples(c,company_id):
    return [{**dict(r),'imported':False} for r in c.execute('SELECT * FROM learning_examples WHERE company_id=?',(company_id,))]+[{**dict(r),'document_id':None,'source_document_id':None,'imported':True} for r in c.execute('SELECT * FROM learning_shared_examples WHERE company_id=?',(company_id,))]


def example_by_id(c,company_id,ident):
    for table in ('learning_examples','learning_shared_examples'):
        row=c.execute(f'SELECT * FROM {table} WHERE id=? AND company_id=?',(ident,company_id)).fetchone()
        if row:return table,dict(row)
    fail('דוגמת לימוד לא נמצאה',404)


def dataset_fingerprint(rows):
    return hashlib.sha256(encode(sorted((r['id'],r['crop_sha'],r['label'],r['writer_id'],r['role'],r['approved']) for r in rows)).encode()).hexdigest()


def split_samples(rows):
    """Group by original file AND invoice identity; no page/crop leakage."""
    groups={}
    parent={}
    def find(x):
        parent.setdefault(x,x)
        if parent[x]!=x:parent[x]=find(parent[x])
        return parent[x]
    for row in rows:
        a=find('source:'+row['source_group']);b=find('invoice:'+row['invoice_key'])
        parent[a]=b
    for row in rows:groups.setdefault(find('source:'+row['source_group']),[]).append(row)
    train=[];test=[];ordered=sorted(groups,key=lambda x:hashlib.sha256(x.encode()).hexdigest())
    auto_test=set(ordered[-max(1,len(ordered)//5):]) if len(ordered)>=3 else set()
    for key in ordered:
        items=groups[key];roles={r['role'] for r in items}
        if 'train' in roles and 'validation' in roles:raise ValueError('עמודים של אותו מסמך סומנו גם לאימון וגם לבדיקה; בחרו תפקיד אחיד למסמך')
        destination=test if 'validation' in roles or ('train' not in roles and key in auto_test) else train
        destination.extend(items)
    return train,test


class Manager:
    def __init__(self,s):
        self.s=s;self.lock=threading.Lock();self.busy=False;self.stop=threading.Event();self.cancel=threading.Event()

    def request(self,company_id,payload):
        writer=payload.get('writer_id') or None
        epochs=payload.get('epochs',60)
        if isinstance(epochs,bool) or not isinstance(epochs,int) or not 1<=epochs<=100:fail('מספר סבבי אימון: 1–100')
        with self.lock:
            if self.busy:fail('אימון מקומי כבר פועל; אפשר לעקוב במסך הלמידה',409)
            with self.s['db']() as c:
                self.s['company'](c,company_id);writer=check_writer(c,company_id,writer)
                count=len(self.rows(c,company_id,writer))
                if not count:fail('אשרו חיתוך צ׳ק אחד לפחות כדי להתחיל ללמוד')
                if count>2000:fail('יש לבחור עד 2,000 דוגמאות לאימון מקומי אחד')
                ident=uid();value={'writer_id':writer,'epochs':epochs,'auto_activate':payload.get('auto_activate',True) is True}
                c.execute('INSERT INTO learning_jobs VALUES(?,?,?,?,?,?)',(ident,company_id,'queued',0,encode(value),now()))
            self.busy=True;self.cancel.clear()
            threading.Thread(target=self.run,args=(ident,company_id,value),daemon=True,name='personal-ocr-training').start()
        return {'id':ident,'state':'queued'}

    def auto(self,company_id):
        if self.busy or self.stop.is_set():return
        with self.s['db']() as c:
            config=settings(c,company_id)
            if not config['auto_train']:return
            count=len(self.rows(c,company_id))
            latest=c.execute("SELECT value FROM learning_jobs WHERE company_id=? AND state='completed' ORDER BY created DESC LIMIT 1",(company_id,)).fetchone()
            previous=json.loads(latest[0]).get('example_count',0) if latest else 0
        if count-previous>=config['minimum_new_examples']:
            try:self.request(company_id,{'epochs':60,'auto_activate':True})
            except HTTPException:pass

    def run(self,job_id,company_id,config):
        from . import personal_digits as model
        try:
            with self.s['db']() as c:
                c.execute("UPDATE learning_jobs SET state='running' WHERE id=?",(job_id,))
                all_rows=self.rows(c,company_id);all_train,all_test=split_samples(all_rows)
                # Persist first assignments so adding files does not rotate the held-out set.
                for role,partition in (('train',all_train),('validation',all_test)):
                    for row in partition:
                        if row['role']=='auto':
                            row['role']=role;table='learning_shared_examples' if row.get('imported') else 'learning_examples';c.execute(f'UPDATE {table} SET role=? WHERE id=?',(role,row['id']))
                train_rows=[r for r in all_train if config['writer_id'] is None or r['writer_id']==config['writer_id']]
                test_rows=[r for r in all_test if config['writer_id'] is None or r['writer_id']==config['writer_id']]
                rows=train_rows+test_rows;fingerprint=dataset_fingerprint(rows)
            if not train_rows:raise ValueError('בחרו לפחות מסמך אחד לאימון לצד מסמכי הבדיקה')
            def convert(row):
                if self.stop.is_set() or self.cancel.is_set():raise InterruptedError('האימון נעצר')
                x,prior=model.features(safe_path(self.s['data'],row['image']));return {'id':row['id'],'writer':row['writer_id'] or 'unknown','text':row['label'],'x':x,'prior':prior}
            samples=[]
            for index,row in enumerate(train_rows):
                if self.stop.is_set() or self.cancel.is_set():raise InterruptedError('האימון נעצר')
                samples.append(convert(row));self.progress(job_id,round((index+1)*20/len(train_rows)),{'phase':'preparing','example_count':len(rows),**config})
            original_samples=list(samples)
            for index,row in enumerate(train_rows):
                if self.stop.is_set() or self.cancel.is_set():raise InterruptedError('האימון נעצר')
                x,prior=model.augmented_features(safe_path(self.s['data'],row['image']),row['reason'],seed=index+2718)
                samples.append({'id':row['id'],'writer':row['writer_id'] or 'unknown','text':row['label'],'x':x,'prior':prior})
            held=[convert(row) for row in test_rows]
            fitted,fit=model.train(samples,held,config['epochs'],progress=lambda epoch,total,loss:self.progress(job_id,20+int(epoch/total*70),{'phase':'training','epoch':epoch,'loss':round(loss,4),'example_count':len(rows),**config}),cancelled=lambda:self.stop.is_set() or self.cancel.is_set())
            evaluation=model.evaluate(fitted,held);training=model.evaluate(fitted,original_samples)
            ident=uid();relative=f'files/learning/{company_id}/models/{ident}.npz';path=safe_path(self.s['data'],relative);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.tmp');model.save(temporary,fitted);temporary.replace(path)
            report={'base_model':model.BASE,'base_sha256':model.base_digest(),'dataset_fingerprint':fingerprint,'example_count':len(rows),'train_ids':[r['id'] for r in train_rows],'training_groups':sorted({r['source_group'] for r in train_rows}),'training_crops':sorted({r['crop_sha'] for r in train_rows}),'training_invoice_keys':sorted({r['invoice_key'] for r in train_rows}),'training_label_hashes':{r['crop_sha']:hashlib.sha256(r['label'].encode()).hexdigest() for r in train_rows},'validation_ids':[r['id'] for r in test_rows],'writers':sorted({r['writer_id'] or 'unknown' for r in rows}),'validation':evaluation,'training':{'tested':training['tested'],'correct':training['personal_correct']},'fitting':fit,'bytes':path.stat().st_size,'activated':False,'scope':'this installation and company only','sample_labels':{r['id']:r['label'] for r in rows}}
            with self.s['db']() as c:
                fresh=self.rows(c,company_id,config['writer_id']);unchanged=dataset_fingerprint(fresh)==fingerprint
                previous=c.execute('SELECT path FROM learning_models WHERE company_id=? AND active=1 AND writer_id IS ?',(company_id,config['writer_id'])).fetchone()
                incumbent=model.evaluate(model.load(safe_path(self.s['data'],previous[0])),held) if previous else None
                qualified=unchanged and evaluation_qualifies(evaluation) and evaluation['personal_correct']>=evaluation['baseline_correct'] and (not incumbent or evaluation['personal_correct']>=incumbent['personal_correct'])
                report.update(qualified=bool(qualified),dataset_unchanged=unchanged)
                if config['auto_activate'] and qualified:
                    c.execute('UPDATE learning_models SET active=0 WHERE company_id=? AND writer_id IS ?',(company_id,config['writer_id']));report['activated']=True
                c.execute('INSERT INTO learning_models VALUES(?,?,?,?,?,?,?)',(ident,company_id,config['writer_id'],relative,int(report['activated']),encode(report),now()))
                self.s['audit'](c,company_id,None,'personal_model_trained',{'model_id':ident,'samples':len(rows),'validation_tested':evaluation['tested'],'active':report['activated']})
                c.execute("UPDATE learning_jobs SET state='completed',progress=100,value=? WHERE id=?",(encode({**config,'model_id':ident,'example_count':len(rows),'phase':'completed'}),job_id))
        except Exception as exc:
            with self.s['db']() as c:c.execute("UPDATE learning_jobs SET state=?,value=? WHERE id=?",('cancelled' if isinstance(exc,InterruptedError) else 'failed',encode({**config,'message':str(exc)[:500]}),job_id))
        finally:
            with self.lock:self.busy=False

    def progress(self,ident,progress,value):
        with self.s['db']() as c:c.execute('UPDATE learning_jobs SET progress=?,value=? WHERE id=?',(progress,encode(value),ident))

    def rows(self,c,company_id,writer_id=None):
        result=[];owner=c.execute('SELECT tax_id FROM companies WHERE id=?',(company_id,)).fetchone()[0]
        for r in c.execute("SELECT e.*,d.fields,d.supplier_id FROM learning_examples e JOIN documents d ON d.id=e.document_id WHERE e.company_id=? AND e.approved=1 AND e.role<>'excluded' AND (? IS NULL OR e.writer_id=?)",(company_id,writer_id,writer_id)):
            value=dict(r);fields=json.loads(r['fields']);supplier=c.execute('SELECT tax_id,code FROM suppliers WHERE id=?',(r['supplier_id'],)).fetchone() if r['supplier_id'] else None
            supplier_key=(supplier['tax_id'] or supplier['code']) if supplier else fields.get('supplier_tax_id','')
            value['invoice_key']=hashlib.sha256(encode((owner,supplier_key,fields.get('kind'),fields.get('number'))).encode()).hexdigest() if fields.get('number') else 'source:'+r['source_group'];result.append(value)
        seen={row['crop_sha'] for row in result}
        for row in c.execute("SELECT * FROM learning_shared_examples WHERE company_id=? AND approved=1 AND role<>'excluded' AND (? IS NULL OR writer_id=?)",(company_id,writer_id,writer_id)):
            if row['crop_sha'] not in seen:result.append({**dict(row),'imported':True});seen.add(row['crop_sha'])
        return result

    def proposals(self,c,company_id,path,writer_id=None):
        from . import personal_digits as model
        rows=c.execute('SELECT * FROM learning_models WHERE company_id=? AND active=1 AND (writer_id IS NULL OR writer_id=?) ORDER BY writer_id DESC',(company_id,writer_id)).fetchall()
        if not rows:return []
        try:x,prior=model.features(path)
        except ValueError:return []
        output=[]
        for row in rows:
            if json.loads(row['report']).get('base_sha256')!=model.base_digest():continue
            try:number=model.decode(model.predict(model.load(safe_path(self.s['data'],row['path'])),x,prior))
            except (OSError,ValueError):continue
            output.append({'engine':'personal-ctc','model_id':row['id'],'writer_id':row['writer_id'],'number':number,'available':True,'confirmed':False,'reason':'מודל אישי שאומן במחשב זה על תיקונים מאושרים'})
        return output


def multi_proposals(path):
    from .sequence_digits import recognize
    output=[]
    with Image.open(path) as image:
        rgb=image.convert('RGB');contrast=ImageOps.autocontrast(rgb)
        variants=[('original',rgb),('contrast',contrast),('margin',ImageOps.expand(contrast,border=max(4,rgb.height//10),fill='white'))]
        gray=np.array(ImageOps.grayscale(rgb));ys,xs=np.where(gray<200)
        if len(xs)>20:
            cov=np.cov(xs,ys);_,vectors=np.linalg.eigh(cov);axis=vectors[:,-1];angle=math.degrees(math.atan2(axis[1],axis[0]));angle=(angle+90)%180-90
            if .4<abs(angle)<10:variants.append(('deskew',rgb.rotate(angle,expand=True,fillcolor='white')))
        with tempfile.TemporaryDirectory() as temp:
            for name,image in variants:
                file=Path(temp)/f'{name}.png';image.save(file)
                for engine in ('paddle-v6-small','paddle-v5'):
                    result=recognize(file,engine);result['variant']=name;output.append(result)
    return output


def register(app,s):
    manager=Manager(s);app.state.learning=manager
    app.router.add_event_handler('shutdown',manager.stop.set)
    db=s['db'];company=s['company'];document=s['document'];data=s['data']
    # Upgrade previously confirmed local crops into the personal dataset once.
    with db() as c:
        previous=c.execute('SELECT r.* FROM check_corrections r LEFT JOIN learning_examples e ON e.company_id=r.company_id AND e.crop_sha=r.crop_sha WHERE e.id IS NULL').fetchall()
        for row in previous:
            try:
                d=document(c,row['document_id'],row['company_id'])
                capture_example(c,d,{'number':row['number'],'confirmed':True,'region':json.loads(row['region']),'source_document_id':row['source_document_id'],'proposals':json.loads(row['predictions'])},data,s['crop'])
            except (HTTPException,ValueError,OSError):continue

    @app.get('/api/companies/{company_id}/learning')
    def overview(company_id:str):
        with db() as c:
            company(c,company_id);installation=c.execute('SELECT id FROM learning_installation').fetchone()[0]
            models=[{**dict(r),'report':json.loads(r['report'])} for r in c.execute('SELECT * FROM learning_models WHERE company_id=? ORDER BY created DESC LIMIT 50',(company_id,))]
            jobs=[{**dict(r),'value':json.loads(r['value'])} for r in c.execute('SELECT * FROM learning_jobs WHERE company_id=? ORDER BY created DESC LIMIT 10',(company_id,))]
            writers=[dict(r) for r in c.execute('SELECT * FROM learning_writers WHERE company_id=? ORDER BY name',(company_id,))]
            sample_rows=[{**r,'image_url':f"/api/companies/{company_id}/learning/examples/{r['id']}/image"} for r in sorted(examples(c,company_id),key=lambda e:e['created'],reverse=True)[:2000]]
            suppliers=[]
            for row in c.execute('SELECT * FROM suppliers WHERE company_id=? AND active=1 ORDER BY name',(company_id,)):
                answer=supplier_review(c,row['id'],company_id);proposed=c.execute('SELECT value FROM learning_supplier_proposals WHERE supplier_id=?',(row['id'],)).fetchone();suppliers.append({**dict(row),'fingerprint':supplier_signature(row,c),'reviewed':answer is not None,'answer':answer,'proposed_answer':json.loads(proposed[0]) if proposed else None})
            return {'installation_id':installation,'settings':settings(c,company_id),'writers':writers,'examples':sample_rows,'models':models,'jobs':jobs,'suppliers':suppliers,'reasons':REASONS,'event_count':c.execute('SELECT COUNT(*) FROM learning_events WHERE company_id=?',(company_id,)).fetchone()[0],'template_count':sum(c.execute(f'SELECT COUNT(*) FROM {table} WHERE company_id=?',(company_id,)).fetchone()[0] for table in ('learning_templates','learning_shared_templates')),'check_register_count':c.execute('SELECT COUNT(*) FROM learning_checks WHERE company_id=?',(company_id,)).fetchone()[0],'local_only':True}

    @app.put('/api/companies/{company_id}/learning/settings')
    def save_settings(company_id:str,payload:dict=Body(...)):
        with db() as c:
            company(c,company_id);value=settings(c,company_id)
            for key in ('auto_train','multi_pass'):
                if key in payload:
                    if not isinstance(payload[key],bool):fail('הגדרת למידה לא תקינה')
                    value[key]=payload[key]
            c.execute('INSERT INTO learning_settings VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET value=excluded.value',(company_id,encode(value)))
            return value

    @app.post('/api/companies/{company_id}/learning/writers')
    def add_writer(company_id:str,payload:dict=Body(...)):
        name=payload.get('name','')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:fail('שם סגנון כתב: 1–100 תווים')
        with db() as c:
            company(c,company_id)
            if c.execute('SELECT 1 FROM learning_writers WHERE company_id=? AND name=?',(company_id,name.strip())).fetchone():fail('סגנון הכתב כבר קיים',409)
            ident=uid();c.execute('INSERT INTO learning_writers VALUES(?,?,?,?)',(ident,company_id,name.strip(),now()));return {'id':ident,'name':name.strip()}

    @app.post('/api/companies/{company_id}/learning/suppliers/{supplier_id}')
    def answer_supplier(company_id:str,supplier_id:str,payload:dict=Body(...)):
        with db() as c:
            company(c,company_id);supplier=c.execute('SELECT * FROM suppliers WHERE id=? AND company_id=?',(supplier_id,company_id)).fetchone()
            if not supplier:fail('כרטיס ספק לא נמצא',404)
            fingerprint=supplier_signature(supplier,c)
            if payload.get('fingerprint')!=fingerprint:fail('פרטי הכרטיס עודכנו; רעננו את השאלה לפני אישור',409)
            if payload.get('identity_confirmed') is not True:fail('אשרו שזהו כרטיס הספק הנכון')
            rules=payload.get('rules',{})
            if not isinstance(rules,dict) or any(k not in RULE_FIELDS or not isinstance(v,str) or len(v)>200 for k,v in rules.items()):fail('כללי ספק לא תקינים')
            if rules.get('purpose') and rules['purpose'] not in ('journal','supplier_invoice','reconciliation','archive'):fail('יעד מסמך לא תקין')
            if rules.get('currency') and rules['currency'] not in ('ILS','USD','EUR','GBP'):fail('מטבע לא תקין')
            checks=payload.get('checks_policy','ask')
            if checks not in ('ask','usually','none'):fail('בחרו מדיניות צ׳קים')
            answer={'identity_confirmed':True,'rules':rules,'checks_policy':checks,'explanation':str(payload.get('explanation',''))[:2000]}
            c.execute('INSERT INTO learning_suppliers VALUES(?,?,?,?,?) ON CONFLICT(supplier_id) DO UPDATE SET fingerprint=excluded.fingerprint,answer=excluded.answer,created=excluded.created',(supplier_id,company_id,fingerprint,encode(answer),now()))
            c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),company_id,None,0,'supplier_confirmation','',supplier_id,encode(answer),1,now()))
            s['audit'](c,company_id,None,'supplier_learning_confirmed',{'supplier_id':supplier_id,'rules':rules});return {'ok':True}

    @app.post('/api/companies/{company_id}/learning/train')
    def start_training(company_id:str,payload:dict=Body(...)):return manager.request(company_id,payload)

    @app.post('/api/companies/{company_id}/learning/cancel')
    def cancel_training(company_id:str):
        with db() as c:
            company(c,company_id)
            if not c.execute("SELECT 1 FROM learning_jobs WHERE company_id=? AND state IN ('running','queued')",(company_id,)).fetchone():fail('אין אימון פעיל בחברה זו',404)
        manager.cancel.set();return {'ok':True}

    @app.patch('/api/companies/{company_id}/learning/examples/{example_id}')
    def edit_example(company_id:str,example_id:str,payload:dict=Body(...)):
        with db() as c:
            company(c,company_id);table,row=example_by_id(c,company_id,example_id)
            role=payload.get('role',row['role']);label=payload.get('label',row['label']);writer=check_writer(c,company_id,payload.get('writer_id',row['writer_id']))
            if role not in ('auto','train','validation','excluded') or not isinstance(label,str) or not re.fullmatch('[0-9]{1,30}',label):fail('תפקיד או ערך אמת לא תקינים')
            approved=int(payload.get('approved',bool(row['approved']) if label==row['label'] else False) is True)
            reason=payload.get('reason',row['reason']);explanation=payload.get('explanation',row['explanation'])
            if reason not in REASONS or not isinstance(explanation,str) or len(explanation)>2000:fail('סיבת תיקון או הסבר לא תקינים')
            c.execute(f'UPDATE {table} SET role=?,label=?,writer_id=?,approved=?,reason=?,explanation=?,created=? WHERE id=?',(role,label,writer,approved,reason,explanation,now(),example_id))
            if approved:c.execute('UPDATE check_corrections SET number=?,created=? WHERE company_id=? AND crop_sha=?',(label,now(),company_id,row['crop_sha']))
            else:c.execute('DELETE FROM check_corrections WHERE company_id=? AND crop_sha=?',(company_id,row['crop_sha']))
            c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),company_id,row.get('document_id'),0,'training_label',row['label'],label,encode({'reason':reason,'explanation':explanation,'writer_id':writer,'role':role,'scope':'training_only'}),approved,now()))
            # A changed label or excluded example invalidates dependent versions immediately.
            if label!=row['label'] or role!=row['role'] or writer!=row['writer_id'] or approved!=row['approved']:c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(company_id,))
            return {'ok':True}

    @app.get('/api/companies/{company_id}/learning/examples/{example_id}/image')
    def example_image(company_id:str,example_id:str):
        with db() as c:
            company(c,company_id);_,row=example_by_id(c,company_id,example_id)
            return Response(safe_path(data,row['image']).read_bytes(),media_type='image/png')

    @app.post('/api/companies/{company_id}/learning/models/{model_id}/activate')
    def activate(company_id:str,model_id:str):
        with db() as c:
            company(c,company_id);row=c.execute('SELECT * FROM learning_models WHERE id=? AND company_id=?',(model_id,company_id)).fetchone()
            if not row:fail('מודל אישי לא נמצא',404)
            report=json.loads(row['report'])
            from .personal_digits import base_digest
            if not report.get('qualified') or not evaluation_qualifies(report['validation']) or report.get('base_sha256')!=base_digest() or dataset_fingerprint(manager.rows(c,company_id,row['writer_id']))!=report['dataset_fingerprint']:fail('הריצו אימון ובדיקה על התיקונים העדכניים לפני הפעלת גרסה זו',409)
            c.execute('UPDATE learning_models SET active=0 WHERE company_id=? AND writer_id IS ?',(company_id,row['writer_id']));c.execute('UPDATE learning_models SET active=1 WHERE id=?',(model_id,));return {'ok':True}

    @app.post('/api/companies/{company_id}/learning/models/deactivate')
    def deactivate(company_id:str):
        with db() as c:company(c,company_id);c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(company_id,))
        return {'ok':True}

    @app.get('/api/companies/{company_id}/learning/models/{model_id}/export')
    def export_model(company_id:str,model_id:str):
        import zipfile
        output=io.BytesIO()
        with db() as c:
            company(c,company_id);row=c.execute('SELECT * FROM learning_models WHERE id=? AND company_id=?',(model_id,company_id)).fetchone()
            if not row:fail('גרסת מודל לא נמצאה',404)
            content=safe_path(data,row['path']).read_bytes()
            manifest={'installation_id':c.execute('SELECT id FROM learning_installation').fetchone()[0],'company_id':company_id,'writer_id':row['writer_id'],'origin_model_id':model_id,'sha256':hashlib.sha256(content).hexdigest(),'report':json.loads(row['report'])}
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json',encode(manifest).encode('utf-8'));archive.writestr('weights.npz',content)
        return Response(output.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="my-personal-digit-model.zip"'})

    @app.post('/api/companies/{company_id}/learning/models/import')
    def import_model(company_id:str,file:UploadFile=File(...)):
        import zipfile
        from . import personal_digits as model
        content=file.file.read(2_000_001)
        if len(content)>2_000_000:fail('קובץ המודל האישי מוגבל ל־2MB')
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sorted(archive.namelist())!=['manifest.json','weights.npz'] or sum(m.file_size for m in archive.infolist())>5_000_000:raise ValueError()
                manifest=json.loads(archive.read('manifest.json'));weights=archive.read('weights.npz')
            if len(weights)>100000 or hashlib.sha256(weights).hexdigest()!=manifest['sha256'] or not isinstance(manifest.get('report'),dict):raise ValueError()
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'weights.npz';path.write_bytes(weights);fitted=model.load(path)
        except (ValueError,KeyError,TypeError,OSError,zipfile.BadZipFile):fail('קובץ גרסת המודל האישי אינו תקין')
        with db() as c:
            company(c,company_id);installation=c.execute('SELECT id FROM learning_installation').fetchone()[0]
            if manifest.get('installation_id')!=installation or manifest.get('company_id')!=company_id:fail('ייבוא גרסה אישית מיועד לאותה התקנה וחברה ששוחזרו מהגיבוי האישי',409)
            writer=check_writer(c,company_id,manifest.get('writer_id'));rows=manager.rows(c,company_id,writer);report=manifest.get('report',{})
            if dataset_fingerprint(rows)!=report.get('dataset_fingerprint') or report.get('base_sha256')!=model.base_digest():fail('גרסת המודל נלמדה על מאגר או מנוע בסיס אחרים; בחרו גיבוי אישי תואם',409)
            training,held=split_samples(rows)
            if set(report.get('train_ids',[]))!={r['id'] for r in training} or set(report.get('validation_ids',[]))!={r['id'] for r in held}:fail('חלוקת האימון והבדיקה אינה תואמת למאגר האישי',409)
            samples=[]
            for row in held:
                x,prior=model.features(safe_path(data,row['image']));samples.append({'id':row['id'],'writer':row['writer_id'] or 'unknown','text':row['label'],'x':x,'prior':prior})
            evaluation=model.evaluate(fitted,samples)
            incumbent=c.execute('SELECT path FROM learning_models WHERE company_id=? AND active=1 AND writer_id IS ?',(company_id,writer)).fetchone()
            previous=model.evaluate(model.load(safe_path(data,incumbent[0])),samples) if incumbent else None
            qualified=evaluation_qualifies(evaluation) and evaluation['personal_correct']>=evaluation['baseline_correct'] and (not previous or evaluation['personal_correct']>=previous['personal_correct'])
            ident=uid();relative=f'files/learning/{company_id}/models/{ident}.npz';target=safe_path(data,relative);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(weights)
            report.update(validation=evaluation,qualified=bool(qualified),activated=False,imported=True,origin_model_id=manifest.get('origin_model_id'))
            c.execute('INSERT INTO learning_models VALUES(?,?,?,?,?,?,?)',(ident,company_id,writer,relative,0,encode(report),now()))
            s['audit'](c,company_id,None,'personal_model_imported',{'model_id':ident,'validation_tested':evaluation['tested']})
            return {'id':ident,'qualified':bool(qualified),'validation':evaluation}

    @app.get('/api/documents/{doc_id}/learning')
    def document_learning(doc_id:str):
        with db() as c:
            d=document(c,doc_id);events=[{**dict(r),'context':json.loads(r['context'])} for r in c.execute('SELECT * FROM learning_events WHERE document_id=? ORDER BY created',(doc_id,))]
            return {'events':events,'supplier_reviewed':bool(supplier_review(c,d['supplier_id'],d['company_id'])) if d['supplier_id'] else False}

    @app.post('/api/documents/{doc_id}/learning/template-proposals')
    def template_proposals(doc_id:str):
        from . import ocr
        from .domain import extract,money
        with db() as c:
            d=document(c,doc_id);s['editable'](d)
            local=c.execute('SELECT * FROM learning_templates WHERE company_id=? AND supplier_id=? AND kind=?',(d['company_id'],d['supplier_id'],d['fields']['kind'])).fetchall()
            shared=c.execute('SELECT * FROM learning_shared_templates WHERE company_id=? AND supplier_id=? AND kind=?',(d['company_id'],d['supplier_id'],d['fields']['kind'])).fetchall();rows=list(local)+[r for r in shared if r['field'] not in {r['field'] for r in local}];result=[]
            with tempfile.TemporaryDirectory() as temp:
                for row in rows:
                    path=Path(temp)/'field.png';region=json.loads(row['region']);ids=[d['id']]+[r[0] for r in c.execute('SELECT child_id FROM document_links WHERE primary_id=?',(d['id'],))]
                    if region.get('page_offset',0)>=len(ids):continue
                    origin=document(c,ids[region.get('page_offset',0)],d['company_id']);s['crop'](origin,region,path);text,engine,_=ocr.recognize(path);value=extract(text).get(row['field'],'')
                    if row['field'] in ('supplier_name','notes'):value=text.strip()
                    if row['field'] in ('net','vat','total') and not value:
                        matches=re.findall(r'(?<!\d)[0-9][0-9,]*\.[0-9]{2}(?!\d)',text)
                        if len(matches)==1:
                            try:value=str(money(matches[0]))
                            except ValueError:pass
                    if row['field']=='number' and not value and re.fullmatch(r'[A-Za-z0-9/-]{1,100}',text.strip()):value=text.strip()
                    result.append({'field':row['field'],'value':value,'raw':text,'region':region,'engine':engine,'confirmed':False})
            return {'proposals':result}

    @app.get('/api/companies/{company_id}/learning/check-register')
    def check_register(company_id:str):
        with db() as c:company(c,company_id);return [{**json.loads(r['value']),'id':r['id'],'number':r['number'],'supplier_id':r['supplier_id']} for r in c.execute('SELECT * FROM learning_checks WHERE company_id=? ORDER BY number',(company_id,))]

    @app.post('/api/companies/{company_id}/learning/check-register')
    def import_checks(company_id:str,payload:dict=Body(...)):
        rows=payload.get('rows',[])
        if not isinstance(rows,list) or not 1<=len(rows)<=10000:fail('יבוא רשימת צ׳קים: 1–10,000 שורות')
        with db() as c:
            company(c,company_id);seen=set()
            for row in rows:
                number=row.get('number') if isinstance(row,dict) else None
                if not isinstance(number,str) or not re.fullmatch('[0-9]{1,30}',number) or number in seen:fail('מספרי צ׳קים ייחודיים כמחרוזות, כולל אפסים מובילים')
                seen.add(number);supplier_id=row.get('supplier_id') or None
                if row.get('supplier_code'):
                    supplier=c.execute('SELECT id FROM suppliers WHERE company_id=? AND code=?',(company_id,str(row['supplier_code']))).fetchone()
                    if not supplier:fail('קוד הספק ברשימת הצ׳קים אינו קיים בחברה')
                    supplier_id=supplier[0]
                if supplier_id and not c.execute('SELECT 1 FROM suppliers WHERE id=? AND company_id=?',(supplier_id,company_id)).fetchone():fail('הספק אינו שייך לחברה')
                value={key:str(row.get(key,''))[:200] for key in ('amount','date','bank','supplier_code')}
                if value['amount']:
                    from .domain import money
                    try:value['amount']=str(money(value['amount']))
                    except ValueError:fail('סכום צ׳ק לא תקין')
                c.execute('INSERT INTO learning_checks VALUES(?,?,?,?,?) ON CONFLICT(company_id,number) DO UPDATE SET supplier_id=excluded.supplier_id,value=excluded.value',(uid(),company_id,number,supplier_id,encode(value)))
            return {'count':len(rows)}

    @app.post('/api/companies/{company_id}/learning/reset')
    def reset(company_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('אשרו במפורש איפוס של נתוני הלמידה האישיים לחברה זו')
        with db() as c:
            company(c,company_id)
            if c.execute("SELECT 1 FROM learning_jobs WHERE company_id=? AND state IN ('queued','running')",(company_id,)).fetchone():fail('עצרו את האימון הפעיל לפני איפוס',409)
            paths=[safe_path(data,r['image']) for r in examples(c,company_id)]+[safe_path(data,r[0]) for r in c.execute('SELECT path FROM learning_models WHERE company_id=?',(company_id,))]
            c.execute('DELETE FROM learning_engine_observations WHERE example_id IN (SELECT id FROM learning_examples WHERE company_id=?)',(company_id,))
            for table in ('learning_models','learning_examples','learning_shared_examples','learning_events','learning_templates','learning_shared_templates','learning_supplier_proposals','learning_aliases','learning_alias_evidence','learning_checks','learning_suppliers','learning_jobs','learning_writer_origins','learning_writers','learning_settings','check_corrections'):c.execute(f'DELETE FROM {table} WHERE company_id=?',(company_id,))
            for row in c.execute('SELECT document_id,value FROM document_meta WHERE document_id IN (SELECT id FROM documents WHERE company_id=?)',(company_id,)).fetchall():
                value=json.loads(row['value']);value.pop('learning_context',None);c.execute('UPDATE document_meta SET value=? WHERE document_id=?',(encode(value),row['document_id']))
            for row in c.execute('SELECT id,checks FROM documents WHERE company_id=?',(company_id,)).fetchall():
                checks=json.loads(row['checks'])
                for check in checks:check.pop('writer_id',None)
                c.execute('UPDATE documents SET checks=? WHERE id=?',(encode(checks),row['id']))
            s['audit'](c,company_id,None,'personal_learning_reset',{})
        for path in paths:path.unlink(missing_ok=True)
        return {'ok':True}

    @app.get('/api/companies/{company_id}/learning/export')
    def export_learning(company_id:str):
        import zipfile
        output=io.BytesIO()
        with db() as c:
            company(c,company_id);tables={name:[dict(row) for row in c.execute(f'SELECT * FROM {name} WHERE company_id=?',(company_id,))] for name in ('learning_writers','learning_suppliers','learning_events','learning_examples','learning_shared_examples','learning_templates','learning_shared_templates','learning_supplier_proposals','learning_aliases','learning_alias_evidence','learning_checks','learning_models')}
            tables['learning_engine_observations']=[dict(r) for r in c.execute('SELECT o.* FROM learning_engine_observations o JOIN learning_examples e ON e.id=o.example_id WHERE e.company_id=?',(company_id,))]
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('personal-learning.json',encode({'installation_id':c.execute('SELECT id FROM learning_installation').fetchone()[0],'company_id':company_id,'tables':tables}).encode())
                for relative in {r['image'] for r in tables['learning_examples']+tables['learning_shared_examples']}|{r['path'] for r in tables['learning_models']}:archive.write(safe_path(data,relative),relative)
        return Response(output.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="personal-learning-private.zip"'})

    return manager


def registered_checks(c,d,proposals):
    import difflib
    numbers={p.get('number') for p in proposals if p.get('number')};output=[]
    if not numbers:return output
    for row in c.execute('SELECT * FROM learning_checks WHERE company_id=? AND (supplier_id=? OR supplier_id IS NULL) LIMIT 10000',(d['company_id'],d['supplier_id'])):
        similarity=max(difflib.SequenceMatcher(None,row['number'],n).ratio() for n in numbers)
        if row['number'] in numbers or similarity>=.75:
            value=json.loads(row['value']);matched=[]
            if row['supplier_id'] and row['supplier_id']==d['supplier_id']:matched.append('supplier')
            if value.get('amount') and d['fields'].get('total'):
                from .domain import money
                try:
                    if money(value['amount'])==money(d['fields']['total']):matched.append('amount')
                except ValueError:pass
            if value.get('date') and value['date'] in (d['fields'].get('date'),d['fields'].get('due_date')):matched.append('date')
            output.append({'engine':'issued-check-register','number':row['number'],'confirmed':False,'available':True,'reason':'הצעה להצלבה מרשימת הצ׳קים האישית; יש להשוות למקור','register':value,'matched_fields':matched,'match_score':similarity+len(matched)})
    return sorted(output,key=lambda p:p['match_score'],reverse=True)[:10]


def calibrate(c,company_id,proposals):
    counts={}
    for row in c.execute("SELECT o.engine,COUNT(*) AS tested,SUM(o.number=e.label) AS correct FROM learning_engine_observations o JOIN learning_examples e ON e.id=o.example_id WHERE e.company_id=? AND e.approved=1 AND e.role<>'excluded' GROUP BY o.engine",(company_id,)):
        counts[row['engine']]={'tested':row['tested'],'correct':row['correct']}
    for proposal in proposals:
        key=str(proposal.get('engine',''))+'@'+str(proposal.get('variant','original'))
        if key in counts:proposal['personal_calibration']=counts[key]
    # Rank recommendations using local exact-match evidence, never raw scores from unrelated models.
    def score(proposal):
        if proposal.get('engine') in ('user-confirmed-exact-crop','team-confirmed-exact-crop'):return (3,0)
        sample=proposal.get('personal_calibration',{})
        if sample.get('tested',0)>=3:return (1,(sample['correct']+1)/(sample['tested']+2))
        if proposal.get('engine')=='personal-ctc':return (1,.5)
        return (0,0)
    return sorted(proposals,key=score,reverse=True)


def imported_memory(c,company_id,path):
    sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    row=c.execute("SELECT s.id,s.label FROM learning_shared_examples s WHERE s.company_id=? AND s.crop_sha=? AND s.approved=1 AND s.role<>'excluded' AND NOT EXISTS(SELECT 1 FROM learning_examples e WHERE e.company_id=s.company_id AND e.crop_sha=s.crop_sha)",(company_id,sha)).fetchone()
    return {'engine':'team-confirmed-exact-crop','number':row['label'],'example_id':row['id'],'confirmed':False,'reason':'אותו חיתוך בדיוק הופיע בלמידת צוות שיובאה ואושרה; יש לאשר את השיוך למסמך הנוכחי'} if row else None
