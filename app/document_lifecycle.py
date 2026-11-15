"""Previewed permanent removal of whole source groups, retaining personal learning."""
import hashlib
import json
import re
import shutil
import uuid
from fastapi import Body
from . import learning as L


def register(app,s):
    db=s['db'];data=s['data'];fail=L.fail;company=s['company']
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS deletion_cleanup(id TEXT PRIMARY KEY,company_id TEXT NOT NULL,sources TEXT NOT NULL)')

    def cleanup(company_id=None):
        pending=0
        with db() as c:
            for job in c.execute('SELECT * FROM deletion_cleanup'+(' WHERE company_id=?' if company_id else ''),(company_id,) if company_id else ()).fetchall():
                try:
                    for ident in json.loads(job['sources']):
                        if not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Invalid source directory')
                        folder=data/'files'/ident
                        if folder.is_symlink():raise ValueError('Refusing linked directory')
                        if folder.exists():shutil.rmtree(folder)
                    c.execute('DELETE FROM deletion_cleanup WHERE id=?',(job['id'],))
                except (OSError,ValueError):pending+=1
        return pending

    # Retry files held open during an earlier deletion; never touch external folders.
    cleanup()

    def plan(c,company_id,payload):
        company(c,company_id);selected=payload.get('ids')
        if not isinstance(selected,list) or not 1<=len(selected)<=1000 or any(not isinstance(i,str) for i in selected) or len(set(selected))!=len(selected):fail('בחרו מסמכים למחיקה')
        rows={r['id']:dict(r) for r in c.execute('SELECT d.*,s.name AS source_name FROM documents d JOIN sources s ON s.id=d.source_id WHERE d.company_id=?',(company_id,))}
        if any(i not in rows for i in selected):fail('מסמך אינו נמצא בחברה שנבחרה',404)
        sources={rows[i]['source_id'] for i in selected}
        links=[tuple(r) for r in c.execute('SELECT primary_id,child_id FROM document_links') if r[0] in rows and r[1] in rows]
        while True:
            ids={i for i,r in rows.items() if r['source_id'] in sources}
            connected={rows[b]['source_id'] for a,b in links if a in ids}|{rows[a]['source_id'] for a,b in links if b in ids}
            if connected<=sources:break
            sources|=connected
        docs=sorted((r for r in rows.values() if r['source_id'] in sources),key=lambda r:(r['source_id'],r['page'],r['id']))
        if len(docs)>10000:fail('בחרו פחות קבצים בכל מחיקה')
        ids=[r['id'] for r in docs];blocked=[]
        for d in docs:
            if d['status'] in ('exported','received') or c.execute('SELECT 1 FROM batch_items WHERE document_id=?',(d['id'],)).fetchone():blocked.append(d['id'])
        training=bool(c.execute("SELECT 1 FROM learning_jobs WHERE company_id=? AND state IN ('queued','running')",(company_id,)).fetchone())
        signature=[(d['id'],d['source_id'],d['revision'],d['status']) for d in docs]
        token=hashlib.sha256(L.encode(signature).encode()).hexdigest()
        examples=sum(c.execute('SELECT COUNT(*) FROM learning_examples WHERE document_id=?',(i,)).fetchone()[0] for i in ids)
        linked=[dict(r) for r in c.execute('SELECT * FROM source_links') if r['source_id'] in sources]
        return {'token':token,'ids':ids,'sources':sorted(sources),'linked_sources':len(linked),'selected_count':len(selected),'documents':[{'id':d['id'],'name':d['source_name'],'page':d['page'],'number':json.loads(d['fields']).get('number',''),'status':d['status']} for d in docs],'blocked':blocked,'training_active':training,'can_delete':not blocked and not training,'retained_examples':examples}

    @app.post('/api/companies/{company_id}/documents/delete-preview')
    def preview(company_id:str,payload:dict=Body(...)):
        with db() as c:return plan(c,company_id,payload)

    @app.post('/api/companies/{company_id}/documents/delete-permanently')
    def remove(company_id:str,payload:dict=Body(...)):
        if payload.get('confirmed') is not True:fail('נדרש אישור מפורש למחיקה לצמיתות')
        with db() as c:
            value=plan(c,company_id,payload)
            if payload.get('token')!=value['token']:fail('המסמכים השתנו. פתחו מחדש את התצוגה המקדימה למחיקה',409)
            if value['blocked']:fail('בקבוצה יש מסמכים שתועדו במנת יצוא. הם נשמרים כחלק מתיעוד היצוא והקליטה',409)
            if value['training_active']:fail('סיימו או עצרו אימון אישי לפני מחיקת המסמכים',409)
            for ident in value['ids']:
                for row in c.execute('SELECT * FROM learning_examples WHERE document_id=? OR source_document_id=?',(ident,ident)).fetchall():
                    d=c.execute('SELECT fields,supplier_id FROM documents WHERE id=?',(row['document_id'],)).fetchone()
                    fields=json.loads(d['fields']);supplier=c.execute('SELECT tax_id,code FROM suppliers WHERE id=?',(d['supplier_id'],)).fetchone() if d['supplier_id'] else None
                    supplier_key=(supplier['tax_id'] or supplier['code']) if supplier else fields.get('supplier_tax_id','')
                    owner=company(c,company_id)['tax_id']
                    invoice_key=hashlib.sha256(L.encode((owner,supplier_key,fields.get('kind'),fields.get('number'))).encode()).hexdigest() if fields.get('number') else 'source:'+row['source_group']
                    prior=c.execute('SELECT label FROM learning_shared_examples WHERE company_id=? AND crop_sha=?',(company_id,row['crop_sha'])).fetchone()
                    c.execute('INSERT OR IGNORE INTO learning_shared_examples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(row['id'],company_id,row['source_group'],invoice_key,row['crop_sha'],row['image'],row['label'],row['writer_id'],row['reason'],row['explanation'],row['role'],row['approved'],L.encode({'source':'retained-after-document-deletion'}),row['created']))
                    if prior and prior[0]!=row['label']:
                        c.execute('UPDATE learning_shared_examples SET approved=0 WHERE company_id=? AND crop_sha=?',(company_id,row['crop_sha']))
                        c.execute('UPDATE learning_models SET active=0 WHERE company_id=?',(company_id,))
                    c.execute('DELETE FROM learning_engine_observations WHERE example_id=?',(row['id'],))
                    c.execute('DELETE FROM learning_examples WHERE id=?',(row['id'],))
                for row in c.execute('SELECT * FROM learning_templates WHERE source_document_id=?',(ident,)).fetchall():
                    c.execute('INSERT OR IGNORE INTO learning_shared_templates VALUES(?,?,?,?,?,?,?)',(row['id'],company_id,row['supplier_id'],row['kind'],row['field'],row['region'],L.encode({'source':'retained-after-document-deletion'})))
                c.execute('DELETE FROM learning_templates WHERE source_document_id=?',(ident,))
                c.execute('UPDATE learning_events SET document_id=NULL WHERE document_id=?',(ident,))
                c.execute('DELETE FROM check_corrections WHERE document_id=? OR source_document_id=?',(ident,ident))
                c.execute('DELETE FROM document_links WHERE primary_id=? OR child_id=?',(ident,ident))
                for table in ('document_meta','recognition_runs','evaluations','review_flags','review_sessions'):
                    c.execute(f'DELETE FROM {table} WHERE document_id=?',(ident,))
            # Split-parent references must be detached before removing the source group.
            for ident in value['ids']:c.execute('UPDATE documents SET parent_id=NULL WHERE parent_id=?',(ident,))
            for ident in value['ids']:c.execute('DELETE FROM documents WHERE id=?',(ident,))
            for ident in value['sources']:
                linked=c.execute('SELECT l.folder_id,s.sha FROM source_links l JOIN sources s ON s.id=l.source_id WHERE l.source_id=?',(ident,)).fetchone()
                if linked:c.execute('INSERT OR IGNORE INTO folder_ignored VALUES(?,?)',(linked['folder_id'],linked['sha']))
                c.execute('DELETE FROM source_links WHERE source_id=?',(ident,))
                c.execute('DELETE FROM sources WHERE id=?',(ident,))
            c.execute('INSERT INTO deletion_cleanup VALUES(?,?,?)',(uuid.uuid4().hex,company_id,L.encode(value['sources'])))
            s['audit'](c,company_id,None,'documents_deleted_permanently',{'documents':len(value['ids']),'source_files':len(value['sources']),'personal_learning_retained':True})
        pending=cleanup(company_id)
        return {'deleted':len(value['ids']),'sources':len(value['sources']),'cleanup_pending':bool(pending)}
