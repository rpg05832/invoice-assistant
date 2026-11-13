"""Verified public model downloads exclusively from this project's GitHub release."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

RELEASE_URL='https://github.com/rpg05832/invoice-assistant/releases/download/public-models-v1/'
PART_SIZE=1_900_000_000
class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,message,headers,newurl):
        parsed=urlparse(newurl);host=parsed.hostname or ''
        if parsed.scheme!='https' or not (host=='github.com' or host.endswith('.githubusercontent.com')):raise ValueError('כתובת ההורדה הופנתה מחוץ לשרתי GitHub')
        return super().redirect_request(request,fp,code,message,headers,newurl)


def github_open(request,timeout=120):return urllib.request.build_opener(GitHubRedirect).open(request,timeout=timeout)


@lru_cache(maxsize=1)
def manifest():
    value=json.loads(Path(__file__).with_name('model-mirrors.json').read_text(encoding='utf-8'))
    if value.get('format')!='invoice-assistant-public-models' or value.get('version')!=1:raise ValueError('רשימת המודלים אינה תקינה')
    return value['files']


def model_info(expected):
    if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('חתימת מודל אינה תקינה')
    row=manifest().get(expected)
    if not row or not isinstance(row.get('size'),int) or not 0<row['size']<20_000_000_000:raise ValueError('קובץ המודל אינו מופיע ברשימת ההורדות המאומתות')
    assets=row.get('assets')
    if not isinstance(assets,list) or not 1<=len(assets)<=12 or len(set(assets))!=len(assets) or any(not isinstance(name,str) or not re.fullmatch(re.escape(expected)+r'-[A-Za-z0-9._-]+',name) for name in assets):raise ValueError('כתובת מודל אינה תקינה')
    return row


def mirror_urls(expected):return [RELEASE_URL+name for name in model_info(expected)['assets']]


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download_model(expected,target,progress=None,cancelled=None):
    target=Path(target);temporary=target.with_suffix(target.suffix+'.download');sidecar=temporary.with_suffix(temporary.suffix+'.json')
    if target.is_file() and digest(target)==expected:
        temporary.unlink(missing_ok=True);sidecar.unlink(missing_ok=True);return
    row=model_info(expected);urls=mirror_urls(expected);total=row['size'];target.parent.mkdir(parents=True,exist_ok=True)
    try:matching=json.loads(sidecar.read_text(encoding='utf-8')).get('sha256')==expected
    except (OSError,ValueError):matching=False
    if not matching or (temporary.exists() and temporary.stat().st_size>total):temporary.unlink(missing_ok=True)
    sidecar.write_text(json.dumps({'sha256':expected}),encoding='utf-8')
    for index,url in enumerate(urls):
        offset=index*PART_SIZE;length=min(PART_SIZE,total-offset)
        if length<=0:raise ValueError('חלוקת מודל אינה תקינה')
        attempts=0
        while True:
            if cancelled and cancelled():raise InterruptedError('הורדת המודל נעצרה; אפשר להמשיך בהפעלה הבאה')
            current=temporary.stat().st_size if temporary.exists() else 0
            received=max(0,current-offset)
            if received>=length:break
            headers={'User-Agent':'InvoiceAssistant/0.1'}
            if received:headers['Range']=f'bytes={received}-'
            try:
                with github_open(urllib.request.Request(url,headers=headers),timeout=120) as response:
                    if received and response.status!=206:
                        with temporary.open('r+b') as output:output.truncate(offset)
                        received=0
                    elif response.status not in (200,206):raise ValueError('שרת ההורדה החזיר תגובה לא צפויה')
                    with temporary.open('ab') as output:
                        while received<length:
                            if cancelled and cancelled():raise InterruptedError('הורדת המודל נעצרה; אפשר להמשיך בהפעלה הבאה')
                            chunk=response.read(min(1024*1024,length-received))
                            if not chunk:raise OSError('הורדת המודל נעצרה לפני סוף הקובץ')
                            output.write(chunk);received+=len(chunk)
                            if progress:progress(offset+received,total)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (400,401,403,404) or attempts>=2:raise
                attempts+=1;time.sleep(attempts)
            except InterruptedError:raise
            except (OSError,TimeoutError):
                if attempts>=2:raise
                attempts+=1;time.sleep(attempts)
    if temporary.stat().st_size!=total or digest(temporary)!=expected:
        temporary.unlink(missing_ok=True);sidecar.unlink(missing_ok=True);raise ValueError('אימות המודל מ־GitHub נכשל')
    temporary.replace(target);sidecar.unlink(missing_ok=True)
