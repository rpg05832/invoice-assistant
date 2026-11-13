"""Personal learning: real CTC optimization, accounting gates and isolation."""
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import fitz
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import personal_digits as pd
from app.learning import split_samples
from app.main import create_app
from scripts.package_windows import validate_distribution_paths
from scripts.restore_backup import restore
from .conftest import upload_doc,get_doc,ready_doc,confirm_supplier,pdf_bytes


def prefix(company):return f"/api/companies/{company['id']}/learning"


def teach_crop(client,company,supplier,number='00002468',index=1,writer=None,confirmed=True):
    pdf=fitz.open(stream=pdf_bytes(f'TRAIN-{index}'),filetype='pdf');pdf[0].insert_text((50,240),number,fontsize=32)
    content=pdf.tobytes();pdf.close()
    response=client.post(f"/api/companies/{company['id']}/upload",files={'file':('training.pdf',content,'application/pdf')})
    assert response.status_code==200,response.text
    ident=response.json()['ids'][0];d=get_doc(client,company,ident)
    region={'x':.06,'y':.24,'w':.65,'h':.09,'rotation':0}
    check={'number':number,'confirmed':confirmed,'region':region,'writer_id':writer,'source_document_id':ident,'proposals':[{'engine':'example-engine','number':'99999999','variant':'original'}]}
    response=client.patch('/api/documents/'+ident,json={'revision':d['revision'],'supplier_id':supplier['id'],'checks':[check],'learning':{'writer_id':writer,'reason':'extra_stroke','explanation':'הקו שייך לקישוט מתחת למספר'},'check_candidates_reviewed':True})
    assert response.status_code==200,response.text
    return ident


def wait_job(client,company,ident,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=client.get(prefix(company)).json();job=next(j for j in result['jobs'] if j['id']==ident)
        if job['state'] not in ('queued','running'):return job,result
        time.sleep(.02)
    pytest.fail('Local training job did not finish')


def test_ctc_gradient_repeated_zeros_and_training_updates_weights():
    random=np.random.default_rng(12);logits=random.normal(size=(9,11));loss,gradient=pd.ctc_loss_gradient(logits,'0018')
    for time_index,label in [(0,0),(1,1),(3,2),(7,9)]:
        delta=np.zeros_like(logits);delta[time_index,label]=1e-4
        measured=(pd.ctc_loss_gradient(logits+delta,'0018')[0]-pd.ctc_loss_gradient(logits-delta,'0018')[0])/2e-4
        assert measured==pytest.approx(float(gradient[time_index,label]),abs=1e-4)
    samples=[]
    for text,writer in [('0018','writer-a'),('0028','writer-b')]:
        x=np.zeros((len(text)*3,79),np.float32);prior=np.full((len(x),11),-3,np.float32);prior[:,0]=0
        for index,digit in enumerate(text):
            x[index*3:index*3+2,int(digit)]=2;prior[index*3:index*3+2,0]=-3;prior[index*3:index*3+2,4]=0
        samples.append({'id':writer,'writer':writer,'x':x,'prior':prior,'text':text})
    model,report=pd.train(samples,[],epochs=60)
    assert np.linalg.norm(model['w2'])>0
    assert pd.evaluate(model,samples)['personal_correct']==2
    other,_=pd.train(samples,[{**samples[0],'text':'9999'}],epochs=60)
    assert all(np.array_equal(model[k],other[k]) for k in model), 'Held-out labels must never change fitted weights'


def test_supplier_question_required_and_changed_card_reopens_question(client,company):
    supplier=client.post(f"/api/companies/{company['id']}/suppliers",json={'code':'001','name':'ספק חדש','tax_id':'987654321'}).json()
    ident=upload_doc(client,company);doc=get_doc(client,company,ident)
    assert any('שאלת' in issue for issue in doc['issues'])
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==422
    confirm_supplier(client,company,supplier)
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==200
    card=client.get(prefix(company)).json()['suppliers'][0]
    assert client.post(prefix(company)+'/suppliers/'+supplier['id'],json={'fingerprint':card['fingerprint'],'identity_confirmed':False}).status_code==400
    response=client.post(f"/api/companies/{company['id']}/suppliers",json={'code':'001','name':'ספק חדש שעודכן','tax_id':'987654321','active':True,'expense_account':''})
    assert response.status_code==200,response.text
    assert client.get(prefix(company)).json()['suppliers'][0]['reviewed'] is False
    assert client.post(prefix(company)+'/suppliers/'+supplier['id'],json={'fingerprint':card['fingerprint'],'identity_confirmed':True}).status_code==409


def test_every_correction_keeps_before_reason_and_region_until_approval(client,company,supplier):
    ident=upload_doc(client,company);doc=get_doc(client,company,ident)
    region={'x':.05,'y':.04,'w':.5,'h':.2,'rotation':0}
    response=client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'fields':{'notes':'הסבר אישי','expense_account':'0600'},'learning':{'reason':'layout','explanation':'כאן נמצאת שורת הסיכום','field_regions':{'number':{'region':region,'source_document_id':ident}}}})
    assert response.status_code==200,response.text
    history=client.get('/api/documents/'+ident+'/learning').json()['events']
    assert {e['field'] for e in history}>={'notes','expense_account','number'}
    assert all(e['context']['explanation']=='כאן נמצאת שורת הסיכום' and not e['approved'] for e in history)
    assert client.get(prefix(company)).json()['template_count']==0
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==200
    assert client.get(prefix(company)).json()['template_count']==1
    assert all(e['approved'] for e in client.get('/api/documents/'+ident+'/learning').json()['events'])


