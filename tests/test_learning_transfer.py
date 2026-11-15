"""Portable learning is explicit, remapped and never creates ledger documents."""
import io
import json
import zipfile

from fastapi.testclient import TestClient

from app.main import create_app
from .test_learning import teach_crop,prefix,wait_job


def new_client(path):return TestClient(create_app(path))


def setup(client):
    company=client.post('/api/companies',json={'name':'חברת צוות','tax_id':'123456789'}).json()
    supplier=client.post(f"/api/companies/{company['id']}/suppliers",json={'code':'0007','name':'ספק צוות','tax_id':'987654321'}).json()
    card=client.get(prefix(company)).json()['suppliers'][0]
    client.post(prefix(company)+'/suppliers/'+supplier['id'],json={'fingerprint':card['fingerprint'],'identity_confirmed':True,'rules':{'expense_account':'0600'}})
    return company,supplier


def preview(client,company,content):
    result=client.post(prefix(company)+'/transfer/preview',files={'file':('team-learning.zip',content,'application/zip')})
    assert result.status_code==200,result.text
    return result.json()


def apply(client,company,stage,**extra):
    payload={'token':stage['token'],'categories':{'examples':True,'supplier_rules':True,'models':True,'check_register':False,'history':False},'supplier_mapping':{s['id']:s['suggested_id'] for s in stage['suppliers']},'writer_mapping':{},'approve_examples':True,'confirmed':True,'conflict_policy':'keep_local',**extra}
    return client.post(prefix(company)+'/transfer/import',json=payload)


def test_team_bundle_imports_crops_and_proposed_rules_without_invoices(tmp_path):
    with new_client(tmp_path/'employee-a') as source,new_client(tmp_path/'employee-b') as target:
        a,sa=setup(source);b,sb=setup(target)
        writer=source.post(prefix(a)+'/writers',json={'name':'כתב משותף'}).json()['id']
        teach_crop(source,a,sa,writer=writer)
        source.post(prefix(a)+'/check-register',json={'rows':[{'number':'00002468','supplier_id':sa['id'],'amount':'118.00'}]})
        blob=source.get(prefix(a)+'/transfer/export').content
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert all(name=='manifest.json' or name.startswith(('examples/','models/')) for name in archive.namelist())
            manifest=json.loads(archive.read('manifest.json'));assert manifest['checks']==manifest['history']==[]
        stage=preview(target,b,blob)
        assert stage['counts']['examples']==1 and stage['suppliers'][0]['suggested_id']==sb['id']
        assert source.get(prefix(a)).json()['installation_id']!=target.get(prefix(b)).json()['installation_id']
        result=apply(target,b,stage);assert result.status_code==200,result.text
        assert result.json()['ledger_documents_created']==0
        assert target.get(f"/api/companies/{b['id']}/documents").json()==[]
        info=target.get(prefix(b)).json();assert len(info['examples'])==1
        assert info['examples'][0]['imported'] is True and info['examples'][0]['document_id'] is None
        assert info['examples'][0]['label']=='00002468'
        assert info['writers'][0]['id']!=writer
        assert info['suppliers'][0]['proposed_answer']['rules']['expense_account']=='0600'
        assert target.get(prefix(b)+'/check-register').json()==[]
        # Re-import is idempotent, including writer provenance.
        again=apply(target,b,preview(target,b,blob));assert again.status_code==200,again.text
        info=target.get(prefix(b)).json();assert len(info['examples'])==len(info['writers'])==1
        # Imported examples can train a new local model without any ledger document.
        job=target.post(prefix(b)+'/train',json={'epochs':1,'auto_activate':False}).json()
        state,_=wait_job(target,b,job['id']);assert state['state']=='completed',state


def test_import_does_not_confirm_new_supplier_and_preview_is_company_bound(tmp_path):
    with new_client(tmp_path/'from') as source,new_client(tmp_path/'to') as target:
        a,sa=setup(source);teach_crop(source,a,sa)
        b=target.post('/api/companies',json={'name':'חברה חדשה'}).json()
        other=target.post('/api/companies',json={'name':'חברה אחרת'}).json()
        blob=source.get(prefix(a)+'/transfer/export').content;stage=preview(target,b,blob)
        assert apply(target,other,stage).status_code==404
        result=apply(target,b,stage,supplier_mapping={sa['id']:'__create__'})
        assert result.status_code==200,result.text
        supplier=target.get(prefix(b)).json()['suppliers'][0]
        assert supplier['reviewed'] is False and supplier['proposed_answer']['identity_confirmed'] is True
        assert target.get(prefix(other)).json()['examples']==[]


