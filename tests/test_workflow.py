import csv
import io
import json
import sqlite3
import zipfile
from decimal import Decimal
from openpyxl import load_workbook, Workbook
from .conftest import pdf_bytes, upload_doc, ready_doc, get_doc

def test_full_workflow_and_leading_zeros(client, company, supplier):
    doc_id = ready_doc(client, company, supplier)
    prefix = f"/api/companies/{company['id']}"
    assert client.post(prefix+'/approve', json={'ids':[doc_id]}).status_code == 200
    batch = client.post(prefix+'/batches', json={'ids':[doc_id]}).json()
    csv_result = client.get(f"/api/batches/{batch['id']}/download?format=csv")
    rows = list(csv.DictReader(io.StringIO(csv_result.content.decode('utf-8-sig'))))
    assert rows[0]['supplier_code'] == '00042'
    assert rows[0]['checks'] == '00001234'
    assert rows[0]['total'] == '118.00'
    xlsx = client.get(f"/api/batches/{batch['id']}/download?format=xlsx")
    book = load_workbook(io.BytesIO(xlsx.content))
    assert book['צ׳קים']['B2'].value == '00001234'
    assert book['צ׳קים']['B2'].data_type == 's'
    assert get_doc(client,company,doc_id)['status'] == 'exported'
    assert client.post(prefix+'/batches',json={'ids':[doc_id]}).status_code == 409
    r = client.post(f"/api/batches/{batch['id']}/receipt", json={'document_id':doc_id,'status':'received','reason':'נבדק מול פקודת יומן 123'})
    assert r.status_code == 200
    assert get_doc(client,company,doc_id)['status'] == 'received'

def test_partial_rejection_and_retry_only_rejected(client, company, supplier):
    a = ready_doc(client,company,supplier,'1001'); b = ready_doc(client,company,supplier,'1002')
    prefix=f"/api/companies/{company['id']}"
    client.post(prefix+'/approve',json={'ids':[a,b]})
    batch=client.post(prefix+'/batches',json={'ids':[a,b]}).json()['id']
    old_csv=client.get(f'/api/batches/{batch}/download?format=csv').content
    for doc_id,status in [(a,'received'),(b,'rejected')]:
        assert client.post(f'/api/batches/{batch}/receipt',json={'document_id':doc_id,'status':status,'reason':'תוצאה מדוח בדיקה'}).status_code==200
    d=get_doc(client,company,b)
    assert client.patch(f'/api/documents/{b}',json={'revision':d['revision'],'supplier_id':supplier['id'],'fields':{'notes':'תוקן'}}).status_code==200
    assert client.post(prefix+'/approve',json={'ids':[b]}).status_code==200
    retry=client.post(prefix+'/batches',json={'ids':[b]}).json()['id']
    assert retry!=batch
    assert client.get(f'/api/batches/{batch}/download?format=csv').content==old_csv
    assert client.post(f'/api/batches/{batch}/receipt',json={'document_id':b,'status':'received','reason':'מנה ישנה'}).status_code==409
    assert len(client.get(prefix+'/batches').json()[0]['items'])==1

def test_file_duplicate_is_company_scoped(client,company):
    content=pdf_bytes()
    assert client.post(f"/api/companies/{company['id']}/upload",files={'file':('original.pdf',content,'application/pdf')}).status_code==200
    assert client.post(f"/api/companies/{company['id']}/upload",files={'file':('copy.pdf',content,'application/pdf')}).status_code==409
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    assert upload_doc(client,other)

def test_accounting_duplicate_and_archive_resolution(client,company,supplier):
    first=ready_doc(client,company,supplier,'DUP-1')
    second=upload_doc(client,company,'DUP-2')
    d=get_doc(client,company,second)
    client.patch(f'/api/documents/{second}',json={'revision':d['revision'],'supplier_id':supplier['id'],'fields':{'number':'DUP-1'}})
    response=client.post(f"/api/companies/{company['id']}/approve",json={'ids':[first]})
    assert response.status_code==422 and 'כפילות' in response.text
    client.post(f'/api/documents/{second}/archive')
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[first]}).status_code==200