def test_confirmed_crops_become_private_examples_and_foreign_writers_rejected(client,company,supplier):
    ident=teach_crop(client,company,supplier,confirmed=False)
    assert client.get(prefix(company)).json()['examples']==[]
    writer=client.post(prefix(company)+'/writers',json={'name':'כתב א'}).json()['id']
    doc=get_doc(client,company,ident);check=doc['checks'][0];check.update(confirmed=True,writer_id=writer)
    response=client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'checks':[check]})
    assert response.status_code==200,response.text
    info=client.get(prefix(company)).json();example=info['examples'][0]
    assert example['label']=='00002468' and example['writer_id']==writer
    assert client.get(example['image_url']).headers['content-type']=='image/png'
    other=client.post('/api/companies',json={'name':'חברה אחרת'}).json()
    assert client.get(prefix(other)).json()['examples']==[]
    assert client.get(prefix(other)+f"/examples/{example['id']}/image").status_code==404
    doc=get_doc(client,company,ident)
    response=client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'learning':{'writer_id':'foreign'},'fields':{'notes':'try'}})
    assert response.status_code==400
    assert get_doc(client,company,ident)['revision']==doc['revision']
    assert client.get(prefix(other)+f"/models/{example['id']}").status_code==404


def test_split_keeps_pdf_pages_and_rephotographed_invoice_in_one_partition():
    rows=[]
    for ident,source,invoice,role in [('a','pdf1','invoice1','auto'),('b','pdf1','invoice2','auto'),('c','pdf2','invoice2','auto'),('d','pdf3','invoice3','auto'),('e','pdf4','invoice4','auto')]:
        rows.append({'id':ident,'source_group':source,'invoice_key':invoice,'role':role})
    training,held=split_samples(rows);sets=[{r['id'] for r in values} for values in (training,held)]
    assert any({'a','b','c'}<=s for s in sets)
    assert not sets[0]&sets[1]
    rows[0]['role']='train';rows[2]['role']='validation'
    with pytest.raises(ValueError):split_samples(rows)


