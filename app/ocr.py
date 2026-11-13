import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from PIL import Image, ImageOps,ImageDraw,ImageFont
from .paths import assets
IS_WINDOWS=os.name=='nt'

def language_directory(quality='fast'):
    bundled = assets() / ({'best':'tessdata_best','rivok':'tessdata_rivok'}.get(quality,'tessdata'))
    configured = os.environ.get('INVOICE_TESSDATA_DIR') if quality=='fast' else None
    directory = Path(configured) if configured else bundled
    return directory.resolve() if directory.is_dir() else None


def language_args(quality='fast'):
    directory=language_directory(quality)
    return ['--tessdata-dir',str(directory)] if directory else []


def binary_path():
    if getattr(sys,'frozen',False):
        path=Path(sys._MEIPASS)/'tesseract/tesseract.exe'
        return str(path) if path.is_file() else None
    return shutil.which('tesseract')


@contextmanager
def command_context(arguments,quality='fast'):
    """Windows Tesseract uses narrow argv paths; keep image/data arguments ASCII.

    CreateProcess still receives the executable and working directory as Unicode.
    Ordinary app files share a parent and need no copies. For unrelated drives or
    Unicode image filenames, stage a private temporary input and model directory.
    """
    binary=binary_path()
    if not binary:raise FileNotFoundError('מנוע Tesseract לא נמצא')
    directory=language_directory(quality);args=list(arguments)
    if not IS_WINDOWS:yield [binary,*language_args(quality),*[str(a) for a in args]],None;return
    images=[a for a in args if isinstance(a,Path)]
    cwd=directory or (images[0].resolve().parent if images else None)
    converted=[];stage=False
    for arg in args:
        if isinstance(arg,Path):
            try:value=os.path.relpath(arg.resolve(),cwd) if cwd else str(arg.resolve())
            except ValueError:value=str(arg.resolve())
            if not value.isascii():stage=True
            converted.append(value)
        else:converted.append(str(arg))
    if not stage:
        yield [binary,*(['--tessdata-dir','.'] if directory else []),*converted],cwd
        return
    with tempfile.TemporaryDirectory(prefix='invoice-ocr-') as temp:
        workspace=Path(temp);converted=[]
        if directory:
            data=workspace/'tessdata';data.mkdir()
            for model in directory.glob('*.traineddata'):shutil.copy2(model,data/model.name)
        for index,arg in enumerate(args):
            if isinstance(arg,Path):
                name=f'input-{index}'+arg.suffix;shutil.copy2(arg,workspace/name);converted.append(name)
            else:converted.append(str(arg))
        yield [binary,*(['--tessdata-dir','tessdata'] if directory else []),*converted],workspace


def run_tesseract(arguments,quality='fast',timeout=60):
    with command_context(arguments,quality) as (command,cwd):
        return subprocess.run(command,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,env={**os.environ,'OMP_THREAD_LIMIT':os.environ.get('OMP_THREAD_LIMIT','2')})

def capabilities(quality='fast'):
    binary = binary_path();directory=language_directory(quality)
    result={'available':False,'languages':[],'hebrew':False,'binary':binary,'model_directory':str(directory) if directory else None,'packaged':bool(getattr(sys,'frozen',False)),'reason':None,'detail':'','returncode':None}
    if not binary:return {**result,'reason':'binary_missing','detail':'מנוע Tesseract לא נמצא בהתקנה הפעילה'}
    try:
        run = run_tesseract(['--list-langs'],quality,timeout=15)
        lines=(run.stdout or run.stderr).splitlines();langs=[s.strip() for s in lines[1:] if s.strip() and not s.startswith(('Error','Warning','exception:'))] if run.returncode==0 else []
        hebrew=run.returncode==0 and bool({'heb','heb_rivok'}&set(langs))
        return {**result,'available':run.returncode==0,'languages':langs,'hebrew':hebrew,'returncode':run.returncode,'reason':None if hebrew else 'engine_exit' if run.returncode else 'hebrew_missing','detail':run.stderr.strip()[:1200] if run.returncode else '' if hebrew else 'מנוע OCR פועל אך עברית אינה מופיעה ברשימת השפות'}
    except subprocess.TimeoutExpired:return {**result,'reason':'timeout','detail':'מנוע OCR לא השיב בזמן הבדיקה'}
    except OSError as exc:return {**result,'reason':'launch_error','detail':str(exc)[:1200]}

