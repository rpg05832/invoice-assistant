"""Local application entry point; never binds to external network interfaces."""
import argparse
import os
import shutil
import threading
import sys
import webbrowser
from pathlib import Path
import uvicorn

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if getattr(sys,'frozen',False):
        bundle=Path(sys._MEIPASS)
        home=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'InvoiceAssistant'
        os.environ.setdefault('INVOICE_DATA_DIR',str(home/'data'))
        os.environ.setdefault('INVOICE_ASSET_DIR',str(home/'data'))
        assets=Path(os.environ['INVOICE_ASSET_DIR'])
        for relative in ('tessdata','models/mnist-12.onnx','models/paddle-digits','models/PP-OCRv6_small_rec_onnx'):
            source=bundle/'data'/relative;target=assets/relative
            if source.is_dir():
                target.mkdir(parents=True,exist_ok=True)
                for file in source.iterdir():
                    if file.is_file() and not (target/file.name).exists():shutil.copy2(file,target/file.name)
            elif source.is_file() and not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        runtime=bundle/'tesseract'
        if runtime.is_dir():os.environ['PATH']=str(runtime)+os.pathsep+os.environ.get('PATH','')
    if os.name == 'nt' and not shutil.which('tesseract'):
        default = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tesseract-OCR'
        if (default / 'tesseract.exe').exists(): os.environ['PATH'] = str(default) + os.pathsep + os.environ.get('PATH', '')
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, log_level='info')

if __name__ == '__main__': main()

