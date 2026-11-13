"""Review shortcuts retain accounting locks, data isolation and explicit editing."""
from .conftest import ready_doc,get_doc


def path(company):return f"/api/companies/{company['id']}"


def test_workspace_is_private_and_session_resists_late_updates(client,company,supplier):
    ident=ready_doc(client,company,supplier)
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    prefs={'source_width':60,'font_size':18,'field_order':['number','total'],'keys':{'approve':'F9','next':'PageDown','previous':'PageUp'}}
    assert client.put(path(company)+'/review-preferences',json=prefs).status_code==200
    assert client.put(path(company)+'/review-session',json={'document_id':ident,'stamp':200,'field':'total','editor_scroll':120}).status_code==200
    client.put(path(company)+'/review-session',json={'document_id':ident,'stamp':100,'field':'number'})
    info=client.get(path(company)+'/review-workspace').json()
    assert info['preferences']==prefs and info['session']['field']=='total'
    assert client.get(path(other)+'/review-workspace').json()['session'] is None
    assert client.put(path(other)+'/review-session',json={'document_id':ident,'stamp':300}).status_code==404


def test_snippets_and_flags_do_not_change_financial_data(client,company,supplier):
    ident=ready_doc(client,company,supplier);before=get_doc(client,company,ident)
    assert client.post(path(company)+'/review-snippets',json={'shortcut':'בדקתי','content':'נבדק מול תעודת משלוח','supplier_id':supplier['id']}).status_code==200
    assert client.put('/api/documents/'+ident+'/review-flag',json={'marked':True,'note':'לבדוק צ׳ק מול הספק'}).status_code==200
    info=client.get(path(company)+'/review-workspace').json();assert info['snippets'][0]['shortcut']=='בדקתי' and info['flags'][0]['note']=='לבדוק צ׳ק מול הספק'
    after=get_doc(client,company,ident);assert (after['fields'],after['status'],after['revision'])==(before['fields'],before['status'],before['revision'])
    other=client.post('/api/companies',json={'name':'אחרת'}).json()
    assert client.get(path(other)+'/review-workspace').json()['snippets']==[]
    assert client.post(path(other)+'/review-snippets',json={'shortcut':'x','content':'x','supplier_id':supplier['id']}).status_code==400
    assert client.get('/api/documents/'+ident+'/review-source').json()['tokens']


def test_calculator_is_decimal_exact_and_only_proposes_values(client):
    assert client.post('/api/review-calculator',json={'mode':'net','amount':'100.00','rate':'18'}).json()=={'net':'100.00','vat':'18.00','total':'118.00'}
    assert client.post('/api/review-calculator',json={'mode':'total','amount':'118.00','rate':'18'}).json()=={'net':'100.00','vat':'18.00','total':'118.00'}
    assert client.post('/api/review-calculator',json={'mode':'total','amount':'-118.00','rate':'18'}).json()['net']=='-100.00'
    assert client.post('/api/review-calculator',json={'mode':'net','amount':'NaN','rate':'18'}).status_code==400


def test_bulk_preview_and_apply_are_atomic_and_require_review(client,company,supplier):
    a=ready_doc(client,company,supplier,'BULK-A');b=ready_doc(client,company,supplier,'BULK-B')
    client.post(path(company)+'/approve',json={'ids':[a,b]})
    payload={'ids':[a,b],'fields':{'cost_center':'007'},'only_empty':True}
    preview=client.post(path(company)+'/review-bulk-preview',json=payload).json()
    assert len(preview['rows'])==2 and get_doc(client,company,a)['fields']['cost_center']==''
    revisions={r['id']:r['revision'] for r in preview['rows']}
    changed=get_doc(client,company,b)
    client.patch('/api/documents/'+b,json={'revision':changed['revision'],'fields':{'notes':'שינוי מקביל'}})
    assert client.post(path(company)+'/review-bulk-apply',json={**payload,'revisions':revisions,'confirmed':True}).status_code==409
    assert get_doc(client,company,a)['fields']['cost_center']==''
    preview=client.post(path(company)+'/review-bulk-preview',json=payload).json();revisions={r['id']:r['revision'] for r in preview['rows']}
    assert client.post(path(company)+'/review-bulk-apply',json={**payload,'revisions':revisions,'confirmed':True}).json()['changed']==2
    for ident in (a,b):
        doc=get_doc(client,company,ident);assert doc['fields']['cost_center']=='007' and doc['status']=='review'
    assert client.post(path(company)+'/review-bulk-preview',json={'ids':[a],'fields':{'total':'0'}}).status_code==400


def test_undo_approval_cannot_change_exported_invoice(client,company,supplier):
    ident=ready_doc(client,company,supplier)
    client.post(path(company)+'/approve',json={'ids':[ident]})
    approved=client.get(path(company)+'/review-workspace').json()['last_approval'];assert approved['id']==ident
    payload={'document_id':ident,'revision':approved['revision'],'confirmed':True}
    assert client.post(path(company)+'/review-undo-approval',json=payload).json()['status']=='review'
    assert client.post(path(company)+'/review-undo-approval',json=payload).status_code==409
    client.post(path(company)+'/approve',json={'ids':[ident]})
    approved=get_doc(client,company,ident);client.post(path(company)+'/batches',json={'ids':[ident]})
    assert client.post(path(company)+'/review-undo-approval',json={**payload,'revision':approved['revision']}).status_code==409
