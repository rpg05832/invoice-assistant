import io
import json
import zipfile
from decimal import Decimal

from .conftest import upload_doc,ready_doc,get_doc


def test_join_is_company_scoped_and_preserves_check_source(client,company,supplier):
    primary=ready_doc(client,company,supplier,'JOIN-1');child=ready_doc(client,company,supplier,'JOIN-2')
    response=client.post(f'/api/documents/{primary}/join',json={'ids':[child]})
    assert response.status_code==200,response.text
    assert len(response.json()['pages'])==2
    assert response.json()['checks'][1]['source_document_id']==child
    assert get_doc(client,company,child)['status']=='archived'
    other=client.post('/api/companies',json={'name':'אחרת'}).json();foreign=upload_doc(client,other)
    assert client.post(f'/api/documents/{primary}/join',json={'ids':[foreign]}).status_code==404
    d=get_doc(client,company,primary)
    assert client.patch(f'/api/documents/{primary}',json={'revision':d['revision'],'checks':d['checks']}).status_code==200
    assert client.post(f'/api/documents/{primary}/unjoin').status_code==200
    assert get_doc(client,company,child)['status']=='review'


def test_line_items_rules_and_ledger_export(client,company,supplier):
    doc=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc)
    items=[{'description':'ציוד','quantity':'2','price':'50','discount':'0','total':'100','item_code':'0001'}]
    response=client.patch(f'/api/documents/{doc}',json={'revision':d['revision'],'fields':{'purpose':'journal','expense_account':'6000','vat_account':'9000'},'items':items,'save_defaults':True})
    assert response.status_code==200 and response.json()['issues']==[]
    second=upload_doc(client,company,'RULE-2');assert get_doc(client,company,second)['fields']['expense_account']=='6000'
    prefix=f"/api/companies/{company['id']}"
    assert client.post(prefix+'/approve',json={'ids':[doc]}).status_code==200
    b=client.post(prefix+'/batches',json={'ids':[doc]}).json()
    entries=b['snapshot']['items'][0]['journal']
    assert sum(Decimal(r['debit'])-Decimal(r['credit']) for r in entries)==0
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/batches/{b['id']}/download?format=zip").content)) as z:
        assert 'ציוד' in z.read('lines.csv').decode('utf-8-sig')
        assert 'journal.csv' in z.namelist()


def test_invalid_items_roll_back_document_edit(client,company,supplier):
    doc=ready_doc(client,company,supplier);d=get_doc(client,company,doc)
    r=client.patch(f'/api/documents/{doc}',json={'revision':d['revision'],'fields':{'number':'SHOULD-NOT-SAVE'},'items':[{'quantity':'2','price':'5','total':'11'}]})
    assert r.status_code==400
    assert get_doc(client,company,doc)['revision']==d['revision']


def test_generic_lists_and_inactive_accounts(client,company,supplier):
    prefix=f"/api/companies/{company['id']}"
    assert client.post(prefix+'/records/accounts/import',json={'rows':[{'code':'6000','name':'הוצאות','active':False}]}).status_code==200
    doc=ready_doc(client,company,supplier);d=get_doc(client,company,doc)
    r=client.patch(f'/api/documents/{doc}',json={'revision':d['revision'],'fields':{'expense_account':'6000'}})
    assert any('פעיל' in i for i in r.json()['issues'])
    old=client.get(prefix+'/records/accounts').json()[0]['id']
    client.post(prefix+'/records/accounts/import',json={'rows':[{'code':'6000','name':'עודכן','active':True}]})
    assert client.get(prefix+'/records/accounts').json()[0]['id']==old


def test_fixed_profile_and_companion_files(client,company,supplier):
    doc=ready_doc(client,company,supplier);prefix=f"/api/companies/{company['id']}"
    p={'format':'fixed','filename':'MOVEIN.DOC','columns':[{'field':'supplier_code','title':'כרטיס','max_length':8},{'field':'number','title':'מספר','max_length':20}],'delimiter':',','encoding':'cp1255','date_format':'%d%m%Y','companion':{'MOVEIN.PRM':'client supplied test layout'}}
    assert client.put(prefix+'/profile',json=p).status_code==200
    client.post(prefix+'/approve',json={'ids':[doc]});batch=client.post(prefix+'/batches',json={'ids':[doc]}).json()['id']
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/batches/{batch}/download?format=zip').content)) as z:
        assert z.read('MOVEIN.DOC').startswith(b'00042   ')
        assert 'MOVEIN.PRM' in z.namelist()


