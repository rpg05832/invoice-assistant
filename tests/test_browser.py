"""Real browser acceptance path, with an isolated local server and database."""
import io
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from playwright.sync_api import sync_playwright, expect
from .conftest import pdf_bytes

@pytest.fixture(scope='module')
def browser_app(tmp_path_factory):
    directory = tmp_path_factory.mktemp('browser')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'INVOICE_DATA_DIR': str(directory / 'data')}
    log = (directory / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)], env=env, stdout=log, stderr=log)
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + '/api/health', timeout=1): break
            except OSError:
                if process.poll() is not None: pytest.fail((directory / 'server.log').read_text())
                time.sleep(.1)
        else: pytest.fail('Local test server did not become ready')
        with sync_playwright() as p:
            executable = os.environ.get('INVOICE_BROWSER_EXECUTABLE') or (None if os.environ.get('CI') else shutil.which('chromium'))
            browser = p.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox'])
            context = browser.new_context(viewport={'width':1440, 'height':1100}, accept_downloads=True)
            page = context.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            yield page, url, directory
            assert not errors, errors
            context.close(); browser.close()
    finally:
        process.terminate(); process.wait(timeout=10); log.close()


def test_browser_accounting_workflow(browser_app):
    page, url, directory = browser_app
    page.goto(url)
    page.get_by_role('button', name='＋ הוספת חברה', exact=True).first.click()
    page.get_by_label('שם החברה').fill('חברת קבלה בע״מ')
    page.get_by_label('ח״פ / מספר עוסק').fill('123456789')
    page.get_by_role('button', name='יצירת חברה').click()
    expect(page.locator('#dropzone')).to_be_visible()
    page.get_by_role('button', name='ספקים וכרטיסים').click()
    page.get_by_role('button', name='יבוא רשימה', exact=True).click()
    book=Workbook();book.active.append(['code','name','tax_id']);book.active.append(['00042','ספק בדיקה','987654321'])
    content=io.BytesIO();book.save(content)
    page.locator('#import-file').set_input_files({'name':'suppliers.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':content.getvalue()})
    page.get_by_role('button', name='בדיקת השורות לפני יבוא').click()
    expect(page.locator('#import-preview')).to_contain_text('00042')
    page.get_by_role('button', name='אישור ושמירת היבוא').click()
    expect(page.locator('#view')).to_contain_text('ספק בדיקה')
    page.get_by_role('button', name='מסמכים', exact=False).first.click()
    page.locator('#upload-input').set_input_files({'name':'invoice.pdf','mimeType':'application/pdf','buffer':pdf_bytes('UI-001')})
    page.get_by_role('button', name='פתיחה ←').first.click()
    expect(page.locator('#supplier-link')).not_to_have_value('')
    page.get_by_label('שם הספק על המסמך').fill('ספק בדיקה')
    page.get_by_role('button', name='＋ הוספת צ׳ק ידנית').click()
    page.get_by_label('מספר צ׳ק 1',exact=True).fill('00001234')
    page.get_by_label('בדקתי',exact=True).check()
    page.get_by_role('button', name='שמירה ואישור ✓').click()
    expect(page.get_by_role('dialog')).to_contain_text('אישור ולימוד הספק')
    page.get_by_label('בדקתי שזהו כרטיס הספק הנכון בחברה זו').check()
    page.get_by_role('button',name='שמירת התשובה ולימוד הספק').click()
    page.get_by_role('button',name='שמירה ואישור ✓').click()
    expect(page.locator('#view')).to_contain_text('מוכן ליצוא')
    page.locator('[data-select]').first.check()
    page.get_by_role('button', name='יצוא נבחרים ↗').click()
    expect(page.get_by_role('dialog')).to_contain_text('00001234')
    page.get_by_role('button', name='יצירת מנת יצוא').click()
    expect(page.locator('#view')).to_contain_text('קליטה לא אומתה')
    with page.expect_download() as download:
        page.get_by_role('link', name='הורדת Excel').first.click()
    target=directory / 'batch.xlsx';download.value.save_as(target)
    assert load_workbook(target)['צ׳קים']['B2'].value=='00001234'
    page.get_by_role('button', name='תיעוד קליטה').first.click()
    page.get_by_label('תוצאה',exact=True).select_option('rejected')
    page.get_by_label('אסמכתא לבדיקה או סיבת הדחייה').fill('בדיקת קליטה: נדרשת הערה')
    page.get_by_role('button', name='שמירת תוצאה ידנית').click()
    expect(page.locator('#view')).to_contain_text('נדחה')
    page.locator('nav [data-view="documents"]').click()
    page.get_by_role('button', name='פתיחה ←').first.click()
    page.get_by_label('הערות',exact=True).fill('תיקון לאחר דחייה')
    page.get_by_role('button', name='שמירה ואישור ✓').click()
    page.locator('[data-select]').first.check()
    page.get_by_role('button', name='יצוא נבחרים ↗').click()
    page.get_by_role('button', name='יצירת מנת יצוא').click()
    page.get_by_role('button', name='תיעוד קליטה').first.click()
    page.get_by_label('אסמכתא לבדיקה או סיבת הדחייה').fill('נבדק ידנית מול חברת בדיקה')
    page.get_by_role('button', name='שמירת תוצאה ידנית').click()
    expect(page.locator('#view')).to_contain_text('נקלט')
    for view in ['settings','audit','deliveries','suppliers','documents']:
        page.locator(f'nav [data-view="{view}"]').click()
        expect(page.locator('#page-title')).not_to_be_empty()
    Path('test-results').mkdir(exist_ok=True)
    page.screenshot(path='test-results/workflow.png',full_page=True)


