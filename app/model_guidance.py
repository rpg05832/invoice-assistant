"""Conservative download guidance using this installation's actual CPU and RAM."""
import os
import platform
from pathlib import Path
from scripts.public_models import manifest
from . import ocr
from .paths import assets


def base_status():
    root=assets();cap=ocr.capabilities()
    records=[{'label':'Tesseract Fast · עברית ואנגלית','ready':cap['hebrew'],'detail':'עברית זמינה במנוע הפעיל' if cap['hebrew'] else 'עברית אינה זמינה במנוע הפעיל'}]
    for label,paths in [('PaddleOCR v5 · ספרות',['models/paddle-digits/inference.onnx','models/paddle-digits/inference.yml']),('PaddleOCR v6 Small · ספרות ואימון אישי',['models/PP-OCRv6_small_rec_onnx/inference.onnx','models/PP-OCRv6_small_rec_onnx/inference.yml']),('MNIST · ספרות',['models/mnist-12.onnx'])]:
        present=all((root/p).is_file() for p in paths)
        records.append({'label':label,'ready':present,'detail':'קובצי הבסיס נמצאים במחשב' if present else 'חסרים קובצי בסיס; אפשר לתקן מהכפתור למטה'})
    records.append({'label':'אימון ספרות אישי מקומי','ready':True,'detail':'כלי האימון כלול; גרסה אישית נוצרת מהתיקונים המאושרים שלכם'})
    return {'ocr':cap,'components':records,'repair_available':True,'message':'מנוע העברית פעיל' if cap['hebrew'] else 'מנוע העברית אינו זמין בהתקנה הפעילה','next_step':'אפשר לבצע בדיקת קריאה של דוגמה בעברית.' if cap['hebrew'] else 'הפעילו בדיקת ותיקון קובצי הבסיס. אם המנוע עצמו חסר או אינו עולה, התקינו את חבילת Windows המעודכנת.'}


