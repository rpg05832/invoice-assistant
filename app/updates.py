"""Explicit GitHub release updates; accounting data never enters a request."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime,timezone
from fastapi import Body,HTTPException
from .version import build_info,VERSION
from scripts.public_models import github_open

REPO='rpg05832/invoice-assistant'
FEED=f'https://api.github.com/repos/{REPO}/releases?per_page=30'

def version_key(value):
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)',value)
    return tuple(map(int,match.groups())) if match else None

def select_release(releases,current):
    found=[]
    for release in releases:
        if release.get('draft'):continue
        tag=release.get('tag_name','');match=re.fullmatch(r'windows-(\d+\.\d+\.\d+)-([a-f0-9]{8,40})',tag)
        if not match:continue
        version=match[1];commit=release.get('target_commitish','')
        if not re.fullmatch('[a-f0-9]{40}',commit) or not commit.startswith(match[2]):continue
        if version_key(version)<version_key(current['version']):continue
        if version==current['version'] and (commit==current.get('commit') or not current.get('built_at') or release.get('published_at','')<=current['built_at']):continue
        name=f'InvoiceAssistant-Setup-{version}-x64.exe';assets={a['name']:a for a in release.get('assets',[])}
        exe=assets.get(name);checksum=assets.get(name+'.sha256');prefix=f'https://github.com/{REPO}/releases/download/{tag}/'
        if not exe or not checksum or exe.get('browser_download_url')!=prefix+name or checksum.get('browser_download_url')!=prefix+name+'.sha256':continue
        if not 0<exe.get('size',0)<1_500_000_000:continue
        digest=exe.get('digest','')
        if not re.fullmatch('sha256:[a-f0-9]{64}',digest):continue
        found.append({'version':version,'commit':commit,'tag':tag,'name':name,'size':exe['size'],'sha256':digest[7:],'url':exe['browser_download_url'],'checksum_url':checksum['browser_download_url'],'published_at':release['published_at'],'notes':str(release.get('body',''))[:12000]})
    return max(found,key=lambda r:(version_key(r['version']),r['published_at'])) if found else None

def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def ps_literal(value):return "'"+str(value).replace("'","''")+"'"

def helper_script(installer,install_dir,pid,log):
    # Values are locally derived paths, never executable text from release notes.
    return f"""$env:PSModulePath=$PSHOME+'\\Modules'