def test_browser_split_and_check_outside_invoice(browser_app):
    import fitz
    page,url,directory=browser_app
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible()
    pdf=fitz.open();p=pdf.new_page(width=1000,height=700)
    for x,number in [(50,'SPLIT-101'),(550,'SPLIT-102')]:
        p.insert_text((x,80),f'Invoice No. {number}\nDate 12/11/2026\nSubtotal: 100.00\nVAT amount: 18.00\nTotal: 118.00',fontsize=23)
    p.insert_text((440,620),'0000987',fontsize=42)
    content=pdf.tobytes();pdf.close()
    page.locator('#upload-input').set_input_files({'name':'two-invoices.pdf','mimeType':'application/pdf','buffer':content})
    page.get_by_role('button',name='פתיחה ←').first.click()
    page.get_by_role('button',name='פיצול חשבוניות',exact=True).click()
    image=page.locator('#source-image');expect(image).to_have_attribute('src',__import__('re').compile(r'original=true'))
    expect(image).to_be_visible()
    image.evaluate("img => img.decode()")
    def drag(x,y,w,h):
        image.scroll_into_view_if_needed();r=image.bounding_box()
        page.mouse.move(r['x']+x*r['width'],r['y']+y*r['height'])
        page.mouse.down();page.mouse.move(r['x']+(x+w)*r['width'],r['y']+(y+h)*r['height'],steps=8);page.mouse.up()
    drag(.03,.03,.44,.62);drag(.53,.03,.44,.62)
    expect(page.locator('#region-actions')).to_contain_text('2 אזורים')
    page.get_by_role('button',name='אישור הפיצול',exact=True).click()
    expect(page.get_by_role('dialog')).not_to_be_visible(timeout=30000)
    expect(page.locator('#view')).to_contain_text('אזור מפוצל')
    page.get_by_role('button',name='פתיחה ←').first.click()
    page.get_by_role('button',name='סמן מספר צ׳ק',exact=True).click()
    image.evaluate("img => img.decode()")
    drag(.42,.79,.30,.16)
    page.get_by_role('button',name='זיהוי באזור המסומן').click()
    expect(page.get_by_label('מספר צ׳ק 1',exact=True)).to_have_value('0000987',timeout=30000)
    expect(page.get_by_label('בדקתי',exact=True)).not_to_be_checked()
    page.screenshot(path='test-results/review.png',full_page=True)
    page.get_by_label('בדקתי',exact=True).check()
    page.get_by_role('button',name='שמירה',exact=True).click()
    expect(page.locator('#toast')).to_contain_text('השינויים נשמרו')
    page.get_by_role('button',name='סגירה',exact=True).last.click()


def test_browser_advanced_lists_profiles_pages_and_metrics(browser_app):
    page,url,directory=browser_app
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible()
    page.locator('nav [data-view="records"]').click()
    page.get_by_label('סוג רשימה').select_option('employees')
    page.get_by_role('button',name='יבוא רשימה ומיפוי').click()
    page.locator('#generic-file').set_input_files({'name':'employees.csv','mimeType':'text/csv','buffer':'code,name,account_code\n0007,עובד בדיקה,0042\n'.encode('utf-8-sig')})
    page.get_by_role('button',name='הצגת הבדלים לפני שמירה').click()
    expect(page.locator('#generic-preview')).to_contain_text('0007')
    page.get_by_role('button',name='אישור היבוא',exact=True).click()
    expect(page.locator('#view')).to_contain_text('עובד בדיקה')
    page.locator('nav [data-view="connections"]').click()
    expect(page.get_by_label('אופן הפעלה')).to_have_value('off')
    expect(page.locator('#view')).to_contain_text('הפרופיל עדיין לא אומת')
    page.locator('nav [data-view="metrics"]').click()
    expect(page.locator('#view')).to_contain_text('מסמך שלם')
    page.locator('nav [data-view="settings"]').click()
    page.get_by_label('שם קובץ הקליטה').fill('documents-test.csv')
    page.get_by_role('button',name='שמירת פרופיל',exact=True).click()
    expect(page.locator('#toast')).to_contain_text('הפרופיל נשמר')
    page.locator('nav [data-view="documents"]').click()
    page.locator('#upload-input').set_input_files({'name':'continuation.pdf','mimeType':'application/pdf','buffer':pdf_bytes('JOIN-UI',pages=2)})
    expect(page.locator('tbody tr').first).to_contain_text('continuation.pdf')
    page.locator('tbody tr').filter(has_text='continuation.pdf').locator('[data-select]').nth(0).check()
    page.locator('tbody tr').filter(has_text='continuation.pdf').locator('[data-select]').nth(1).check()
    page.get_by_role('button',name='חיבור עמודי המשך',exact=True).click()
    page.get_by_role('button',name='אישור חיבור',exact=True).click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    expect(page.locator('tbody tr').filter(has_text='continuation.pdf')).to_have_count(1)
    page.locator('tbody tr').filter(has_text='continuation.pdf').get_by_role('button',name='פתיחה ←').click()
    expect(page.locator('#preview-page option')).to_have_count(2)
    page.locator('#preview-page').select_option(index=1)
    page.locator('#approval-extra-actions>summary').click()
    page.get_by_role('button',name='הפרדת עמודי המשך').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    page.set_viewport_size({'width':800,'height':1000});page.screenshot(path='test-results/tablet.png',full_page=True)
    page.set_viewport_size({'width':1440,'height':1100})


def test_browser_personal_learning_and_real_local_training(browser_app):
    page,url,directory=browser_app
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible()
    page.locator('nav [data-view="learning"]').click()
    expect(page.locator('#view')).to_contain_text('התקנה חדשה מתחילה עם מאגר אישי ריק')
    expect(page.locator('[data-learning-example]')).to_have_count(1)
    page.get_by_label('שם לסגנון כתב').fill('כתב יד בדיקת דפדפן')
    page.get_by_role('button',name='הוספת סגנון',exact=True).click()
    expect(page.locator('#view')).to_contain_text('כתב יד בדיקת דפדפן')
    row=page.locator('[data-learning-example]').first
    row.get_by_label('סגנון כתב לדוגמה').select_option(label='כתב יד בדיקת דפדפן')
    row.get_by_role('button',name='שמירת תיוג').click()
    expect(page.locator('#toast')).to_contain_text('התיוג האישי נשמר')
    page.get_by_label('סבבי אימון').select_option('30')
    page.get_by_role('button',name='אימון ובדיקה מקומיים',exact=True).click()
    expect(page.locator('#learning-jobs')).to_contain_text('האימון והבדיקה הסתיימו',timeout=30000)
    expect(page.locator('#learning-models')).to_contain_text('גרסת אימון שמורה')
    expect(page.locator('#learning-models')).to_contain_text('חיתוכים מאושרים')
    page.screenshot(path='test-results/personal-learning.png',full_page=True)
    # The learned model was built by the UI, inside this installation's private data.
    assert list((directory/'data/files/learning').rglob('*.npz'))