def test_actual_background_training_versions_holdout_and_inference(client,company,supplier):
    writers=[client.post(prefix(company)+'/writers',json={'name':name}).json()['id'] for name in ('כתב א','כתב ב')]
    for index,label in enumerate(['00002468','00003579','00004680','00005791']):teach_crop(client,company,supplier,label,index+1,writers[index%2])
    info=client.get(prefix(company)).json();examples=sorted(info['examples'],key=lambda e:e['label'])
    for index,example in enumerate(examples):
        result=client.patch(prefix(company)+'/examples/'+example['id'],json={'role':'train' if index<2 else 'validation'})
        assert result.status_code==200,result.text
    response=client.post(prefix(company)+'/train',json={'epochs':3,'auto_activate':True})
    assert response.status_code==200,response.text
    job,info=wait_job(client,company,response.json()['id'])
    assert job['state']=='completed',job
    record=info['models'][0];report=record['report']
    assert report['validation']['tested']==2 and set(report['train_ids']).isdisjoint(report['validation_ids'])
    assert set(report['writers'])==set(writers)
    path=client.app.state.data_dir/record['path'];model=pd.load(path)
    assert 0<path.stat().st_size<100000 and np.linalg.norm(model['w2'])>0
    assert report['base_sha256']==pd.base_digest()
    assert all(get_doc(client,company,e['document_id'])['status']=='review' for e in examples)
    if report['qualified']:
        assert record['active']
        example=examples[0];doc=get_doc(client,company,example['document_id'])
        check=doc['checks'][0]
        proposal=client.post('/api/documents/'+doc['id']+'/check-region',json={'region':check['region']}).json()
        assert any(p['engine']=='personal-ctc' for p in proposal['proposals'])
        assert proposal['confirmed'] is False
    else:assert client.post(prefix(company)+'/models/'+record['id']+'/activate').status_code==409
    exported=client.get(prefix(company)+'/models/'+record['id']+'/export')
    assert exported.status_code==200
    imported=client.post(prefix(company)+'/models/import',files={'file':('my-model.zip',exported.content,'application/zip')})
    assert imported.status_code==200,imported.text
    client.patch(prefix(company)+'/examples/'+examples[0]['id'],json={'label':'00002469'})
    assert not any(m['active'] for m in client.get(prefix(company)).json()['models'])
    assert client.post(prefix(company)+'/models/'+record['id']+'/activate').status_code==409
    # Full private backup retains weights, crops, installation identity and their DB references.
    content=client.get('/api/backup').content
    with tempfile_dir() as root:
        archive=root/'private.zip';archive.write_bytes(content);target=restore(archive,root/'restored')
        assert (target/record['path']).is_file()
        with TestClient(create_app(target)) as restored:
            assert restored.get(prefix(company)).json()['installation_id']==info['installation_id']
            assert len(restored.get(prefix(company)).json()['examples'])==4


def tempfile_dir():
    from contextlib import contextmanager
    import tempfile
    @contextmanager
    def directory():
        with tempfile.TemporaryDirectory() as name:yield Path(name)
    return directory()


def test_fresh_installation_and_distribution_never_inherit_learning(client,company,supplier,tmp_path):
    teach_crop(client,company,supplier)
    old=client.get(prefix(company)).json()
    with TestClient(create_app(tmp_path/'fresh-install')) as fresh:
        assert fresh.get('/api/companies').json()==[]
        other=fresh.post('/api/companies',json={'name':'התקנה חדשה'}).json()
        result=fresh.get(prefix(other)).json()
        assert result['installation_id']!=old['installation_id']
        assert result['examples']==result['writers']==result['models']==result['suppliers']==[]
    for path in ['data/files/learning/customer/model.npz','data/invoices.sqlite3','data/models/private-customer.onnx','test-results/private.png','.env','secrets.key']:
        with pytest.raises(RuntimeError):validate_distribution_paths([Path(path)],{Path('data/models/generic.onnx')})
    validate_distribution_paths([Path('app/learning.py'),Path('data/models/generic.onnx')],{Path('data/models/generic.onnx')})


def test_check_register_is_personal_and_preserves_leading_zeros(client,company,supplier):
    response=client.post(prefix(company)+'/check-register',json={'rows':[{'number':'00002468','supplier_code':supplier['code'],'amount':'118.00'}]})
    assert response.status_code==200,response.text
    rows=client.get(prefix(company)+'/check-register').json();assert rows[0]['number']=='00002468'
    assert client.post(prefix(company)+'/check-register',json={'rows':[{'number':2468}]}).status_code==400
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    assert client.get(prefix(other)+'/check-register').json()==[]
    assert client.post(prefix(other)+'/check-register',json={'rows':[{'number':'00002468','supplier_id':supplier['id']}]}).status_code==400


def test_learning_reset_keeps_accounting_documents_and_other_companies(client,company,supplier):
    writer=client.post(prefix(company)+'/writers',json={'name':'כתב אישי'}).json()['id']
    ident=teach_crop(client,company,supplier,writer=writer)
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    client.post(prefix(other)+'/writers',json={'name':'כתב אחר'})
    assert client.post(prefix(company)+'/reset',json={}).status_code==400
    assert client.post(prefix(company)+'/reset',json={'confirmed':True}).status_code==200
    info=client.get(prefix(company)).json();assert info['examples']==info['writers']==info['models']==[]
    assert len(client.get(prefix(other)).json()['writers'])==1
    doc=get_doc(client,company,ident);assert doc['checks'][0]['number']=='00002468' and not doc['checks'][0].get('writer_id')
    assert client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'fields':{'notes':'עדכון לאחר איפוס'}}).status_code==200


