import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager, closing
from starlette.background import BackgroundTask
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import fitz
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from openpyxl import Workbook, load_workbook
from PIL import Image, ImageOps

from . import ocr, vision, advanced, learning, model_runtime, learning_transfer, review_tools
from .domain import TARGETS, TYPES, FIELDS, EXPORT_FIELDS, extract, issues, money, safe_cell

def uid(): return uuid.uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat()
def encode(value): return json.dumps(value, ensure_ascii=False)
def fail(message, status=400): raise HTTPException(status, message)

def create_app(data_dir=None):
    data = Path(data_dir or os.environ.get('INVOICE_DATA_DIR', Path(__file__).resolve().parents[1] / 'data')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    files = data / 'files'; files.mkdir(exist_ok=True)
    database = data / 'invoices.sqlite3'
    database_lock = threading.RLock()

    @contextmanager
    def db():
        with database_lock:
            conn = sqlite3.connect(database, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            try:
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    yield conn
            finally: conn.close()

    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY,name TEXT NOT NULL,tax_id TEXT NOT NULL,target TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS suppliers(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),code TEXT NOT NULL,name TEXT NOT NULL,tax_id TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,expense_account TEXT NOT NULL DEFAULT '',UNIQUE(company_id,code));
        CREATE TABLE IF NOT EXISTS aliases(company_id TEXT NOT NULL REFERENCES companies(id),alias TEXT NOT NULL,supplier_id TEXT NOT NULL REFERENCES suppliers(id),PRIMARY KEY(company_id,alias));
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),name TEXT NOT NULL,path TEXT NOT NULL,sha TEXT NOT NULL,created TEXT NOT NULL,UNIQUE(company_id,sha));
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),source_id TEXT NOT NULL REFERENCES sources(id),page INTEGER NOT NULL,image TEXT NOT NULL,region TEXT,fields TEXT NOT NULL,original_fields TEXT NOT NULL,ocr_text TEXT NOT NULL,engine TEXT NOT NULL,warning TEXT NOT NULL,supplier_id TEXT REFERENCES suppliers(id),checks TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'review',revision INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,parent_id TEXT REFERENCES documents(id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,company_id TEXT NOT NULL,document_id TEXT,action TEXT NOT NULL,detail TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),target TEXT NOT NULL,snapshot TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batch_items(batch_id TEXT NOT NULL REFERENCES batches(id),document_id TEXT NOT NULL REFERENCES documents(id),status TEXT NOT NULL DEFAULT 'exported',reason TEXT NOT NULL DEFAULT '',PRIMARY KEY(batch_id,document_id));
        CREATE TABLE IF NOT EXISTS profiles(company_id TEXT PRIMARY KEY REFERENCES companies(id),settings TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),supplier_code TEXT NOT NULL,number TEXT NOT NULL,UNIQUE(company_id,supplier_code,number));
        ''')
        advanced.initialize(c)
        learning.initialize(c)

    app = FastAPI(title='Invoice Assistant', version='0.1.0')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])
    app.state.data_dir = data

    @app.middleware('http')
    async def local_origin(request, call_next):
        # The application is a local single-user tool; reject browser cross-site writes.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != str(request.base_url).rstrip('/')):
                return JSONResponse({'detail': 'בקשה ממקור חיצוני נחסמה'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        return response

    def company(c, company_id):
        row = c.execute('SELECT * FROM companies WHERE id=?', (company_id,)).fetchone()
        if not row: fail('החברה לא נמצאה', 404)
        return {**dict(row),**advanced.company_meta(c,company_id)}

    def document(c, document_id, company_id=None):
        row = c.execute('SELECT d.*,s.name AS source_name FROM documents d JOIN sources s ON s.id=d.source_id WHERE d.id=?', (document_id,)).fetchone()
        if not row or (company_id and row['company_id'] != company_id): fail('המסמך לא נמצא בחברה', 404)
        result = dict(row)
        for key in ('fields', 'original_fields', 'checks', 'region'): result[key] = json.loads(result[key]) if result[key] else None
        return result

    def audit(c, company_id, doc_id, action, detail):
        c.execute('INSERT INTO audit(company_id,document_id,action,detail,created) VALUES(?,?,?,?,?)', (company_id, doc_id, action, encode(detail), now()))

    def supplier_for(c, d):
        row = c.execute('SELECT * FROM suppliers WHERE id=? AND company_id=?', (d['supplier_id'], d['company_id'])).fetchone()
        return {**dict(row),**advanced.supplier_meta(c,row['id'])} if row else None

    def doc_issues(c, d):
        result = issues(d['fields'], supplier_for(c, d), d['checks'], company(c, d['company_id']))
        result.extend(advanced.extra_issues(c,d))
        result.extend(learning.approval_issues(c,d))
        f = d['fields']
        if d['supplier_id'] and f.get('number'):
            selected = supplier_for(c, d)
            tax_id = selected['tax_id'] if selected else ''
            peers = c.execute("SELECT d.id,d.fields FROM documents d LEFT JOIN suppliers s ON s.id=d.supplier_id WHERE d.company_id=? AND (d.supplier_id=? OR (?<>'' AND s.tax_id=?)) AND d.id<>? AND d.status<>'archived'", (d['company_id'], d['supplier_id'], tax_id, tax_id, d['id']))
            for peer in peers:
                pf = json.loads(peer['fields'])
                if pf.get('number', '').strip().casefold() == f['number'].strip().casefold() and pf.get('kind') == f.get('kind'):
                    result.append('חשד לכפילות: אותו ספק, סוג ומספר מסמך'); break
        return result

    def describe(c, d):
        advanced.enrich(c, d)
        d['issues'] = doc_issues(c, d)
        d['supplier'] = supplier_for(c, d)
        d['learning_context']=advanced.meta(c,d['id']).get('learning_context',{})
        d['supplier_reviewed']=bool(learning.supplier_review(c,d['supplier_id'],d['company_id'])) if d['supplier_id'] else False
        return d

    def match_supplier(c, company_id, fields):
        if fields.get('supplier_tax_id'):
            rows = c.execute('SELECT id FROM suppliers WHERE company_id=? AND tax_id=? AND active=1', (company_id, fields['supplier_tax_id'])).fetchall()
            if len(rows) == 1: return rows[0]['id']
        if fields.get('supplier_name'):
            row = c.execute('SELECT a.supplier_id FROM aliases a JOIN suppliers s ON s.id=a.supplier_id WHERE a.company_id=? AND a.alias=? AND s.active=1', (company_id, fields['supplier_name'].strip().casefold())).fetchone()
            if row: return row['supplier_id']
            return learning.match_alias(c,company_id,fields['supplier_name'])
        return None

    def insert_document(c, company_id, source_id, page, image_path, text, engine, warning, region=None, parent_id=None, tokens=None):
        doc_id = uid(); fields = extract(text,tokens,company(c,company_id)['tax_id'])
        original_fields = dict(fields)
        supplier_id = match_supplier(c, company_id, fields)
        defaults=learning.defaults(c,company_id,supplier_id)
        defaults.update(advanced.defaults_for(c, company_id, supplier_id, fields['kind']))
        for key,value in defaults.items():
            if key=='currency' and fields.get('currency'):continue
            fields[key]=value
        c.execute('INSERT INTO documents(id,company_id,source_id,page,image,region,fields,original_fields,ocr_text,engine,warning,supplier_id,created,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (doc_id, company_id, source_id, page, image_path.relative_to(data).as_posix(), encode(region) if region else None, encode(fields), encode(original_fields), text, engine, warning, supplier_id, now(), parent_id))
        suggestions=advanced.meta(c,parent_id).get('suggested_checks',[]) if parent_id else vision.suggested_checks(image_path,[fields.get(key,'') for key in ('number','supplier_tax_id','recipient_tax_id','allocation')],words=tokens or [])
        for proposal in suggestions:proposal['source_document_id']=doc_id
        advanced.save_meta(c,doc_id,{'suggested_checks':suggestions})
        advanced.save_meta(c, doc_id, {'tokens':tokens or [],'field_sources':vision.field_sources(original_fields,tokens or []),'field_engines':{key:engine for key,value in original_fields.items() if value}})
        audit(c, company_id, doc_id, 'created', {'engine': engine, 'page': page})
        return doc_id

    @app.get('/api/health')
    def health():
        with db() as c: c.execute('SELECT 1').fetchone()
        return {'ok': True, 'version': '0.1.0', 'ocr': ocr.capabilities(), 'storage': 'sqlite', 'cloud_enabled': False}

    @app.get('/api/catalog')
    def catalog():
        return {'targets': [{'name': t, 'verified': False, 'support': 'generic_export'} for t in TARGETS], 'types': TYPES, 'export_fields': EXPORT_FIELDS}

    @app.get('/api/companies')
    def companies():
        with db() as c: return [company(c,r['id']) for r in c.execute('SELECT * FROM companies ORDER BY created')]

    @app.post('/api/companies')
    def add_company(payload: dict = Body(...)):
        name = str(payload.get('name', '')).strip()
        if not name or len(name) > 150: fail('יש להזין שם חברה עד 150 תווים')
        tax_id = str(payload.get('tax_id', '')).strip()
        if tax_id and not re.fullmatch(r'\d{9}', tax_id): fail('מספר עוסק צריך להכיל 9 ספרות')
        target = payload.get('target', TARGETS[0])
        if target not in TARGETS: fail('תוכנת יעד לא נתמכת')
        with db() as c:
            company_id = uid()
            c.execute('INSERT INTO companies VALUES(?,?,?,?,?)', (company_id, name, tax_id, target, now()))
            advanced.save_company_meta(c,company_id,{'code':str(payload.get('code',''))[:100]})
            return company(c, company_id)

    @app.get('/api/companies/{company_id}/suppliers')
    def suppliers(company_id: str):
        with db() as c:
            company(c, company_id)
            return [{**dict(r),**advanced.supplier_meta(c,r['id'])} for r in c.execute('SELECT * FROM suppliers WHERE company_id=? ORDER BY name', (company_id,))]

    def save_supplier(c, company_id, p):
        code, name = str(p.get('code', '')).strip(), str(p.get('name', '')).strip()
        if not code or not name or max(len(code), len(name)) > 200: fail('חסרים קוד ושם ספק או שהם ארוכים מדי')
        existing=c.execute('SELECT * FROM suppliers WHERE company_id=? AND code=?',(company_id,code)).fetchone()
        tax_id = str(p.get('tax_id', existing['tax_id'] if existing else '')).strip()
        if tax_id and not re.fullmatch(r'\d{9}', tax_id): fail('מספר עוסק ספק צריך להכיל 9 ספרות')
        c.execute('INSERT INTO suppliers(id,company_id,code,name,tax_id,active,expense_account) VALUES(?,?,?,?,?,?,?) ON CONFLICT(company_id,code) DO UPDATE SET name=excluded.name,tax_id=excluded.tax_id,active=excluded.active,expense_account=excluded.expense_account',
                  (uid(), company_id, code, name, tax_id, existing['active'] if existing and p.get('active') in (None,'') else 0 if str(p.get('active','1')).strip().casefold() in ('false','0','לא','inactive','no') else 1, str(p.get('expense_account',existing['expense_account'] if existing else ''))[:200]))
        row=dict(c.execute('SELECT * FROM suppliers WHERE company_id=? AND code=?', (company_id, code)).fetchone())
        additional=advanced.supplier_meta(c,row['id'])
        additional.update({key:str(p[key])[:1000] for key in ('sort_code','branch','parent_code','company_code','notes') if key in p})
        c.execute('INSERT INTO supplier_meta VALUES(?,?) ON CONFLICT(supplier_id) DO UPDATE SET value=excluded.value',(row['id'],encode(additional)))
        return {**row,**additional}

    @app.post('/api/companies/{company_id}/suppliers')
    def add_supplier(company_id: str, payload: dict = Body(...)):
        with db() as c:
            company(c, company_id)
            result = save_supplier(c, company_id, payload)
            audit(c, company_id, None, 'supplier_saved', {'code': result['code']})
            return result

    @app.post('/api/companies/{company_id}/suppliers/preview')
    def preview_suppliers(company_id: str, file: UploadFile = File(...)):
        with db() as c: company(c, company_id)
        content = file.file.read(5_000_001)
        if len(content) > 5_000_000: fail('קובץ הרשימות מוגבל ל־5MB')
        try:
            if (file.filename or '').lower().endswith('.xlsx'):
                # Bound expansion before parsing arbitrary ZIP/XML input.
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    if sum(i.file_size for i in z.infolist()) > 40_000_000: fail('קובץ Excel גדול מדי לאחר פתיחה')
                book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                sheets = {}
                for sheet in book.worksheets[:20]:
                    rows = []
                    for row in sheet.iter_rows(max_row=1002, max_col=min(sheet.max_column or 50, 50)):
                        values = []
                        for cell in row:
                            value = cell.value
                            if value is None: values.append('')
                            elif isinstance(value, (int, float)) and re.fullmatch(r'0+', cell.number_format or ''):
                                values.append(str(int(value)).zfill(len(cell.number_format)))
                            else: values.append(str(value))
                        rows.append(values)
                    if len(rows) > 1001: fail('אפשר לייבא עד 1,000 שורות בכל קובץ')
                    sheets[sheet.title] = rows
                book.close()
            else:
                try: text = content.decode('utf-8-sig')
                except UnicodeDecodeError: text = content.decode('cp1255')
                try: dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
                except csv.Error: dialect = csv.excel
                rows = list(csv.reader(io.StringIO(text), dialect))
                if len(rows) > 1001 or any(len(r) > 50 for r in rows): fail('אפשר לייבא עד 1,000 שורות ו־50 עמודות')
                sheets = {'CSV': rows}
        except HTTPException: raise
        except Exception: fail('לא ניתן לקרוא את הקובץ. יש לבחור CSV, TXT או XLSX תקין')
        return {'sheets': sheets}

    @app.post('/api/companies/{company_id}/suppliers/import')
    def import_suppliers(company_id: str, payload: dict = Body(...)):
        rows = payload.get('rows', [])
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000 or any(not isinstance(r, dict) for r in rows): fail('יש לבחור 1–1,000 שורות')
        with db() as c:
            company(c, company_id)
            codes = [str(r.get('code', '')).strip() for r in rows]
            if len(codes) != len(set(codes)): fail('יש קודי ספק כפולים בקובץ; יש לתקן לפני היבוא')
            results = [save_supplier(c, company_id, p) for p in rows]
            audit(c, company_id, None, 'suppliers_imported', {'count': len(results)})
            return {'count': len(results)}

    @app.get('/api/companies/{company_id}/documents')
    def documents(company_id: str):
        with db() as c:
            company(c, company_id)
            ids = c.execute('SELECT id FROM documents WHERE company_id=? ORDER BY created DESC', (company_id,)).fetchall()
            return [describe(c, document(c, r['id'])) for r in ids]

    @app.post('/api/companies/{company_id}/upload')
    def upload(company_id: str, file: UploadFile = File(...)):
        content = file.file.read(25_000_001)
        if not content or len(content) > 25_000_000: fail('יש לבחור קובץ עד 25MB')
        sha = hashlib.sha256(content).hexdigest()
        with db() as c:
            company_info=company(c, company_id)
            duplicate = c.execute('SELECT id FROM sources WHERE company_id=? AND sha=?', (company_id, sha)).fetchone()
            if duplicate: fail('הקובץ הזה כבר קיים בחברה', 409)
        source_id = uid(); source_dir = files / source_id; source_dir.mkdir()
        original = source_dir / 'original'
        original.write_bytes(content)
        pages = []
        persisted=False
        try:
            if content.startswith(b'%PDF-'):
                with fitz.open(stream=content, filetype='pdf') as pdf:
                    if pdf.needs_pass: fail('PDF מוצפן אינו נתמך; יש להעלות עותק פתוח')
                    if not 1 <= len(pdf) <= 50: fail('אפשר להעלות עד 50 עמודים בקובץ')
                    for index, page in enumerate(pdf):
                        if page.rect.width * page.rect.height * 4 > 40_000_000: fail('עמוד PDF גדול מדי')
                        path = source_dir / f'page-{index+1}.png'
                        page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(path)
                        text = page.get_text().strip()
                        if len(text) >= 20: engine, warning = 'pdf-text', ''
                        else:
                            prepared=source_dir/f'prepared-{index+1}.png'
                            vision.preprocess(path,prepared);prepared.replace(path)
                            text,engine,warning=ocr.recognize(path,quality=company_info['ocr_quality']) if company_info.get('ocr_quality') in ('best','rivok') else ocr.recognize(path)
                        words = vision.pdf_tokens(page) if engine == 'pdf-text' else vision.tokens(path)
                        pages.append((index+1, path, text, engine, warning, words, {'items':vision.pdf_items(page),'processing':{'pdf_rotation':page.rotation}}))
            else:
                with Image.open(io.BytesIO(content)) as img:
                    if img.format not in ('JPEG', 'PNG', 'TIFF', 'WEBP'): fail('פורמט תמונה לא נתמך')
                    if getattr(img, 'n_frames', 1) > 50: fail('אפשר להעלות עד 50 עמודים בקובץ')
                    for index in range(getattr(img, 'n_frames', 1)):
                        img.seek(index)
                        if img.width * img.height > 40_000_000: fail('התמונה גדולה מדי')
                        path = source_dir / f'page-{index+1}.png'
                        ImageOps.exif_transpose(img.copy()).convert('RGB').save(path)
                        prepared = source_dir / f'prepared-{index+1}.png'
                        processing=vision.preprocess(path, prepared)
                        prepared.replace(path)
                        text,engine,warning=ocr.recognize(path,quality=company_info['ocr_quality']) if company_info.get('ocr_quality') in ('best','rivok') else ocr.recognize(path)
                        pages.append((index+1, path, text, engine, warning, vision.tokens(path), {'processing':processing}))
            with db() as c:
                c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)', (source_id, company_id, (file.filename or 'document')[:250], original.relative_to(data).as_posix(), sha, now()))
                ids=[]
                for number,path,text,engine,warning,words,extra in pages:
                    ident=insert_document(c,company_id,source_id,number,path,text,engine,warning,tokens=words)
                    advanced.save_meta(c,ident,extra);ids.append(ident)
                from .cloud import settings as recognition_settings
                config=recognition_settings(c,company_id)
                failures=[ident for ident in ids if document(c,ident)['engine'] in ('failed','unavailable') or any(not document(c,ident)['fields'].get(key) for key in ('number','date','total'))]
            persisted=True
            proposals=[]
            if config.get('mode')=='on_failure':
                for ident in failures:
                    try:
                        proposal=app.state.additional_recognize(ident,{'trigger':'failure','local_failed':True,'action':'document'})
                        proposals.append({'document_id':ident,'run_id':proposal['id']})
                    except HTTPException:pass
            return {'ids':ids,'pages':len(ids),'additional_proposals':proposals}
        except Exception as exc:
            if persisted:return {'ids':ids,'pages':len(ids),'warning':'המסמכים נשמרו לבדיקה. הזיהוי הנוסף לא הושלם; אפשר להפעילו ידנית.'}
            import shutil
            shutil.rmtree(source_dir, ignore_errors=True)
            if isinstance(exc, HTTPException): raise
            if isinstance(exc, sqlite3.IntegrityError): fail('הקובץ הזה כבר קיים בחברה', 409)
            fail('הקובץ אינו PDF או תמונה תקינים, או שלא ניתן לעבד אותו')

    @app.get('/api/documents/{doc_id}/image')
    def document_image(doc_id: str, original: bool = False):
        with db() as c:
            d = document(c, doc_id)
            path = files / d['source_id'] / f"page-{d['page']}.png" if original else data / d['image']
        return FileResponse(path, media_type='image/png')

    @app.get('/api/documents/{doc_id}/source')
    def source_file(doc_id: str):
        with db() as c:
            d = document(c, doc_id)
            s = c.execute('SELECT * FROM sources WHERE id=?', (d['source_id'],)).fetchone()
            return FileResponse(data / s['path'], filename=s['name'], media_type='application/octet-stream')

    @app.patch('/api/documents/{doc_id}')
    def update_document(doc_id: str, payload: dict = Body(...)):
        with db() as c:
            d = document(c, doc_id)
            if d['status'] in ('exported', 'received', 'archived'): fail('מסמך יוצא, נקלט או בארכיון אינו ניתן לשינוי', 409)
            if payload.get('revision') != d['revision']: fail('המסמך עודכן. יש לרענן לפני שמירה', 409)
            f = dict(d['fields'])
            if not isinstance(payload.get('fields', {}), dict): fail('שדות לא תקינים')
            for key, value in payload.get('fields', {}).items():
                if key not in FIELDS or not isinstance(value, str) or len(value) > 4000: fail('שדה לא תקין')
                f[key] = value.strip()
            supplier_id = payload.get('supplier_id', d['supplier_id']) or None
            if supplier_id and not c.execute('SELECT 1 FROM suppliers WHERE id=? AND company_id=?', (supplier_id, d['company_id'])).fetchone(): fail('הספק אינו שייך לחברה')
            checks = payload.get('checks', d['checks'])
            if not isinstance(checks, list) or len(checks) > 50: fail('רשימת צ׳קים לא תקינה')
            clean_checks = []
            for check in checks:
                if not isinstance(check, dict): fail('צ׳ק לא תקין')
                number = check.get('number', '')
                if not isinstance(number, str) or len(number) > 30: fail('מספר צ׳ק צריך להישמר כטקסט')
                region = check.get('region')
                if region is not None: validate_region(region)
                origin = advanced.source_document(c,d,check.get('source_document_id'))
                learning.check_writer(c,d['company_id'],check.get('writer_id'))
                if check.get('reason','corrected_value') not in learning.REASONS:fail('סיבת תיקון צ׳ק לא תקינה')
                clean_checks.append({'number': number.strip(), 'confirmed': check.get('confirmed') is True, 'region': region, 'engine': str(check.get('engine', 'manual'))[:100], 'source_document_id':origin['id'], 'source_id': origin['source_id'], 'page': origin['page'], 'proposals':check.get('proposals',[])[:30] if isinstance(check.get('proposals',[]),list) else []})
                clean_checks[-1].update(writer_id=check.get('writer_id') or None,reason=check.get('reason',learning.feedback(payload)['reason']),explanation=str(check.get('explanation',learning.feedback(payload)['explanation']))[:2000])
            c.execute("UPDATE documents SET fields=?,supplier_id=?,checks=?,status='review',revision=revision+1 WHERE id=?", (encode(f), supplier_id, encode(clean_checks), doc_id))
            if payload.get('remember') and supplier_id and f.get('supplier_name'):
                c.execute('INSERT INTO aliases VALUES(?,?,?) ON CONFLICT(company_id,alias) DO UPDATE SET supplier_id=excluded.supplier_id', (d['company_id'], f['supplier_name'].casefold(), supplier_id))
            audit(c, d['company_id'], doc_id, 'edited', {'before': d['fields'], 'after': f, 'checks_before': d['checks'], 'checks_after': clean_checks, 'supplier_before': d['supplier_id'], 'supplier_after': supplier_id, 'revision': d['revision'] + 1})
            learning.record_edit(c,d,payload,f,supplier_id,clean_checks,data,crop,validate_region)
            advanced.update_extra(c, d, payload, f, supplier_id)
            advanced.remember_checks(c,d,clean_checks,data,crop)
            result=describe(c, document(c, doc_id))
        app.state.learning.auto(d['company_id'])
        return result

    def editable(d):
        if d['status'] in ('exported', 'received', 'archived'):
            fail('המסמך נעול לשינוי במצב זה', 409)

    def validate_region(region):
        import math
        if not isinstance(region, dict): fail('אזור לא תקין')
        values = [region.get(k) for k in ('x', 'y', 'w', 'h')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values): fail('אזור לא תקין')
        x, y, w, h = values
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001 or region.get('rotation', 0) not in (0, 90, 180, 270): fail('האזור מחוץ לעמוד או שהסיבוב לא תקין')

    def crop(d, region, destination):
        validate_region(region)
        with Image.open(files / d['source_id'] / f"page-{d['page']}.png") as img:
            x, y, w, h = [region[k] for k in ('x', 'y', 'w', 'h')]
            box = (round(x * img.width), round(y * img.height), round((x+w) * img.width), round((y+h) * img.height))
            if box[2] <= box[0] or box[3] <= box[1]: fail('האזור קטן מדי')
            img.crop(box).rotate(-region.get('rotation', 0), expand=True).save(destination, 'PNG')

    @app.post('/api/documents/{doc_id}/check-region')
    def check_region(doc_id: str, payload: dict = Body(...)):
        region = payload.get('region'); validate_region(region)
        with db() as c:
            d = document(c, doc_id); editable(d)
            source_doc = advanced.source_document(c, d, payload.get('source_document_id'))
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'check.png'; crop(source_doc, region, path)
                text, engine, warning = ocr.recognize(path, digits=True)
                independent = vision.digit_proposal(path)
                from .sequence_digits import recognize as sequence_recognize
                writer=learning.check_writer(c,d['company_id'],payload.get('writer_id'))
                sequences=learning.multi_proposals(path) if learning.settings(c,d['company_id'])['multi_pass'] else [sequence_recognize(path,model) for model in ('paddle-v6-small','paddle-v5')]
                personal=app.state.learning.proposals(c,d['company_id'],path,writer)
                remembered=advanced.check_memory(c,d['company_id'],path) or learning.imported_memory(c,d['company_id'],path)
            # Keep uncertainty visible: multiple runs of digits are never concatenated.
            candidates = re.findall(r'[0-9]{1,30}', text)
            proposals=([remembered] if remembered else [])+personal+sequences+[{'engine':engine,'number':candidates[0] if len(candidates)==1 else ''},independent]
            proposals+=learning.registered_checks(c,d,proposals)
            proposals=learning.calibrate(c,d['company_id'],proposals)
            primary=next((p for p in proposals if p.get('number') and p.get('available') is not False),None)
            return {'number': primary['number'] if primary else candidates[0] if len(candidates) == 1 else '', 'candidates': candidates, 'confirmed': False, 'region': region, 'source_document_id':source_doc['id'], 'writer_id':writer,'engine': primary.get('engine',engine) if primary else engine, 'warning': warning, 'proposals':proposals}

    @app.get('/api/documents/{doc_id}/region-image')
    def region_image(doc_id: str, x: float, y: float, w: float, h: float, rotation: int = 0):
        region = dict(x=x, y=y, w=w, h=h, rotation=rotation)
        validate_region(region)
        with db() as c:
            d = document(c, doc_id)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'region.png'; crop(d, region, path)
                return Response(path.read_bytes(), media_type='image/png')

    @app.post('/api/documents/{doc_id}/split')
    def split_document(doc_id: str, payload: dict = Body(...)):
        regions = payload.get('regions', [])
        if not isinstance(regions, list) or not 2 <= len(regions) <= 20: fail('יש לסמן 2–20 אזורים')
        for region in regions: validate_region(region)
        created = []
        try:
            with db() as c:
                d = document(c, doc_id); editable(d)
                if c.execute('SELECT 1 FROM document_links WHERE primary_id=?',(doc_id,)).fetchone():fail('יש לפרק את חיבור עמודי ההמשך לפני פיצול העמוד')
                ids = []
                for region in regions:
                    path = files / d['source_id'] / f'crop-{uid()}.png'; created.append(path)
                    crop(d, region, path)
                    text, engine, warning = ocr.recognize(path)
                    ids.append(insert_document(c, d['company_id'], d['source_id'], d['page'], path, text, engine, warning, region, doc_id, tokens=vision.tokens(path)))
                c.execute("UPDATE documents SET status='archived',revision=revision+1 WHERE id=?", (doc_id,))
                audit(c, d['company_id'], doc_id, 'split', {'regions': regions, 'children': ids})
                return {'ids': ids}
        except Exception:
            for path in created: path.unlink(missing_ok=True)
            raise

    @app.post('/api/documents/{doc_id}/archive')
    def archive_document(doc_id: str):
        with db() as c:
            d = document(c, doc_id); editable(d)
            c.execute("UPDATE documents SET status='archived',revision=revision+1 WHERE id=?", (doc_id,))
            audit(c, d['company_id'], doc_id, 'archived', {})
        return {'ok': True}

    def selected_documents(c, company_id, payload):
        company(c, company_id)
        ids = payload.get('ids', [])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids): fail('יש לבחור מסמכים ללא כפילויות')
        return [document(c, i, company_id) for i in ids]

    @app.post('/api/companies/{company_id}/approve')
    def approve(company_id: str, payload: dict = Body(...)):
        with db() as c:
            docs = selected_documents(c, company_id, payload)
            errors = {}
            for d in docs:
                editable(d)
                found = doc_issues(c, d)
                if found: errors[d['id']] = found
            if errors: fail(errors, 422)
            for d in docs:
                c.execute("UPDATE documents SET status='approved',revision=revision+1 WHERE id=?", (d['id'],))
                learning.on_approve(c,d)
                audit(c, company_id, d['id'], 'approved', {'revision': d['revision'] + 1})
        app.state.learning.auto(company_id)
        return {'count': len(docs)}

    def default_profile():
        return {'columns': [{'field': f, 'title': f, 'max_length': 0} for f in EXPORT_FIELDS], 'delimiter': ',', 'encoding': 'utf-8-sig', 'date_format': '%Y-%m-%d'}

    def profile(c, company_id):
        company(c, company_id)
        row = c.execute('SELECT settings FROM profiles WHERE company_id=?', (company_id,)).fetchone()
        return json.loads(row[0]) if row else default_profile()

    @app.get('/api/companies/{company_id}/profile')
    def get_profile(company_id: str):
        with db() as c: return profile(c, company_id)

    @app.put('/api/companies/{company_id}/profile')
    def put_profile(company_id: str, payload: dict = Body(...)):
        columns = payload.get('columns')
        if not isinstance(columns, list) or not 1 <= len(columns) <= len(EXPORT_FIELDS): fail('עמודות יצוא לא תקינות')
        seen = set()
        for col in columns:
            if not isinstance(col, dict) or col.get('field') not in EXPORT_FIELDS or not isinstance(col.get('title'), str) or not col['title'].strip() or len(col['title']) > 100: fail('עמודת יצוא לא תקינה')
            if col['title'] in seen: fail('כותרות עמודות צריכות להיות ייחודיות')
            seen.add(col['title'])
            limit = col.get('max_length', 0)
            if type(limit) is not int or not 0 <= limit <= 4000: fail('אורך שדה לא תקין')
        if payload.get('delimiter') not in (',', ';', '\t') or payload.get('encoding') not in ('utf-8-sig', 'cp1255') or payload.get('date_format') not in ('%Y-%m-%d', '%d/%m/%Y', '%d%m%Y'): fail('פורמט יצוא לא נתמך')
        clean = {k: payload[k] for k in ('columns', 'delimiter', 'encoding', 'date_format')}
        from .integrations import profile_options
        clean.update(profile_options(payload))
        with db() as c:
            company(c, company_id)
            c.execute('INSERT INTO profiles VALUES(?,?) ON CONFLICT(company_id) DO UPDATE SET settings=excluded.settings', (company_id, encode(clean)))
            c.execute('DELETE FROM verification WHERE company_id=?',(company_id,))
            audit(c, company_id, None, 'profile_saved', clean)
        return clean

    def export_row(c, d):
        supplier = supplier_for(c, d)
        row = dict(d['fields'])
        row.update(supplier_code=supplier['code'], checks=';'.join(ch['number'] for ch in d['checks']),document_id=d['id'],company_code=company(c,d['company_id']).get('code',''))
        row['supplier_name']=row.get('supplier_name') or supplier['name']
        row['supplier_tax_id']=row.get('supplier_tax_id') or supplier['tax_id']
        for k in ('net', 'vat', 'total'): row[k] = str(money(row[k]).quantize(Decimal('.01')))
        return row

    def formatted_rows(snapshot):
        settings = snapshot['profile']; output = []
        for item in snapshot['items']:
            row = []
            for col in settings['columns']:
                value = item['row'].get(col['field'], '')
                if col['field'] in ('date', 'due_date') and value:
                    value = datetime.strptime(value, '%Y-%m-%d').strftime(settings['date_format'])
                if col.get('max_length', 0) and len(value) > col['max_length']: fail(f"השדה {col['title']} חורג מהאורך המותר במסמך {item['row']['number']}")
                row.append(safe_cell(value))
            output.append(row)
        return output

    def csv_bytes(snapshot):
        out = io.StringIO(newline=''); writer = csv.writer(out, delimiter=snapshot['profile']['delimiter'])
        writer.writerow([safe_cell(c['title']) for c in snapshot['profile']['columns']]); writer.writerows(formatted_rows(snapshot))
        try: return out.getvalue().encode(snapshot['profile']['encoding'])
        except UnicodeEncodeError: fail('קידוד היצוא אינו תומך בחלק מהתווים; יש לבחור UTF-8')

    def batch_details(c, batch_id):
        row = c.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
        if not row: fail('מנת היצוא לא נמצאה', 404)
        result = dict(row); result['snapshot'] = json.loads(result['snapshot'])
        result['items'] = [dict(i) for i in c.execute('SELECT * FROM batch_items WHERE batch_id=? ORDER BY document_id', (batch_id,))]
        return result

    def prepare_batch(c, company_id, payload):
        docs = selected_documents(c, company_id, payload)
        for d in docs:
            if d['status'] != 'approved': fail('אפשר לייצא רק מסמכים מאושרים שטרם יוצאו', 409)
            found = doc_issues(c, d)
            if found: fail({d['id']: found}, 422)
        settings=profile(c, company_id)
        from .integrations import journal, fixed_bytes
        items=[]
        for d in docs:
            row=export_row(c,d);meta=advanced.meta(c,d['id'])
            if settings.get('check_policy')=='single' and len(d['checks'])>1:fail('הפרופיל מקבל מספר צ׳ק אחד. יש לבחור במפורש כיצד לטפל בכמה צ׳קים לפני יצוא')
            if settings.get('check_policy')=='first':row['checks']=d['checks'][0]['number'] if d['checks'] else ''
            row['reference']=row['checks']
            if settings.get('check_mapping')=='notes':row['notes']=(row.get('notes','')+' צ׳קים: '+row['checks']).strip();row['reference']=''
            if settings.get('check_mapping')=='payment' and not meta.get('payment'):fail('מיפוי לצ׳ק בתנועת תשלום מחייב יצירה ואישור מפורשים של פרטי תשלום')
            items.append({'document_id':d['id'],'row':row,'checks':d['checks'],'revision':d['revision'],'items':meta.get('items',[]),'payment':meta.get('payment'),'journal':journal(row,meta.get('payment')),'ocr_engine':d['engine'],'field_engines':meta.get('field_engines',{}),'original_fields':d['original_fields'],'mapping_version':settings.get('version','default-v1'),'pages':[d['id']]+[r[0] for r in c.execute('SELECT child_id FROM document_links WHERE primary_id=?',(d['id'],))]})
        snapshot = {'profile':settings,'items':items,'verified':False}
        if settings.get('format')=='fixed':fixed_bytes(snapshot)
        csv_bytes(snapshot)  # Validate all lengths and encoding before changing any status.
        return docs, snapshot

    @app.post('/api/companies/{company_id}/batches/preview')
    def preview_batch(company_id: str, payload: dict = Body(...)):
        with db() as c:
            _, snapshot = prepare_batch(c, company_id, payload)
            return {'columns': [col['title'] for col in snapshot['profile']['columns']], 'rows': formatted_rows(snapshot), 'verified': False}

    @app.post('/api/companies/{company_id}/batches')
    def create_batch(company_id: str, payload: dict = Body(...)):
        with db() as c:
            docs, snapshot = prepare_batch(c, company_id, payload)
            batch_id = uid()
            c.execute('INSERT INTO batches VALUES(?,?,?,?,?)', (batch_id, company_id, company(c, company_id)['target'], encode(snapshot), now()))
            for d in docs:
                c.execute('INSERT INTO batch_items(batch_id,document_id) VALUES(?,?)', (batch_id, d['id']))
                c.execute("UPDATE documents SET status='exported',revision=revision+1 WHERE id=?", (d['id'],))
                audit(c, company_id, d['id'], 'exported', {'batch_id': batch_id, 'revision': d['revision']})
            return batch_details(c, batch_id)

    @app.get('/api/companies/{company_id}/batches')
    def batches(company_id: str):
        with db() as c:
            company(c, company_id)
            return [batch_details(c, r[0]) for r in c.execute('SELECT id FROM batches WHERE company_id=? ORDER BY created DESC', (company_id,)).fetchall()]

    @app.get('/api/batches/{batch_id}/download')
    def download_batch(batch_id: str, format: str = 'xlsx'):
        with db() as c: snapshot = batch_details(c, batch_id)['snapshot']
        if format in ('zip','fixed','json'):
            from .integrations import archive_bytes, fixed_bytes
            content=archive_bytes(snapshot,csv_bytes) if format=='zip' else fixed_bytes(snapshot) if format=='fixed' else encode(snapshot).encode('utf-8')
            return Response(content,media_type='application/zip' if format=='zip' else 'application/json' if format=='json' else 'text/plain',headers={'Content-Disposition':f'attachment; filename="batch-{batch_id}.{format if format!="fixed" else "txt"}"'})
        if format == 'csv':
            return Response(csv_bytes(snapshot), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="batch-{batch_id}.csv"'})
        if format != 'xlsx': fail('פורמט לא נתמך')
        book = Workbook(); sheet = book.active; sheet.title = 'מסמכים'
        sheet.append([safe_cell(c['title']) for c in snapshot['profile']['columns']])
        for row in formatted_rows(snapshot): sheet.append(row)
        checks = book.create_sheet('צ׳קים'); checks.append(['document_id', 'number', 'confirmed', 'region'])
        for item in snapshot['items']:
            for ch in item['checks']: checks.append([item['document_id'], safe_cell(ch['number']), ch['confirmed'], encode(ch.get('region'))])
        lines = book.create_sheet('שורות'); lines.append(['document_id', 'description', 'quantity', 'price', 'discount', 'total','item_code'])
        for item in snapshot['items']:
            for line in item.get('items',[]):lines.append([item['document_id']]+[safe_cell(line.get(k,'')) for k in ('description','quantity','price','discount','total','item_code')])
        ledger=book.create_sheet('פקודות יומן');ledger.append(['document_id','account','debit','credit','reference','reference2'])
        for item in snapshot['items']:
            for entry in item.get('journal',[]):ledger.append([item['document_id']]+[safe_cell(entry.get(k,'')) for k in ('account','debit','credit','reference','reference2')])
        exceptions = book.create_sheet('חריגים'); exceptions.append(['הערה']); exceptions.append(['בדקו אימות פרופיל וקליטה בחברת בדיקה לפני שימוש בתוכנת היעד.'])
        for ws in book:
            ws.sheet_view.rightToLeft = True; ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
            for column in ws.columns:
                ws.column_dimensions[column[0].column_letter].width = 24
                for cell in column: cell.number_format = '@'
        out = io.BytesIO(); book.save(out)
        return Response(out.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': f'attachment; filename="batch-{batch_id}.xlsx"'})

    @app.post('/api/batches/{batch_id}/receipt')
    def record_receipt(batch_id: str, payload: dict = Body(...)):
        doc_id, status, reason = payload.get('document_id'), payload.get('status'), payload.get('reason')
        if status not in ('received', 'rejected') or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000: fail('יש לציין תוצאת קליטה וסיבת אישור או דחייה')
        with db() as c:
            batch = batch_details(c, batch_id)
            item = next((i for i in batch['items'] if i['document_id'] == doc_id), None)
            if not item: fail('המסמך אינו במנה', 404)
            d = document(c, doc_id)
            # Receipt can only resolve the current, still outstanding export.
            if item['status'] != 'exported' or d['status'] != 'exported': fail('תוצאת המנה כבר תועדה או שהמסמך התקדם למנה אחרת', 409)
            c.execute('UPDATE batch_items SET status=?,reason=? WHERE batch_id=? AND document_id=?', (status, reason.strip(), batch_id, doc_id))
            c.execute('UPDATE documents SET status=?,revision=revision+1 WHERE id=?', (status, doc_id))
            audit(c, d['company_id'], doc_id, 'receipt_manual', {'batch_id': batch_id, 'status': status, 'reason': reason.strip()})
        return {'ok': True}

    @app.get('/api/companies/{company_id}/deliveries')
    def deliveries(company_id: str):
        with db() as c:
            company(c, company_id)
            docs = [document(c, r[0]) for r in c.execute("SELECT id FROM documents WHERE company_id=? AND status<>'archived'", (company_id,)).fetchall()]
            result = []
            for row in c.execute('SELECT * FROM deliveries WHERE company_id=? ORDER BY number,supplier_code', (company_id,)):
                item = dict(row); item['matches'] = []
                for d in docs:
                    supplier = supplier_for(c, d)
                    if supplier and supplier['code'] == item['supplier_code'] and item['number'] in [n.strip() for n in d['fields'].get('delivery_refs', '').split(',')]: item['matches'].append(d['id'])
                result.append(item)
            return result

    @app.post('/api/companies/{company_id}/deliveries')
    def import_deliveries(company_id: str, payload: dict = Body(...)):
        rows = payload.get('rows')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000: fail('יש לבחור עד 1,000 תעודות')
        with db() as c:
            company(c, company_id)
            for row in rows:
                if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not 1 <= len(row[k].strip()) <= 200 for k in ('supplier_code', 'number')): fail('חסרים קוד ספק או מספר תעודה')
                c.execute('INSERT INTO deliveries VALUES(?,?,?,?) ON CONFLICT(company_id,supplier_code,number) DO NOTHING', (uid(), company_id, row['supplier_code'].strip(), row['number'].strip()))
            audit(c, company_id, None, 'deliveries_imported', {'count': len(rows)})
        return {'count': len(rows)}

    @app.get('/api/companies/{company_id}/audit')
    def history(company_id: str):
        with db() as c:
            company(c, company_id)
            return [{**dict(r), 'detail': json.loads(r['detail'])} for r in c.execute('SELECT * FROM audit WHERE company_id=? ORDER BY id DESC LIMIT 2000', (company_id,))]

    @app.get('/api/backup')
    def backup():
        temp = tempfile.TemporaryDirectory()
        try:
            target = Path(temp.name) / 'invoices.sqlite3'
            # Take the SQLite snapshot and its referenced files while mutations are locked.
            with db() as c:
                with closing(sqlite3.connect(target)) as destination:
                    with closing(sqlite3.connect(database)) as source: source.backup(destination)
                referenced = {r[0].replace('\\','/') for r in c.execute('SELECT path FROM sources')} | {r[0].replace('\\','/') for r in c.execute('SELECT image FROM documents')}
                for r in c.execute('SELECT source_id,page FROM documents'):
                    referenced.add(f'files/{r[0]}/page-{r[1]}.png')
                referenced.update(learning.backup_paths(c))
                out = Path(temp.name) / 'backup.zip'
                with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.write(target, 'invoices.sqlite3')
                    for relative in sorted(referenced): archive.write(data / relative, relative)
            return FileResponse(out,media_type='application/zip',filename='invoice-assistant-backup.zip',background=BackgroundTask(temp.cleanup))
        except Exception:
            temp.cleanup()
            raise

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    def index(): return FileResponse(static / 'index.html')

    advanced.register(app, {**locals(),'TARGETS':TARGETS,'EXPORT_FIELDS':EXPORT_FIELDS})
    learning.register(app,locals())
    model_runtime.register(app)
    learning_transfer.register(app,locals())
    review_tools.register(app,locals())
    return app

app = create_app()