def test_browser_learning_export_preview_and_import_to_new_company(browser_app):
    page,url,directory=browser_app
    page.goto(url);page.locator('nav [data-view="learning"]').click()
    export_button=page.get_by_role('button',name='יצוא חבילת למידה למחשב אחר או לצוות',exact=True)
    expect(export_button).to_be_visible()
    source_company=page.locator('#company').input_value()
    export_button.click()
    with page.expect_download() as download:
        page.get_by_role('button',name='הורדת חבילת הלמידה שנבחרה',exact=True).click()
    bundle=directory/'learning-for-team.zip';download.value.save_as(bundle)
    page.get_by_role('button',name='＋ הוספת חברה',exact=True).first.click()
    page.get_by_label('שם החברה').fill('חברת יעד ללמידה')
    with page.expect_response(lambda response: response.url==url+'/api/companies' and response.request.method=='POST') as created:
        page.get_by_role('button',name='יצירת חברה').click()
    target_company=created.value.json()['id']
    expect(page.locator('#company')).to_have_value(target_company)
    assert target_company!=source_company
    page.locator('nav [data-view="learning"]').click()
    page.get_by_role('button',name='יבוא חבילת למידה ממחשב אחר או מצוות',exact=True).click()
    page.locator('#learning-bundle-file').set_input_files(str(bundle))
    expect(page.locator('#learning-bundle-preview')).to_contain_text('חברת היעד: חברת יעד ללמידה')
    page.get_by_label('מיפוי ספק מיובא').select_option('__create__')
    page.get_by_label('בדקתי את מקור החבילה, חברת היעד והמיפויים, ואני מאשר את היבוא שבחרתי').check()
    page.get_by_role('button',name='יבוא הלמידה שנבחרה לחברה שלי',exact=True).click()
    expect(page.locator('[data-learning-example]')).to_have_count(1)
    expect(page.locator('#view')).to_contain_text('ממתין לתשובתך')
    assert page.request.get(url+f'/api/companies/{target_company}/documents').json()==[]
    assert page.request.get(url+f'/api/companies/{source_company}/documents').json()
    page.screenshot(path='test-results/learning-transfer.png',full_page=True)
    # Reload must display the remembered target company, even when it is not first.
    page.goto(url)
    expect(page.locator('#company')).to_have_value(target_company)
    expect(page.locator('#dropzone')).to_be_visible()
    assert page.evaluate('state.company.id')==target_company


def seed_fast_review(page,url,name,confirm=True,count=3):
    company=page.request.post(url+'/api/companies',data={'name':name,'tax_id':'123456789'}).json()
    path=url+f"/api/companies/{company['id']}"
    supplier=page.request.post(path+'/suppliers',data={'code':'0042','name':'ספק מהיר','tax_id':'987654321'}).json()
    if confirm:
        card=page.request.get(path+'/learning').json()['suppliers'][0]
        assert page.request.post(path+'/learning/suppliers/'+supplier['id'],data={'fingerprint':card['fingerprint'],'identity_confirmed':True,'rules':{}}).ok
    for i in range(count):
        upload=page.request.post(path+'/upload',multipart={'file':{'name':f'fast-{i+1}.pdf','mimeType':'application/pdf','buffer':pdf_bytes(f'FAST-{i+1}')}})
        assert upload.ok,upload.text()
    return company,path,page.request.get(path+'/documents').json()


def test_browser_fast_review_saves_navigation_and_checks_every_approval(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'בדיקה מהירה')
    first,second,third=docs
    assert page.request.patch(url+'/api/documents/'+second['id'],data={'revision':second['revision'],'checks':[{'number':'00002468','confirmed':False,'region':None}]}).ok
    assert page.request.patch(url+'/api/documents/'+third['id'],data={'revision':third['revision'],'fields':{'currency':''}}).ok
    page.goto(url);expect(page.locator('#company')).to_be_visible()
    page.locator('#company').select_option(company['id'])
    page.get_by_role('button',name='בדיקה מהירה · 3 ממתינים',exact=True).click()
    expect(page.locator('#review-progress')).to_contain_text('מסמך 1 מתוך 3')
    expect(page.locator('#source-image')).to_be_visible()
    page.locator('#field-number').fill('FAST-CORRECTED')
    expect(page.locator('[data-original-value="number"]')).to_contain_text(first['fields']['number'])
    page.keyboard.press('Alt+ArrowLeft')
    expect(page.locator('#field-number')).to_have_value(second['fields']['number'])
    saved=page.request.get(path+'/documents').json()
    record=next(d for d in saved if d['id']==first['id'])
    assert record['fields']['number']=='FAST-CORRECTED' and record['status']=='review'
    page.get_by_role('button',name='→ הקודם',exact=True).click()
    expect(page.locator('#field-number')).to_have_value('FAST-CORRECTED')
    page.get_by_role('tab',name='טקסט הפענוח',exact=True).click()
    expect(page.locator('#review-ocr-panel')).to_contain_text(first['fields']['number'])
    expect(page.locator('#source-image')).to_have_attribute('src',f"/api/documents/{first['id']}/image")
    page.locator('#source-image').evaluate('img=>img.decode()')
    initial_width=page.locator('#canvas').bounding_box()['width']
    page.get_by_role('button',name='הגדלת המקור',exact=True).click()
    assert page.locator('#canvas').bounding_box()['width']>initial_width*1.2
    page.screenshot(path='test-results/fast-review-ocr.png',full_page=True)
    page.get_by_role('tab',name='נתונים לתיקון',exact=True).click()
    page.locator('#field-number').press('Enter')
    expect(page.locator('#field-number')).to_have_value(second['fields']['number'])
    page.locator('#field-number').dispatch_event('keydown',{'key':'Enter','repeat':True})
    expect(page.locator('#field-number')).to_have_value(second['fields']['number'])
    # An unconfirmed check blocks Enter and keeps the same invoice open.
    page.locator('#field-number').press('Enter')
    expect(page.locator('#review-issues [role=alert]')).to_contain_text('לאשר כל מספר צ׳ק')
    expect(page.locator('#field-number')).to_have_value(second['fields']['number'])
    page.get_by_label('בדקתי',exact=True).check()
    page.locator('#field-number').press('Enter')
    expect(page.locator('#field-number')).to_have_value(third['fields']['number'])
    expect(page.locator('#field-currency')).to_have_value('')
    page.locator('#field-date').fill('')
    page.locator('#field-number').press('Enter')
    expect(page.locator('#review-issues [role=alert]')).to_contain_text('תאריך')
    expect(page.locator('#field-date')).to_be_focused()
    page.locator('#field-date').fill('2026-11-12')
    page.locator('#field-notes').fill('שורה ראשונה')
    page.locator('#field-notes').press('End');page.locator('#field-notes').press('Enter')
    expect(page.locator('#field-notes')).to_have_value('שורה ראשונה\n')
    page.screenshot(path='test-results/fast-review-fields.png',full_page=True)
    page.locator('#field-notes').press('Control+Enter')
    expect(page.locator('#review-issues [role=alert]')).to_contain_text('מטבע')
    expect(page.locator('#field-currency')).to_be_focused()
    page.locator('#field-currency').select_option('ILS')
    page.locator('#field-notes').press('Control+Enter')
    expect(page.locator('#modal')).not_to_be_visible()
    results=page.request.get(path+'/documents').json()
    assert all(d['status']=='approved' for d in results)
    assert next(d for d in results if d['id']==second['id'])['checks'][0]['number']=='00002468'