def test_cross_company_supplier_and_approval_blocked(client,company,supplier):
    other=client.post('/api/companies',json={'name':'חברה אחרת'}).json()
    doc_id=upload_doc(client,other)
    d=get_doc(client,other,doc_id)
    assert client.patch(f'/api/documents/{doc_id}',json={'revision':d['revision'],'supplier_id':supplier['id']}).status_code==400
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[doc_id]}).status_code==404

def test_wrong_recipient_and_unbalanced_amounts_block_approval(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc_id)
    client.patch(f'/api/documents/{doc_id}',json={'revision':d['revision'],'supplier_id':supplier['id'],'fields':{'recipient_tax_id':'999999999','total':'119.00'}})
    r=client.post(f"/api/companies/{company['id']}/approve",json={'ids':[doc_id]})
    assert r.status_code==422
    assert 'אחרת' in r.text and 'מסתכמים' in r.text

def test_credit_sign_and_decimal_precision(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc_id)
    r=client.patch(f'/api/documents/{doc_id}',json={'revision':d['revision'],'supplier_id':supplier['id'],'fields':{'kind':'credit'}})
    assert any('שליליים' in i for i in r.json()['issues'])
    r=client.patch(f'/api/documents/{doc_id}',json={'revision':r.json()['revision'],'supplier_id':supplier['id'],'fields':{'net':'-100.00','vat':'-18.00','total':'-118.00'}})
    assert r.json()['issues']==[]
    r=client.patch(f'/api/documents/{doc_id}',json={'revision':r.json()['revision'],'supplier_id':supplier['id'],'fields':{'total':'-118.001'}})
    assert r.json()['issues']

def test_check_confirmation_and_concurrent_edits(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc_id)
    payload={'revision':d['revision'],'supplier_id':supplier['id'],'checks':[{'number':'0000123','confirmed':False}]}
    r=client.patch(f'/api/documents/{doc_id}',json=payload)
    assert any('לאשר' in i for i in r.json()['issues'])
    assert client.patch(f'/api/documents/{doc_id}',json=payload).status_code==409

def test_suppliers_import_atomic_and_preserves_aliases(client,company,supplier):
    prefix=f"/api/companies/{company['id']}"
    doc_id=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc_id)
    client.patch(f'/api/documents/{doc_id}',json={'revision':d['revision'],'supplier_id':supplier['id'],'remember':True})
    rows=[{'code':'NEW','name':'חדש'},{'code':'BAD','name':''}]
    assert client.post(prefix+'/suppliers/import',json={'rows':rows}).status_code==400
    assert len(client.get(prefix+'/suppliers').json())==1
    assert client.post(prefix+'/suppliers/import',json={'rows':[{'code':'00042','name':'שם מעודכן','tax_id':'987654321'}]}).status_code==200
    assert get_doc(client,company,doc_id)['supplier']['id']==supplier['id']

def test_import_excel_preserves_zero_formatted_codes(client,company):
    book=Workbook();book.active.append(['code','name','tax_id']);book.active.append([42,'ספק',123456789]);book.active['A2'].number_format='00000'
    content=io.BytesIO();book.save(content)
    r=client.post(f"/api/companies/{company['id']}/suppliers/preview",files={'file':('suppliers.xlsx',content.getvalue())})
    assert r.status_code==200
    assert r.json()['sheets']['Sheet'][1][0]=='00042'

