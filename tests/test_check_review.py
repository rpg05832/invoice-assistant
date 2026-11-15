import json
import sqlite3
from PIL import Image
from app import vision
from .conftest import upload_doc,get_doc


def save_suggestions(client,ident,suggestions,reviewed=False):
    with sqlite3.connect(client.app.state.data_dir/'invoices.sqlite3') as c:
        value=json.loads(c.execute('SELECT value FROM document_meta WHERE document_id=?',(ident,)).fetchone()[0])
        value.update(suggested_checks=suggestions,check_candidates_reviewed=reviewed)
        c.execute('UPDATE document_meta SET value=? WHERE document_id=?',(json.dumps(value),ident))


def candidate(ident,number='00007531'):
    return {'number':number,'engine':'tesseract','source_document_id':ident,'confirmed':False,'region':{'x':.1,'y':.2,'w':.3,'h':.08,'rotation':0},'reason':'דיו שנוסף לסריקה; יש לוודא שזה מספר צ׳ק'}


def test_legacy_detector_description_can_be_accepted_and_saved_as_check(client,company,supplier):
    ident=upload_doc(client,company);old=candidate(ident);save_suggestions(client,ident,[old])
    d=get_doc(client,company,ident);proposal=d['suggested_checks'][0]
    assert proposal['reason']=='corrected_value' and proposal['detection_reason']==old['reason']
    reply=client.patch('/api/documents/'+ident,json={'revision':d['revision'],'checks':[{**proposal,'confirmed':True}],'check_candidates_reviewed':True})
    assert reply.status_code==200,reply.text
    assert reply.json()['checks'][0]['detection_reason']==old['reason']
    assert client.post(f"/api/companies/{company['id']}/approve",json={'ids':[ident]}).status_code==200


def test_invalid_reason_type_is_validation_error_without_partial_save(client,company):
    ident=upload_doc(client,company);before=get_doc(client,company,ident)
    reply=client.patch('/api/documents/'+ident,json={'revision':before['revision'],'checks':[{'number':'00007531','confirmed':True,'reason':{'invalid':'shape'}}]})
    assert reply.status_code==400 and 'סיבת תיקון' in reply.json()['detail']
    assert get_doc(client,company,ident)['revision']==before['revision']


def test_check_rescan_replaces_old_candidates_and_updates_revision(client,company,monkeypatch):
    ident=upload_doc(client,company);old=candidate(ident);save_suggestions(client,ident,[old,candidate(ident,'2000')],reviewed=True)
    before=get_doc(client,company,ident)
    fresh={**old,'reason':'corrected_value','detection_reason':'אזור כתב יד'}
    monkeypatch.setattr(vision,'suggested_checks',lambda *a,**k:[dict(fresh)])
    result=client.post('/api/documents/'+ident+'/check-suggestions');assert result.status_code==200,result.text
    assert result.json()['changed'] and result.json()['revision']==before['revision']+1
    after=get_doc(client,company,ident)
    assert [p['number'] for p in after['suggested_checks']]==['00007531']
    assert after['check_candidates_reviewed'] is False


def test_product_bag_and_decimal_amount_are_not_cheque_labels(tmp_path,monkeypatch):
    path=tmp_path/'invoice.png';Image.new('RGB',(1000,1000),'white').save(path)
    monkeypatch.setattr(vision,'colored_digit_regions',lambda path:[])
    words=[]
    for y,labels in [(.3,['שק','ירק','2000']),(.4,['שק','מספר','00007531']),(.5,['צ׳ק','20.00']),(.6,['שיק','00007532']),(.45,['שק:','00007533']),(.48,['מספר','שק','00007534'])]:
        words.extend({'text':text,'x':.85-i*.18,'y':y,'w':.12,'h':.02,'rotation':0,'confidence':.4} for i,text in enumerate(labels))
    proposals=vision.suggested_checks(path,words=words)
    assert {p['number'] for p in proposals}=={'00007531','00007532','00007533','00007534'}
    assert all(p['reason']=='corrected_value' and p['detection_reason'] for p in proposals)