def test_browser_fast_review_supplier_question_and_escape_preserve_work(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'בדיקה עם שאלת ספק',confirm=False,count=1)
    page.goto(url);expect(page.locator('#company')).to_be_visible()
    page.locator('#company').select_option(company['id'])
    page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    page.locator('#field-notes').fill('תיקון שנשמר בחזרה לרשימה')
    page.keyboard.press('Escape')
    expect(page.locator('#modal')).not_to_be_visible()
    saved=page.request.get(path+'/documents').json()[0]
    assert saved['fields']['notes']=='תיקון שנשמר בחזרה לרשימה' and saved['status']=='review'
    page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    page.locator('#field-number').press('Enter')
    expect(page.locator('#supplier-teaching-form')).to_be_visible()
    assert page.request.get(path+'/documents').json()[0]['status']=='review'
    page.get_by_label('בדקתי שזהו כרטיס הספק הנכון בחברה זו').check()
    page.get_by_role('button',name='שמירת התשובה ולימוד הספק',exact=True).click()
    expect(page.locator('#review-workbench')).to_be_visible()
    assert page.request.get(path+'/documents').json()[0]['status']=='review'
    page.locator('#field-number').press('Enter')
    expect(page.locator('#modal')).not_to_be_visible()
    assert page.request.get(path+'/documents').json()[0]['status']=='approved'


def test_browser_late_company_response_cannot_replace_active_company(browser_app):
    page,url,directory=browser_app
    a,path_a,docs_a=seed_fast_review(page,url,'חברה עם תשובה מושהית',count=1)
    b,path_b,docs_b=seed_fast_review(page,url,'חברה פעילה מהירה',count=1)
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible()
    held=[]
    page.route(path_a+'/documents',lambda route:held.append(route))
    try:
        with page.expect_request(path_a+'/documents'):
            page.locator('#company').select_option(a['id'])
        page.locator('#company').select_option(b['id'])
        expect(page.locator(f'[data-open="{docs_b[0]["id"]}"]')).to_be_visible()
        # Release the old response after the new company's view has finished loading.
        with page.expect_response(path_a+'/documents'):
            held[0].fulfill(response=held[0].fetch())
        page.evaluate('async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }')
        expect(page.locator('#company')).to_have_value(b['id'])
        expect(page.locator(f'[data-open="{docs_b[0]["id"]}"]')).to_be_visible()
        expect(page.locator(f'[data-open="{docs_a[0]["id"]}"]')).to_have_count(0)
        assert page.evaluate('state.docs.every(d=>d.company_id===state.company.id)')
    finally:page.unroute(path_a+'/documents')


def test_browser_source_copy_loupe_calculator_snippets_preferences_and_resume(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'כלי בדיקה אישיים',count=2)
    current,previous=docs
    assert page.request.patch(url+'/api/documents/'+previous['id'],data={'revision':previous['revision'],'fields':{'cost_center':'007','expense_account':'0600'}}).ok
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.get_by_role('button',name='בדיקה מהירה · 2 ממתינים',exact=True).click()
    search=page.get_by_label('חיפוש ספק לפי שם, קוד או מספר עוסק',exact=True)
    search.fill('0042');search.press('Enter')
    expect(page.locator('#field-number')).to_be_focused()
    expect(page.locator('#review-field-loupe canvas')).to_be_visible()
    page.locator('#field-number').fill('תיקון זמני')
    page.locator('#review-workspace-tools summary').click()
    page.get_by_role('button',name='לחיצה להעתקה מהמקור',exact=True).click()
    page.get_by_role('button',name='העתקת '+current['fields']['number'],exact=True).click()
    expect(page.locator('#field-number')).to_have_value(current['fields']['number'])
    page.screenshot(path='test-results/review-tools.png',full_page=True)
    page.get_by_role('button',name='מחשבון מע״מ',exact=True).click()
    page.get_by_label('סכום',exact=True).fill('200.00')
    page.get_by_role('button',name='חישוב והצגת התוצאה',exact=True).click()
    expect(page.locator('#calculator-result')).to_contain_text('236.00')
    page.get_by_role('button',name='העתקת התוצאה לשדות לבדיקה',exact=True).click()
    expect(page.locator('#field-total')).to_have_value('236.00')
    assert page.request.get(path+'/documents').json()[0]['status']=='review'
    page.locator('#review-workspace-tools summary').click()
    page.get_by_role('button',name='השלמות אישיות',exact=True).click()
    page.get_by_label('קיצור ללא רווחים',exact=True).fill('בדק')
    page.get_by_label('הטקסט להשלמה',exact=True).fill('נבדק מול תעודת משלוח')
    page.get_by_role('button',name='שמירת השלמה אישית',exact=True).click()
    expect(page.locator('#snippet-list')).to_contain_text(';בדק')
    page.get_by_role('button',name='חזרה למסמך',exact=True).click()
    page.locator('#field-notes').fill(';בדק ')
    expect(page.locator('#field-notes')).to_have_value('נבדק מול תעודת משלוח')
    page.locator('#review-workspace-tools summary').click()
    page.get_by_role('button',name='חשבונית קודמת מהספק',exact=True).click()
    expect(page.get_by_alt_text('חשבונית קודמת מאותו ספק')).to_be_visible()
    page.get_by_label('העתקת מרכז עלות',exact=True).check()
    page.get_by_role('button',name='העתקת השדות שנבחרו לבדיקה',exact=True).click()
    expect(page.locator('#field-cost_center')).to_have_value('007')
    page.locator('#review-workspace-tools summary').click()
    page.get_by_role('button',name='לחזור למסמך הזה',exact=True).click()
    page.get_by_label('מה נשאר לבדוק?',exact=True).fill('בדיקה נוספת של התשלום')
    page.get_by_role('button',name='שמירת הסימון וחזרה למסמך',exact=True).click()
    expect(page.locator('#review-workspace-tools')).to_be_visible()
    page.locator('#review-workspace-tools summary').click()
    page.get_by_role('button',name='סדר שדות וקיצורים',exact=True).click()
    page.get_by_label('רוחב המקור',exact=True).select_option('60')
    page.get_by_label('גודל אות בשדות',exact=True).select_option('16')
    page.get_by_label('אישור והבא',exact=True).select_option('F9')
    page.locator('[data-order-field="number"] input').check();page.locator('[data-order-field="total"] input').check()
    page.get_by_role('button',name='שמירת הסביבה האישית',exact=True).click()
    expect(page.locator('#personal-review-fields')).to_contain_text('מספר מסמך')
    expect(page.locator('[data-action="review-approve-next"]')).to_contain_text('F9')
    page.locator('#field-notes').fill('מיקום עבודה אישי')
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()
    saved_session=page.request.get(path+'/review-workspace').json()['session']
    page.reload();expect(page.locator('#dropzone')).to_be_visible()
    page.get_by_role('button',name='המשך מהמקום שהפסקתי',exact=True).click()
    expect(page.locator('#field-notes')).to_have_value('מיקום עבודה אישי')
    expect(page.locator('#field-notes')).to_be_focused()
    assert abs(page.locator('.editor').evaluate('el=>el.scrollTop')-saved_session['editor_scroll'])<3
    expect(page.locator('[data-action="review-approve-next"]')).to_contain_text('F9')
    page.locator('.review-thumbnails summary').click()
    page.locator(f'[data-review-thumb="{previous["id"]}"]').click()
    expect(page.locator('#field-number')).to_have_value(previous['fields']['number'])
    page.locator('#field-number').press('F9')
    expect(page.locator('#field-number')).to_have_value(current['fields']['number'])
    assert next(d for d in page.request.get(path+'/documents').json() if d['id']==previous['id'])['status']=='approved'
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()


