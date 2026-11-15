import sqlite3
from app.main import create_app
from .conftest import upload_doc,get_doc,ready_doc


def preview(client,company,ids):
    response=client.post(f"/api/companies/{company['id']}/documents/delete-preview",json={'ids':ids})
    assert response.status_code==200,response.text
    return response.json()

def remove(client,company,ids,value,**extra):
    return client.post(f"/api/companies/{company['id']}/documents/delete-permanently",json={'ids':ids,'token':value['token'],'confirmed':True,**extra})

def test_permanent_delete_previews_entire_source_and_requires_confirmation(client,company):
    ident=upload_doc(client,company,pages=2);source=get_doc(client,company,ident)['source_id'];directory=client.app.state.data_dir/'files'/source
    original=(directory/'original').read_bytes()
    assert client.post(f"/api/companies/{company['id']}/upload",files={'file':('renamed.pdf',original,'application/pdf')}).status_code==409
    value=preview(client,company,[ident]);assert len(value['ids'])==2 and value['can_delete']
    assert remove(client,company,[ident],value,confirmed=False).status_code==400
    assert directory.is_dir()
    assert remove(client,company,[ident],value).json()['deleted']==2
    assert not directory.exists()
    assert client.get(f"/api/companies/{company['id']}/documents").json()==[]
    # Removing the saved content releases its duplicate hash; renaming alone never did.
    assert client.post(f"/api/companies/{company['id']}/upload",files={'file':('renamed.pdf',original,'application/pdf')}).status_code==200

def test_deletion_rejects_stale_revision_and_other_company(client,company):
    ident=upload_doc(client,company);d=get_doc(client,company,ident);value=preview(client,company,[ident])
    client.patch('/api/documents/'+ident,json={'revision':d['revision'],'fields':{'notes':'new change'}})
    assert remove(client,company,[ident],value).status_code==409
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    assert client.post(f"/api/companies/{other['id']}/documents/delete-preview",json={'ids':[ident]}).status_code==404
    assert get_doc(client,company,ident)['fields']['notes']=='new change'

def test_export_history_prevents_permanent_deletion(client,company,supplier):
    ident=ready_doc(client,company,supplier)
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==200
    batch=client.post(f"/api/companies/{company['id']}/batches",json={'ids':[ident]});assert batch.status_code==200,batch.text
    value=preview(client,company,[ident]);assert not value['can_delete'] and value['blocked']==[ident]
    assert remove(client,company,[ident],value).status_code==409
    assert get_doc(client,company,ident)['status']=='exported'

def test_deletion_keeps_confirmed_learning_and_cleans_review_foreign_keys(client,company,supplier):
    ident=ready_doc(client,company,supplier);d=get_doc(client,company,ident)
    region={'x':.05,'y':.03,'w':.6,'h':.15,'rotation':0}
    result=client.patch('/api/documents/'+ident,json={'revision':d['revision'],'checks':[{'number':'00007531','confirmed':True,'region':region}]})
    assert result.status_code==200,result.text
    assert client.put('/api/documents/'+ident+'/review-flag',json={'marked':True,'note':'later'}).status_code==200
    assert client.put(f"/api/companies/{company['id']}/review-session",json={'document_id':ident,'stamp':123}).status_code==200
    data=client.app.state.data_dir
    with sqlite3.connect(data/'invoices.sqlite3') as c:
        c.row_factory=sqlite3.Row
        before=client.app.state.learning.rows(c,company['id'])
        image=c.execute('SELECT image FROM learning_examples WHERE document_id=?',(ident,)).fetchone()[0]
    value=preview(client,company,[ident]);assert value['retained_examples']==1
    reply=remove(client,company,[ident],value);assert reply.status_code==200,reply.text
    assert (data/image).is_file()
    with sqlite3.connect(data/'invoices.sqlite3') as c:
        c.row_factory=sqlite3.Row
        after=client.app.state.learning.rows(c,company['id'])
        assert [r['invoice_key'] for r in before]==[r['invoice_key'] for r in after]
        assert [r['crop_sha'] for r in before]==[r['crop_sha'] for r in after]
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert c.execute('SELECT COUNT(*) FROM learning_examples').fetchone()[0]==0
        assert c.execute('SELECT label FROM learning_shared_examples').fetchone()[0]=='00007531'
        assert c.execute('SELECT COUNT(*) FROM review_sessions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM suppliers').fetchone()[0]==1
    assert client.get(f"/api/companies/{company['id']}/learning").status_code==200

def test_joined_sources_are_explicitly_included_and_unrelated_source_survives(client,company):
    first=upload_doc(client,company,'JOIN-A');second=upload_doc(client,company,'JOIN-B');third=upload_doc(client,company,'OTHER')
    assert client.post('/api/documents/'+first+'/join',json={'ids':[second]}).status_code==200
    value=preview(client,company,[second]);assert set(value['ids'])=={first,second} and len(value['sources'])==2
    assert remove(client,company,[second],value).status_code==200
    assert [d['id'] for d in client.get(f"/api/companies/{company['id']}/documents").json()]==[third]

def test_locked_source_cleanup_is_retried_on_next_startup(client,company,monkeypatch):
    import app.document_lifecycle as module
    ident=upload_doc(client,company);source=get_doc(client,company,ident)['source_id'];data=client.app.state.data_dir;directory=data/'files'/source
    value=preview(client,company,[ident])
    with monkeypatch.context() as patch:
        patch.setattr(module.shutil,'rmtree',lambda *a,**k:(_ for _ in ()).throw(PermissionError('file held open')))
        reply=remove(client,company,[ident],value);assert reply.status_code==200 and reply.json()['cleanup_pending']
    assert directory.exists()
    create_app(data)
    assert not directory.exists()


def test_active_training_blocks_deletion_without_mutating_source(client,company):
    ident=upload_doc(client,company);value=preview(client,company,[ident])
    with sqlite3.connect(client.app.state.data_dir/'invoices.sqlite3') as c:
        c.execute('INSERT INTO learning_jobs VALUES(?,?,?,?,?,?)',('test-active-job',company['id'],'queued',0,'{}','2026-11-15'))
    assert remove(client,company,[ident],value).status_code==409
    assert get_doc(client,company,ident)['source_id'] in value['sources']
