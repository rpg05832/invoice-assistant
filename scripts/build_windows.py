"""Build a standalone Windows folder, including Python, OCR runtime and light models."""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    if os.name!='nt':raise SystemExit('Build this executable on Windows (CI provides the tested build).')
    tesseract=shutil.which('tesseract')
    if not tesseract:raise SystemExit('Install Tesseract before building')
    staging=ROOT/'build/tesseract';staging.mkdir(parents=True,exist_ok=True)
    engine=Path(tesseract).parent
    for pattern in ('tesseract.exe','*.dll','*LICENSE*','*COPYING*'):
        for p in engine.glob(pattern):
            if p.is_file():shutil.copy2(p,staging/p.name)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','InvoiceAssistant','--hidden-import','app.main','--hidden-import','uvicorn.logging','--hidden-import','uvicorn.loops.auto','--hidden-import','uvicorn.protocols.http.auto','--hidden-import','uvicorn.lifespan.on','--collect-all','onnxruntime','--collect-all','indra_ocr','--add-data',f'{ROOT / "app/static"};app/static','--add-data',f'{ROOT / "scripts/indra-models.json"};scripts','--add-data',f'{ROOT / "data/tessdata"};data/tessdata','--add-data',f'{ROOT / "data/models/mnist-12.onnx"};data/models','--add-data',f'{ROOT / "data/models/paddle-digits"};data/models/paddle-digits','--add-data',f'{ROOT / "data/models/PP-OCRv6_small_rec_onnx"};data/models/PP-OCRv6_small_rec_onnx','--add-data',f'{ROOT / "scripts/sequence-models.json"};scripts','--add-data',f'{ROOT / "scripts/vision-models.json"};scripts','--add-data',f'{staging};tesseract',str(ROOT/'run.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    output=ROOT/'dist/InvoiceAssistant-Windows-x64.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in (ROOT/'dist/InvoiceAssistant').rglob('*'):
            if path.is_file():archive.write(path,path.relative_to(ROOT/'dist'))
        for name in ('README.md','docs/VALIDATION.md','third_party/tesseract-models-LICENSE.txt','third_party/digit-model-LICENSE.txt','third_party/indra-model-LICENSE.txt','third_party/sequence-models-LICENSE.txt','docs/MODEL_RESEARCH.md','docs/PERSONAL_LEARNING.md'):
            archive.write(ROOT/name,Path('InvoiceAssistant')/name)
    print(output)

if __name__=='__main__':main()
