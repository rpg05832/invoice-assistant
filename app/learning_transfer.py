"""Explicit, previewed learning exchange between computers and team members.

Bundles carry cropped examples and learned settings, never accounting documents
or cloud credentials. All imported IDs are remapped into the chosen local company.
"""
import hashlib
import io
import json
import re
import tempfile
import threading
import zipfile
from pathlib import Path

from fastapi import Body,File,UploadFile
from fastapi.responses import Response
from PIL import Image
from . import learning as L,personal_digits as PD
from .domain import FIELDS,TYPES

CATEGORIES=('examples','supplier_rules','models','check_register','history')


class Transfer:
    def __init__(self,s,app):self.s=s;self.app=app;self.pending={};self.lock=threading.Lock()

    def close(self):
        for entry in list(self.pending.values()):entry['path'].unlink(missing_ok=True)
        self.pending.clear()

    def export(self,company_id,selected,writer_id=None):
        output=io.BytesIO();s=self.s
        with s['db']() as c:
            company=s['company'](c,company_id);installation=c.execute('SELECT id FROM learning_installation').fetchone()[0]
            writers=[dict(r) for r in c.execute('SELECT id,name FROM learning_writers WHERE company_id=?',(company_id,))]
            for writer in writers:writer['origins']=[dict(r) for r in c.execute('SELECT origin_installation,origin_id FROM learning_writer_origins WHERE company_id=? AND writer_id=?',(company_id,writer['id']))]+[{'origin_installation':installation,'origin_id':writer['id']}]
            if writer_id:L.check_writer(c,company_id,writer_id)
            if writer_id:writers=[w for w in writers if w['id']==writer_id]
            if not selected['examples'] and not selected['models']:writers=[]
            rows=self.app.state.learning.rows(c,company_id);by_id={r['id']:r for r in rows};all_examples={r['id']:r for r in L.examples(c,company_id)}
            samples=[];models=[];suppliers=[]
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                if selected['examples']:
                    seen_crops=set()
                    for sample in L.examples(c,company_id):
                        if writer_id and sample['writer_id']!=writer_id:continue
                        if sample['crop_sha'] in seen_crops:continue
                        seen_crops.add(sample['crop_sha'])
                        record={k:sample[k] for k in ('id','source_group','crop_sha','label','writer_id','reason','explanation','role','approved')}
                        # No invoice reference or full-page image is included in the bundle.
                        record['invoice_key']=by_id.get(sample['id'],sample).get('invoice_key','source:'+sample['source_group'])
                        record['asset']='examples/'+sample['id']+'.png';archive.write(L.safe_path(s['data'],sample['image']),record['asset']);samples.append(record)
                if selected['models']:
                    exported_styles=set()
                    for row in c.execute('SELECT * FROM learning_models WHERE company_id=? ORDER BY active DESC,created DESC LIMIT 50',(company_id,)):
                        if writer_id and row['writer_id']!=writer_id:continue
                        if row['writer_id'] in exported_styles:continue
                        exported_styles.add(row['writer_id'])
                        report=json.loads(row['report']);content=L.safe_path(s['data'],row['path']).read_bytes();asset='models/'+row['id']+'.npz'
                        archive.writestr(asset,content)
                        trained=[by_id[i] for i in report.get('train_ids',[]) if i in by_id]
                        label_hashes=report.get('training_label_hashes') or {all_examples[i]['crop_sha']:hashlib.sha256(report['sample_labels'][i].encode()).hexdigest() for i in report.get('train_ids',[]) if i in all_examples and i in report.get('sample_labels',{})}
                        compact_report={k:report[k] for k in ('base_model','base_sha256','train_ids','validation_ids','training','fitting') if k in report}
                        models.append({'id':row['id'],'writer_id':row['writer_id'],'asset':asset,'sha256':hashlib.sha256(content).hexdigest(),'report':compact_report,'training_groups':report.get('training_groups',sorted({r['source_group'] for r in trained})),'training_crops':report.get('training_crops',sorted({r['crop_sha'] for r in trained})),'training_invoice_keys':report.get('training_invoice_keys',sorted({r['invoice_key'] for r in trained})),'training_label_hashes':label_hashes})
                if selected['supplier_rules'] or selected['check_register']:
                    for row in c.execute('SELECT * FROM suppliers WHERE company_id=?',(company_id,)):
                        templates=[{k:r[k] for k in ('kind','field','region')} for r in c.execute('SELECT * FROM learning_templates WHERE company_id=? AND supplier_id=?',(company_id,row['id']))]
                        local={(r['kind'],r['field']) for r in templates}
                        templates += [{k:r[k] for k in ('kind','field','region')} for r in c.execute('SELECT * FROM learning_shared_templates WHERE company_id=? AND supplier_id=?',(company_id,row['id'])) if (r['kind'],r['field']) not in local]
                        suppliers.append({'id':row['id'],'code':row['code'],'name':row['name'],'tax_id':row['tax_id'],'active':row['active'],'answer':L.supplier_review(c,row['id'],company_id),'aliases':[r[0] for r in c.execute('SELECT alias FROM learning_aliases WHERE company_id=? AND supplier_id=?',(company_id,row['id']))],'templates':templates})
                checks=[{'number':r['number'],'supplier_id':r['supplier_id'],**json.loads(r['value'])} for r in c.execute('SELECT * FROM learning_checks WHERE company_id=?',(company_id,))] if selected['check_register'] else []
                history=[{**dict(r),'context':json.loads(r['context'])} for r in c.execute('SELECT * FROM learning_events WHERE company_id=? ORDER BY created',(company_id,))] if selected['history'] else []
                manifest={'format':'invoice-assistant-learning','version':2,'origin_installation':installation,'company':{'name':company['name'],'tax_id':company['tax_id']},'exported':L.now(),'categories':selected,'writers':writers,'examples':samples,'models':models,'suppliers':suppliers,'checks':checks,'history':history}
                archive.writestr('manifest.json',L.encode(manifest).encode('utf-8'))
        return Response(output.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="personal-or-team-learning.zip"'})

    def validate(self,path):
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            if len(names)!=len(set(names)) or len(names)>5001 or sum(m.file_size for m in archive.infolist())>500_000_000:raise ValueError('חבילת למידה גדולה מדי או מכילה רשומות כפולות')
            if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>30_000_000:raise ValueError('תיאור חבילת הלמידה חסר או גדול מדי')
            if any(name!='manifest.json' and not re.fullmatch(r'(?:examples/[a-f0-9]{32}\.png|models/[a-f0-9]{32}\.npz)',name) for name in names):raise ValueError('החבילה מכילה קבצים שאינם חיתוכי למידה או משקולות אישיות')
            manifest=json.loads(archive.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format')!='invoice-assistant-learning' or manifest.get('version')!=2:raise ValueError('פורמט חבילת למידה לא נתמך')
            for key,limit in [('examples',2000),('models',50),('writers',2000),('suppliers',10000),('checks',10000),('history',100000)]:
                if not isinstance(manifest.get(key),list) or len(manifest[key])>limit or any(not isinstance(row,dict) for row in manifest[key]):raise ValueError('מבנה חבילת למידה לא תקין')
            if not isinstance(manifest.get('origin_installation'),str) or len(manifest['origin_installation'])>100 or not isinstance(manifest.get('company'),dict):raise ValueError('מקור חבילת הלמידה לא תקין')
            writer_ids={w['id'] for w in manifest['writers']};supplier_ids={w['id'] for w in manifest['suppliers']}
            if any(not isinstance(row.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',row['id']) for key in ('writers','suppliers','examples','models') for row in manifest[key]):raise ValueError('מזהה בחבילת הלמידה לא תקין')
            if len(writer_ids)!=len(manifest['writers']) or len(supplier_ids)!=len(manifest['suppliers']):raise ValueError('מזהים כפולים בחבילה')
            seen=set();crop_labels={}
            for row in manifest['examples']:
                if not isinstance(row.get('label'),str) or not re.fullmatch('[0-9]{1,30}',row['label']) or row.get('writer_id') not in writer_ids|{None} or row.get('role') not in ('auto','train','validation','excluded') or row.get('reason') not in L.REASONS or not isinstance(row.get('explanation'),str) or len(row['explanation'])>2000:raise ValueError('דוגמת למידה לא תקינה')
                if row.get('approved') not in (0,1,False,True):raise ValueError('מצב אישור של דוגמה לא תקין')
                if row['id'] in seen or row.get('asset')!='examples/'+row['id']+'.png' or row['asset'] not in names:raise ValueError('קובץ דוגמה חסר או כפול')
                seen.add(row['id']);content=archive.read(row['asset'])
                if hashlib.sha256(content).hexdigest()!=row.get('crop_sha'):raise ValueError('אימות חיתוך הלמידה נכשל')
                if row['crop_sha'] in crop_labels and crop_labels[row['crop_sha']]!=row['label']:raise ValueError('שני תיוגים שונים לאותו חיתוך בחבילת המקור')
                crop_labels[row['crop_sha']]=row['label']
                with Image.open(io.BytesIO(content)) as image:
                    if image.format!='PNG' or image.width*image.height>40_000_000:raise ValueError('תמונת למידה לא תקינה')
                    image.verify()
                if any(not isinstance(row.get(k),str) or not 1<=len(row[k])<=256 for k in ('source_group','invoice_key')):raise ValueError('קבוצת מקור לא תקינה')
            for row in manifest['models']:
                if row.get('asset')!='models/'+row['id']+'.npz' or row['asset'] not in names or row.get('writer_id') not in writer_ids|{None} or not isinstance(row.get('report'),dict):raise ValueError('גרסת מודל לא תקינה')
                content=archive.read(row['asset'])
                if len(content)>100000 or hashlib.sha256(content).hexdigest()!=row.get('sha256'):raise ValueError('אימות משקולות נכשל')
                with tempfile.TemporaryDirectory() as temp:
                    file=Path(temp)/'weights.npz';file.write_bytes(content);PD.load(file)
                report=row['report']
                if any(not isinstance(report.get(k,[]),list) or len(report.get(k,[]))>2000 for k in ('train_ids','validation_ids')) or any(not isinstance(row.get(k,[]),list) or len(row.get(k,[]))>2000 or any(not isinstance(v,str) or len(v)>256 for v in row.get(k,[])) for k in ('training_groups','training_crops','training_invoice_keys')):raise ValueError('פרטי קבוצות אימון לא תקינים')
                hashes=row.get('training_label_hashes',{})
                if not isinstance(hashes,dict) or len(hashes)>2000 or any(not re.fullmatch('[a-f0-9]{64}',k) or not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for k,v in hashes.items()):raise ValueError('חתימות תוויות אימון לא תקינות')
                training=report.get('training',{})
                if not isinstance(training,dict) or any(not isinstance(training.get(k,0),int) or not 0<=training.get(k,0)<=2000 for k in ('tested','correct')):raise ValueError('סיכום אימון לא תקין')
            for writer in manifest['writers']:
                if not isinstance(writer.get('name'),str) or not 1<=len(writer['name'])<=100:raise ValueError('שם סגנון כתב לא תקין')
            for supplier in manifest['suppliers']:
                if any(not isinstance(supplier.get(k),str) or len(supplier[k])>200 for k in ('id','code','name','tax_id')) or (supplier['tax_id'] and not re.fullmatch('[0-9]{9}',supplier['tax_id'])):raise ValueError('פרטי ספק לא תקינים')
                answer=supplier.get('answer')
                if answer:
                    rules=answer.get('rules',{})
                    if not isinstance(rules,dict) or any(k not in L.RULE_FIELDS or not isinstance(v,str) or len(v)>200 for k,v in rules.items()):raise ValueError('כללי ספק לא תקינים')
                if not isinstance(supplier.get('templates'),list) or not isinstance(supplier.get('aliases'),list):raise ValueError('תבנית ספק לא תקינה')
                for template in supplier['templates']:
                    if template['field'] not in FIELDS or template['kind'] not in TYPES:raise ValueError('שדה תבנית לא תקין')
                    region=json.loads(template['region']);self.s['validate_region'](region)
                    if isinstance(region.get('page_offset',0),bool) or not isinstance(region.get('page_offset',0),int) or not 0<=region.get('page_offset',0)<50:raise ValueError('עמוד תבנית לא תקין')
            return manifest

    def preview(self,company_id,file):
        with self.s['db']() as c:self.s['company'](c,company_id)
        ident=L.uid();directory=self.s['data']/f'files/learning/{company_id}/imports';directory.mkdir(parents=True,exist_ok=True);path=directory/(ident+'.zip')
        try:
            total=0
            with path.open('wb') as output:
                while True:
                    chunk=file.file.read(1024*1024)
                    if not chunk:break
                    total+=len(chunk)
                    if total>200_000_000:raise ValueError('חבילת הלמידה מוגבלת ל־200MB')
                    output.write(chunk)
            manifest=self.validate(path)
            with self.s['db']() as c:
                local_suppliers=[dict(r) for r in c.execute('SELECT id,name,code,tax_id FROM suppliers WHERE company_id=?',(company_id,))]
                suppliers=[]
                for row in manifest['suppliers']:
                    matches=[s for s in local_suppliers if row['tax_id'] and row['tax_id']==s['tax_id']]
                    if not matches:matches=[s for s in local_suppliers if row['code']==s['code'] and (not row['tax_id'] or not s['tax_id'] or row['tax_id']==s['tax_id'])]
                    suppliers.append({**{k:row[k] for k in ('id','name','code','tax_id')},'suggested_id':matches[0]['id'] if len(matches)==1 else None})
                local_samples={r['crop_sha']:r for r in L.examples(c,company_id)}
                duplicates=sum(row['crop_sha'] in local_samples for row in manifest['examples'])
                conflicts=[{'incoming_id':row['id'],'local_id':local_samples[row['crop_sha']]['id'],'local_label':local_samples[row['crop_sha']]['label'],'incoming_label':row['label']} for row in manifest['examples'] if row['crop_sha'] in local_samples and row['label']!=local_samples[row['crop_sha']]['label']]
                writers=[]
                for row in manifest['writers']:
                    origins=row.get('origins',[])+[{'origin_installation':manifest['origin_installation'],'origin_id':row['id']}];known=None
                    for origin in origins:
                        record=c.execute('SELECT writer_id FROM learning_writer_origins WHERE company_id=? AND origin_installation=? AND origin_id=?',(company_id,origin.get('origin_installation'),origin.get('origin_id'))).fetchone()
                        if record:known=record[0];break
                    writers.append({'id':row['id'],'name':row['name'],'suggested_id':known})
            with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            with self.lock:self.pending[ident]={'company_id':company_id,'path':path,'manifest':manifest,'sha256':checksum}
            return {'token':ident,'source':manifest['company'],'counts':{k:len(manifest[k]) for k in ('examples','models','writers','suppliers','checks','history')},'suppliers':suppliers,'local_suppliers':local_suppliers,'writers':writers,'duplicates':duplicates,'conflicts':conflicts,'ledger_documents':0}
        except Exception as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc,L.HTTPException):raise
            L.fail('חבילת הלמידה לא אומתה: '+str(exc)[:250])

    def apply(self,company_id,payload):
        token=payload.get('token')
        with self.lock:
            entry=self.pending.get(token)
            if not entry or entry['company_id']!=company_id:L.fail('תצוגת היבוא אינה שייכת לחברה זו או פגה',404)
            if payload.get('confirmed') is not True:L.fail('אשרו את בחירת החברה ומיפויי הספקים והכתבים')
            selected=payload.get('categories',{})
            if not isinstance(selected,dict) or any(k not in CATEGORIES or not isinstance(v,bool) for k,v in selected.items()):L.fail('בחירת חלקי הלמידה לא תקינה')
            if not any(selected.values()):L.fail('בחרו לפחות חלק אחד לייבוא')
            if any(not isinstance(payload.get(key,{}),dict) or any(not isinstance(k,str) or (v is not None and not isinstance(v,str)) for k,v in payload.get(key,{}).items()) for key in ('supplier_mapping','writer_mapping')):L.fail('מיפויי היבוא לא תקינים')
            with entry['path'].open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=entry['sha256']:L.fail('קובץ היבוא השתנה מאז התצוגה המקדימה; העלו אותו שוב',409)
            policy=payload.get('conflict_policy','keep_local')
            if policy not in ('keep_local','use_imported'):L.fail('בחרו כיצד לטפל בתוויות שונות')
            self._apply(company_id,entry,selected,payload,policy)
            self.pending.pop(token,None);entry['path'].unlink(missing_ok=True)
            return entry['result']

    def _apply(self,company_id,entry,selected,payload,policy):
        s=self.s;manifest=entry['manifest'];origin_installation=manifest['origin_installation'];receipt=L.uid();origin={'installation':origin_installation,'company':manifest['company'].get('name',''),'transfer_id':receipt,'date':L.now()};writer_map={None:None};supplier_map={};id_map={};created=[];feature_cache={};counts={'examples_added':0,'examples_reused':0,'examples_updated':0,'models':0,'supplier_proposals':0,'templates':0,'checks':0,'history':0}
        try:
            with s['db']() as c,zipfile.ZipFile(entry['path']) as archive:
                s['company'](c,company_id)
                for row in manifest['writers'] if selected.get('examples') or selected.get('models') else []:
                    chosen=payload.get('writer_mapping',{}).get(row['id'])
                    if chosen:L.check_writer(c,company_id,chosen)
                    else:
                        for provenance in row.get('origins',[])+[{'origin_installation':origin_installation,'origin_id':row['id']}]:
                            known=c.execute('SELECT writer_id FROM learning_writer_origins WHERE company_id=? AND origin_installation=? AND origin_id=?',(company_id,provenance.get('origin_installation'),provenance.get('origin_id'))).fetchone()
                            if known:chosen=known[0];break
                    if not chosen:
                        name=row['name'];suffix=1
                        while c.execute('SELECT 1 FROM learning_writers WHERE company_id=? AND name=?',(company_id,name)).fetchone():suffix+=1;name=row['name'][:85]+f' · צוות {suffix}'
                        chosen=L.uid();c.execute('INSERT INTO learning_writers VALUES(?,?,?,?)',(chosen,company_id,name,L.now()))
                    writer_map[row['id']]=chosen
                    for provenance in row.get('origins',[])+[{'origin_installation':origin_installation,'origin_id':row['id']}]:c.execute('INSERT INTO learning_writer_origins VALUES(?,?,?,?) ON CONFLICT(company_id,origin_installation,origin_id) DO UPDATE SET writer_id=excluded.writer_id',(company_id,str(provenance.get('origin_installation',''))[:100],str(provenance.get('origin_id',''))[:100],chosen))
                for row in manifest['suppliers'] if selected.get('supplier_rules') or selected.get('check_register') else []:
                    chosen=payload.get('supplier_mapping',{}).get(row['id'])
                    if chosen=='__create__':
                        if not row['code'].strip() or not row['name'].strip() or c.execute('SELECT 1 FROM suppliers WHERE company_id=? AND code=?',(company_id,row['code'])).fetchone():L.fail('ליצירת כרטיס מהחבילה בחרו קוד חדש ושם תקין; קוד קיים יש למפות לכרטיס הקיים')
                        chosen=L.uid();c.execute('INSERT INTO suppliers VALUES(?,?,?,?,?,?,?)',(chosen,company_id,row['code'],row['name'],row['tax_id'],int(bool(row.get('active',True))),''))
                    elif chosen and not c.execute('SELECT 1 FROM suppliers WHERE id=? AND company_id=?',(chosen,company_id)).fetchone():L.fail('מיפוי הספק אינו שייך לחברה שנבחרה')
                    supplier_map[row['id']]=chosen or None
                    if not chosen or not selected.get('supplier_rules'):continue
                    if row.get('answer'):
                        c.execute('INSERT INTO learning_supplier_proposals VALUES(?,?,?,?) ON CONFLICT(supplier_id) DO UPDATE SET value=excluded.value,origin=excluded.origin',(chosen,company_id,L.encode(row['answer']),L.encode(origin)));counts['supplier_proposals']+=1
                    for alias in row['aliases']:
                        if not isinstance(alias,str) or len(alias)>4000:L.fail('כינוי ספק לא תקין בחבילה')
                        c.execute('INSERT OR IGNORE INTO learning_alias_evidence VALUES(?,?,?)',(company_id,alias.casefold(),chosen));c.execute('INSERT OR IGNORE INTO learning_aliases VALUES(?,?,?)',(company_id,alias.casefold(),chosen))
                    for template in row['templates']:
                        c.execute('INSERT INTO learning_shared_templates VALUES(?,?,?,?,?,?,?) ON CONFLICT(company_id,supplier_id,kind,field) DO UPDATE SET region=excluded.region,origin=excluded.origin',(L.uid(),company_id,chosen,template['kind'],template['field'],template['region'],L.encode(origin)));counts['templates']+=1
                existing={row['crop_sha']:row for row in L.examples(c,company_id)}
                if selected.get('examples'):
                    for row in manifest['examples']:
                        previous=existing.get(row['crop_sha']);approved=int(bool(row['approved']) and payload.get('approve_examples') is True)
                        if previous:
                            id_map[row['id']]=previous['id'];counts['examples_reused']+=1
                            if row['label']!=previous['label'] and policy=='use_imported':
                                table='learning_shared_examples' if previous['imported'] else 'learning_examples'
                                c.execute(f'UPDATE {table} SET label=?,approved=?,writer_id=?,reason=?,explanation=?,created=? WHERE id=?',(row['label'],approved,writer_map[row['writer_id']],row['reason'],row['explanation'],L.now(),previous['id']))
                                c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(company_id,));c.execute('DELETE FROM check_corrections WHERE company_id=? AND crop_sha=?',(company_id,row['crop_sha']));counts['examples_updated']+=1
                            continue
                        ident=L.uid();relative=f'files/learning/{company_id}/examples/{ident}.png';path=L.safe_path(s['data'],relative);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.read(row['asset']));created.append(path)
                        c.execute('INSERT INTO learning_shared_examples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,company_id,row['source_group'],row['invoice_key'],row['crop_sha'],relative,row['label'],writer_map[row['writer_id']],row['reason'],row['explanation'],row['role'],approved,L.encode(origin),L.now()));id_map[row['id']]=ident;counts['examples_added']+=1;existing[row['crop_sha']]={'id':ident,'label':row['label'],'imported':True}
                if selected.get('check_register'):
                    for row in manifest['checks']:
                        if not isinstance(row.get('number'),str) or not re.fullmatch('[0-9]{1,30}',row['number']):L.fail('מספר צ׳ק לא תקין בחבילה')
                        supplier=supplier_map.get(row.get('supplier_id'))
                        if row.get('supplier_id') and not supplier:continue
                        value={k:str(row.get(k,''))[:200] for k in ('amount','date','bank','supplier_code')}
                        c.execute('INSERT OR IGNORE INTO learning_checks VALUES(?,?,?,?,?)',(L.uid(),company_id,row['number'],supplier,L.encode(value)));counts['checks']+=1
                if selected.get('history'):
                    for row in manifest['history']:
                        c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(L.uid(),company_id,None,0,str(row.get('field',''))[:100],str(row.get('before_value',''))[:4000],str(row.get('after_value',''))[:4000],L.encode({'imported_context':row.get('context',{}),'origin':origin}),int(bool(row.get('approved'))),L.now()));counts['history']+=1
                if selected.get('models'):
                    for row in manifest['models']:
                        self.import_weights(c,archive,company_id,row,writer_map,id_map,origin,created,feature_cache);counts['models']+=1
                s['audit'](c,company_id,None,'personal_learning_imported',{'origin':origin,'counts':counts,'categories':selected,'policy':policy})
                entry['result']={'ok':True,'counts':counts,'ledger_documents_created':0,'supplier_confirmation_required':True}
        except Exception:
            for path in created:path.unlink(missing_ok=True)
            raise

    def import_weights(self,c,archive,company_id,row,writer_map,id_map,origin,created,feature_cache):
        manager=self.app.state.learning;content=archive.read(row['asset']);writer=writer_map[row['writer_id']];source_report=row['report'];rows=manager.rows(c,company_id,writer)
        try:training,held=L.split_samples(rows)
        except ValueError:training,held=[],[]
        prior_groups=set(row.get('training_groups',[]));prior_crops=set(row.get('training_crops',[]));prior_invoices=set(row.get('training_invoice_keys',[]));label_hashes=row.get('training_label_hashes',{})
        # Even a locally-held-out row is not independent if the source model trained on it.
        held=[r for r in held if r['source_group'] not in prior_groups and r['crop_sha'] not in prior_crops and r['invoice_key'] not in prior_invoices]
        local_training_ids={r['id'] for r in training};mapped_training={id_map[i] for i in source_report.get('train_ids',[]) if i in id_map}
        ids_valid=not mapped_training or mapped_training<=local_training_ids
        if any(r['crop_sha'] in label_hashes and hashlib.sha256(r['label'].encode()).hexdigest()!=label_hashes[r['crop_sha']] for r in rows):ids_valid=False
        samples=[]
        for record in held:
            if record['id'] not in feature_cache:feature_cache[record['id']]=PD.features(L.safe_path(self.s['data'],record['image']))
            x,prior=feature_cache[record['id']];samples.append({'id':record['id'],'writer':record['writer_id'] or 'unknown','text':record['label'],'x':x,'prior':prior})
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'weights.npz';path.write_bytes(content);fitted=PD.load(path)
        evaluation=PD.evaluate(fitted,samples);base_ok=source_report.get('base_sha256')==PD.base_digest()
        incumbent=c.execute('SELECT path FROM learning_models WHERE company_id=? AND active=1 AND writer_id IS ?',(company_id,writer)).fetchone()
        previous=PD.evaluate(PD.load(L.safe_path(self.s['data'],incumbent[0])),samples) if incumbent else None
        qualified=base_ok and ids_valid and bool(prior_groups) and bool(prior_crops) and L.evaluation_qualifies(evaluation) and evaluation['personal_correct']>=evaluation['baseline_correct'] and (not previous or evaluation['personal_correct']>=previous['personal_correct'])
        ident=L.uid();relative=f'files/learning/{company_id}/models/{ident}.npz';path=L.safe_path(self.s['data'],relative);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);created.append(path)
        source_training=source_report.get('training',{})
        report={'base_model':PD.BASE,'base_sha256':source_report.get('base_sha256'),'dataset_fingerprint':L.dataset_fingerprint(rows),'example_count':len(rows),'train_ids':sorted(mapped_training),'training_groups':sorted(prior_groups),'training_crops':sorted(prior_crops),'training_invoice_keys':sorted(prior_invoices),'training_label_hashes':label_hashes,'validation_ids':[r['id'] for r in held],'writers':sorted({r['writer_id'] or 'unknown' for r in rows}),'validation':evaluation,'training':{'tested':int(source_training.get('tested',0)),'correct':int(source_training.get('correct',0))},'bytes':len(content),'qualified':bool(qualified),'activated':False,'imported':True,'origin':origin,'origin_model_id':row['id'],'sample_labels':{r['id']:r['label'] for r in rows}}
        c.execute('INSERT INTO learning_models VALUES(?,?,?,?,?,?,?)',(ident,company_id,writer,relative,0,L.encode(report),L.now()))


