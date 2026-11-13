"""Exercise local OCR and personal training in the packaged app, then a fresh install."""
import argparse
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image,ImageDraw,ImageFont


def exercise(command,directory,port,source=False,incoming=None):
    log_path=directory/'server.log';stream=log_path.open('w',encoding='utf-8')
    env={**os.environ,'INVOICE_DATA_DIR':str(directory/'personal-data')}
    if not source:
        # Exercise real per-user defaults, including Hebrew account directories.
        # A developer's installed Tesseract must never hide missing bundled files.
        for key in ('INVOICE_DATA_DIR','INVOICE_ASSET_DIR','INVOICE_TESSDATA_DIR','TESSDATA_PREFIX'):env.pop(key,None)
        env['LOCALAPPDATA']=str(directory/'משתמש עברי')
        if os.name=='nt':env['PATH']=os.pathsep.join((str(Path(env['SystemRoot'])/'System32'),env['SystemRoot']))
    process=subprocess.Popen([*command,'--no-browser','--port',str(port)],stdout=stream,stderr=stream,env=env)
    client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=30)
    try:
        for _ in range(180):
            if process.poll() is not None:raise AssertionError(log_path.read_text(encoding='utf-8'))
            try:
                response=client.get('/api/health')
                if response.is_success:break
            except httpx.HTTPError:pass
            time.sleep(.25)
        else:raise AssertionError('Standalone server did not become ready')
        assert response.json()['ocr']['hebrew']
        reading=client.post('/api/ocr/self-test');assert reading.is_success and reading.json()['ok'],reading.text
        assert 'חשבונית' in reading.json()['sample_text'] and '00001234' in reading.json()['sample_text']
        assert client.get('/api/companies').json()==[], 'Fresh install inherited company data'
        company=client.post('/api/companies',json={'name':'Standalone personal test'}).json();prefix=f"/api/companies/{company['id']}/learning"
        fresh=client.get(prefix).json();assert fresh['examples']==fresh['models']==fresh['writers']==[]
        workspace=client.get(f"/api/companies/{company['id']}/review-workspace").json()
        assert workspace['flags']==workspace['snippets']==[] and workspace['session'] is None
        assert client.post('/api/review-calculator',json={'mode':'total','amount':'118.00','rate':'18'}).json()['net']=='100.00'
        assert client.get('/api/local-models/guidance').json()['download_source']=='GitHub'
        if not incoming:
            installed=client.post('/api/local-models/rivok/install');assert installed.is_success,installed.text
            for _ in range(360):
                download=client.get('/api/local-models').json()['job']
                if download['state']!='running':break
                time.sleep(.25)
            assert download['state']=='completed',download
        if incoming:
            preview=client.post(prefix+'/transfer/preview',files={'file':('team-learning.zip',incoming,'application/zip')});assert preview.is_success,preview.text
            result=client.post(prefix+'/transfer/import',json={'token':preview.json()['token'],'confirmed':True,'approve_examples':True,'categories':{'examples':True,'models':True},'supplier_mapping':{},'writer_mapping':{}});assert result.is_success,result.text
            assert client.get(f"/api/companies/{company['id']}/documents").json()==[]
            assert len(client.get(prefix).json()['examples'])==1
        else:
            image=Image.new('RGB',(700,180),'white');draw=ImageDraw.Draw(image);font_path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arial.ttf'
            font=ImageFont.truetype(str(font_path),60) if font_path.is_file() else ImageFont.load_default(size=60)
            draw.text((40,40),'00002468',font=font,fill='black');buffer=io.BytesIO();image.save(buffer,'PNG')
            upload=client.post(f"/api/companies/{company['id']}/upload",files={'file':('synthetic.png',buffer.getvalue(),'image/png')});assert upload.is_success,upload.text
            ident=upload.json()['ids'][0];doc=client.get(f"/api/companies/{company['id']}/documents").json()[0]
            reply=client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'checks':[{'number':'00002468','confirmed':True,'region':{'x':0,'y':0,'w':1,'h':1,'rotation':0}}]});assert reply.is_success,reply.text
            assert client.put('/api/documents/'+ident+'/review-flag',json={'marked':True,'note':'Synthetic review marker'}).is_success
        job=client.post(prefix+'/train',json={'epochs':1,'auto_activate':False});assert job.is_success,job.text
        for _ in range(180):
            state=client.get(prefix).json();record=next(j for j in state['jobs'] if j['id']==job.json()['id'])
            if record['state'] not in ('queued','running'):break
            time.sleep(.25)
        assert record['state']=='completed',record
        assert state['models'][0]['report']['bytes']>0 and not state['models'][0]['active']
        assert client.get('/api/local-models').is_success
        bundle=client.get(prefix+'/transfer/export');assert bundle.is_success,bundle.text
        return fresh['installation_id'],bundle.content
    finally:
        client.close();process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        stream.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('executable',nargs='?');parser.add_argument('--source',action='store_true');args=parser.parse_args()
    if not args.source and not args.executable:parser.error('Provide the packaged executable or --source')
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'run.py')] if args.source else [str(Path(args.executable).resolve())]
    with tempfile.TemporaryDirectory(prefix='חשבוניות-') as temp:
        root=Path(temp);identities=[];incoming=None
        for index in range(2):
            directory=root/f'install-{index}';directory.mkdir();identity,incoming=exercise(command,directory,8877+index,args.source,incoming);identities.append(identity)
        assert identities[0]!=identities[1], 'Independent installs reused a personal identity'
    print('Standalone OCR, local CTC training, clean installs and explicit team-learning transfer: passed')

if __name__=='__main__':main()