def test_cloud_off_budget_and_conflicts(client,company,supplier,monkeypatch):
    doc=ready_doc(client,company,supplier);prefix=f"/api/companies/{company['id']}"
    assert client.post(f'/api/documents/{doc}/recognize',json={'confirmed':True}).status_code==409
    config={'mode':'manual','provider':'openai','model':'test-vision','monthly_budget':'1.00','max_cost_per_call':'1.00','api_key':'secret-test'}
    assert client.put(prefix+'/recognition-settings',json=config).status_code==200
    public=client.get(prefix+'/recognition-settings').json();assert public['has_key'] and 'secret-test' not in json.dumps(public)
    monkeypatch.setattr('app.cloud.provider_call',lambda *a:{'fields':{'total':'119.00'},'checks':['000123'],'items':[]})
    result=client.post(f'/api/documents/{doc}/recognize',json={'confirmed':True})
    assert result.status_code==200 and 'total' in result.json()['conflicts']
    assert get_doc(client,company,doc)['fields']['total']=='118.00'
    assert client.post(f'/api/documents/{doc}/recognize',json={'confirmed':True}).status_code==409


def test_receipt_report_requires_current_export_and_explicit_confirmation(client,company,supplier):
    doc=ready_doc(client,company,supplier);prefix=f"/api/companies/{company['id']}"
    client.post(prefix+'/approve',json={'ids':[doc]});batch=client.post(prefix+'/batches',json={'ids':[doc]}).json()['id']
    rows=[{'document_id':doc,'status':'received','reason':'דוח 123'}]
    assert client.post(f'/api/batches/{batch}/receipt-report',json={'rows':rows}).status_code==200
    assert get_doc(client,company,doc)['status']=='exported'
    assert client.post(f'/api/batches/{batch}/receipt-report',json={'rows':rows,'confirmed':True}).status_code==200
    assert get_doc(client,company,doc)['status']=='received'


def test_profile_verification_requires_evidence_and_is_invalidated(client,company,supplier):
    doc=ready_doc(client,company,supplier);prefix=f"/api/companies/{company['id']}"
    client.post(prefix+'/approve',json={'ids':[doc]});batch=client.post(prefix+'/batches',json={'ids':[doc]}).json()['id']
    evidence={'batch_id':batch,'target_version':'test','evidence':'חברת בדיקה','field_checks':dict.fromkeys(['supplier_code','number','checks','hebrew','net','vat','total'],True)}
    assert client.post(prefix+'/verify-profile',json=evidence).status_code==400
    client.post(f'/api/batches/{batch}/receipt',json={'document_id':doc,'status':'received','reason':'נבדק'})
    assert client.post(prefix+'/verify-profile',json=evidence).status_code==200
    p=client.get(prefix+'/profile').json();p['delimiter']=';';client.put(prefix+'/profile',json=p)
    assert client.get(prefix+'/integration').json()['verification'] is None


def test_payment_requires_explicit_confirmation_and_confirmed_checks(client,company,supplier):
    doc=ready_doc(client,company,supplier)
    p={'amount':'118','date':'2026-11-12','bank_account':'1000'}
    assert client.post(f'/api/documents/{doc}/payment',json=p).status_code==400
    assert client.post(f'/api/documents/{doc}/payment',json={**p,'confirmed':True}).status_code==200


def test_field_source_coordinates_from_pdf_and_metrics(client,company,supplier):
    doc=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc)
    assert 'number' in d['field_sources']
    r=client.post(f'/api/documents/{doc}/evaluate',json={'truth':{'number':'INV-001','total':'118.00'}})
    assert r.status_code==200
    metrics=client.get(f"/api/companies/{company['id']}/metrics").json()
    assert metrics['exact_accuracy']['number']['rate']==1


def test_failure_mode_proposes_without_approving(client,company,monkeypatch):
    from PIL import Image
    p=f"/api/companies/{company['id']}"
    config={'mode':'on_failure','provider':'openai','model':'test','monthly_budget':'1','max_cost_per_call':'.10','api_key':'test-key'}
    assert client.put(p+'/recognition-settings',json=config).status_code==200
    monkeypatch.setattr('app.ocr.recognize',lambda *a,**k:('','failed','test'))
    monkeypatch.setattr('app.vision.tokens',lambda *a:[])
    monkeypatch.setattr('app.cloud.provider_call',lambda *a:{'fields':{'number':'PROPOSED-1'},'checks':[],'items':[]})
    content=io.BytesIO();Image.new('RGB',(400,400),'white').save(content,'PNG')
    response=client.post(p+'/upload',files={'file':('empty.png',content.getvalue(),'image/png')})
    assert response.status_code==200 and len(response.json()['additional_proposals'])==1
    doc=get_doc(client,company,response.json()['ids'][0])
    assert doc['status']=='review' and doc['fields']['number']==''