def test_browser_review_queues_bulk_preview_and_undo(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'תורי טיפול ותיקון מרוכז',count=2)
    a,b=docs
    page.request.patch(url+'/api/documents/'+a['id'],data={'revision':a['revision'],'fields':{'total':'120.00'}})
    page.request.put(url+'/api/documents/'+b['id']+'/review-flag',data={'marked':True,'note':'חזרה לספק'})
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.get_by_label('תור בדיקה',exact=True).select_option('amounts')
    page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    expect(page.locator('#field-number')).to_have_value(a['fields']['number'])
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()
    page.get_by_label('תור בדיקה',exact=True).select_option('revisit')
    page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    expect(page.locator('#field-number')).to_have_value(b['fields']['number'])
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()
    page.locator('#select-all').check()
    page.get_by_role('button',name='תיקון מרוכז · 2',exact=True).click()
    page.get_by_label('שדה לתיקון',exact=True).select_option('cost_center')
    page.get_by_label('הערך החדש',exact=True).fill('007')
    page.get_by_role('button',name='תצוגה מקדימה לפני תיקון',exact=True).click()
    expect(page.locator('#bulk-review-preview')).to_contain_text('007')
    assert all(not d['fields']['cost_center'] for d in page.request.get(path+'/documents').json())
    page.get_by_role('button',name='אישור התיקון שהוצג למסמכים האלה',exact=True).click()
    expect(page.locator('#modal')).not_to_be_visible()
    assert all(d['fields']['cost_center']=='007' and d['status']=='review' for d in page.request.get(path+'/documents').json())
    page.locator(f'[data-open="{b["id"]}"]').click();page.locator('#field-number').press('Enter')
    expect(page.locator('#modal')).not_to_be_visible()
    page.get_by_role('button',name='החזרת אישור אחרון לבדיקה',exact=True).click()
    expect(page.locator('#modal')).to_contain_text(b['fields']['number'])
    page.get_by_role('button',name='החזרת המסמך הזה לבדיקה',exact=True).click()
    expect(page.locator('#review-workbench')).to_be_visible()
    assert next(d for d in page.request.get(path+'/documents').json() if d['id']==b['id'])['status']=='review'
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()


def test_browser_digit_choice_keys_do_not_replace_typing(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'בחירת הצעות במקלדת',count=1);doc=docs[0]
    def proposals(route):
        body=route.request.post_data_json
        route.fulfill(json={'number':'00002468','confirmed':False,'region':body['region'],'source_document_id':doc['id'],'proposals':[{'engine':'engine-a','number':'00002468'},{'engine':'engine-b','number':'00002469'}],'warning':'הצעות לבדיקה'})
    page.route(url+'/api/documents/'+doc['id']+'/check-region',proposals)
    try:
        page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
        page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
        page.get_by_role('button',name='סמן מספר צ׳ק',exact=True).click()
        image=page.locator('#source-image');image.evaluate('img=>img.decode()');page.locator('.document-preview').evaluate('el=>{el.scrollTop=0}');r=image.bounding_box()
        page.mouse.move(r['x']+.05*r['width'],r['y']+.04*r['height']);page.mouse.down();page.mouse.move(r['x']+.45*r['width'],r['y']+.18*r['height'],steps=6);page.mouse.up()
        page.get_by_role('button',name='זיהוי באזור המסומן',exact=True).click()
        expect(page.locator('#quick-check-choices')).to_be_focused()
        page.keyboard.press('2')
        expect(page.get_by_label('מספר צ׳ק 1',exact=True)).to_have_value('00002469')
        expect(page.get_by_label('בדקתי',exact=True)).not_to_be_checked()
        page.get_by_label('מספר צ׳ק 1',exact=True).fill('1')
        expect(page.get_by_label('מספר צ׳ק 1',exact=True)).to_have_value('1')
        page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()
    finally:page.unroute(url+'/api/documents/'+doc['id']+'/check-region')