def test_conflicting_labels_keep_local_by_default_and_require_explicit_override(tmp_path):
    with new_client(tmp_path/'source') as source,new_client(tmp_path/'target') as target:
        a,sa=setup(source);b,sb=setup(target);teach_crop(source,a,sa)
        blob=source.get(prefix(a)+'/transfer/export').content;assert apply(target,b,preview(target,b,blob)).status_code==200
        example=target.get(prefix(b)).json()['examples'][0]
        target.patch(prefix(b)+'/examples/'+example['id'],json={'label':'00002469','approved':True})
        stage=preview(target,b,blob);assert stage['conflicts'][0]['local_label']=='00002469'
        assert apply(target,b,stage).status_code==200
        assert target.get(prefix(b)).json()['examples'][0]['label']=='00002469'
        result=apply(target,b,preview(target,b,blob),conflict_policy='use_imported')
        assert result.status_code==200,result.text
        assert target.get(prefix(b)).json()['examples'][0]['label']=='00002468'
        assert target.get(f"/api/companies/{b['id']}/documents").json()==[]


def test_import_rejects_unexpected_files_and_preserves_database(tmp_path):
    with new_client(tmp_path/'source') as source,new_client(tmp_path/'target') as target:
        a,sa=setup(source);b,sb=setup(target);teach_crop(source,a,sa)
        blob=source.get(prefix(a)+'/transfer/export').content
        unsafe=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as original,zipfile.ZipFile(unsafe,'w') as out:
            for entry in original.infolist():out.writestr(entry,original.read(entry))
            out.writestr('../outside.txt','not allowed')
        result=target.post(prefix(b)+'/transfer/preview',files={'file':('unsafe.zip',unsafe.getvalue(),'application/zip')})
        assert result.status_code==400
        assert target.get(prefix(b)).json()['examples']==[]
        assert not (tmp_path/'outside.txt').exists()


def test_models_and_multiple_writers_transfer_to_an_independent_install(tmp_path):
    with new_client(tmp_path/'writer-source') as source,new_client(tmp_path/'writer-target') as target:
        a,sa=setup(source);b,sb=setup(target)
        writers=[source.post(prefix(a)+'/writers',json={'name':name}).json()['id'] for name in ('כתב א','כתב ב')]
        target.post(prefix(b)+'/writers',json={'name':'כתב א'})
        for index,label in enumerate(('00002468','00003579','00004680','00005791')):teach_crop(source,a,sa,label,index+1,writers[index%2])
        samples=sorted(source.get(prefix(a)).json()['examples'],key=lambda e:e['label'])
        for index,row in enumerate(samples):source.patch(prefix(a)+'/examples/'+row['id'],json={'role':'train' if index<2 else 'validation'})
        job=source.post(prefix(a)+'/train',json={'epochs':1,'auto_activate':False}).json();state,_=wait_job(source,a,job['id']);assert state['state']=='completed'
        blob=source.get(prefix(a)+'/transfer/export').content
        result=apply(target,b,preview(target,b,blob));assert result.status_code==200,result.text
        incoming=target.get(prefix(b)).json()
        assert len(incoming['models'])==1 and not incoming['models'][0]['active']
        assert incoming['models'][0]['report']['validation']['tested']==2
        assert len(incoming['writers'])==3, 'Identical names must not merge independent writers silently'
        assert set(r['id'] for r in incoming['examples']).isdisjoint(r['id'] for r in samples)
        assert incoming['models'][0]['report']['dataset_fingerprint']
        assert target.get(f"/api/companies/{b['id']}/documents").json()==[]


def test_model_transfer_excludes_samples_used_by_source_training(tmp_path):
    with new_client(tmp_path/'source-learning') as source,new_client(tmp_path/'receiver-learning') as target:
        a,sa=setup(source);b,sb=setup(target)
        for index,label in enumerate(('00002468','00003579','00004680','00005791')):teach_crop(source,a,sa,label,index+1)
        samples=sorted(source.get(prefix(a)).json()['examples'],key=lambda e:e['label'])
        for index,row in enumerate(samples):source.patch(prefix(a)+'/examples/'+row['id'],json={'role':'train' if index<2 else 'validation'})
        job=source.post(prefix(a)+'/train',json={'epochs':1,'auto_activate':False}).json();state,_=wait_job(source,a,job['id']);assert state['state']=='completed'
        examples_only=source.get(prefix(a)+'/transfer/export?models=false').content
        assert apply(target,b,preview(target,b,examples_only),categories={'examples':True}).status_code==200
        received=sorted(target.get(prefix(b)).json()['examples'],key=lambda e:e['label'])
        for index,row in enumerate(received):target.patch(prefix(b)+'/examples/'+row['id'],json={'role':'validation' if index<2 else 'train'})
        weights_only=source.get(prefix(a)+'/transfer/export?examples=false&supplier_rules=false').content
        with zipfile.ZipFile(io.BytesIO(weights_only)) as archive:
            meta=json.loads(archive.read('manifest.json'))
            assert 'sample_labels' not in meta['models'][0]['report']
            assert 'rows' not in meta['models'][0]['report'].get('validation',{})
        result=apply(target,b,preview(target,b,weights_only),categories={'models':True})
        assert result.status_code==200,result.text
        model=target.get(prefix(b)).json()['models'][0]
        assert model['report']['validation']['tested']==0
        assert not model['report']['qualified'] and not model['active']