def test_check_specific_provider_is_used_without_exposing_key(client,company,supplier,monkeypatch):
    doc=ready_doc(client,company,supplier);p=f"/api/companies/{company['id']}"
    config={'mode':'manual','provider':'openai','model':'document-test','monthly_budget':'1','max_cost_per_call':'.10','api_key':'document-key','check_provider':'gemini','check_model':'check-test','check_api_key':'check-key'}
    assert client.put(p+'/recognition-settings',json=config).status_code==200
    captured=[]
    def invoke(config,key,image):
        captured.append((config['provider'],config['model'],key))
        return {'fields':{},'checks':['0001234'],'items':[]}
    monkeypatch.setattr('app.cloud.provider_call',invoke)
    result=client.post(f'/api/documents/{doc}/recognize',json={'confirmed':True,'action':'check','region':{'x':0,'y':0,'w':.3,'h':.3}})
    assert result.status_code==200
    assert captured==[('gemini','check-test','check-key')]
    assert 'check-key' not in json.dumps(client.get(p+'/recognition-settings').json())


def test_fixed_input_preserves_codes_and_rejects_wrong_width(client,company):
    p=f"/api/companies/{company['id']}/import-fixed-preview"
    response=client.post(p,files={'file':('cards.dat','00042ספק       '.encode('cp1255'))},data={'widths':'5,10','encoding':'cp1255'})
    assert response.status_code==200 and response.json()['sheets']['Fixed width'][1][0]=='00042'
    assert client.post(p,files={'file':('cards.dat',b'bad')},data={'widths':'5,10'}).status_code==400


def test_partial_supplier_import_keeps_inactive_state_and_tax_id(client,company,supplier):
    p=f"/api/companies/{company['id']}"
    client.post(p+'/suppliers',json={**supplier,'active':False})
    client.post(p+'/suppliers/import',json={'rows':[{'code':supplier['code'],'name':'מעודכן'}]})
    current=client.get(p+'/suppliers').json()[0]
    assert current['active']==0 and current['tax_id']==supplier['tax_id']


def test_model_registry_distinguishes_rejected_hebrew_model(client):
    rows=client.get('/api/model-status').json()['engines']
    assert next(r for r in rows if r['id']=='qwen3-vl-2b-q4')['selected'] is False
    assert next(r for r in rows if r['id']=='indra-rtl')['hebrew'] is True


def test_digits_only_model_cannot_return_hebrew_fields(monkeypatch):
    from app.cloud import provider_call
    from fastapi import HTTPException
    import pytest
    class Result:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':'{"supplier_name":"wrong Hebrew","total":"999.00","checks":["000123",123]}'}}]}
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):assert url.startswith('http://127.0.0.1:8090/');return Result()
    monkeypatch.setattr('app.cloud.httpx.Client',Client)
    result=provider_call({'provider':'local_digits','model':'Qwen3VL-2B-Instruct','action':'check'},'',b'test')
    assert result['fields']=={} and result['checks']==['000123']
    with pytest.raises(HTTPException):provider_call({'provider':'local_digits','model':'test','action':'document'},'',b'test')


def test_credit_csv_preserves_negative_decimal_without_formula_prefix(client,company,supplier):
    import csv
    doc=ready_doc(client,company,supplier);d=get_doc(client,company,doc)
    client.patch(f'/api/documents/{doc}',json={'revision':d['revision'],'fields':{'kind':'credit','net':'-100.00','vat':'-18.00','total':'-118.00'}})
    p=f"/api/companies/{company['id']}";client.post(p+'/approve',json={'ids':[doc]});batch=client.post(p+'/batches',json={'ids':[doc]}).json()['id']
    content=client.get(f'/api/batches/{batch}/download?format=csv').content.decode('utf-8-sig')
    assert list(csv.DictReader(io.StringIO(content)))[0]['total']=='-118.00'


def test_rotate_preserves_original_source_and_field_values(client,company,supplier):
    from PIL import Image
    doc=ready_doc(client,company,supplier);d=get_doc(client,company,doc)
    original=client.get(f'/api/documents/{doc}/source').content
    response=client.post(f'/api/documents/{doc}/rotate',json={'angle':90,'revision':d['revision']})
    assert response.status_code==200
    assert response.json()['fields']==d['fields']
    assert client.get(f'/api/documents/{doc}/source').content==original
    with Image.open(io.BytesIO(client.get(f'/api/documents/{doc}/image').content)) as image:assert image.width>image.height