def test_browser_model_download_guidance_and_indra_terms(browser_app):
    page,url,directory=browser_app
    company,path,_=seed_fast_review(page,url,'מדריך מודלים',count=1)
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.locator('nav [data-view="connections"]').click()
    expect(page.locator('#model-download-guide')).to_contain_text('מה כדאי להוריד?')
    expect(page.locator('#model-download-guide')).to_contain_text('כבר כלול במתקין')
    expect(page.locator('.model-hardware')).to_contain_text('המחשב שמפעיל את התוכנה')
    expect(page.locator('#model-task-rankings')).to_contain_text('מספרי צ׳קים וספרות בכתב יד')
    expect(page.locator('#model-task-rankings')).to_contain_text('מלל והקשר של עמוד שלם')
    page.get_by_role('button',name='בדיקת קריאת עברית',exact=True).click()
    expect(page.locator('#ocr-self-test-result')).to_contain_text('העברית והספרות נקראו בהצלחה',timeout=30000)
    expect(page.locator('#ocr-self-test-result')).to_contain_text('חשבונית')
    expect(page.locator('#ocr-runtime-status')).to_contain_text('מנוע העברית פעיל')
    expect(page.get_by_role('button',name='בדיקה ותיקון קובצי הבסיס מ־GitHub',exact=True)).to_be_visible()
    expect(page.get_by_role('link',name='קובצי המודלים והמקורות ב־GitHub',exact=True)).to_have_attribute('href','https://github.com/rpg05832/invoice-assistant/releases/tag/public-models-v1')
    page.get_by_text('מה לומד מהתיקונים שלי?',exact=True).click()
    expect(page.locator('#model-download-guide')).to_contain_text('מודלי הבסיס נשארים מקוריים')
    page.screenshot(path='test-results/model-download-guide.png',full_page=True)
    # Installing restricted weights opens terms instead of starting a background download.
    page.locator('#public-local-models [data-public-install="indra"]').click()
    expect(page.locator('#model-indra-form')).to_contain_text('רישיון המודל כולל תנאי שימוש')
    expect(page.locator('#model-indra-form [name=accepted]')).not_to_be_checked()
    page.get_by_role('button',name='סגירה',exact=True).last.click()
    assert page.request.get(path+'/recognition-settings').json()['mode']=='off'


def test_browser_inline_region_review_accept_reject_and_rescan(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'פענוח ליד השדה',count=1);doc=docs[0]
    writer=page.request.post(path+'/learning/writers',data={'name':'סגנון הצ׳ק'}).json()['id']
    document_writer=page.request.post(path+'/learning/writers',data={'name':'סגנון המסמך'}).json()['id']
    requested_writers=[]
    def check_proposal(route):
        body=route.request.post_data_json
        requested_writers.append(body.get('writer_id'))
        route.fulfill(json={'number':'00002468','engine':'engine-a','confirmed':False,'region':body['region'],'source_document_id':doc['id'],'proposals':[{'engine':'engine-a','number':'00002468'},{'engine':'engine-b','number':'00002469'}]})
    def field_proposal(route):
        body=route.request.post_data_json
        route.fulfill(json={'field':body['field'],'values':['07/000531'],'engine':'tesseract','text':'07/000531','confirmed':False,'region':body['region'],'source_document_id':doc['id']})
    page.route(url+'/api/documents/'+doc['id']+'/check-region',check_proposal)
    page.route(url+'/api/documents/'+doc['id']+'/field-region',field_proposal)
    def select_region():
        image=page.locator('#source-image');image.evaluate('img=>img.decode()');page.locator('.document-preview').evaluate('el=>{el.scrollTop=0}');r=image.bounding_box()
        page.mouse.move(r['x']+.06*r['width'],r['y']+.04*r['height']);page.mouse.down();page.mouse.move(r['x']+.45*r['width'],r['y']+.16*r['height'],steps=6);page.mouse.up()
        expect(page.locator('#inline-region-value')).to_be_visible(timeout=30000)
    try:
        page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
        page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
        page.get_by_role('button',name='סימון ופענוח צ׳ק',exact=True).click();select_region()
        expect(page.locator('#inline-region-value')).to_have_value('00002468')
        expect(page.locator('#region-reading-draft img.check-preview')).to_have_count(1)
        expect(page.locator('#review-field-loupe')).not_to_be_visible()
        expect(page.locator('#region-actions img.check-preview')).to_have_count(0)
        assert page.locator('[data-check-number]').count()==0, 'A draft must not silently create a check'
        page.locator('#inline-region-value').fill('1');expect(page.locator('#inline-region-value')).to_have_value('1')
        page.locator('#field-number').press('Enter')
        expect(page.locator('#toast')).to_contain_text('אשרו או דחו תחילה')
        assert page.request.get(path+'/documents').json()[0]['status']=='review'
        page.get_by_role('button',name='דחייה וחזרה לשדה',exact=True).click()
        assert page.locator('[data-check-number]').count()==0
        page.get_by_role('button',name='סימון ופענוח צ׳ק',exact=True).click();select_region()
        page.locator('[data-region-choice="1"]').click()
        page.get_by_role('button',name='אישור מספר הצ׳ק',exact=True).click()
        expect(page.get_by_label('מספר צ׳ק 1',exact=True)).to_have_value('00002469')
        expect(page.get_by_label('בדקתי',exact=True)).to_be_checked()
        page.locator('[data-check-writer="0"]').select_option(writer)
        page.locator('#learning-writer').select_option(document_writer)
        page.locator('[data-region-check="0"]').click();select_region()
        assert requested_writers[-1]==writer, 'A per-check handwriting profile must override the document profile'
        page.get_by_role('button',name='אישור מספר הצ׳ק',exact=True).click()
        assert page.locator('[data-check-number]').count()==1, 'Rescanning replaces the selected check'
        expect(page.get_by_label('מספר צ׳ק 1',exact=True)).to_have_value('00002468')
        previous=page.locator('#field-number').input_value()
        page.locator('[data-region-field="number"]').click();select_region()
        expect(page.locator('#field-number')).to_have_value(previous)
        page.locator('#inline-region-value').press('Enter')
        expect(page.locator('#field-number')).to_have_value('07/000531')
        page.screenshot(path='test-results/inline-region-review.png',full_page=True)
        page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()
        saved=page.request.get(path+'/documents').json()[0]
        assert saved['fields']['number']=='07/000531' and saved['checks'][0]['confirmed'] and saved['status']=='review'
        assert saved['field_sources']['number']['w']>.3, 'The loupe must retain the newly accepted source area'
    finally:
        page.unroute(url+'/api/documents/'+doc['id']+'/check-region')
        page.unroute(url+'/api/documents/'+doc['id']+'/field-region')


