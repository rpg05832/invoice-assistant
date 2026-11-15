"""Owned native Windows window, local server lifecycle and quiet diagnostics."""
import ctypes
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import uvicorn

TITLE='חשבונית · מסמכים לאישור'

def data_home():
    return Path(os.environ.get('LOCALAPPDATA',Path.home()))/'InvoiceAssistant'

def quiet_streams():
    root=data_home()/'logs';root.mkdir(parents=True,exist_ok=True)
    stream=(root/'application.log').open('a',encoding='utf-8',buffering=1)
    sys.stdout=sys.stderr=stream
    return stream

def native_error(message):
    if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,message,'חשבונית',0x10)
    else:logging.error(message)

@contextmanager
def instance_lock(directory):
    """Acquire before creating the app/DB so a second launch cannot recover jobs."""
    mutex=None
    if os.name=='nt':
        kernel=ctypes.windll.kernel32;kernel.CreateMutexW.restype=ctypes.c_void_p
        key=hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()[:24]
        mutex=kernel.CreateMutexW(None,False,'Local\\InvoiceAssistant-'+key)
        if kernel.GetLastError()==183:
            user=ctypes.windll.user32;user.FindWindowW.restype=ctypes.c_void_p
            handle=user.FindWindowW(None,TITLE)
            if handle:
                if user.IsIconic(ctypes.c_void_p(handle)):user.ShowWindow(ctypes.c_void_p(handle),9)
                user.SetForegroundWindow(ctypes.c_void_p(handle))
            kernel.CloseHandle(ctypes.c_void_p(mutex));yield False;return
    try:yield True
    finally:
        if mutex:ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))

def launch(app,port=8765):
    import webview
    sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try:sock.bind(('127.0.0.1',port))
    except OSError:sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1];sock.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='info',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True,name='invoice-local-server');thread.start()
    status_path=Path(os.environ.get('INVOICE_DESKTOP_STATUS',str(data_home()/'logs/desktop.json')));status_path.parent.mkdir(parents=True,exist_ok=True)
    def status(phase):status_path.write_text(json.dumps({'pid':os.getpid(),'port':port,'phase':phase},ensure_ascii=False),encoding='utf-8')
    status('starting');window=None
    try:
        for _ in range(240):
            if server.started:break
            if not thread.is_alive():raise RuntimeError('השרת המקומי לא עלה')
            time.sleep(.1)
        else:raise RuntimeError('פתיחת התוכנה לא הושלמה בזמן')
        window=webview.create_window(TITLE,f'http://127.0.0.1:{port}',width=1440,height=960,min_size=(1060,650),maximized=True,text_select=True)
        app.state.desktop_close=lambda:window.destroy()
        def closing():
            if getattr(app.state,'update_closing',False):return True
            try:dirty=window.evaluate_js("typeof invoicePendingChanges==='function' && invoicePendingChanges()")
            except Exception:dirty=False
            if dirty:return window.create_confirmation_dialog('תיקונים שלא נשמרו','יש תיקונים שלא נשמרו. לסגור בלי לשמור אותם?')
            return True
        window.events.closing+=closing
        window.events.loaded+=lambda:status('ready')
        window.events.closed+=lambda:setattr(server,'should_exit',True)
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.start(gui='edgechromium',private_mode=False,storage_path=str(data_home()/'webview'))
    finally:
        server.should_exit=True;thread.join(timeout=20);sock.close();status('closed')
        if thread.is_alive():logging.error('Local server did not stop within shutdown deadline')
