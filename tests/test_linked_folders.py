import hashlib
import io
import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.main import create_app
from scripts.restore_backup import restore
from .conftest import pdf_bytes,get_doc,ready_doc


def connect(client,company,root):
    response=client.post(f"/api/companies/{company['id']}/folders",json={'path':str(root)})
    assert response.status_code==200,response.text
    return response.json()


def scan(client,company,folder):
    prefix=f"/api/companies/{company['id']}/folders"
    response=client.post(prefix+'/'+folder['id']+'/scan');assert response.status_code==200,response.text
    for _ in range(400):
        job=client.get(prefix).json()['job']
        if job['state']!='running':
            client.app.state.linked_folders.worker.join(timeout=2)
            assert job['state']=='completed',job
            return job
        time.sleep(.02)
    pytest.fail('Folder scan did not finish')


def documents(client,company):return client.get(f"/api/companies/{company['id']}/documents").json()
def clear(client,company):return client.post(f"/api/companies/{company['id']}/folders/cache/clear")
def pixels(response):
    assert response.status_code==200,response.text[:300]
    with Image.open(io.BytesIO(response.content)) as image:return image.size,image.convert('RGB').tobytes()


def test_folder_recursion_content_dedup_and_no_original_copies(client,company,tmp_path):
    root=tmp_path/'מקורות';sub=root/'שנת מס';sub.mkdir(parents=True)
    content=pdf_bytes('LINK-001');(root/'חשבונית.pdf').write_bytes(content);(sub/'שם אחר.pdf').write_bytes(content)
    (sub/'new.pdf').write_bytes(pdf_bytes('LINK-002'));(root/'ignore.txt').write_text('not a document')
    before={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    folder=connect(client,company,root);job=scan(client,company,folder)
    assert job['seen']==3 and job['imported']==2 and job['duplicates']==1 and not job['errors']
    docs=documents(client,company);assert len(docs)==2 and all(d['source_link']['linked'] for d in docs)
    data=client.app.state.data_dir
    assert not list((data/'files').glob('*/original'))
    assert before=={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    assert scan(client,company,folder)['imported']==0


def test_cache_clear_regenerates_pdf_crop_and_rotation_identically(client,company,tmp_path):
    root=tmp_path/'originals';root.mkdir();(root/'file.pdf').write_bytes(pdf_bytes())
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0]
    before=pixels(client.get('/api/documents/'+doc['id']+'/image'))
    region={'x':.04,'y':.02,'w':.7,'h':.4,'rotation':90}
    split=client.post('/api/documents/'+doc['id']+'/split',json={'regions':[region,{'x':.04,'y':.5,'w':.7,'h':.3,'rotation':0}]})
    assert split.status_code==200,split.text
    child=get_doc(client,company,split.json()['ids'][0])
    rotated=client.post('/api/documents/'+child['id']+'/rotate',json={'angle':90,'revision':child['revision']});assert rotated.status_code==200,rotated.text
    cropped_before=pixels(client.get('/api/documents/'+child['id']+'/image'))
    assert clear(client,company).status_code==200
    assert not list((client.app.state.data_dir/'files'/doc['source_id']).glob('*.png'))
    assert pixels(client.get('/api/documents/'+doc['id']+'/image'))==before
    assert pixels(client.get('/api/documents/'+child['id']+'/image'))==cropped_before
    assert pixels(client.get('/api/documents/'+child['id']+'/image?original=true'))==before


def test_existing_owned_source_converts_only_after_exact_pixel_verification(client,company,tmp_path):
    content=pdf_bytes();upload=client.post(f"/api/companies/{company['id']}/upload",files={'file':('old.pdf',content,'application/pdf')});ident=upload.json()['ids'][0]
    before=get_doc(client,company,ident);picture=pixels(client.get('/api/documents/'+ident+'/image'))
    data=client.app.state.data_dir;owned=data/'files'/before['source_id']/'original';assert owned.is_file()
    root=tmp_path/'external';root.mkdir();(root/'renamed.pdf').write_bytes(content)
    folder=connect(client,company,root);job=scan(client,company,folder)
    assert job['linked_existing']==1 and job['imported']==0 and not owned.exists()
    after=get_doc(client,company,ident);assert after['fields']==before['fields'] and after['status']==before['status']
    clear(client,company);assert pixels(client.get('/api/documents/'+ident+'/image'))==picture


def test_missing_changed_and_relocated_sources_never_replace_saved_values(client,company,tmp_path):
    root=tmp_path/'originals';root.mkdir();path=root/'file.pdf';content=pdf_bytes();path.write_bytes(content)
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0]
    path.write_bytes(pdf_bytes('CHANGED'))
    assert client.get('/api/documents/'+doc['id']+'/image').status_code==409
    assert 'השתנה' in client.get('/api/documents/'+doc['id']+'/source-status').json()['message']
    assert get_doc(client,company,doc['id'])['fields']==doc['fields']
    path.write_bytes(content);new_root=tmp_path/'moved';root.rename(new_root)
    assert client.get('/api/documents/'+doc['id']+'/source').status_code==409
    reply=client.post(f"/api/companies/{company['id']}/folders/{folder['id']}/path",json={'path':str(new_root)});assert reply.status_code==200
    assert client.get('/api/documents/'+doc['id']+'/source').content==content