def recognize(path, digits=False, quality='fast'):
    cap = capabilities(quality)
    if not cap['available']: return '', 'unavailable', 'מנוע OCR אינו מותקן; אפשר להזין שדות ידנית'
    langs = 'heb_rivok' if quality=='rivok' and 'heb_rivok' in cap['languages'] else '+'.join(x for x in ('heb', 'eng') if x in cap['languages'])
    if not langs: return '', 'unavailable', 'לא מותקנות שפות עברית או אנגלית'
    arguments = [Path(path),'stdout','-l',langs,'--psm','6' if digits else '3']
    if digits: arguments += ['-c', 'tessedit_char_whitelist=0123456789']
    try:
        result = run_tesseract(arguments,quality,timeout=60)
        if result.returncode: return '', 'failed', 'זיהוי מקומי נכשל; אפשר לתקן ידנית'
        warning = '' if cap['hebrew'] else 'עברית אינה מותקנת במנוע הזיהוי'
        if digits: warning = 'תוצאת זיהוי מקומי; כתב יד מחייב בדיקה ואישור'
        return result.stdout.strip(), 'tesseract', warning
    except (OSError, subprocess.TimeoutExpired):
        return '', 'failed', 'הזיהוי לא הסתיים בזמן; אפשר לתקן ידנית'


def self_test():
    """Read known Hebrew and digits, without opening any customer document."""
    paths=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/arial.ttf',Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font_path=next((p for p in paths if p.is_file()),None)
    if not font_path:return {'ok':False,'reason':'test_font_missing','message':'לא נמצא גופן עברי לבדיקת הדוגמה'}
    font=ImageFont.truetype(str(font_path),64,layout_engine=ImageFont.Layout.BASIC)
    image=Image.new('RGB',(1400,800),'white');draw=ImageDraw.Draw(image)
    draw.text((600,80),'חשבונית מס'[::-1],font=font,fill='black');draw.text((600,200),'ספק בדיקה'[::-1],font=font,fill='black');draw.text((600,400),'00001234',font=font,fill='black')
    with tempfile.TemporaryDirectory(prefix='invoice-ocr-check-') as directory:
        path=Path(directory)/'בדיקת עברית.png';image.save(path);text,engine,warning=recognize(path)
    ok=engine=='tesseract' and 'חשבונית' in text and '00001234' in text
    return {'ok':ok,'engine':engine,'sample_text':text,'message':'העברית והספרות נקראו בהצלחה' if ok else 'בדיקת הקריאה לא הושלמה בהצלחה','warning':warning,'capabilities':capabilities()}

def normalize_image(source, destination):
    with Image.open(source) as img:
        if img.width * img.height > 40_000_000: raise ValueError('התמונה גדולה מדי')
        ImageOps.exif_transpose(img).convert('RGB').save(destination, 'PNG')



def orientation(path):
    """Return a quarter turn only when OSD provides sufficient evidence."""
    import re
    binary=binary_path()
    if not binary:return 0
    try:
        result=run_tesseract([Path(path),'stdout','-l','osd','--psm','0'],timeout=20)
        rotation=re.search(r'Rotate: (\d+)',result.stdout);confidence=re.search(r'Orientation confidence: ([\d.]+)',result.stdout)
        return int(rotation[1]) if result.returncode==0 and rotation and confidence and float(confidence[1])>=2 and int(rotation[1]) in (90,180,270) else 0
    except (OSError,subprocess.TimeoutExpired):return 0
