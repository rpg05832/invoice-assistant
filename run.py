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
    parser.add_argument('--no-browser', action='store_true', help='Run only the local server for diagnostics')
    parser.add_argument('--browser',action='store_true',help='Use an external browser instead of the Windows application window')
    args = parser.parse_args()
    if getattr(sys,'frozen',False):
        from app.desktop import quiet_streams
        quiet_streams()
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
    if os.name=='nt' and not args.no_browser and not args.browser:
        from app.desktop import launch,instance_lock
        directory=Path(os.environ.get('INVOICE_DATA_DIR',Path(__file__).resolve().parent/'data'))
        with instance_lock(directory) as acquired:
            if not acquired:return
            from app.main import app
            launch(app,args.port)
        return
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, log_level='info')

if __name__ == '__main__':
    try:main()
    except Exception:
        import logging
        logging.exception('Application startup failed')
        if getattr(sys,'frozen',False):
            from app.desktop import native_error
            native_error('לא ניתן לפתוח את התוכנה. פרטי האבחון נשמרו בתיקיית InvoiceAssistant\\logs במחשב. בדקו שהותקנה החבילה המעודכנת.')
        raise SystemExit(1)