def test_browser_local_reread_keeps_unsaved_work_and_copies_selected_fields(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'פענוח חוזר למסמך קיים',count=1);doc=docs[0]
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    page.locator('#field-number').fill('UNSAVED-EDIT');page.locator('#field-notes').fill('להשאיר את ההערה שלי')
    page.get_by_role('button',name='סריקה חוזרת למסמך',exact=True).click()
    expect(page.locator('[data-local-reread-field="number"]')).to_be_visible()
    expect(page.locator('#region-reading-draft')).to_contain_text('UNSAVED-EDIT')
    expect(page.locator('[data-local-reread-field]')).to_have_count(1)
    expect(page.locator('#field-number')).to_have_value('UNSAVED-EDIT')
    assert page.request.get(path+'/documents').json()[0]['fields']['number']==doc['fields']['number']
    page.get_by_role('button',name='דחייה וחזרה לטופס',exact=True).click()
    expect(page.locator('#field-number')).to_have_value('UNSAVED-EDIT')
    page.get_by_role('button',name='סריקה חוזרת למסמך',exact=True).click()
    expect(page.locator('[data-local-reread-field="number"]')).to_be_visible()
    page.locator('[data-local-reread-field="number"]').check()
    page.get_by_role('button',name='העתקת השדות שסומנו לבדיקה',exact=True).click()
    expect(page.locator('#field-number')).to_have_value(doc['fields']['number'])
    expect(page.locator('#field-notes')).to_have_value('להשאיר את ההערה שלי')
    assert page.request.get(path+'/documents').json()[0]['status']=='review'
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()


def test_browser_reread_reports_no_changes_and_uses_numeric_amount_equality(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'סריקה ללא הבדלים',count=1)
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    page.locator('#field-net').fill('100')
    page.get_by_role('button',name='סריקה חוזרת למסמך',exact=True).click()
    expect(page.locator('#region-reading-draft')).to_contain_text('לא נמצאו שינויים')
    expect(page.locator('[data-local-reread-field]')).to_have_count(0)
    expect(page.locator('[data-action="local-reread-accept"]')).to_have_count(0)
    page.get_by_role('button',name='חזרה למסמך',exact=True).click()
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible()


def test_browser_blocking_approval_and_indra_errors_are_visible_in_modal_layer(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'הודעות חסימה גלויות',count=2);first=docs[0]
    assert page.request.patch(url+'/api/documents/'+first['id'],data={'revision':first['revision'],'supplier_id':None}).ok
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.get_by_role('button',name='בדיקה מהירה · 2 ממתינים',exact=True).click()
    page.get_by_role('button',name='אישור והבא · Enter ✓',exact=True).click()
    expect(page.locator('#review-operation-error')).to_be_visible()
    expect(page.locator('#review-operation-error')).to_contain_text('כרטיס ספק פעיל')
    assert page.locator('#review-operation-error').bounding_box()['y']<200
    expect(page.locator('#modal #toast')).to_be_visible()
    expect(page.locator('#modal #toast')).to_contain_text('כרטיס ספק פעיל')
    # A blocked approval must never prevent navigating to another pending invoice.
    page.get_by_role('button',name='הבא שממתין לבדיקה',exact=True).click()
    expect(page.locator('#review-progress')).to_contain_text('מסמך 2 מתוך 2')
    page.locator('#approval-extra-actions>summary').click()
    page.get_by_role('button',name='השוואה עם Indra בעברית',exact=True).click()
    page.get_by_role('checkbox',name='בדקתי שהרישיון מתאים לשימוש שלי').check()
    route=url+'/api/documents/'+docs[1]['id']+'/recognize-indra'
    def missing_model(request):request.fulfill(status=409,json={'detail':'מודל Indra RTL אינו מותקן'})
    page.route(route,missing_model)
    try:
        page.get_by_role('button',name='הפעלת זיהוי מקומי והשוואה',exact=True).click()
        expect(page.locator('#modal #toast')).to_be_visible()
        expect(page.locator('#modal #toast')).to_contain_text('אינו מותקן')
        assert page.locator('#busy').evaluate('el=>el.parentElement.id')=='modal'
    finally:page.unroute(route)
    page.get_by_role('button',name='ביטול',exact=True).click()


def test_browser_permanent_deletion_requires_preview_and_checked_confirmation(browser_app):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'מחיקת מסמכי בדיקה',count=1)
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.locator('[data-select]').first.check()
    page.get_by_role('button',name='מחיקה לצמיתות',exact=True).click()
    expect(page.get_by_role('dialog')).to_contain_text('fast-1.pdf')
    confirm=page.get_by_role('button',name='מחיקת המסמכים וקובצי המקור לצמיתות',exact=True)
    expect(confirm).to_be_disabled()
    page.get_by_role('button',name='ביטול וחזרה',exact=True).click()
    assert len(page.request.get(path+'/documents').json())==1
    page.get_by_role('button',name='מחיקה לצמיתות',exact=True).click()
    page.locator('#delete-documents-confirm').check();confirm.click()
    expect(page.locator('#modal')).not_to_be_visible()
    expect(page.locator('[data-select]')).to_have_count(0)
    assert page.request.get(path+'/documents').json()==[]


