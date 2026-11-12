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
    page.get_by_label('בדקתי').check()
    page.get_by_role('button', name='שמירה ואישור ✓').click()
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
    expect(page.get_by_label('בדקתי')).not_to_be_checked()
    page.screenshot(path='test-results/review.png',full_page=True)
    page.get_by_label('בדקתי').check()
    page.get_by_role('button',name='שמירה',exact=True).click()
    expect(page.locator('#toast')).to_contain_text('השינויים נשמרו')
    page.get_by_role('button',name='סגירה',exact=True).last.click()
