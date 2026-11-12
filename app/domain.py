"""Accounting rules shared by review and export. Money is never stored as float."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

TARGETS = ['חשבשבת H-ERP', 'חשבשבת H-WEB', 'ריווחית', 'לירם רמפלוס',
           'Priority ERP', 'SUMIT Books', 'SAP Business One', 'מחשבת · מיכונים',
           'כספית בענן', 'Morning', 'iCount', 'COMAX', 'Manager Premium', 'לינט',
           'EasyCount', 'Dynamics 365 Business Central', 'Oracle NetSuite', 'Odoo', 'אחר']
TYPES = {'invoice': 'חשבונית מס', 'tax_receipt': 'חשבונית מס־קבלה',
         'credit': 'זיכוי', 'receipt': 'קבלה', 'proforma': 'חשבון עסקה', 'delivery': 'תעודת משלוח'}
FIELDS = ['supplier_name', 'supplier_tax_id', 'recipient_tax_id', 'number', 'date',
          'due_date', 'net', 'vat', 'total', 'currency', 'allocation', 'delivery_refs',
          'expense_account', 'notes', 'kind']
EXPORT_FIELDS = ['supplier_code', 'supplier_name', 'supplier_tax_id', 'kind', 'number',
                 'date', 'due_date', 'net', 'vat', 'total', 'currency', 'allocation',
                 'checks', 'delivery_refs', 'expense_account', 'notes']

def money(value):
    try:
        amount = Decimal(str(value).replace(',', '').strip())
        if not amount.is_finite() or abs(amount) > Decimal('999999999999.99'):
            raise ValueError('סכום מחוץ לטווח')
        if amount != amount.quantize(Decimal('.01')):
            raise ValueError('לסכום מותרות שתי ספרות אחרי הנקודה')
        return amount
    except (InvalidOperation, ValueError):
        raise ValueError('סכום לא תקין') from None

def issues(fields, supplier, checks, company):
    out = []
    if fields.get('kind') not in {'invoice', 'tax_receipt', 'credit'}:
        out.append('סוג המסמך מיועד להתאמה או לארכיון, ולא ליצוא חשבוניות')
    if not fields.get('number', '').strip(): out.append('חסר מספר מסמך')
    try: date.fromisoformat(fields.get('date', ''))
    except (ValueError, TypeError): out.append('תאריך המסמך חסר או לא תקין')
    if fields.get('due_date'):
        try: date.fromisoformat(fields['due_date'])
        except ValueError: out.append('תאריך הפירעון לא תקין')
    if not supplier or not supplier['active']: out.append('יש לבחור כרטיס ספק פעיל')
    if supplier and fields.get('supplier_tax_id') and supplier['tax_id'] and fields['supplier_tax_id'] != supplier['tax_id']:
        out.append('מספר העוסק במסמך שונה מזה של כרטיס הספק')
    if fields.get('recipient_tax_id') and company['tax_id'] and fields['recipient_tax_id'] != company['tax_id']:
        out.append('המסמך מופנה לחברה אחרת')
    if fields.get('currency') not in {'ILS', 'USD', 'EUR', 'GBP'}: out.append('יש לבחור מטבע נתמך')
    try:
        net, vat, total = [money(fields.get(k, '')) for k in ('net', 'vat', 'total')]
        if abs(net + vat - total) > Decimal('.01'): out.append('סכום לפני מע״מ ומע״מ אינם מסתכמים לסך הכול')
        if fields.get('kind') == 'credit':
            if total >= 0 or net > 0 or vat > 0: out.append('סכומי זיכוי צריכים להיות שליליים')
        elif total <= 0 or net < 0 or vat < 0: out.append('סכומי חשבונית צריכים להיות חיוביים')
    except ValueError: out.append('יש להשלים סכומים תקינים, עד שתי ספרות אחרי הנקודה')
    for check in checks:
        if not re.fullmatch(r'[0-9]{1,30}', check.get('number', '')): out.append('מספר צ׳ק חייב להכיל ספרות בלבד')
        if not check.get('confirmed'): out.append('יש לאשר כל מספר צ׳ק')
    return list(dict.fromkeys(out))

def extract(text):
    """Conservative local suggestions; never approves a document or invents amounts."""
    result = dict.fromkeys(FIELDS, '')
    result.update(currency='ILS', kind='invoice')
    if 'זיכוי' in text: result['kind'] = 'credit'
    elif 'תעודת משלוח' in text: result['kind'] = 'delivery'
    elif 'חשבון עסקה' in text: result['kind'] = 'proforma'
    elif 'קבלה' in text: result['kind'] = 'tax_receipt' if 'חשבונית' in text else 'receipt'
    patterns = {
        'number': r'(?:חשבונית(?:\s+מס)?|Invoice)\s*(?:מספר|מס[׳\'\.]?|No\.?)?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{1,29})',
        'supplier_tax_id': r'(?:עוסק(?:\s+מורשה)?|ח[.״\"]?פ[.״\"]?|VAT\s*ID)\s*[:#]?\s*(\d{9})',
        'allocation': r'(?:מספר\s+הקצאה)\s*[:#]?\s*(\d+)',
        'net': r'(?:לפני\s+מע[״\"]?מ|Subtotal)\s*[:₪]?\s*(-?[\d,]+\.\d{2})',
        'vat': r'(?:סכום\s+מע[״\"]?מ|VAT\s+amount)\s*[:₪]?\s*(-?[\d,]+\.\d{2})',
        'total': r'(?:סה[״\"]?כ(?:\s+לתשלום)?|סך\s+הכול|\bTotal)\s*[:₪]?\s*(-?[\d,]+\.\d{2})',
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match: result[field] = match[1].replace(',', '')
    match = re.search(r'\b(\d{2})[/.](\d{2})[/.](\d{4})\b', text)
    if match:
        try: result['date'] = date(int(match[3]), int(match[2]), int(match[1])).isoformat()
        except ValueError: pass
    else:
        match = re.search(r'\b\d{4}-\d{2}-\d{2}\b', text)
        if match: result['date'] = match[0]
    return result

def safe_cell(value):
    """Prevent spreadsheet formulas in user/OCR controlled strings."""
    text = str(value or '')
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text

