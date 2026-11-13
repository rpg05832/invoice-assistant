import io
import fitz
import pytest
from fastapi.testclient import TestClient
from app.main import create_app

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'data')) as c:
        yield c

@pytest.fixture
def company(client):
    return client.post('/api/companies', json={'name': 'חברת בדיקה', 'tax_id': '123456789', 'target': 'חשבשבת H-ERP'}).json()

@pytest.fixture
def supplier(client, company):
    value=client.post(f"/api/companies/{company['id']}/suppliers", json={'code': '00042', 'name': 'ספק בדיקה', 'tax_id': '987654321'}).json()
    confirm_supplier(client,company,value)
    return value

def pdf_bytes(number='INV-001', pages=1):
    pdf = fitz.open()
    for i in range(pages):
        page = pdf.new_page()
        page.insert_text((50, 60), f'Invoice No. {number}\nDate 12/11/2026\nVAT ID 987654321\nSubtotal: 100.00\nVAT amount: 18.00\nTotal: 118.00\nPage {i+1}', fontsize=14)
    content = pdf.tobytes(); pdf.close()
    return content

def upload_doc(client, company, number='INV-001', pages=1):
    r = client.post(f"/api/companies/{company['id']}/upload", files={'file': ('invoice.pdf', pdf_bytes(number,pages), 'application/pdf')})
    assert r.status_code == 200, r.text
    return r.json()['ids'][0]

def get_doc(client, company, doc_id):
    return next(d for d in client.get(f"/api/companies/{company['id']}/documents").json() if d['id'] == doc_id)

def ready_doc(client, company, supplier, number='INV-001'):
    doc_id = upload_doc(client, company, number)
    d = get_doc(client, company, doc_id)
    r = client.patch(f'/api/documents/{doc_id}', json={'revision': d['revision'], 'supplier_id': supplier['id'], 'fields': {'supplier_name': 'שם שונה במסמך', 'number': number, 'date': '2026-11-12', 'net': '100.00', 'vat': '18.00', 'total': '118.00', 'currency': 'ILS', 'kind': 'invoice'}, 'checks': [{'number':'00001234','confirmed':True,'region':None}]})
    assert r.status_code == 200, r.text
    assert r.json()['issues'] == [], r.text
    return doc_id



def confirm_supplier(client,company,supplier):
    info=client.get(f"/api/companies/{company['id']}/learning").json()
    card=next(s for s in info["suppliers"] if s["id"]==supplier["id"])
    response=client.post(f"/api/companies/{company['id']}/learning/suppliers/{supplier['id']}",json={"fingerprint":card["fingerprint"],"identity_confirmed":True,"rules":{}})
    assert response.status_code==200,response.text
