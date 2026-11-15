import fitz
import pytest
from app.domain import extract
from app import document_reading
from .conftest import upload_doc,get_doc


def test_supplier_name_ignores_print_banner_and_digital_signature_before_issuer():
    fields=extract('Page 1 of 1 הדפסת חשבונית מקורית\nמסמך ממוחשב חתום\nדיגיטלית ומאושר\nעל ידי גורם\nאופק אל-גל בע"מ אופק\nרחוב הראשונים 158\nטלפון: 08-1111111\nעוסק מורשה: 987654321\nלכבוד: לקוח אחר\nחשבונית מס מרכזת 76543210 מקור')
    assert fields['supplier_name']=='אופק אל-גל בע"מ'
    assert fields['supplier_tax_id']=='987654321' and fields['number']=='76543210'


def test_indra_reads_unicode_image_paths_without_native_filename_decoder(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from PIL import Image
    from app import vision
    root=tmp_path/'מודלים בעברית';models=root/'models/indra';models.mkdir(parents=True);(models/'rec_rtl.onnx').write_bytes(b'model placeholder')
    image=tmp_path/'מסמך בעברית.png';Image.new('RGB',(120,80),'white').save(image)
    seen=[]
    def read(gray):
        seen.append(gray.shape);assert gray.dtype.name=='uint8' and gray.flags.c_contiguous
        return SimpleNamespace(text='חשבונית מס')
    monkeypatch.setattr(vision,'assets',lambda:root)
    monkeypatch.setitem(sys.modules,'indra_ocr',SimpleNamespace(load=lambda **kwargs:SimpleNamespace(ocr=read)))
    result=vision.indra_recognize(image)
    assert result['available'] and result['text']=='חשבונית מס' and seen==[(80,120)]


@pytest.mark.parametrize('title',['חשבונית מס מספר :','חשבונית on מספר :','מספר :'])
def test_supplier_header_and_slash_number_survive_ocr_title_noise(title):
    fields=extract('אופק שירותי חשבונאות עוסק מורשה 123456782\nכתובת: רחוב ראשון 12\n'+title+' 07/000531 מקור\nלכבוד: לקוח אחר\nשק מספר 9955')
    assert fields['number']=='07/000531' and fields['supplier_name']=='אופק שירותי חשבונאות'
    assert fields['supplier_tax_id']=='123456782'


@pytest.mark.parametrize('net_label',['לפני מע״מ','ללא מע״מ','בלי מע״מ','לא מע״מ'])
def test_inclusive_exclusive_labels_do_not_turn_discount_zero_into_vat(net_label):
    fields=extract(f'סך הכול כולל מע״מ 1,170.00\nהנחה: 0.00\nסה״כ לתשלום 1,170.00\nסה״כ {net_label} 1,000.00\nמע״מ 17.00% 170.00')
    assert (fields['net'],fields['vat'],fields['total'])==('1000.00','170.00','1170.00')


def test_spatial_rows_keep_including_vat_as_one_label():
    words=[]
    for y,labels,value in [(.5,['סה״כ','כולל','מע״מ'],'1170.00'),(.6,['לפני','מע״מ'],'1000.00'),(.7,['מע״מ'],'170.00')]:
        words.extend({'text':text,'x':.9-i*.08,'y':y,'w':.06,'h':.02,'rotation':0} for i,text in enumerate(labels))
        words.append({'text':value,'x':.4,'y':y,'w':.1,'h':.02,'rotation':0})
    fields=extract('סה״כ כולל מע״מ 1170.00\nהנחה 0.00',words)
    assert (fields['net'],fields['vat'],fields['total'])==('1000.00','170.00','1170.00')
    # Arithmetic alone must never invent an unread component.
    assert extract('לפני מע״מ 1000.00\nכולל מע״מ 1170.00')['vat']==''
    assert extract('לפני מע״מ 100.00\nמע״מ 18.00\nסה״כ 118.00')['total']=='118.00'


def test_initial_and_retry_checks_use_the_same_unconfirmed_proposals(client,company,monkeypatch):
    region={'x':.2,'y':.5,'w':.4,'h':.1,'rotation':0,'reason':'synthetic ink'}
    monkeypatch.setattr('app.vision.colored_digit_regions',lambda path:[region])
    monkeypatch.setattr('app.vision.tokens',lambda *a,**k:[])
    monkeypatch.setattr('app.ocr.recognize',lambda *a,**k:('','test',''))
    monkeypatch.setattr('app.vision.digit_proposal',lambda path:{'engine':'other','number':'','available':True})
    monkeypatch.setattr('app.learning.multi_proposals',lambda path:[{'engine':'sequence-test','number':'00007531','available':True}])
    ident=upload_doc(client,company)
    d=get_doc(client,company,ident);initial=next(p for p in d['suggested_checks'] if p['engine']=='sequence-test')
    retry=client.post('/api/documents/'+ident+'/check-suggestions').json()['candidates'][0]
    focused=client.post('/api/documents/'+ident+'/check-region',json={'region':{k:region[k] for k in ('x','y','w','h','rotation')}}).json()
    assert initial['number']==retry['number']==focused['number']=='00007531'
    assert not any(p['confirmed'] for p in (initial,retry,focused)) and get_doc(client,company,ident)['checks']==[]


def test_field_region_is_a_scoped_read_without_document_changes(client,company,monkeypatch):
    ident=upload_doc(client,company);before=get_doc(client,company,ident)
    monkeypatch.setattr(document_reading,'read_region',lambda *a,**k:('07/000531','tesseract','',[]))
    region={'x':.1,'y':.1,'w':.4,'h':.1,'rotation':0}
    result=client.post('/api/documents/'+ident+'/field-region',json={'field':'number','region':region})
    assert result.status_code==200 and result.json()['values']==['07/000531'] and not result.json()['confirmed']
    assert get_doc(client,company,ident)['fields']==before['fields'] and get_doc(client,company,ident)['revision']==before['revision']
    other=client.post('/api/companies',json={'name':'אחרת'}).json();foreign=upload_doc(client,other)
    assert client.post('/api/documents/'+ident+'/field-region',json={'field':'number','region':region,'source_document_id':foreign}).status_code==404
    assert client.post('/api/documents/'+ident+'/field-region',json={'field':'number','region':{**region,'x':-1}}).status_code==400


def test_zero_vat_with_positive_printed_rate_requires_explicit_review(client,company,supplier):
    pdf=fitz.open();page=pdf.new_page();page.insert_text((40,60),'Invoice No. ZERO-1\nDate 14/11/2026\nSubtotal: 100.00\nVAT 17.00%: 0.00\nTotal: 100.00')
    upload=client.post(f"/api/companies/{company['id']}/upload",files={'file':('zero-vat.pdf',pdf.tobytes(),'application/pdf')});pdf.close();ident=upload.json()['ids'][0]
    d=get_doc(client,company,ident)
    d=client.patch('/api/documents/'+ident,json={'revision':d['revision'],'supplier_id':supplier['id']}).json()
    assert d['amount_review_reason'] and any('עם ובלי מע״מ' in v for v in d['issues'])
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==422
    d=client.patch('/api/documents/'+ident,json={'revision':d['revision'],'amount_reviewed':True}).json()
    assert d['amount_reviewed'] and not any('עם ובלי מע״מ' in v for v in d['issues'])
    d=client.patch('/api/documents/'+ident,json={'revision':d['revision'],'fields':{'net':'200.00','total':'200.00'}}).json()
    assert not d['amount_reviewed'] and any('עם ובלי מע״מ' in v for v in d['issues'])


def test_check_suggestions_skip_postcodes_and_amounts(tmp_path,monkeypatch):
    from PIL import Image
    from app import vision
    path=tmp_path/'synthetic.png';Image.new('RGB',(1000,1000),'white').save(path)
    monkeypatch.setattr(vision,'colored_digit_regions',lambda p:[])
    words=[]
    for y,labels in [(.1,['כתובת','5136004']),(.4,['שק','מספר','00007531']),(.6,['סה״כ','2000']),(.8,['00007532'])]:
        words.extend({'text':t,'x':.8-i*.15,'y':y,'w':.1,'h':.02,'rotation':0,'confidence':.5} for i,t in enumerate(labels))
    assert {p['number'] for p in vision.suggested_checks(path,words=words)}=={'00007531','00007532'}


def test_issuer_name_survives_header_id_ocr_damage():
    fields=extract('אופק שירותי חשבונאות pow מורשה 123456782\nחשבונית מס 07/000531\nמפיק המסמך: שם המקבל חתימה תאריך\nאופק שירותי חשבונאות')
    assert fields['supplier_name']=='אופק שירותי חשבונאות'


def test_focused_summary_retries_small_text_without_calculating_amounts(monkeypatch,tmp_path):
    original='אופק שירותי חשבונאות עוסק מורשה 123456782\nחשבונית מס 07/000531\nסה״כ כולל מע״מ 1170.00\nהנחה 0.00\nסה״כ ללא מע״מ 1170.00\nמע״מ 17.00% 0.00'
    words=[{'text':t,'x':.7-i*.1,'y':.6,'w':.08,'h':.02,'rotation':0} for i,t in enumerate(['סה״כ','ללא','מע״מ','1170.00'])]
    calls=[]
    def reading(path,region,quality):
        calls.append(region)
        return 'לפני מע״מ 1000.00\nמע״מ 170.00\nסה״כ לתשלום 1170.00','tesseract','',[]
    monkeypatch.setattr(document_reading,'read_region',reading)
    text,_,info=document_reading.refine(tmp_path/'synthetic.png',original,words)
    fields=extract(text)
    assert (fields['net'],fields['vat'],fields['total'])==('1000.00','170.00','1170.00')
    assert len(calls)==1 and info[0]['purpose']=='amount-summary'


def test_local_document_reread_preserves_edits_until_acceptance(client,company):
    ident=upload_doc(client,company);before=get_doc(client,company,ident)
    changed=client.patch('/api/documents/'+ident,json={'revision':before['revision'],'fields':{'number':'MANUAL-EDIT','notes':'keep my work'}}).json()
    reply=client.post('/api/documents/'+ident+'/local-reread')
    assert reply.status_code==200 and reply.json()['fields']['number']=='INV-001' and not reply.json()['confirmed']
    after=get_doc(client,company,ident)
    assert after['revision']==changed['revision'] and after['fields']==changed['fields']
    assert client.post('/api/documents/'+ident+'/archive').status_code==200
    assert client.post('/api/documents/'+ident+'/local-reread').status_code==409


@pytest.mark.parametrize('rotation,expected',[(0,(.14,.3,.12,.2)),(90,(.18,.5,.16,.15)),(180,(.34,.4,.12,.2)),(270,(.26,.25,.16,.15))])
def test_reread_coordinates_return_to_original_split_page(rotation,expected):
    doc={'id':'test','region':{'x':.1,'y':.2,'w':.4,'h':.5,'rotation':rotation}}
    region=document_reading.original_region(doc,{'x':.1,'y':.2,'w':.3,'h':.4,'rotation':0})
    assert tuple(region[k] for k in ('x','y','w','h'))==pytest.approx(expected)
    assert region['source_document_id']=='test' and region['rotation']==rotation
