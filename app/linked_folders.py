"""Company-scoped folder links and explicit background scans of local originals."""
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid
from fastapi import Body,HTTPException,UploadFile
from .learning import now

TYPES={'.pdf','.png','.jpg','.jpeg','.tif','.tiff','.webp'}


class Folders:
    def __init__(self,app,s):
        self.app=app;self.s=s;self.db=s['db'];self.store=s['source_store'];self.lock=threading.Lock();self.job=None;self.stop=threading.Event();self.worker=None

    def root(self,value):
        if not isinstance(value,str) or not value.strip():raise HTTPException(400,'בחרו תיקייה או הדביקו את הנתיב המלא שלה')
        path=Path(value.strip().strip('"')).expanduser()
        if not path.is_absolute():raise HTTPException(400,'יש להזין נתיב מלא לתיקייה')
        path=path.resolve()
        if not path.is_dir():raise HTTPException(400,'התיקייה אינה זמינה במחשב שמפעיל את התוכנה')
        if path.is_relative_to(self.s['data']):raise HTTPException(400,'בחרו תיקיית מסמכים מחוץ לתיקיית הנתונים של התוכנה')
        return path

    def folder(self,c,company_id,folder_id):
        self.s['company'](c,company_id)
        row=c.execute('SELECT * FROM linked_folders WHERE id=? AND company_id=?',(folder_id,company_id)).fetchone()
        if not row:raise HTTPException(404,'התיקייה אינה נמצאת בחברה זו')
        return dict(row)

    def status(self,company_id):
        with self.lock:return dict(self.job) if self.job and self.job['company_id']==company_id else None

    def start(self,company_id,folder_id):
        with self.lock:
            if self.worker and self.worker.is_alive():raise HTTPException(409,'סריקת תיקייה כבר פועלת. המתינו לסיומה או עצרו אותה')
            with self.db() as c:folder=self.folder(c,company_id,folder_id)
            if not folder['active']:raise HTTPException(409,'חברו מחדש את התיקייה לפני סריקה')
            self.stop.clear();self.job={'id':uuid.uuid4().hex,'company_id':company_id,'folder_id':folder_id,'state':'running','seen':0,'imported':0,'linked_existing':0,'duplicates':0,'ignored':0,'changed':0,'errors':[],'error_count':0,'current':''}
            self.worker=threading.Thread(target=self.scan,args=(folder,),daemon=True,name='linked-folder-scan');self.worker.start()
            return dict(self.job)

    def update(self,**values):
        with self.lock:self.job.update(values)

    def increment(self,key):
        with self.lock:self.job[key]+=1

    def error(self,path,message):
        with self.lock:
            self.job['error_count']+=1
            if len(self.job['errors'])<100:self.job['errors'].append({'file':str(path),'message':message})

    def scan(self,folder):
        root=Path(folder['path']);company_id=folder['company_id'];folder_id=folder['id']
        try:
            if not root.is_dir():raise ValueError('התיקייה אינה זמינה. חברו את הכונן או בחרו את מיקומה החדש.')
            for directory,dirs,names in os.walk(root,followlinks=False,onerror=lambda exc:self.error(Path(exc.filename or root).name,'אין הרשאה לקרוא את התיקייה')):
                dirs[:]=sorted(d for d in dirs if not (Path(directory)/d).is_symlink() and not (Path(directory)/d).resolve().is_relative_to(self.s['data']))
                for name in sorted(names):
                    if self.stop.is_set():break
                    path=Path(directory)/name
                    if path.suffix.lower() not in TYPES or path.is_symlink():continue
                    resolved=path.resolve()
                    if not resolved.is_relative_to(root) or resolved.is_relative_to(self.s['data']):continue
                    relative=path.relative_to(root).as_posix();self.increment('seen');self.update(current=relative)
                    try:
                        with path.open('rb') as stream:content=stream.read(25_000_001)
                        if not content or len(content)>25_000_000:raise ValueError('הקובץ ריק או גדול מ־25MB')
                        sha=hashlib.sha256(content).hexdigest()
                        with self.db() as c:
                            ignored=c.execute('SELECT 1 FROM folder_ignored WHERE folder_id=? AND sha=?',(folder_id,sha)).fetchone()
                            existing=c.execute('SELECT * FROM sources WHERE company_id=? AND sha=?',(company_id,sha)).fetchone()
                            existing=dict(existing) if existing else None
                            old_link=self.store.link(c,existing['id']) if existing else None
                            pages=[r[0] for r in c.execute('SELECT DISTINCT page FROM documents WHERE source_id=?',(existing['id'],))] if existing else []
                            changed=bool(c.execute('SELECT 1 FROM source_links l JOIN sources s ON s.id=l.source_id WHERE l.folder_id=? AND l.relative_path=? AND s.sha<>?',(folder_id,relative,sha)).fetchone())
                        if ignored:self.increment('ignored');continue
                        if existing:
                            if old_link:
                                try:
                                    with self.db() as c:self.store.content(c,existing['id'])
                                except HTTPException:
                                    with self.db() as c:c.execute('UPDATE source_links SET folder_id=?,relative_path=? WHERE source_id=?',(folder_id,relative,existing['id']))
                                    self.increment('linked_existing');continue
                            else:
                                recipes=self.store.legacy_recipes(existing['id'],content,pages)
                                if recipes is not None:
                                    with self.db() as c:self.store.add(c,existing['id'],folder_id,relative,recipes);self.store.content(c,existing['id'])
                                    if not self.store.remove_owned_original(existing['id']):self.error(relative,'החיבור נשמר, אך עותק המקור הישן פתוח; ניתן להסירו בניקוי המטמון הבא')
                                    self.increment('linked_existing');continue
                            self.increment('duplicates');continue
                        with path.open('rb') as stream:
                            result=self.s['ingest'](company_id,UploadFile(filename=name,file=stream),linked={'folder_id':folder_id,'relative_path':relative},cancelled=self.stop.is_set)
                        self.increment('imported')
                        if changed:self.increment('changed')
                    except (OSError,ValueError,HTTPException) as exc:
                        if self.stop.is_set():break
                        self.error(relative,exc.detail if isinstance(exc,HTTPException) else str(exc)[:300])
                    finally:self.store.trim()
                if self.stop.is_set():break
            self.update(state='cancelled' if self.stop.is_set() else 'completed',current='')
        except Exception as exc:self.update(state='failed',message=str(exc)[:500])

    def close(self):
        self.stop.set()
        if self.worker:self.worker.join(timeout=10)