def test_unconfirming_a_crop_withdraws_its_training_label_and_exact_memory(client,company,supplier):
    ident=teach_crop(client,company,supplier)
    doc=get_doc(client,company,ident);check=doc['checks'][0];check['confirmed']=False
    response=client.patch('/api/documents/'+ident,json={'revision':doc['revision'],'checks':[check]})
    assert response.status_code==200,response.text
    info=client.get(prefix(company)).json();assert info['examples'][0]['approved']==0
    result=client.post('/api/documents/'+ident+'/check-region',json={'region':check['region']}).json()
    assert not any(p['engine']=='user-confirmed-exact-crop' for p in result['proposals'])


def test_public_model_installer_accepts_only_fixed_generic_profiles(client):
    info=client.get('/api/local-models').json();assert info['one_model_at_a_time']
    assert {p['id'] for p in info['profiles']}>={'rivok','best','glm-ocr','qwen35-08'}
    assert client.post('/api/local-models/unknown/install').status_code==400
    assert client.post('/api/local-models/rivok/start').status_code==400
    assert client.post('/api/local-models/stop').status_code==200


def test_model_import_rejects_files_from_another_installation(client,company):
    model=pd.init_model(79)
    with tempfile_dir() as directory:
        path=directory/'weights.npz';pd.save(path,model);content=path.read_bytes()
    import hashlib
    manifest={'installation_id':'another-installation','company_id':company['id'],'sha256':hashlib.sha256(content).hexdigest(),'report':{}}
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('weights.npz',content);z.writestr('manifest.json',json.dumps(manifest))
    response=client.post(prefix(company)+'/models/import',files={'file':('personal.zip',archive.getvalue(),'application/zip')})
    assert response.status_code==409
    assert client.get(prefix(company)).json()['models']==[]


def test_supplier_defaults_preserve_currency_observed_in_document(client,company,supplier):
    card=client.get(prefix(company)).json()['suppliers'][0]
    client.post(prefix(company)+'/suppliers/'+supplier['id'],json={'fingerprint':card['fingerprint'],'identity_confirmed':True,'rules':{'currency':'ILS'}})
    pdf=fitz.open(stream=pdf_bytes('FOREIGN-001'),filetype='pdf');pdf[0].insert_text((50,280),'Currency USD');content=pdf.tobytes();pdf.close()
    response=client.post(f"/api/companies/{company['id']}/upload",files={'file':('foreign.pdf',content,'application/pdf')})
    assert response.status_code==200,response.text
    doc=get_doc(client,company,response.json()['ids'][0])
    assert doc['original_fields']['currency']==doc['fields']['currency']=='USD'


def test_check_register_compares_supplier_amount_and_date(client,company,supplier):
    ident=teach_crop(client,company,supplier)
    response=client.post(prefix(company)+'/check-register',json={'rows':[{'number':'00002467','supplier_id':supplier['id'],'amount':'10.00','date':'2020-01-01'},{'number':'00002468','supplier_id':supplier['id'],'amount':'118.00','date':'2026-11-12'}]})
    assert response.status_code==200,response.text
    doc=get_doc(client,company,ident)
    result=client.post('/api/documents/'+ident+'/check-region',json={'region':doc['checks'][0]['region']}).json()
    candidates=[p for p in result['proposals'] if p['engine']=='issued-check-register']
    assert candidates[0]['number']=='00002468'
    assert set(candidates[0]['matched_fields'])=={'supplier','amount','date'}
    assert result['confirmed'] is False


def test_personal_activation_requires_all_heldout_checks_to_match():
    from app.learning import evaluation_qualifies
    assert not evaluation_qualifies({'tested':2,'personal_correct':0,'regressions':0})
    assert not evaluation_qualifies({'tested':3,'personal_correct':2,'regressions':0})
    assert not evaluation_qualifies({'tested':1,'personal_correct':1,'regressions':0})
    assert evaluation_qualifies({'tested':3,'personal_correct':3,'regressions':0})