def test_browser_main_navigation_secondary_dialogs_and_action_registry(browser_app):
    import json
    import re
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,'ביקורת תפריטים וכפתורים',count=1)
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id'])
    page.wait_for_load_state('networkidle')
    source=Path(__file__).resolve().parents[1]/'app/static'
    action_names=sorted({action for script in source.glob('*.js') for action in re.findall(r'data-action="([a-z0-9-]+)"',script.read_text(encoding='utf-8'))})
    missing=page.evaluate('(keys)=>keys.filter(key=>typeof actions[key]!=="function")',action_names)
    assert missing==[],f'Visible action names without handlers: {missing}'
    views=page.locator('nav [data-view]').evaluate_all('(nodes)=>nodes.map(node=>node.dataset.view)')
    report={'registered_actions':action_names,'views':{},'dialogs':[]}
    for view in views:
        page.locator(f'nav [data-view="{view}"]').click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#page-title')).not_to_be_empty()
        expect(page.locator('#view')).not_to_be_empty()
        assert page.evaluate('state.view')==view
        assert not page.locator('#toast.error:visible').count(),page.locator('#toast').inner_text()
        report['views'][view]=page.locator('#view button').evaluate_all('(nodes)=>nodes.map(node=>({text:node.innerText,action:node.dataset.action||null,disabled:node.disabled}))')
    dialogs=[('documents','group-pages','בדיקת עמודי המשך וכפילויות'),('documents','review-preferences','סביבת הבדיקה האישית שלי'),('suppliers','new-supplier','ספק'),('suppliers','import-suppliers','יבוא'),('deliveries','import-deliveries','יבוא'),('records','import-records','יבוא'),('learning','export-learning-bundle','יצוא למידה'),('learning','import-learning-bundle','יבוא למידה')]
    for view,action,title in dialogs:
        page.locator(f'nav [data-view="{view}"]').click();page.wait_for_load_state('networkidle')
        page.locator(f'#view [data-action="{action}"]').first.click()
        expect(page.locator('#modal')).to_be_visible()
        expect(page.locator('#modal .dialog-header')).to_contain_text(title)
        report['dialogs'].append(action)
        page.locator('#modal .dialog-header [data-action="close"]').click()
        expect(page.locator('#modal')).not_to_be_visible()
    review_dialogs=[('review-calculator','review-workspace-tools'),('review-snippets','review-workspace-tools'),('review-flag','review-workspace-tools'),('evaluate-document','approval-extra-actions'),('prepare-payment','approval-extra-actions'),('document-learning-history','approval-extra-actions'),('personal-template-proposals','approval-more')]
    for action,details in review_dialogs:
        page.locator('nav [data-view="documents"]').click();page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
        if action=='document-learning-history':page.locator('#field-notes').fill('הערה לפני פתיחת תפריט משני')
        page.locator(f'#{details}>summary').click()
        page.locator(f'#modal [data-action="{action}"]').click()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#modal .dialog-header')).not_to_contain_text('בדיקת מסמך')
        expect(page.locator('#modal')).to_be_visible()
        report['dialogs'].append(action)
        if action=='document-learning-history':assert page.request.get(path+'/documents').json()[0]['fields']['notes']=='הערה לפני פתיחת תפריט משני'
        page.locator('#modal .dialog-header [data-action="close"]').click()
        expect(page.locator('#modal')).not_to_be_visible()
    # Native-window updates is a separate primary control, outside the navigation list.
    page.get_by_role('button',name='גרסה ועדכון התוכנה',exact=True).click()
    expect(page.locator('#application-updates')).to_be_visible()
    page.locator('#modal .dialog-header [data-action="close"]').click()
    Path('test-results/menu-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.parametrize('width,height',[(1280,690),(1366,768),(1600,900),(1920,1080)])
def test_browser_approval_workspace_fits_page_and_core_fields(browser_app,width,height):
    page,url,directory=browser_app
    company,path,docs=seed_fast_review(page,url,f'מסך אישור {width}',count=1);doc=docs[0]
    page.request.patch(url+'/api/documents/'+doc['id'],data={'revision':doc['revision'],'checks':[{'number':'00007531','confirmed':False,'region':None}]})
    page.set_viewport_size({'width':width,'height':height})
    page.goto(url);expect(page.locator('#dropzone')).to_be_visible();page.locator('#company').select_option(company['id']);page.get_by_role('button',name='בדיקה מהירה · 1 ממתינים',exact=True).click()
    page.locator('#source-image').evaluate('img=>img.decode()');page.wait_for_timeout(150)
    bounds=page.evaluate('''()=>{const box=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};};return {source:box(document.querySelector('#source-image')),stage:box(document.querySelector('#approval-source-stage')),editor:box(document.querySelector('.editor')),fields:['field-supplier_name','field-supplier_tax_id','supplier-link','field-number','field-date','field-net','field-vat','field-total'].map(id=>box(document.getElementById(id))),check:box(document.querySelector('[data-check-number]'))};}''')
    assert bounds['source']['bottom']<=bounds['stage']['bottom']+1 and bounds['source']['right']<=bounds['stage']['right']+1
    assert bounds['source']['top']>=bounds['stage']['top']-1 and bounds['source']['left']>=bounds['stage']['left']-1
    for field in [*bounds['fields'],bounds['check']]:assert field['top']>=bounds['editor']['top'] and field['bottom']<=bounds['editor']['bottom']+1,bounds
    page.screenshot(path=f'test-results/approval-workspace-{width}.png',full_page=True)
    page.keyboard.press('Escape');expect(page.locator('#modal')).not_to_be_visible();page.set_viewport_size({'width':1440,'height':1100})


def test_browser_update_flow_requires_check_download_then_install(browser_app):
    page,url,directory=browser_app
    page.set_viewport_size({'width':1280,'height':690})
    state={'release':None,'job':None,'installed':False}
    def status():return {'current':{'version':'0.2.0'},'supported':True,'source':'GitHub','release':state['release'],'job':state['job']}
    def updates(route):route.fulfill(json=status())
    def check(route):
        state['release']={'version':'0.3.0','size':12345678,'notes':'<script>unsafe()</script>'};route.fulfill(json=status())
    def download(route):state['job']={'state':'ready','progress':100};route.fulfill(json=status())
    def install(route):
        assert route.request.post_data_json['confirmed'] is True;state['installed']=True;route.fulfill(json={'installing':True})
    routes={'/api/updates':updates,'/api/updates/check':check,'/api/updates/download':download,'/api/updates/install':install}
    for path,handler in routes.items():page.route(url+path,handler)
    try:
        page.goto(url);page.get_by_role('button',name='גרסה ועדכון התוכנה',exact=True).click()
        expect(page.locator('#application-updates')).to_contain_text('0.2.0')
        page.get_by_role('button',name='בדיקת עדכון חדש',exact=True).click()
        expect(page.locator('#application-updates')).to_contain_text('0.3.0')
        assert page.locator('#application-updates script').count()==0 and not state['installed']
        page.get_by_role('button',name='הורדת העדכון מ־GitHub',exact=True).click()
        expect(page.get_by_role('button',name='התקנה והפעלה מחדש',exact=True)).to_be_visible()
        assert not state['installed']
        page.get_by_role('button',name='התקנה והפעלה מחדש',exact=True).click()
        expect(page.locator('#application-update-status')).to_contain_text('תיסגר ותיפתח מחדש')
        assert state['installed'];page.get_by_role('button',name='סגירה',exact=True).last.click()
    finally:
        for path in routes:page.unroute(url+path)
        page.set_viewport_size({'width':1440,'height':1100})
