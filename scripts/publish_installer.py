"""Publish only the tested installer artifact to a public GitHub release."""
import argparse
from pathlib import Path
import os
import re
import urllib.error
from publish_model_mirrors import api,upload


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args()
    directory=Path(args.directory);sha=os.environ['GITHUB_SHA']
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Invalid build revision')
    files=list(directory.rglob('InvoiceAssistant-Setup-0.3.0-x64.exe'))
    if len(files)!=1:raise ValueError('Expected one tested installer')
    installer=files[0];checksum=installer.with_suffix('.exe.sha256')
    if not checksum.is_file():raise ValueError('Installer checksum missing')
    import hashlib
    with installer.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if checksum.read_text(encoding='utf-8').split()[0]!=digest:raise ValueError('Installer checksum mismatch')
    tag='windows-0.3.0-'+sha[:8]
    try:rel=api('/releases/tags/'+tag)
    except urllib.error.HTTPError as exc:
        if exc.code!=404:raise
        rel=api('/releases',method='POST',payload={'tag_name':tag,'target_commitish':sha,'name':'Invoice Assistant · Windows 0.3.0 · '+sha[:8],'draft':True,'prerelease':True,'body':'תיקיות מחוברות ללא העתקת המקורות, סריקה לפי תוכן וניקוי מטמון. חיבור אוטומטי של רצף עמודי חשבונית מלא לפי סימוני דף מתוך, גם כשהעמודים בקובץ אינם מסודרים. חיבור מפורש למסמכים קיימים שומר את התיקונים. שיפור בחירת שם ספק מול שורות פרסום והפרדת מספר עוסק של מקבל החשבונית. זכוכית מגדלת חופשית וסריקת אזור מהסרגל עם אישור ההצעה ליד השדה. Enter לצ׳ק יחיד ללא סתירה, חלון Windows עצמאי ועדכון מתוך התוכנה. כולל Python, OCR בעברית ומודלי ספרות קלים. נתונים ולמידה אישית נשמרים בנפרד. נבדק ב־Windows וב־Linux, כולל התקנה והסרה ששומרת נתונים. Build: '+sha})
    upload(rel,installer);upload(rel,checksum)
    api('/releases/'+str(rel['id']),method='PATCH',payload={'draft':False,'prerelease':True})
    print('Installer:',rel['html_url'])


if __name__=='__main__':main()
