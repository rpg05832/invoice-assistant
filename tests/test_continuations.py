import fitz
import pytest
from app.continuations import page_marker,groups
from .conftest import get_doc,pdf_bytes,upload_doc


def continuation_pdf(order=(3,1,2),number='MULTI-004',markers=True):
    pdf=fitz.open()
    for n in order:
        page=pdf.new_page()
        text=f'Office Supplies Ltd\nVAT ID 987654321\nInvoice No. {number}\nDate 12/11/2026\n'
        text+=('Subtotal: 100.00\nVAT amount: 18.00\nTotal: 118.00\n' if n==3 else 'Item price: 20.00\n')
        text+=f'Page {n} of 3' if markers else f'Page {n}'
        page.insert_text((50,60),text,fontsize=14)
    data=pdf.tobytes();pdf.close();return data


def test_complete_out_of_order_pdf_becomes_one_reviewable_invoice(client,company):
    r=client.post(f"/api/companies/{company['id']}/upload",files={'file':('pages.pdf',continuation_pdf(),'application/pdf')})
    assert r.status_code==200,r.text
    assert r.json()['pages']==3 and len(r.json()['joined'])==1
    docs=[get_doc(client,company,i) for i in r.json()['ids']]
    main=next(d for d in docs if d['status']=='review')
    assert sum(d['status']=='archived' for d in docs)==2
    assert [p['page'] for p in main['pages']]==[2,3,1]
    assert main['fields']['total']=='118.00'
    origin=main['field_sources']['total']['image_document_id']
    assert get_doc(client,company,origin)['page']==1
    assert all(client.get('/api/documents/'+p['id']+'/image').status_code==200 for p in main['pages'])
    reread=client.post('/api/documents/'+main['id']+'/local-reread').json()
    assert reread['pages']==3 and reread['fields']['total']=='118.00'
    assert reread['field_sources']['total']['source_document_id']==origin
    assert get_doc(client,company,main['id'])['fields']==main['fields']
    assert client.post('/api/documents/'+main['id']+'/unjoin').status_code==200
    assert all(get_doc(client,company,i)['status']=='review' for i in r.json()['ids'])


@pytest.mark.parametrize('order,markers', [((1,2),True),((1,1,3),True),((1,2,3),False)])
def test_incomplete_ambiguous_or_unmarked_pages_are_never_automatically_joined(client,company,order,markers):
    r=client.post(f"/api/companies/{company['id']}/upload",files={'file':('pages.pdf',continuation_pdf(order=order,markers=markers),'application/pdf')})
    assert r.status_code==200 and r.json()['joined']==[]
    assert all(get_doc(client,company,i)['status']=='review' for i in r.json()['ids'])


def test_conflicting_supplier_date_and_number_do_not_form_a_group():
    template={'source_id':'one','region':None,'page':1,'ocr_text':'דף 1 מתוך 2','fields':{'number':'A-2','kind':'invoice','supplier_tax_id':'987654321','date':'2026-11-12'}}
    second={**template,'page':2,'ocr_text':'דף 2 מתוך 2','fields':dict(template['fields'])}
    assert len(groups([template,second]))==1
    assert len(groups([template,{**second,'fields':{**second['fields'],'kind':'tax_receipt'}}]))==1
    assert groups([template,{**second,'fields':{**second['fields'],'kind':'credit'}}])==[]
    for key,value in [('supplier_tax_id','111111111'),('date','2026-11-13'),('number','B-3')]:
        assert groups([template,{**second,'fields':{**second['fields'],key:value}}])==[]
    assert page_marker('דף 3 מתוך 3\nדף 3 מתוך 3')==(3,3)
    assert page_marker('עמוד ג מתור 2') is None
    assert page_marker('Page 1 of 3\nPage 2 of 3') is None


def test_existing_reviewed_pages_group_explicitly_without_overwriting_edits(client,company):
    ids=[]
    for n in (3,1,2):
        ident=upload_doc(client,company,f'OLD-{n}');ids.append(ident)
    import sqlite3,json
    snapshots={ident:get_doc(client,company,ident) for ident in ids}
    with sqlite3.connect(client.app.state.data_dir/'invoices.sqlite3') as db:
        source=db.execute('SELECT source_id FROM documents WHERE id=?',(ids[0],)).fetchone()[0]
        for n,ident in enumerate(ids,1):
            d=snapshots[ident];fields={**d['fields'],'number':'EXISTING-77','notes':'personal edit'}
            db.execute('UPDATE documents SET source_id=?,page=?,fields=?,ocr_text=?,revision=4 WHERE id=?',(source,n,json.dumps(fields),f'Page {n} of 3',ident))
    suggested=client.get(f"/api/companies/{company['id']}/grouping-suggestions").json()
    group=next(g for g in suggested if g.get('continuation'))
    revisions={d['id']:d['revision'] for d in group['documents']}
    assert client.post('/api/documents/'+ids[0]+'/join',json={'ids':ids[1:],'_automatic':True}).status_code==409
    assert client.post('/api/documents/'+ids[0]+'/join',json={'ids':ids[1:],'revisions':{**revisions,ids[0]:1}}).status_code==409
    joined=client.post('/api/documents/'+ids[0]+'/join',json={'ids':ids[1:],'revisions':revisions})
    assert joined.status_code==200,joined.text
    assert joined.json()['fields']['notes']=='personal edit'