def test_revalidate_inactive_supplier_at_export(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    prefix=f"/api/companies/{company['id']}"
    client.post(prefix+'/approve',json={'ids':[doc_id]})
    client.post(prefix+'/suppliers',json={**supplier,'active':False})
    assert client.post(prefix+'/batches',json={'ids':[doc_id]}).status_code==422

def test_custom_profile_length_and_formula_safety(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    prefix=f"/api/companies/{company['id']}"
    d=get_doc(client,company,doc_id)
    client.patch(f'/api/documents/{doc_id}',json={'revision':d['revision'],'supplier_id':supplier['id'],'fields':{'notes':'=HYPERLINK("https://example.com")'}})
    client.post(prefix+'/approve',json={'ids':[doc_id]})
    profile={'columns':[{'field':'notes','title':'Notes','max_length':5}],'delimiter':';','encoding':'utf-8-sig','date_format':'%d/%m/%Y'}
    assert client.put(prefix+'/profile',json=profile).status_code==200
    assert client.post(prefix+'/batches',json={'ids':[doc_id]}).status_code==400
    profile['columns'][0]['max_length']=0
    client.put(prefix+'/profile',json=profile)
    batch=client.post(prefix+'/batches',json={'ids':[doc_id]}).json()['id']
    r=client.get(f'/api/batches/{batch}/download?format=xlsx')
    cell=load_workbook(io.BytesIO(r.content)).active['A2']
    assert cell.data_type=='s' and cell.value.startswith("'=HYPERLINK")

def test_multi_page_and_original_download(client,company):
    first=upload_doc(client,company,pages=2)
    docs=client.get(f"/api/companies/{company['id']}/documents").json()
    assert len(docs)==2 and {d['page'] for d in docs}=={1,2}
    assert all(d['engine']=='pdf-text' for d in docs)
    assert client.get(f'/api/documents/{first}/source').content.startswith(b'%PDF-')
    assert client.get(f'/api/documents/{first}/image').content.startswith(b'\x89PNG')

def test_split_and_check_region_provenance(client,company,monkeypatch):
    monkeypatch.setattr('app.ocr.recognize',lambda path,digits=False: ('0000987' if digits else 'Invoice 12345','test-ocr',''))
    doc_id=upload_doc(client,company)
    regions=[{'x':0,'y':0,'w':.49,'h':1},{'x':.5,'y':0,'w':.5,'h':1,'rotation':90}]
    r=client.post(f'/api/documents/{doc_id}/split',json={'regions':regions})
    assert r.status_code==200
    children=[get_doc(client,company,i) for i in r.json()['ids']]
    assert all(d['parent_id']==doc_id and d['region'] for d in children)
    assert get_doc(client,company,doc_id)['status']=='archived'
    r=client.post(f"/api/documents/{children[0]['id']}/check-region",json={'region':{'x':.8,'y':.8,'w':.15,'h':.15}})
    assert r.status_code==200 and r.json()['number']=='0000987' and r.json()['confirmed'] is False
    assert client.post(f'/api/documents/{doc_id}/check-region',json={'region':{'x':-1,'y':0,'w':1,'h':1}}).status_code==400

def test_backup_contains_consistent_database_and_sources(client,company,tmp_path):
    doc_id=upload_doc(client,company)
    r=client.get('/api/backup'); assert r.status_code==200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert 'invoices.sqlite3' in z.namelist()
        p=tmp_path/'backup.sqlite3';p.write_bytes(z.read('invoices.sqlite3'))
        with sqlite3.connect(p) as c:
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert c.execute('SELECT id FROM documents').fetchone()[0]==doc_id
        assert any(n.endswith('/original') for n in z.namelist())

def test_delivery_reconciliation_company_scoped(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    d=get_doc(client,company,doc_id)
    client.patch(f'/api/documents/{doc_id}',json={'revision':d['revision'],'supplier_id':supplier['id'],'fields':{'delivery_refs':'00001, 00002'}})
    prefix=f"/api/companies/{company['id']}"
    client.post(prefix+'/deliveries',json={'rows':[{'supplier_code':'00042','number':'00001'},{'supplier_code':'00042','number':'00003'}]})
    rows=client.get(prefix+'/deliveries').json()
    assert [len(r['matches']) for r in rows]==[1,0]

def test_cross_origin_write_rejected(client):
    r=client.post('/api/companies',json={'name':'injected'},headers={'Origin':'https://attacker.example'})
    assert r.status_code==403

def test_unknown_document_and_invalid_file(client,company):
    assert client.get('/api/documents/missing/image').status_code==404
    assert client.post(f"/api/companies/{company['id']}/upload",files={'file':('evil.html',b'<script>bad()</script>')}).status_code==400

def test_empty_database_has_no_fabricated_documents(client):
    assert client.get('/api/companies').json()==[]
    assert client.get('/api/health').json()['ok']
    assert all(not target['verified'] for target in client.get('/api/catalog').json()['targets'])

