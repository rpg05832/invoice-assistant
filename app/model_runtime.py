"""Download generic public weights and run one owned loopback model process.

This component never receives invoices, corrections or personal model weights.
"""
import hashlib
import json
import os
import platform
import secrets
import subprocess
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from fastapi import Body,HTTPException
from .paths import assets
from scripts.setup_vision import PROFILES,RUNTIMES
from scripts.setup_tesseract_rivok import MODELS as RIVOK,REVISION as RIVOK_REVISION

BEST={'heb':'dbaa827aea6bc21215638447f17783a1004987c2d0bf5573d111fee397abdae5','eng':'8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba'}
LABELS={'rivok':'heb_rivok · עברית, אנגלית וספרות','best':'Tesseract Best · עברית ואנגלית','glm-ocr':'GLM-OCR · השוואת ספרות','qwen35-08':'Qwen3.5 0.8B · השוואת ספרות','paddle-vl16':'PaddleOCR-VL 1.6 · השוואת ספרות','lighton':'LightOnOCR 2 · השוואת ספרות','digits':'Qwen3-VL 2B · ספרות','multilingual':'Qwen3-VL 4B · הצעות מלל והקשר'}


def downloads(profile,root):
    if profile=='rivok':return [(f'https://huggingface.co/Rivok/tesseract-heb-rivok/resolve/{RIVOK_REVISION}/tessdata/{name}.traineddata',root/'tessdata_rivok'/f'{name}.traineddata',sha) for name,sha in RIVOK.items()]
    if profile=='best':return [(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/{name}.traineddata',root/'tessdata_best'/f'{name}.traineddata',sha) for name,sha in BEST.items()]
    spec=PROFILES[profile]
    return [(f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}?download=true",root/'models'/name,sha) for name,sha in spec.get('models',spec.get('files',{})).items()]


def local_headers(port):
    path=assets()/'runtime'/f'vision-{port}.key'
    return {'Authorization':'Bearer '+path.read_text(encoding='ascii').strip()} if path.is_file() else {}


class Runtime:
    def __init__(self):
        self.lock=threading.Lock();self.job=None;self.process=None;self.active=None;self.log=None;self.closed=threading.Event()

    def binary(self):
        name='llama-server.exe' if os.name=='nt' else 'llama-server'
        return next(iter((assets()/'runtime/vision').rglob(name)),None)

    def status(self):
        root=assets();process=self.process;running=self.active if process and process.poll() is None else None
        return {'profiles':[{'id':key,'label':label,'installed':all(path.is_file() for _,path,_ in downloads(key,root)) and (key in ('best','rivok') or self.binary() is not None),'running':key==running,'digits_only':key not in ('best','rivok','multilingual')} for key,label in LABELS.items()],'job':dict(self.job) if self.job else None,'running':running,'one_model_at_a_time':True}

    def install(self,profile):
        if profile not in LABELS:raise HTTPException(400,'מודל לא נתמך')
        with self.lock:
            if self.job and self.job['state']=='running':raise HTTPException(409,'הורדת מודל כבר פועלת')
            self.job={'profile':profile,'state':'running','progress':0,'file':''}
            threading.Thread(target=self._install,args=(profile,),daemon=True,name='public-model-download').start()
        return dict(self.job)

    def _download(self,url,path,sha,done,total):
        if path.is_file():
            with path.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()==sha:return
        path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix(path.suffix+'.download')
        self.job.update(file=path.name,progress=round(done*100/total))
        try:
            with urllib.request.urlopen(url,timeout=90) as response,temporary.open('wb') as output:
                size=int(response.headers.get('Content-Length',0));received=0;digest=hashlib.sha256()
                while True:
                    if self.closed.is_set():raise InterruptedError('ההורדה נעצרה עם סגירת התוכנה')
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    output.write(chunk);digest.update(chunk);received+=len(chunk)
                    if size:self.job['progress']=min(99,round((done+min(1,received/size))*100/total))
            if digest.hexdigest()!=sha:raise ValueError('אימות קובץ המודל נכשל')
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)

    def _install(self,profile):
        try:
            items=downloads(profile,assets())
            if profile in PROFILES:
                if platform.machine().lower() not in ('x86_64','amd64') or platform.system() not in RUNTIMES:raise ValueError('מנוע ראייה זה מיועד למחשב x64 עם Windows או Linux')
                filename,sha=RUNTIMES[platform.system()];archive=assets()/'runtime'/filename
                items.append((f'https://github.com/ggml-org/llama.cpp/releases/download/b12322/{filename}',archive,sha))
            for index,(url,path,sha) in enumerate(items):self._download(url,path,sha,index,len(items))
            if profile in PROFILES:
                destination=assets()/'runtime/vision';marker=destination/'.verified-runtime'
                if not marker.is_file() or marker.read_text(encoding='ascii')!=sha or self.binary() is None:
                    destination.mkdir(parents=True,exist_ok=True)
                    if self.process and self.process.poll() is None:raise ValueError('עצרו את מנוע הראייה לפני עדכון קובצי ההפעלה')
                    if archive.suffix=='.zip':
                        with zipfile.ZipFile(archive) as z:
                            for member in z.infolist():
                                if not (destination/member.filename).resolve().is_relative_to(destination.resolve()):raise ValueError('נתיב ארכיון לא תקין')
                            z.extractall(destination)
                    else:
                        with tarfile.open(archive) as t:t.extractall(destination,filter='data')
                    marker.write_text(sha,encoding='ascii')
            self.job.update(state='completed',progress=100)
        except Exception as exc:self.job.update(state='failed',message=str(exc)[:500])

    def start(self,profile):
        if profile not in PROFILES:raise HTTPException(400,'מודל זה משמש את Tesseract ואינו דורש הפעלת מנוע נפרד')
        with self.lock:
            if self.job and self.job['state']=='running':raise HTTPException(409,'המתינו לסיום הורדת קובצי המודל')
            root=assets();files=downloads(profile,root);binary=self.binary()
            if not binary or not all(path.is_file() for _,path,_ in files):raise HTTPException(409,'הורידו תחילה את המודל מתוך המסך')
            self._stop();port=8089 if profile=='multilingual' else 8090
            import socket
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',port))==0:raise HTTPException(409,'מנוע אחר כבר פועל בפורט המקומי. עצרו אותו במקום שהופעל ובחרו שוב הפעלה')
            key=root/'runtime'/f'vision-{port}.key';key.parent.mkdir(parents=True,exist_ok=True)
            if not key.is_file():
                with key.open('x',encoding='ascii') as f:f.write(secrets.token_urlsafe(32))
                if os.name!='nt':key.chmod(0o600)
            model=next(path for _,path,_ in files if 'mmproj' not in path.name);projector=next(path for _,path,_ in files if 'mmproj' in path.name)
            self.log=(root/'runtime/local-model.log').open('w',encoding='utf-8')
            command=[str(binary),'-m',str(model),'--mmproj',str(projector),'--alias',profile,'--host','127.0.0.1','--port',str(port),'--api-key-file',str(key),'-c','4096','-np','1','-t','2','-tb','2','-ngl','0','--no-mmproj-offload','--image-max-tokens','1024']
            self.process=subprocess.Popen(command,stdout=self.log,stderr=self.log,cwd=binary.parent,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0);self.active=profile
            for _ in range(240):
                if self.process.poll() is not None:self._stop();raise HTTPException(409,'המנוע לא עלה. ניתן לראות את יומן ההפעלה המקומי בתיקיית הנתונים')
                try:
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/health',headers=local_headers(port))
                    with urllib.request.urlopen(request,timeout=1):return {'running':profile,'port':port}
                except OSError:time.sleep(.25)
            self._stop();raise HTTPException(409,'ההפעלה לא הושלמה בזמן; נסו שוב מתוך המסך')

    def _stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.process=None;self.active=None
        if self.log:self.log.close();self.log=None

    def close(self):
        self.closed.set()
        with self.lock:self._stop()


def register(app):
    runtime=Runtime();app.state.model_runtime=runtime;app.router.add_event_handler('shutdown',runtime.close)
    @app.get('/api/local-models')
    def status():return runtime.status()
    @app.post('/api/local-models/{profile}/install')
    def install(profile:str):return runtime.install(profile)
    @app.post('/api/local-models/{profile}/start')
    def start(profile:str):return runtime.start(profile)
    @app.post('/api/local-models/stop')
    def stop():
        with runtime.lock:runtime._stop()
        return {'ok':True}
    return runtime
