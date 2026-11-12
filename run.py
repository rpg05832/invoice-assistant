"""Local application entry point; never binds to external network interfaces."""
import argparse
import os
import shutil
import threading
import webbrowser
from pathlib import Path
import uvicorn

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if os.name == 'nt' and not shutil.which('tesseract'):
        default = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tesseract-OCR'
        if (default / 'tesseract.exe').exists(): os.environ['PATH'] = str(default) + os.pathsep + os.environ.get('PATH', '')
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, log_level='info')

if __name__ == '__main__': main()