def hardware():
    total=available=None;cpus=os.cpu_count() or 1
    try:
        if os.name=='nt':
            import ctypes
            class Memory(ctypes.Structure):
                _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),*[(name,ctypes.c_ulonglong) for name in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]]
            status=Memory();status.length=ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):total=status.total;available=status.available
        else:
            total=os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
            memory={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.split(':')[0] in ('MemAvailable','MemTotal')}
            available=memory.get('MemAvailable')
            limit=Path('/sys/fs/cgroup/memory.max').read_text().strip()
            if limit.isdigit():total=min(total,int(limit));available=min(available or total,max(0,total-int(Path('/sys/fs/cgroup/memory.current').read_text())))
            quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
            if quota.isdigit():cpus=min(cpus,max(1,(int(quota)+int(period)-1)//int(period)))
    except (OSError,ValueError,AttributeError):pass
    return {'logical_cpus':cpus,'architecture':platform.machine(),'ram_gib':round(total/2**30,1) if total else None,'available_gib':round(available/2**30,1) if available is not None else None,'gpu_required':False}


def guidance(status):
    machine=hardware();ram=machine['ram_gib'] or 0;free=machine['available_gib'] or 0
    tier='strong' if ram>=16 and free>=6 and machine['logical_cpus']>=4 else 'balanced' if ram>=8 and free>=3 and machine['logical_cpus']>=4 else 'light'
    installed={p['id']:p['installed'] for p in status['profiles']};files=manifest().values()
    specifications=[
        ('rivok',1,'שיפור עברית מודפסת','מודל קטן לעברית, אנגלית וספרות. התוספת הראשונה שכדאי לנסות על המסמכים שלך.',1,'עברית מודפסת'),
        ('best',2,'השוואה נוספת לעברית','חלופה לעמודים מודפסים. השוו את הקריאה ואת זמן העבודה ל־Fast ול־heb_rivok.',1,'עברית מודפסת'),
        ('glm-ocr',3,'השוואת ספרות בכתב יד','מודל ראייה להשוואה ממוקדת של חיתוכי צ׳קים. הורידו תחילה מנוע ראייה אחד ונסו אותו.',8,'ספרות בלבד'),
        ('qwen35-08',4,'חלופה קטנה להשוואת ספרות','לניסוי נוסף אחרי המנוע הראשון; הפעלת מנוע אחד בכל פעם חוסכת זיכרון.',8,'ספרות בלבד'),
        ('paddle-vl16',5,'חלופת ראייה נוספת','לבדיקת חיתוכים קשים ולמדידה מול ההצעות שכבר קיימות.',12,'ספרות בלבד'),
        ('lighton',5,'חלופת ראייה נוספת','מתאים לניסוי השוואתי ממוקד; בחרו לפי התוצאה על הדוגמאות שלכם.',12,'ספרות בלבד'),
        ('digits',6,'Qwen 2B להשוואת ספרות','מנוע נוסף לספרות. בתוכנה הוא משמש לחיתוכי מספרים.',12,'ספרות בלבד'),
        ('indra',6,'חלופה לעברית RTL','עברית מודפסת עם רישיון מודל נפרד. קראו את התנאים לפני ההורדה והבחירה.',8,'עברית מודפסת'),
        ('multilingual',7,'הקשר ועמודים במחשב חזק','הורדה גדולה יותר, בעיקר למחשב עם זיכרון פנוי. התוצאה מוצגת כהצעה להשוואה.',16,'מלל והקשר')]
    profiles=[]
    for ident,priority,title,why,minimum,role in specifications:
        size=sum(r['size'] for r in files if ident in r.get('profiles',[r['profile']]))
        required_free=6 if minimum>=16 else 5 if minimum>=12 else 3 if minimum>=8 else .5
        suitable=minimum<8 or (ram>=minimum-.5 and free>=required_free and machine['logical_cpus']>=4)
        speed='מהיר יחסית · מתאים לעבודה שוטפת' if minimum<8 else 'בינוני · לבדיקה ממוקדת' if ident=='indra' else 'איטי יותר על מעבד · חיתוך מספר בכל פעם' if minimum<12 else 'איטי על מעבד · ניסוי בדוגמאות קשות'
        profiles.append({'id':ident,'priority':priority,'title':title,'why':why,'role':role,'download_bytes':size,'installed':installed[ident],'recommended':suitable,'minimum_ram_gib':minimum,'free_ram_gib':required_free,'logical_cpus':1 if minimum<8 else 4,'speed':speed,'fit':('מתאים לניסוי במחשב הזה' if suitable else 'התחילו בבסיס ובמודלים הקטנים; זה מסלול למחשב עם יותר כוח עיבוד או זיכרון פנוי')})
    next_model=next((p['id'] for p in profiles if p['recommended'] and not p['installed']),None)
    tasks=[
        {'title':'עברית מודפסת בחשבוניות','order':['Tesseract Fast · כבר כלול','heb_rivok · התוספת הראשונה','Tesseract Best · השוואה נוספת','Indra RTL · ניסוי נוסף אחרי בדיקת תנאי הרישיון'],'why':'Fast הוא נקודת התחלה מהירה. rivok קטן ומתמקד בעברית; Best מספק חלופה נוספת. השוו את מספר התיקונים ואת זמן הקריאה באותם מסמכים.'},
        {'title':'מספרי צ׳קים וספרות בכתב יד','order':['PaddleOCR v6 Small ו־v5 + המודל האישי לאחר אימון ובדיקה · כבר כלולים','MNIST · הצעה נוספת לספרות מופרדות, כבר כלול','GLM-OCR · ההורדה הראשונה להשוואת חיתוכים','Qwen3.5 0.8B · חלופה קומפקטית','PaddleOCR-VL / LightOnOCR / Qwen3-VL 2B · ניסויים נוספים'],'why':'מנועי הרצף קוראים מספר שלם בלי לפצל כל ספרה. המודל האישי לומד מהכתבים ומהתיקונים המאושרים שלכם. מנועי הראייה מוסיפים הצעה לחיתוכים קשים, כגון קו מתחת לספרה.'},
        {'title':'מלל והקשר של עמוד שלם','order':['Tesseract Fast / rivok / Best · הקריאה השוטפת בעברית','Qwen3-VL 4B · השוואת הקשר במחשב חזק'],'why':'Qwen 4B מיועד כאן להצעות הקשר. בודקים מלל, סדר מילים ומספרים מול המקור; ההצעה מוצגת לצד הנתונים לתיקון מהיר.'}]
    return {'hardware':machine,'tier':tier,'tier_label':{'light':'מסלול קל וחסכוני','balanced':'מסלול מאוזן','strong':'אפשר גם ניסויים במודלים גדולים'}[tier],'profiles':profiles,'recommended_next':next_model,'download_source':'GitHub','base_status':base_status(),'base_included':['Tesseract Fast · עברית ואנגלית','PaddleOCR v5 + v6 Small · ספרות','MNIST · ספרות','אימון ספרות אישי מקומי'],'task_rankings':tasks,'ranking_basis':'זהו סדר ניסיון מומלץ לפי תפקיד, גודל ודרישות מחשב. דירוג דיוק אישי נקבע לפי תוצאות על המסמכים והכתבים שלכם. במסך הלמידה מוצגים מספרי ההצלחות והבדיקות בפועל.','requirements_basis':'דרישות הזיכרון הן אומדן שמרני לניסוי על מעבד x64. המהירות תלויה במעבד, בגודל התמונה ובעומס; גודל ההורדה שונה מצריכת הזיכרון בזמן עבודה.','learning_scope':'האימון מעדכן את מודל הספרות האישי; תיקוני ספקים ומלל נשמרים כהתאמות וכללים. מודלי הבסיס נשארים מקוריים.'}