def register(app,s):
    transfer=Transfer(s,app);app.state.learning_transfer=transfer;app.router.add_event_handler('shutdown',transfer.close)
    @app.get('/api/companies/{company_id}/learning/transfer/export')
    def export(company_id:str,examples:bool=True,supplier_rules:bool=True,models:bool=True,check_register:bool=False,history:bool=False,writer_id:str|None=None):
        return transfer.export(company_id,{'examples':examples,'supplier_rules':supplier_rules,'models':models,'check_register':check_register,'history':history},writer_id)
    @app.post('/api/companies/{company_id}/learning/transfer/preview')
    def preview(company_id:str,file:UploadFile=File(...)):return transfer.preview(company_id,file)
    @app.get('/api/companies/{company_id}/learning/transfer/{token}/image/{example_id}')
    def preview_image(company_id:str,token:str,example_id:str):
        with transfer.lock:
            entry=transfer.pending.get(token)
            if not entry or entry['company_id']!=company_id:L.fail('תצוגת דוגמה אינה שייכת לחברה זו',404)
            row=next((e for e in entry['manifest']['examples'] if e['id']==example_id),None)
            if not row:L.fail('חיתוך לא נמצא בחבילת היבוא',404)
            with zipfile.ZipFile(entry['path']) as archive:return Response(archive.read(row['asset']),media_type='image/png')
    @app.post('/api/companies/{company_id}/learning/transfer/import')
    def apply(company_id:str,payload:dict=Body(...)):return transfer.apply(company_id,payload)
    return transfer
