import io
import json
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from .conftest import pdf_bytes, ready_doc, upload_doc, get_doc
from app.domain import extract
from scripts.restore_backup import restore


def test_extract_total_never_uses_subtotal():
    fields = extract('Invoice No. A123\nSubtotal: 100.00\nVAT amount: 18.00\nTotal: 118.00')
    assert fields['net']=='100.00' and fields['total']=='118.00'
    assert extract('Subtotal: 100.00')['total']==''


def test_edit_preserves_supplier_and_original_values(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    before=get_doc(client,company,doc_id)
    response=client.patch(f'/api/documents/{doc_id}',json={'revision':before['revision'],'fields':{'notes':'נבדק'}})
    assert response.status_code==200
    assert response.json()['supplier_id']==supplier['id']
    assert response.json()['original_fields']==before['original_fields']
    rows=client.get(f"/api/companies/{company['id']}/audit").json()
    assert rows[0]['detail']['before']==before['fields']


def test_concurrent_edits_only_one_revision_wins(client,company,supplier):
    doc_id=ready_doc(client,company,supplier)
    revision=get_doc(client,company,doc_id)['revision']
    barrier=threading.Barrier(2)
    def change(note):
        barrier.wait(timeout=5)
        return client.patch(f'/api/documents/{doc_id}',json={'revision':revision,'fields':{'notes':note}}).status_code
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(change,['a','b']))
    assert sorted(results)==[200,409]


def test_approval_and_export_are_atomic(client,company,supplier):
    good=ready_doc(client,company,supplier,'GOOD-001')
    bad=upload_doc(client,company,'BAD-002')
    d=get_doc(client,company,bad)
    client.patch(f'/api/documents/{bad}',json={'revision':d['revision'],'fields':{'total':'119.00'}})
    prefix=f"/api/companies/{company['id']}"
    assert client.post(prefix+'/approve',json={'ids':[good,bad]}).status_code==422
    assert get_doc(client,company,good)['status']=='review'
    client.post(prefix+'/approve',json={'ids':[good]})
    assert client.post(prefix+'/batches',json={'ids':[good,bad]}).status_code==409
    assert get_doc(client,company,good)['status']=='approved'
    assert client.get(prefix+'/batches').json()==[]


def test_restored_split_retains_full_original_page(client,company,tmp_path,monkeypatch):
    monkeypatch.setattr('app.ocr.recognize',lambda *a,**k:('Invoice 123','test',''))
    parent=upload_doc(client,company)
    ids=client.post(f'/api/documents/{parent}/split',json={'regions':[{'x':0,'y':0,'w':.49,'h':1},{'x':.5,'y':0,'w':.5,'h':1}]}).json()['ids']
    archive=tmp_path/'backup.zip';archive.write_bytes(client.get('/api/backup').content)
    target=restore(archive,tmp_path/'restored')
    with sqlite3.connect(target/'invoices.sqlite3') as db:
        source,page=db.execute('SELECT source_id,page FROM documents WHERE id=?',(ids[0],)).fetchone()
    assert (target/'files'/source/f'page-{page}.png').is_file()
    with pytest.raises(ValueError,match='exists'): restore(archive,target)


def test_restore_rejects_traversal(tmp_path):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape','bad')
    with pytest.raises(ValueError,match='Unsafe'): restore(archive,tmp_path/'restore')
    assert not (tmp_path/'escape').exists()


@pytest.mark.parametrize('coordinate',[-1,float('inf'),float('nan'),True,'1'])
def test_invalid_region_values_do_not_reach_crop(client,company,coordinate):
    doc=upload_doc(client,company)
    # JSON has no infinity/NaN; exercise numeric JSON over the wire deliberately.
    body=json.dumps({'region':{'x':coordinate,'y':0,'w':.2,'h':.2}})
    response=client.post(f'/api/documents/{doc}/check-region',content=body,headers={'Content-Type':'application/json'})
    assert response.status_code==400


def test_batch_snapshot_survives_profile_and_supplier_changes(client,company,supplier):
    doc=ready_doc(client,company,supplier)
    prefix=f"/api/companies/{company['id']}"
    client.post(prefix+'/approve',json={'ids':[doc]})
    batch=client.post(prefix+'/batches',json={'ids':[doc]}).json()['id']
    original=client.get(f'/api/batches/{batch}/download?format=csv').content
    client.post(prefix+'/suppliers',json={**supplier,'name':'שם חדש'})
    p=client.get(prefix+'/profile').json();p['delimiter']=';'
    client.put(prefix+'/profile',json=p)
    assert client.get(f'/api/batches/{batch}/download?format=csv').content==original


def test_duplicate_supplier_cards_for_same_tax_id_are_reviewed(client,company,supplier):
    first=ready_doc(client,company,supplier,'DUP-CARD-001')
    second_supplier=client.post(f"/api/companies/{company['id']}/suppliers",json={'code':'OTHER','name':'אותו ספק','tax_id':supplier['tax_id']}).json()
    second=upload_doc(client,company,'DUP-CARD-002')
    doc=get_doc(client,company,second)
    result=client.patch(f'/api/documents/{second}',json={'revision':doc['revision'],'supplier_id':second_supplier['id'],'fields':{'number':'DUP-CARD-001'}})
    assert any('כפילות' in issue for issue in result.json()['issues'])
    assert any('כפילות' in issue for issue in get_doc(client,company,first)['issues'])


def test_invalid_local_host_is_rejected(client):
    assert client.get('/api/companies',headers={'Host':'attacker.example'}).status_code==400