$ErrorActionPreference='Stop'
try {{
 $p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue
 if($p) {{ Wait-Process -Id {int(pid)} -Timeout 60 -ErrorAction Stop }}
 $args=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR="'+{ps_literal(install_dir)}+'"'),('/LOG="'+{ps_literal(log)}+'"'))
 $setup=Start-Process -FilePath {ps_literal(installer)} -ArgumentList $args -Wait -PassThru
 if($setup.ExitCode -notin @(0,3010)) {{ throw 'Installer exit code '+$setup.ExitCode }}
 Start-Process -FilePath {ps_literal(Path(install_dir)/'InvoiceAssistant.exe')}
}} catch {{
 $_ | Out-File -FilePath {ps_literal(log)} -Append -Encoding utf8
 Add-Type -AssemblyName PresentationFramework
 [System.Windows.MessageBox]::Show('העדכון לא הושלם. אפשר לפתוח שוב את התוכנה ולנסות מחדש. פרטי התקלה נשמרו ביומן העדכון.','חשבונית')
}}
"""

class Updates:
    def __init__(self,app,data,db):
        self.app=app;self.data=data;self.db=db;self.lock=threading.Lock();self.release=None;self.job=None
        self.supported=os.name=='nt' and bool(getattr(sys,'frozen',False));self.closed=threading.Event()
        self.root=data.parent/'updates';self.current=build_info()
    def status(self):
        with self.lock:return {'current':self.current,'supported':self.supported,'release':dict(self.release) if self.release else None,'job':dict(self.job) if self.job else None,'source':'GitHub'}
    def check(self):
        request=urllib.request.Request(FEED,headers={'Accept':'application/vnd.github+json','User-Agent':'InvoiceAssistant/'+VERSION})
        try:
            with urllib.request.urlopen(request,timeout=20) as response:payload=response.read(2_000_001)
            if len(payload)>2_000_000:raise ValueError('Release index too large')
            releases=json.loads(payload)
            if not isinstance(releases,list):raise ValueError('Invalid release index')
            candidate=select_release(releases,self.current)
        except Exception as exc:raise HTTPException(409,'לא ניתן לבדוק עדכונים כרגע. בדקו את החיבור ל־GitHub ונסו שוב.') from exc
        with self.lock:
            if self.job and self.job['state']=='downloading':raise HTTPException(409,'הורדת עדכון כבר פועלת')
            if self.release!=candidate:self.job=None
            self.release=candidate
        return self.status()
    def download(self):
        if not self.supported:raise HTTPException(409,'התקנת עדכון מתוך התוכנה זמינה בחבילת Windows המותקנת')
        with self.lock:
            if not self.release:raise HTTPException(409,'בדקו תחילה אם קיימת גרסה חדשה')
            if self.job and self.job['state']=='downloading':raise HTTPException(409,'הורדת עדכון כבר פועלת')
            release=dict(self.release);self.job={'state':'downloading','progress':0}
        threading.Thread(target=self._download,args=(release,),daemon=True,name='application-update').start();return self.status()
    def path(self,release):return self.root/release['tag']/release['name']
    def _download(self,release):
        target=self.path(release);partial=target.with_suffix('.exe.download')
        try:
            with github_open(urllib.request.Request(release['checksum_url']),timeout=30) as response:checksum=response.read(4097).decode('utf-8').strip()
            if len(checksum)>4096 or checksum.split()!=[release['sha256'],release['name']]:raise ValueError('חתימת קובץ העדכון אינה תואמת לפרסום ב־GitHub')
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.is_file() or target.stat().st_size!=release['size'] or file_hash(target)!=release['sha256']:
                digest=hashlib.sha256();received=0
                with github_open(urllib.request.Request(release['url']),timeout=60) as response,partial.open('wb') as output:
                    while True:
                        if self.closed.is_set():raise InterruptedError('ההורדה נעצרה עם סגירת התוכנה')
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        received+=len(chunk)
                        if received>release['size']:raise ValueError('גודל קובץ העדכון שונה מהפרסום')
                        output.write(chunk);digest.update(chunk)
                        with self.lock:self.job['progress']=round(received/release['size']*100)
                if received!=release['size'] or digest.hexdigest()!=release['sha256']:raise ValueError('קובץ העדכון לא עבר אימות חתימה')
                partial.replace(target)
            with self.lock:self.job={'state':'ready','progress':100}
        except Exception as exc:
            partial.unlink(missing_ok=True)
            with self.lock:self.job={'state':'failed','progress':0,'message':str(exc)[:500]}
    def install(self,confirmed):
        if not confirmed:raise HTTPException(400,'אשרו התקנה והפעלה מחדש')
        with self.lock:
            if not self.supported or not self.release or not self.job or self.job['state']!='ready':raise HTTPException(409,'הורידו ואמתו תחילה עדכון מתאים למחשב')
            release=dict(self.release)
        close=getattr(self.app.state,'desktop_close',None)
        if not close:raise HTTPException(409,'פתחו את התוכנה בחלון Windows העצמאי כדי להתקין ולהפעיל מחדש')
        target=self.path(release)
        try:
            valid=file_hash(target)==release['sha256']
        except OSError:valid=False
        if not valid:
            with self.lock:self.job={'state':'failed','progress':0,'message':'קובץ העדכון חסר או השתנה; הורידו אותו מחדש'}
            raise HTTPException(409,'קובץ העדכון חסר או השתנה; הורידו אותו מחדש')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        with self.db() as connection:
            if connection.execute("SELECT 1 FROM learning_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():raise HTTPException(409,'יש אימון אישי פעיל. סיימו או עצרו אותו לפני ההתקנה')
            backup=self.data/'backups'/f'pre-update-{stamp}.sqlite3';backup.parent.mkdir(exist_ok=True)
            # BEGIN IMMEDIATE belongs to the application's write transaction;
            # make the backup through a separate read connection to avoid a lock.
            with sqlite3.connect(self.data/'invoices.sqlite3') as original,sqlite3.connect(backup) as copy:original.backup(copy)
        log=self.root/f'update-{stamp}.log';install_dir=Path(sys.executable).resolve().parent
        script=helper_script(target,install_dir,os.getpid(),log)
        encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        subprocess.Popen([str(powershell),'-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',encoded],creationflags=0x08000000,close_fds=True)
        self.app.state.update_closing=True
        threading.Timer(.6,close).start()
        return {'installing':True,'backup_created':True}

def register(app,s):
    manager=Updates(app,s['data'],s['db']);app.state.updates=manager;app.router.add_event_handler('shutdown',manager.closed.set)
    @app.get('/api/updates')
    def status():return manager.status()
    @app.post('/api/updates/check')
    def check():return manager.check()
    @app.post('/api/updates/download')
    def download():return manager.download()
    @app.post('/api/updates/install')
    def install(payload:dict=Body(...)):return manager.install(payload.get('confirmed') is True)
    return manager