def test_changed_file_cannot_be_exported_as_the_previously_approved_original(client,company,supplier,tmp_path):
    ident=ready_doc(client,company,supplier)
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==200
    doc=get_doc(client,company,ident);root=tmp_path/'external';root.mkdir();path=root/'file.pdf';path.write_bytes((client.app.state.data_dir/'files'/doc['source_id']/'original').read_bytes())
    folder=connect(client,company,root);assert scan(client,company,folder)['linked_existing']==1
    path.write_bytes(pdf_bytes('UPDATED'))
    assert client.post(f"/api/companies/{company['id']}/batches",json={'ids':[ident]}).status_code==409
    assert get_doc(client,company,ident)['status']=='approved'
    job=scan(client,company,folder);assert job['changed']==1 and job['imported']==1
    assert len(documents(client,company))==2


def test_linked_backup_restores_metadata_without_copying_external_originals(client,company,tmp_path):
    root=tmp_path/'originals';root.mkdir();(root/'file.pdf').write_bytes(pdf_bytes())
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0]
    archive=tmp_path/'backup.zip';archive.write_bytes(client.get('/api/backup').content)
    with zipfile.ZipFile(archive) as z:assert all(not n.startswith('files/'+doc['source_id']+'/') for n in z.namelist())
    target=restore(archive,tmp_path/'restored')
    with TestClient(create_app(target)) as other:
        assert get_doc(other,company,doc['id'])['fields']==doc['fields']
        assert other.get('/api/documents/'+doc['id']+'/source').content==(root/'file.pdf').read_bytes()
        assert other.get('/api/documents/'+doc['id']+'/image').status_code==200


def test_delete_linked_document_keeps_original_and_does_not_reimport_until_requested(client,company,tmp_path):
    root=tmp_path/'originals';root.mkdir();path=root/'file.pdf';content=pdf_bytes();path.write_bytes(content)
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0];prefix=f"/api/companies/{company['id']}/documents"
    preview=client.post(prefix+'/delete-preview',json={'ids':[doc['id']]}).json();assert preview['linked_sources']==1
    result=client.post(prefix+'/delete-permanently',json={'ids':[doc['id']],'token':preview['token'],'confirmed':True});assert result.status_code==200,result.text
    assert path.read_bytes()==content and scan(client,company,folder)['ignored']==1
    assert documents(client,company)==[]
    assert client.post(f"/api/companies/{company['id']}/folders/{folder['id']}/reset-ignored").status_code==200
    assert scan(client,company,folder)['imported']==1


