"""Smoke tests on actual printed images; these do not measure handwriting accuracy."""
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pytest
from app import ocr


def font_path():
    candidates = [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/'arial.ttf']
    return next((p for p in candidates if p.is_file()), None)


def test_real_hebrew_ocr_and_image_upload(client,company,tmp_path):
    assert ocr.capabilities()['hebrew'], 'Install Tesseract and run scripts/setup_ocr.py before the OCR acceptance tests'
    path=font_path()
    assert path is not None, 'A font with Hebrew glyphs is required for OCR acceptance tests'
    font=ImageFont.truetype(str(path),64,layout_engine=ImageFont.Layout.BASIC)
    image=Image.new('RGB',(1400,800),'white');draw=ImageDraw.Draw(image)
    for y,text in [(80,'חשבונית מס'),(200,'ספק בדיקה')]:
        draw.text((600,y),text[::-1],font=font,fill='black')
    draw.text((600,400),'00001234',font=font,fill='black')
    file=tmp_path/'scan.png';image.save(file)
    text,engine,warning=ocr.recognize(file)
    assert engine=='tesseract' and 'חשבונית' in text and '00001234' in text
    response=client.post(f"/api/companies/{company['id']}/upload",files={'file':('סריקה.png',file.read_bytes(),'image/png')})
    assert response.status_code==200
    doc=client.get(f"/api/companies/{company['id']}/documents").json()[0]
    assert doc['engine']=='tesseract' and 'חשבונית' in doc['ocr_text']
    assert doc['status']=='review'


def test_missing_tesseract_keeps_manual_entry_available(monkeypatch,tmp_path):
    monkeypatch.setattr('app.ocr.shutil.which',lambda _:None)
    assert ocr.recognize(tmp_path/'does-not-matter.png')[1]=='unavailable'
