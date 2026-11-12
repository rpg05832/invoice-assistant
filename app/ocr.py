import os
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageOps

def language_args():
    bundled = Path(__file__).resolve().parents[1] / 'data' / 'tessdata'
    configured = os.environ.get('INVOICE_TESSDATA_DIR')
    directory = Path(configured) if configured else bundled
    return ['--tessdata-dir', str(directory)] if (directory / 'heb.traineddata').exists() else []

def capabilities():
    binary = shutil.which('tesseract')
    if not binary: return {'available': False, 'languages': [], 'hebrew': False}
    try:
        run = subprocess.run([binary, *language_args(), '--list-langs'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        langs = [s.strip() for s in run.stdout.splitlines()[1:] if s.strip()]
        return {'available': run.returncode == 0, 'languages': langs, 'hebrew': 'heb' in langs}
    except (OSError, subprocess.TimeoutExpired):
        return {'available': False, 'languages': [], 'hebrew': False}

def recognize(path, digits=False):
    cap = capabilities()
    if not cap['available']: return '', 'unavailable', 'מנוע OCR אינו מותקן; אפשר להזין שדות ידנית'
    langs = '+'.join(x for x in ('heb', 'eng') if x in cap['languages'])
    if not langs: return '', 'unavailable', 'לא מותקנות שפות עברית או אנגלית'
    command = ['tesseract', str(path), 'stdout', *language_args(), '-l', langs, '--psm', '6' if digits else '3']
    if digits: command += ['-c', 'tessedit_char_whitelist=0123456789']
    try:
        env = dict(os.environ)
        env.setdefault('OMP_THREAD_LIMIT', '2')
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, env=env)
        if result.returncode: return '', 'failed', 'זיהוי מקומי נכשל; אפשר לתקן ידנית'
        warning = '' if cap['hebrew'] else 'עברית אינה מותקנת במנוע הזיהוי'
        if digits: warning = 'תוצאת זיהוי מקומי; כתב יד מחייב בדיקה ואישור'
        return result.stdout.strip(), 'tesseract', warning
    except (OSError, subprocess.TimeoutExpired):
        return '', 'failed', 'הזיהוי לא הסתיים בזמן; אפשר לתקן ידנית'

def normalize_image(source, destination):
    with Image.open(source) as img:
        if img.width * img.height > 40_000_000: raise ValueError('התמונה גדולה מדי')
        ImageOps.exif_transpose(img).convert('RGB').save(destination, 'PNG')

