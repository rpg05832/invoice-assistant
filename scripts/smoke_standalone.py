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
    log_path=directory/'server.log'
    env={**os.environ,'INVOICE_DATA_DIR':str(directory/'personal-data')}
    if not source:
        # Exercise real per-user defaults, including Hebrew account directories.
        # A developer's installed Tesseract must never hide missing bundled files.
        for key in ('INVOICE_DATA_DIR','INVOICE_ASSET_DIR','INVOICE_TESSDATA_DIR','TESSDATA_PREFIX'):env.pop(key,None)
        env['LOCALAPPDATA']=str(directory/'משתמש עברי')
        if os.name=='nt':
            system=Path(os.environ.get('SystemRoot','C:/Windows'))
            env['PATH']=os.pathsep.join((str(system/'System32'),str(system)))
    stream=log_path.open('w',encoding='utf-8')
    try:process=subprocess.Popen([*command,'--no-browser','--port',str(port)],stdout=stream,stderr=stream,env=env)
    except BaseException:stream.close();raise
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
        assert reading.json()['word_count']>0, 'Packaged OCR must return source coordinates without external configs'
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
            # This is a synthetic, research-only validation of the optional RTL
            # engine. Its model files stay in this isolated test user profile.
            installed=client.post('/api/local-models/indra/install',json={'license_accepted':True});assert installed.is_success,installed.text
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
            reread=client.post('/api/documents/'+ident+'/field-region',json={'field':'number','region':{'x':0,'y':0,'w':1,'h':1,'rotation':0}})
            assert reread.is_success and '00002468' in reread.json()['values'],reread.text
            whole=client.post('/api/documents/'+ident+'/local-reread')
            assert whole.is_success and '00002468' in whole.json()['text'],whole.text
            assert client.get(f"/api/companies/{company['id']}/documents").json()[0]['revision']==doc['revision']
            reply=client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'checks':[{'number':'00002468','confirmed':True,'region':{'x':0,'y':0,'w':1,'h':1,'rotation':0}}]});assert reply.is_success,reply.text
            assert client.put('/api/documents/'+ident+'/review-flag',json={'marked':True,'note':'Synthetic review marker'}).is_success
            hebrew_font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arial.ttf'
            if not hebrew_font.is_file():hebrew_font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
            font=ImageFont.truetype(str(hebrew_font),64,layout_engine=ImageFont.Layout.BASIC)
            hebrew=Image.new('RGB',(1400,800),'white');draw=ImageDraw.Draw(hebrew)
            draw.text((600,80),'חשבונית מס'[::-1],font=font,fill='black');draw.text((600,200),'ספק בדיקה'[::-1],font=font,fill='black');draw.text((600,400),'00001234',font=font,fill='black')
            buffer=io.BytesIO();hebrew.save(buffer,'PNG')
            hebrew_upload=client.post(f"/api/companies/{company['id']}/upload",files={'file':('עברית.png',buffer.getvalue(),'image/png')});assert hebrew_upload.is_success,hebrew_upload.text
            hebrew_id=hebrew_upload.json()['ids'][0]
            indra=client.post('/api/documents/'+hebrew_id+'/recognize-indra',json={'license_accepted':True});assert indra.is_success,indra.text
            assert 'חשבונית' in indra.json()['text'],indra.json()['text']
        job=client.post(prefix+'/train',json={'epochs':1,'auto_activate':False});assert job.is_success,job.text
        for _ in range(180):
            state=client.get(prefix).json();record=next(j for j in state['jobs'] if j['id']==job.json()['id'])
            if record['state'] not in ('queued','running'):break
            time.sleep(.25)
        assert record['state']=='completed',record
        assert state['models'][0]['report']['bytes']>0 and not state['models'][0]['active']
        assert client.get('/api/local-models').is_success
        if not incoming:
            prefix_documents=f"/api/companies/{company['id']}/documents"
            ids=[ident,hebrew_id]
            preview=client.post(prefix_documents+'/delete-preview',json={'ids':ids});assert preview.is_success,preview.text
            removal=client.post(prefix_documents+'/delete-permanently',json={'ids':ids,'token':preview.json()['token'],'confirmed':True});assert removal.is_success,removal.text
            assert removal.json()['deleted']==2 and not removal.json()['cleanup_pending']
            assert client.get(prefix_documents).json()==[]
            retained=client.get(prefix).json();assert len(retained['examples'])==len(retained['models'])==1
            home=Path(env['INVOICE_DATA_DIR']) if source else Path(env['LOCALAPPDATA'])/'InvoiceAssistant/data'
            assert not (home/'files'/doc['source_id']).exists()
            assert (home/retained['examples'][0]['image']).is_file()
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
    print('Standalone Hebrew OCR including optional Indra RTL, local CTC training, permanent deletion with retained learning, clean installs and explicit team-learning transfer: passed')

if __name__=='__main__':main()
