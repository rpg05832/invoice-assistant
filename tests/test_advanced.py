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
