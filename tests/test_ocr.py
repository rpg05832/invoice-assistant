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


def test_windows_hebrew_models_use_relative_ascii_arguments(monkeypatch,tmp_path):
    root=tmp_path/'משתמש עברי';models=root/'data/tessdata';models.mkdir(parents=True)
    image=root/'data/images/scan.png';image.parent.mkdir();image.write_bytes(b'image')
    monkeypatch.setattr(ocr,'IS_WINDOWS',True)
    monkeypatch.setattr(ocr,'binary_path',lambda:'bundled-tesseract.exe')
    monkeypatch.setattr(ocr,'language_directory',lambda quality='fast':models)
    with ocr.command_context(['--list-langs']) as (command,cwd):
        assert cwd==models and command==['bundled-tesseract.exe','--tessdata-dir','.','--list-langs']
    with ocr.command_context([image,'stdout']) as (command,cwd):
        assert cwd==models and all(arg.isascii() for arg in command)
        assert (cwd/command[-2]).resolve()==image.resolve()
        assert list(models.iterdir())==[], 'Private inputs must never enter the model cache'


def test_windows_hebrew_image_filename_is_staged_privately_and_cleaned(monkeypatch,tmp_path):
    models=tmp_path/'עברית';models.mkdir();(models/'heb.traineddata').write_bytes(b'weights')
    image=tmp_path/'בדיקה.png';image.write_bytes(b'private image')
    monkeypatch.setattr(ocr,'IS_WINDOWS',True)
    monkeypatch.setattr(ocr,'binary_path',lambda:'bundled-tesseract.exe')
    monkeypatch.setattr(ocr,'language_directory',lambda quality='fast':models)
    with ocr.command_context([image,'stdout']) as (command,cwd):
        staged=cwd
        assert all(arg.isascii() for arg in command)
        assert (cwd/'input-0.png').read_bytes()==image.read_bytes()
        assert (cwd/'tessdata/heb.traineddata').read_bytes()==b'weights'
    assert not staged.exists() and image.read_bytes()==b'private image'
    assert [p.name for p in models.iterdir()]==['heb.traineddata']


def test_frozen_app_requires_its_own_tesseract(monkeypatch,tmp_path):
    monkeypatch.setattr(ocr.sys,'frozen',True,raising=False)
    monkeypatch.setattr(ocr.sys,'_MEIPASS',str(tmp_path),raising=False)
    monkeypatch.setattr(ocr.shutil,'which',lambda _:pytest.fail('Packaged app must not depend on system Tesseract'))
    assert ocr.binary_path() is None and ocr.capabilities()['reason']=='binary_missing'
    binary=tmp_path/'tesseract/tesseract.exe';binary.parent.mkdir();binary.write_bytes(b'bundled binary')
    assert ocr.binary_path()==str(binary)


@pytest.mark.parametrize('returncode,stdout,reason',[(1,'','engine_exit'),(0,'Languages (1):\neng\n','hebrew_missing'),(0,'Languages (2):\neng\nheb\n',None)])
def test_capabilities_distinguish_launch_and_language_failures(monkeypatch,returncode,stdout,reason):
    from types import SimpleNamespace
    monkeypatch.setattr(ocr,'binary_path',lambda:'tesseract.exe')
    monkeypatch.setattr(ocr,'run_tesseract',lambda *a,**kw:SimpleNamespace(returncode=returncode,stdout=stdout,stderr='failure' if returncode else ''))
    cap=ocr.capabilities()
    assert cap['reason']==reason and cap['hebrew']==(reason is None)
    assert cap['languages']==([] if returncode else ['eng','heb'] if reason is None else ['eng'])


def test_local_hebrew_self_test_reads_known_sample(client):
    result=client.post('/api/ocr/self-test')
    assert result.status_code==200 and result.json()['ok'],result.text
    assert 'חשבונית' in result.json()['sample_text'] and '00001234' in result.json()['sample_text']
