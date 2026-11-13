"""Mirror checksum-pinned public weights to GitHub Releases, retaining notices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from model_catalog import ROOT,REPOSITORY,TAG,catalog

PART_SIZE=1_900_000_000
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,message,headers,newurl):
        raise urllib.error.HTTPError(request.full_url,code,'Authenticated redirects are disabled',headers,fp)

AUTH_OPENER=urllib.request.build_opener(NoRedirect)


def api(path,method='GET',payload=None):
    token=os.environ['GITHUB_TOKEN'];url='https://api.github.com/repos/'+REPOSITORY+path
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    if payload is not None:headers['Content-Type']='application/json'
    request=urllib.request.Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers=headers,method=method)
    with AUTH_OPENER.open(request,timeout=120) as response:return json.load(response) if response.status!=204 else None


def release():
    try:return api('/releases/tags/'+TAG)
    except urllib.error.HTTPError as exc:
        if exc.code!=404:raise
    for row in api('/releases?per_page=100'):
        if row['tag_name']==TAG:return row
    return None


def upload(rel,path,name=None):
    name=name or path.name;size=path.stat().st_size
    existing=next((a for a in api(f"/releases/{rel['id']}/assets?per_page=100") if a['name']==name),None)
    if existing and existing['size']==size and existing['state']=='uploaded':return
    if existing:api('/releases/assets/'+str(existing['id']),method='DELETE')
    url=rel['upload_url'].split('{',1)[0]+'?'+urllib.parse.urlencode({'name':name})
    if urllib.parse.urlparse(url).hostname!='uploads.github.com':raise ValueError('Unexpected upload host')
    for attempt in range(3):
        try:
            with path.open('rb') as stream:
                request=urllib.request.Request(url,data=stream,headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Content-Type':'application/octet-stream','Content-Length':str(size)},method='POST')
                with AUTH_OPENER.open(request,timeout=600) as response:json.load(response)
            print('Uploaded:',name,flush=True);return
        except (OSError,urllib.error.HTTPError):
            if attempt==2:raise
            time.sleep(2**(attempt+1))


def fetch(row,path):
    for attempt in range(3):
        try:
            digest=hashlib.sha256();count=0
            with urllib.request.urlopen(row['url'],timeout=180) as source,path.open('wb') as output:
                while True:
                    block=source.read(1024*1024)
                    if not block:break
                    output.write(block);digest.update(block);count+=len(block)
            if digest.hexdigest()!=row['sha256']:raise ValueError('Upstream checksum mismatch: '+row['name'])
            return count
        except (OSError,urllib.error.HTTPError):
            if attempt==2:raise
            time.sleep(2**(attempt+1))


def publish_profile(profile):
    rel=release()
    if not rel:raise RuntimeError('Prepare the release first')
    index={}
    for row in (r for r in catalog() if r['profile']==profile):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source';size=fetch(row,path);names=[]
            base=row['sha256']+'-'+re.sub('[^A-Za-z0-9._-]','_',row['name'])[:120]
            with path.open('rb') as source:
                part=0
                while True:
                    part+=1;name=base+(f'.part{part:03d}' if size>PART_SIZE else '')
                    target=Path(temp)/name;remaining=PART_SIZE;written=0
                    with target.open('wb') as output:
                        while remaining:
                            chunk=source.read(min(1024*1024,remaining))
                            if not chunk:break
                            output.write(chunk);remaining-=len(chunk);written+=len(chunk)
                    if not written:target.unlink();break
                    upload(rel,target);names.append(name);target.unlink()
                    if size<=PART_SIZE:break
            index[row['sha256']]={**row,'size':size,'assets':names}
    output=ROOT/'test-results'/f'model-index-{profile}.json';output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(index,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--profile');parser.add_argument('--finalize',action='store_true');args=parser.parse_args()
    if args.prepare:
        if not release():api('/releases',method='POST',payload={'tag_name':TAG,'target_commitish':os.environ.get('GITHUB_SHA','work/full-spec'),'name':'Verified public OCR and numeral models','draft':True,'body':'Unmodified public model files, pinned to upstream SHA-256 values. Original licenses and source attribution are retained. Indra is subject to the included modified OpenRAIL-M LICENSE, including its use and redistribution restrictions. No customer documents or personal learned weights are included.'})
        return
    if args.profile:publish_profile(args.profile);return
    if args.finalize:
        rel=release();entries={}
        if not rel:raise RuntimeError('Release missing')
        for path in (ROOT/'test-results').glob('model-index-*.json'):entries.update(json.loads(path.read_text(encoding='utf-8')))
        required={r['sha256'] for r in catalog()}
        if not required<=set(entries):raise RuntimeError('Mirror incomplete; refusing publication')
        output=ROOT/'test-results/model-index.json';output.write_text(json.dumps({'format':'invoice-assistant-public-models','version':1,'files':entries},indent=2),encoding='utf-8')
        # Include complete license texts and upstream attribution beside the weights.
        notices=ROOT/'test-results/MODEL_SOURCES_AND_LICENSES.md'
        notices.write_text('# Public model mirrors\n\nFiles are copied without modification. Original terms continue to apply.\n\n'+'\n'.join(f"- {r['name']} ({', '.join(r['profiles'])}): {r['license']}\n  Source: {r['url']}\n  SHA256: {r['sha256']}" for r in catalog()),encoding='utf-8')
        for path in [output,notices,*sorted((ROOT/'third_party').glob('*LICENSE*')),*sorted((ROOT/'third_party').glob('*MODEL_CARD*'))]:
            old=next((a for a in api(f"/releases/{rel['id']}/assets?per_page=100") if a['name']==path.name),None)
            if old:api('/releases/assets/'+str(old['id']),method='DELETE')
            upload(rel,path)
        api('/releases/'+str(rel['id']),method='PATCH',payload={'draft':False})
        print('Published complete verified mirror:',TAG)


if __name__=='__main__':main()
