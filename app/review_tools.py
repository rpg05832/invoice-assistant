"""Private review preferences, work markers, snippets and explicit editing tools."""
import json
import re
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP

from fastapi import Body
from . import learning as L,advanced
from .domain import money

BULK_FIELDS={'expense_account','vat_account','transaction_type','cost_center','project','notes','purpose'}
ORDER_FIELDS=['supplier_id','number','date','due_date','supplier_tax_id','recipient_tax_id','allocation','net','vat','total','currency','expense_account','delivery_refs','notes']
DEFAULTS={'source_width':50,'font_size':14,'field_order':[], 'keys':{'approve':'Enter','next':'Alt+ArrowLeft','previous':'Alt+ArrowRight'}}
KEYS={'approve':{'Enter','Ctrl+Enter','F9'},'next':{'Alt+ArrowLeft','PageDown'},'previous':{'Alt+ArrowRight','PageUp'}}


def register(app,s):
    db=s['db'];document=s['document'];company=s['company'];audit=s['audit'];fail=L.fail
    with db() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS review_preferences(company_id TEXT PRIMARY KEY REFERENCES companies(id),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS review_flags(document_id TEXT PRIMARY KEY REFERENCES documents(id),company_id TEXT NOT NULL REFERENCES companies(id),note TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS review_snippets(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),supplier_id TEXT NOT NULL DEFAULT '',shortcut TEXT NOT NULL,content TEXT NOT NULL,UNIQUE(company_id,supplier_id,shortcut));
    CREATE TABLE IF NOT EXISTS review_sessions(company_id TEXT PRIMARY KEY REFERENCES companies(id),document_id TEXT NOT NULL REFERENCES documents(id),stamp INTEGER NOT NULL,value TEXT NOT NULL);
    ''')

    def last_approval(c,company_id):
        row=c.execute("SELECT d.id FROM audit a JOIN documents d ON d.id=a.document_id WHERE a.company_id=? AND a.action='approved' AND d.status='approved' ORDER BY a.id DESC LIMIT 1",(company_id,)).fetchone()
        return s['describe'](c,document(c,row[0],company_id)) if row else None

    @app.get('/api/companies/{company_id}/review-workspace')
    def workspace(company_id:str):
        with db() as c:
            company(c,company_id);prefs=c.execute('SELECT value FROM review_preferences WHERE company_id=?',(company_id,)).fetchone();session=c.execute('SELECT value,document_id FROM review_sessions WHERE company_id=?',(company_id,)).fetchone()
            return {'company_id':company_id,'preferences':json.loads(prefs[0]) if prefs else DEFAULTS,'flags':[dict(r) for r in c.execute('SELECT * FROM review_flags WHERE company_id=?',(company_id,))],'snippets':[dict(r) for r in c.execute('SELECT * FROM review_snippets WHERE company_id=? ORDER BY shortcut',(company_id,))],'session':{'document_id':session['document_id'],**json.loads(session['value'])} if session else None,'last_approval':last_approval(c,company_id)}

    @app.put('/api/companies/{company_id}/review-preferences')
    def preferences(company_id:str,payload:dict=Body(...)):
        value={**DEFAULTS,**payload};order=value['field_order'];keys=value['keys']
        if value['source_width'] not in (40,50,60) or isinstance(value['font_size'],bool) or not isinstance(value['font_size'],int) or not 12<=value['font_size']<=20 or not isinstance(order,list) or len(order)!=len(set(order)) or any(k not in ORDER_FIELDS for k in order):fail('העדפות תצוגה לא תקינות')
        if not isinstance(keys,dict) or set(keys)!=set(KEYS) or any(keys[k] not in KEYS[k] for k in KEYS):fail('קיצורי מקלדת לא נתמכים')
        value={k:value[k] for k in DEFAULTS}
        with db() as c:
            company(c,company_id);c.execute('INSERT INTO review_preferences VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET value=excluded.value',(company_id,L.encode(value)))
        return value

    @app.put('/api/companies/{company_id}/review-session')
    def session(company_id:str,payload:dict=Body(...)):
        ident=payload.get('document_id');stamp=payload.get('stamp');value={k:payload.get(k,v) for k,v in {'field':'','editor_scroll':0,'source_scroll':0,'zoom':1,'tab':'fields'}.items()}
        if not isinstance(stamp,int) or isinstance(stamp,bool) or not 0<stamp<9_000_000_000_000_000 or value['field'] not in ORDER_FIELDS+[''] or value['tab'] not in ('fields','ocr') or any(not isinstance(value[k],(int,float)) or not 0<=value[k]<=10_000_000 for k in ('editor_scroll','source_scroll')) or not isinstance(value['zoom'],(int,float)) or not 1<=value['zoom']<=3:fail('מיקום עבודה לא תקין')
        with db() as c:
            document(c,ident,company_id);c.execute('INSERT INTO review_sessions VALUES(?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET document_id=excluded.document_id,stamp=excluded.stamp,value=excluded.value WHERE excluded.stamp>review_sessions.stamp',(company_id,ident,stamp,L.encode(value)))
        return {'ok':True}

    @app.put('/api/documents/{doc_id}/review-flag')
    def flag(doc_id:str,payload:dict=Body(...)):
        if not isinstance(payload.get('marked'),bool) or not isinstance(payload.get('note',''),str) or len(payload.get('note',''))>2000:fail('סימון עבודה לא תקין')
        with db() as c:
            d=document(c,doc_id)
            if payload['marked']:c.execute('INSERT INTO review_flags VALUES(?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET note=excluded.note,created=excluded.created',(doc_id,d['company_id'],payload.get('note',''),L.now()))
            else:c.execute('DELETE FROM review_flags WHERE document_id=?',(doc_id,))
            audit(c,d['company_id'],doc_id,'review_flag',payload)
        return {'ok':True}

    @app.post('/api/companies/{company_id}/review-snippets')
    def snippet(company_id:str,payload:dict=Body(...)):
        shortcut=payload.get('shortcut');content=payload.get('content');supplier=payload.get('supplier_id') or ''
        if not isinstance(shortcut,str) or not re.fullmatch('[A-Za-z0-9א-ת_]{1,40}',shortcut) or not isinstance(content,str) or not 1<=len(content.strip())<=2000:fail('יש להזין קיצור ללא רווחים וטקסט להשלמה')
        with db() as c:
            company(c,company_id)
            if supplier and not c.execute('SELECT 1 FROM suppliers WHERE id=? AND company_id=?',(supplier,company_id)).fetchone():fail('הספק אינו שייך לחברה')
            c.execute('INSERT INTO review_snippets VALUES(?,?,?,?,?) ON CONFLICT(company_id,supplier_id,shortcut) DO UPDATE SET content=excluded.content',(L.uid(),company_id,supplier,shortcut,content.strip()))
        return {'ok':True}

    @app.delete('/api/companies/{company_id}/review-snippets/{snippet_id}')
    def delete_snippet(company_id:str,snippet_id:str):
        with db() as c:
            company(c,company_id);c.execute('DELETE FROM review_snippets WHERE id=? AND company_id=?',(snippet_id,company_id))
        return {'ok':True}

    @app.get('/api/documents/{doc_id}/review-source')
    def source(doc_id:str):
        with db() as c:
            d=document(c,doc_id);tokens=advanced.meta(c,doc_id).get('tokens',[])
            return {'document_id':d['id'],'page':d['page'],'tokens':tokens,'field_sources':advanced.meta(c,doc_id).get('field_sources',{})}

    @app.post('/api/review-calculator')
    def calculator(payload:dict=Body(...)):
        try:
            amount=money(payload.get('amount',''));rate=Decimal(str(payload.get('rate','18')))
            if not rate.is_finite() or not 0<=rate<=100:raise ValueError()
            mode=payload.get('mode');q=lambda v:v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            if mode=='net':net=amount;vat=q(net*rate/100);total=net+vat
            elif mode=='total':total=amount;net=q(total/(1+rate/100));vat=total-net
            else:raise ValueError()
            return {k:format(v,'.2f') for k,v in {'net':net,'vat':vat,'total':total}.items()}
        except (ValueError,InvalidOperation,TypeError):fail('יש להזין סכום תקין ושיעור מע״מ בין 0 ל־100')

    @app.post('/api/companies/{company_id}/review-undo-approval')
    def undo(company_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('אשרו החזרת המסמך לבדיקה')
        with db() as c:
            d=document(c,payload.get('document_id'),company_id)
            if d['status']!='approved' or d['revision']!=payload.get('revision'):fail('ניתן להחזיר לבדיקה רק אישור עדכני שטרם יוצא',409)
            c.execute("UPDATE documents SET status='review',revision=revision+1 WHERE id=?",(d['id'],));audit(c,company_id,d['id'],'approval_returned_to_review',{'reason':str(payload.get('note','בדיקה חוזרת'))[:2000]})
            return s['describe'](c,document(c,d['id'],company_id))

    def bulk_rows(c,company_id,payload):
        company(c,company_id);ids=payload.get('ids');fields=payload.get('fields');only_empty=payload.get('only_empty',False)
        if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or not isinstance(fields,dict) or not fields or any(k not in BULK_FIELDS or not isinstance(v,str) or len(v)>2000 for k,v in fields.items()) or not isinstance(only_empty,bool):fail('בחירת מסמכים או שדות לתיקון מרוכז אינה תקינה')
        if 'purpose' in fields and fields['purpose'] not in ('supplier_invoice','journal','reconciliation','archive'):fail('יעד מסמך לא נתמך')
        rows=[]
        for ident in ids:
            d=document(c,ident,company_id);changes={k:v.strip() for k,v in fields.items() if d['fields'].get(k,'')!=v.strip() and (not only_empty or not d['fields'].get(k,''))}
            rows.append({'id':ident,'revision':d['revision'],'number':d['fields'].get('number',''),'supplier':(s['supplier_for'](c,d) or {}).get('name',''),'status':d['status'],'editable':d['status'] in ('review','rejected','approved'),'before':{k:d['fields'].get(k,'') for k in changes},'after':changes})
        return rows

    @app.post('/api/companies/{company_id}/review-bulk-preview')
    def bulk_preview(company_id:str,payload:dict=Body(...)):
        with db() as c:return {'rows':bulk_rows(c,company_id,payload)}

    @app.post('/api/companies/{company_id}/review-bulk-apply')
    def bulk_apply(company_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('אשרו את התצוגה המקדימה לפני התיקון')
        with db() as c:
            rows=bulk_rows(c,company_id,payload);revisions=payload.get('revisions',{})
            if not isinstance(revisions,dict) or any(revisions.get(r['id'])!=r['revision'] or not r['editable'] for r in rows):fail('מסמך השתנה או ננעל מאז התצוגה המקדימה; הציגו את התיקון מחדש',409)
            changed=0
            for row in rows:
                if not row['after']:continue
                d=document(c,row['id'],company_id);fields={**d['fields'],**row['after']}
                c.execute("UPDATE documents SET fields=?,status='review',revision=revision+1 WHERE id=?",(L.encode(fields),d['id']))
                for key,value in row['after'].items():c.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?,?,?,?,?)',(L.uid(),company_id,d['id'],d['revision']+1,key,d['fields'].get(key,''),value,L.encode({'reason':'other','explanation':'תיקון מרוכז שאושר בתצוגה מקדימה'}),0,L.now()))
                audit(c,company_id,d['id'],'bulk_review_correction',{'before':row['before'],'after':row['after'],'revision':d['revision']+1});changed+=1
            return {'changed':changed,'approved':0}
