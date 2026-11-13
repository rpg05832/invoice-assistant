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
    source_company=page.locator('#company').input_value()
    page.get_by_role('button',name='יצוא חבילת למידה למחשב אחר או לצוות',exact=True).click()
    with page.expect_download() as download:
        page.get_by_role('button',name='הורדת חבילת הלמידה שנבחרה',exact=True).click()
    bundle=directory/'learning-for-team.zip';download.value.save_as(bundle)
    page.get_by_role('button',name='＋ הוספת חברה',exact=True).first.click()
    page.get_by_label('שם החברה').fill('חברת יעד ללמידה')
    page.get_by_role('button',name='יצירת חברה').click()
    expect(page.locator('#company')).not_to_have_value(source_company)
    target_company=page.locator('#company').input_value()
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
    page.get_by_role('button',name='הגדלת המקור',exact=True).click()
    expect(page.locator('#canvas')).to_have_attribute('style','width: 125%;')
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