def test_folder_company_scope_private_data_exclusion_and_disconnect(client,company,tmp_path):
    assert client.post(f"/api/companies/{company['id']}/folders",json={'path':str(client.app.state.data_dir)}).status_code==400
    root=tmp_path/'external';root.mkdir();(root/'file.pdf').write_bytes(pdf_bytes())
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0]
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    assert client.post(f"/api/companies/{other['id']}/folders/{folder['id']}/scan").status_code==404
    assert client.post(f"/api/companies/{company['id']}/folders/{folder['id']}/active",json={'active':False}).status_code==200
    assert client.get('/api/documents/'+doc['id']+'/image').status_code==409
    assert (root/'file.pdf').is_file()
    connect(client,company,root)
    assert client.get('/api/documents/'+doc['id']+'/image').status_code==200


def test_native_picker_cancel_and_selection_only_return_selected_path(client,company,tmp_path):
    assert client.post('/api/folders/choose').status_code==409
    client.app.state.folder_picker=lambda:None
    assert client.post('/api/folders/choose').json()=={'path':None}
    client.app.state.folder_picker=lambda:tmp_path
    assert client.post('/api/folders/choose').json()['path']==str(tmp_path)


def test_cache_limit_and_clear_respect_active_previews_and_company_scope(client,company,tmp_path):
    root=tmp_path/'one';root.mkdir();(root/'file.pdf').write_bytes(pdf_bytes('CACHE-1'))
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0]
    other=client.post('/api/companies',json={'name':'אחרת'}).json();second=connect(client,other,root);scan(client,other,second);foreign=documents(client,other)[0]
    store=client.app.state.source_store
    with store.db() as db:path=store.acquire(db,doc)
    foreign_path=client.app.state.data_dir/foreign['image']
    clear(client,company);assert path.is_file() and foreign_path.is_file()
    store.limit=0;store.release(path)
    assert not path.exists() and not foreign_path.exists()
    assert client.get('/api/documents/'+doc['id']+'/image').status_code==200
    assert (root/'file.pdf').is_file()


def test_image_cache_replays_stored_transform_without_new_orientation_guess(client,company,tmp_path,monkeypatch):
    root=tmp_path/'pictures';root.mkdir();image=Image.new('RGB',(220,160),'white')
    from PIL import ImageDraw
    ImageDraw.Draw(image).rectangle((10,15,40,70),fill='black');image.save(root/'scan.png')
    monkeypatch.setattr('app.ocr.orientation',lambda path:90)
    monkeypatch.setattr('app.ocr.recognize',lambda *a,**k:('Invoice 123','test',''))
    monkeypatch.setattr('app.vision.tokens',lambda *a,**k:[])
    folder=connect(client,company,root);scan(client,company,folder);doc=documents(client,company)[0]
    before=pixels(client.get('/api/documents/'+doc['id']+'/image'))
    clear(client,company)
    monkeypatch.setattr('app.ocr.orientation',lambda path:pytest.fail('Cache must replay its original preprocessing'))
    assert pixels(client.get('/api/documents/'+doc['id']+'/image'))==before


def test_symlink_escapes_are_skipped_and_bad_file_does_not_stop_scan(client,company,tmp_path):
    root=tmp_path/'inputs';root.mkdir();outside=tmp_path/'outside';outside.mkdir();(outside/'private.pdf').write_bytes(pdf_bytes('OUTSIDE'))
    try:(root/'escape').symlink_to(outside,target_is_directory=True)
    except OSError:pass  # Symlink creation can require privileges on Windows.
    (root/'bad.pdf').write_bytes(b'invalid PDF');(root/'good.pdf').write_bytes(pdf_bytes('GOOD-001'))
    folder=connect(client,company,root);job=scan(client,company,folder)
    assert job['imported']==1 and job['error_count']==1 and job['seen']==2
    assert documents(client,company)[0]['fields']['number']=='GOOD-001'
