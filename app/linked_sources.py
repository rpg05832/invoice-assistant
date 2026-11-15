"""External originals with verified content and regenerable, bounded page cache."""
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time
import tempfile
import shutil
import re
import uuid
import fitz
from PIL import Image,ImageOps
from fastapi import HTTPException
from . import vision


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS linked_folders(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),path TEXT NOT NULL,created TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,UNIQUE(company_id,path));
    CREATE TABLE IF NOT EXISTS source_links(source_id TEXT PRIMARY KEY REFERENCES sources(id),folder_id TEXT NOT NULL REFERENCES linked_folders(id),relative_path TEXT NOT NULL,recipes TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS folder_ignored(folder_id TEXT NOT NULL REFERENCES linked_folders(id),sha TEXT NOT NULL,PRIMARY KEY(folder_id,sha));
    ''')
    if 'active' not in {r[1] for r in c.execute('PRAGMA table_info(linked_folders)')}:c.execute('ALTER TABLE linked_folders ADD COLUMN active INTEGER NOT NULL DEFAULT 1')


class Sources:
    def __init__(self,data,db):
        self.data=data;self.db=db;self.leases={};self.lock=threading.Lock();self.limit=256*1024*1024
        with self.db() as c:
            for directory in (data/'files').iterdir():
                marker=directory/'pending-link.json'
                if not re.fullmatch('[a-f0-9]{32}',directory.name) or directory.is_symlink() or not marker.is_file():continue
                try:
                    if not c.execute('SELECT 1 FROM sources WHERE id=?',(directory.name,)).fetchone():shutil.rmtree(directory)
                    else:marker.unlink(missing_ok=True)
                except OSError:pass

    def link(self,c,source_id):
        row=c.execute('SELECT l.*,f.path AS folder_path,f.active,s.sha FROM source_links l JOIN linked_folders f ON f.id=l.folder_id JOIN sources s ON s.id=l.source_id WHERE l.source_id=?',(source_id,)).fetchone()
        return dict(row) if row else None

    def content(self,c,source_id):
        link=self.link(c,source_id)
        if not link:
            row=c.execute('SELECT path FROM sources WHERE id=?',(source_id,)).fetchone()
            return (self.data/row['path']).read_bytes()
        if not link['active']:raise HTTPException(409,'התיקייה מנותקת. חברו אותה מחדש במסך תיקיות מחוברות כדי להציג את המקור.')
        root=Path(link['folder_path']).resolve();path=(root/link['relative_path']).resolve()
        if not path.is_relative_to(root):raise HTTPException(409,'המקור חורג מהתיקייה המחוברת')
        try:
            with path.open('rb') as stream:content=stream.read(25_000_001)
        except OSError as exc:raise HTTPException(409,'קובץ המקור אינו זמין בתיקייה המחוברת. חברו מחדש את התיקייה או החזירו את הקובץ למקומו.') from exc
        if len(content)>25_000_000 or hashlib.sha256(content).hexdigest()!=link['sha']:raise HTTPException(409,'קובץ המקור השתנה מאז הקליטה. הנתונים הקודמים נשמרו; סרקו את התיקייה לקליטת הגרסה החדשה.')
        return content

    def add(self,c,source_id,folder_id,relative,recipes):
        c.execute('INSERT INTO source_links VALUES(?,?,?,?)',(source_id,folder_id,relative,json.dumps(recipes)))

    def render(self,content,page,recipe,target):
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name('render-'+uuid.uuid4().hex+'.png')
        try:
            if content.startswith(b'%PDF-'):
                with fitz.open(stream=content,filetype='pdf') as pdf:pdf[page-1].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(temp)
            else:
                with Image.open(io.BytesIO(content)) as image:
                    image.seek(page-1);ImageOps.exif_transpose(image.copy()).convert('RGB').save(temp,'PNG')
            if recipe.get('preprocess'):
                processed=temp.with_name('processed-'+uuid.uuid4().hex+'.png')
                try:vision.preprocess(temp,processed,transform=recipe['preprocess']);processed.replace(temp)
                finally:processed.unlink(missing_ok=True)
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)

    def image(self,c,d,original=False):
        target=self.data/('files/'+d['source_id']+f"/page-{d['page']}.png" if original else d['image'])
        link=self.link(c,d['source_id'])
        if not link:return target
        content=self.content(c,d['source_id'])
        if not target.exists():
            base=self.data/'files'/d['source_id']/f"page-{d['page']}.png"
            if not base.exists():self.render(content,d['page'],json.loads(link['recipes'])[str(d['page'])],base)
            if target!=base:
                with Image.open(base) as image:
                    image=image.copy();region=d.get('region')
                    if region:
                        x,y,w,h=[region[k] for k in ('x','y','w','h')]
                        image=image.crop((round(x*image.width),round(y*image.height),round((x+w)*image.width),round((y+h)*image.height))).rotate(-region.get('rotation',0),expand=True)
                    row=c.execute('SELECT value FROM document_meta WHERE document_id=?',(d['id'],)).fetchone()
                    angle=json.loads(row[0]).get('processing',{}).get('manual_rotation',0) if row else 0
                    if angle:image=image.rotate(-angle,expand=True)
                    image.save(target,'PNG')
        os.utime(target,None)
        return target

    def acquire(self,c,d,original=False):
        path=self.image(c,d,original)
        with self.lock:self.leases[str(path)]=self.leases.get(str(path),0)+1
        return path

    def release(self,path):
        with self.lock:
            remaining=self.leases.get(str(path),1)-1
            if remaining:self.leases[str(path)]=remaining
            else:self.leases.pop(str(path),None)
        self.trim()

    def cache_files(self,c,company_id=None):
        files=[]
        linked={row[0] for row in c.execute('SELECT l.source_id FROM source_links l JOIN sources s ON s.id=l.source_id'+(' WHERE s.company_id=?' if company_id else ''),(company_id,) if company_id else ())}
        for directory in (self.data/'files').iterdir():
            if directory.name not in linked or directory.is_symlink() or not directory.is_dir():continue
            files.extend(p for p in directory.glob('*.png') if p.is_file() and not p.is_symlink())
        return files

    def trim(self,clear=False,company_id=None):
        with self.db() as c:
            paths=self.cache_files(c,company_id);sizes={p:p.stat().st_size for p in paths if p.exists()};total=sum(sizes.values());removed=0
            with self.lock:
                for path in sorted(sizes,key=lambda p:p.stat().st_mtime if p.exists() else 0):
                    if total<= (0 if clear else self.limit):break
                    if self.leases.get(str(path)):continue
                    try:path.unlink(missing_ok=True);total-=sizes[path];removed+=sizes[path]
                    except OSError:pass
            for directory in {p.parent for p in paths}:
                try:directory.rmdir()
                except OSError:pass
            return {'bytes':total,'removed_bytes':removed,'limit':self.limit}

    def legacy_recipes(self,source_id,content,pages):
        """Only convert an old owned copy when its existing page pixels match."""
        recipes={}
        with tempfile.TemporaryDirectory(prefix='linked-verify-') as directory:
            for page in pages:
                existing=self.data/'files'/source_id/f'page-{page}.png'
                if not existing.is_file():return None
                raw=Path(directory)/'page.png';self.render(content,page,{},raw)
                def same(path):
                    with Image.open(existing) as old,Image.open(path) as new:return old.size==new.size and old.convert('RGB').tobytes()==new.convert('RGB').tobytes()
                if same(raw):recipes[str(page)]={'preprocess':None};continue
                prepared=Path(directory)/'prepared.png';recipe=vision.preprocess(raw,prepared)
                if not same(prepared):return None
                recipes[str(page)]={'preprocess':recipe}
        return recipes

    def remove_owned_original(self,source_id):
        path=self.data/'files'/source_id/'original'
        if path.is_symlink() or path.parent.is_symlink():return False
        try:path.unlink(missing_ok=True);return True
        except OSError:return False

    def status(self,c,source_id):
        link=self.link(c,source_id)
        if not link:return None
        root=Path(link['folder_path']).resolve();path=(root/link['relative_path']).resolve()
        return {'linked':True,'folder_id':link['folder_id'],'relative_path':link['relative_path'],'available':bool(link['active']) and path.is_relative_to(root) and path.is_file()}