def register(app,s):
    manager=Folders(app,s);app.state.linked_folders=manager;db=s['db'];store=s['source_store']
    app.router.add_event_handler('shutdown',manager.close)

    @app.get('/api/companies/{company_id}/folders')
    def list_folders(company_id:str):
        with db() as c:
            s['company'](c,company_id)
            rows=[{**dict(r),'available':Path(r['path']).is_dir(),'sources':c.execute('SELECT COUNT(*) FROM source_links WHERE folder_id=?',(r['id'],)).fetchone()[0],'ignored':c.execute('SELECT COUNT(*) FROM folder_ignored WHERE folder_id=?',(r['id'],)).fetchone()[0]} for r in c.execute('SELECT * FROM linked_folders WHERE company_id=? ORDER BY created',(company_id,))]
            cache=sum(p.stat().st_size for p in store.cache_files(c,company_id) if p.exists())
        return {'folders':rows,'job':manager.status(company_id),'cache_bytes':cache,'cache_limit':store.limit}

    @app.get('/api/companies/{company_id}/folders/job')
    def scan_job(company_id:str):return {'job':manager.status(company_id)}

    @app.post('/api/folders/choose')
    def choose():
        picker=getattr(app.state,'folder_picker',None)
        if not picker:raise HTTPException(409,'בחירת תיקייה זמינה בחלון Windows העצמאי. אפשר להדביק את הנתיב המלא בשדה התיקייה.')
        path=picker();return {'path':str(path) if path else None}

    @app.post('/api/companies/{company_id}/folders')
    def connect(company_id:str,payload:dict=Body(...)):
        root=manager.root(payload.get('path'));path=os.path.normcase(str(root))
        with db() as c:
            s['company'](c,company_id);row=c.execute('SELECT * FROM linked_folders WHERE company_id=? AND path=?',(company_id,path)).fetchone()
            if row:
                c.execute('UPDATE linked_folders SET active=1 WHERE id=?',(row['id'],));return {**dict(row),'active':1}
            ident=uuid.uuid4().hex;c.execute('INSERT INTO linked_folders(id,company_id,path,created) VALUES(?,?,?,?)',(ident,company_id,path,now()));s['audit'](c,company_id,None,'folder_connected',{'folder_id':ident,'name':root.name,'originals_copied':False})
        return {'id':ident,'company_id':company_id,'path':path}

    @app.post('/api/companies/{company_id}/folders/{folder_id}/scan')
    def scan(company_id:str,folder_id:str):return manager.start(company_id,folder_id)

    @app.post('/api/companies/{company_id}/folders/cancel')
    def cancel(company_id:str):
        if not manager.status(company_id):raise HTTPException(404,'אין סריקה פעילה בחברה זו')
        manager.stop.set();return {'ok':True}

    @app.post('/api/companies/{company_id}/folders/{folder_id}/path')
    def relink(company_id:str,folder_id:str,payload:dict=Body(...)):
        root=manager.root(payload.get('path'))
        if manager.worker and manager.worker.is_alive():raise HTTPException(409,'עצרו את הסריקה לפני שינוי נתיב התיקייה')
        with db() as c:
            manager.folder(c,company_id,folder_id);c.execute('UPDATE linked_folders SET path=? WHERE id=?',(os.path.normcase(str(root)),folder_id))
        return {'ok':True}

    @app.post('/api/companies/{company_id}/folders/cache/clear')
    def clear(company_id:str):
        with db() as c:
            s['company'](c,company_id)
            linked=[r[0] for r in c.execute('SELECT l.source_id FROM source_links l JOIN sources s ON s.id=l.source_id WHERE s.company_id=?',(company_id,))]
        for ident in linked:store.remove_owned_original(ident)
        return store.trim(clear=True,company_id=company_id)

    @app.post('/api/companies/{company_id}/folders/{folder_id}/active')
    def active(company_id:str,folder_id:str,payload:dict=Body(...)):
        if not isinstance(payload.get('active'),bool):raise HTTPException(400,'מצב חיבור לא תקין')
        if manager.status(company_id) and manager.status(company_id)['folder_id']==folder_id:manager.stop.set()
        with db() as c:
            manager.folder(c,company_id,folder_id);c.execute('UPDATE linked_folders SET active=? WHERE id=?',(int(payload['active']),folder_id))
        return {'ok':True}

    @app.post('/api/companies/{company_id}/folders/{folder_id}/reset-ignored')
    def reset_ignored(company_id:str,folder_id:str):
        with db() as c:
            manager.folder(c,company_id,folder_id);c.execute('DELETE FROM folder_ignored WHERE folder_id=?',(folder_id,))
        return {'ok':True}

    @app.get('/api/documents/{doc_id}/source-status')
    def source_status(doc_id:str):
        with db() as c:
            d=s['document'](c,doc_id);status=store.status(c,d['source_id'])
            if not status:return {'linked':False,'available':True}
            try:store.content(c,d['source_id']);return {**status,'available':True}
            except HTTPException as exc:return {**status,'available':False,'message':exc.detail}
