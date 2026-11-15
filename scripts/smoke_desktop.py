"""Exercise a real windowed Windows executable and its owned shutdown."""
import argparse
import base64
import sys
import shutil
import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import urllib.request

TITLE='חשבונית · מסמכים לאישור'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('executable');parser.add_argument('--installer');args=parser.parse_args()
    if os.name!='nt':raise SystemExit('Run the native-window acceptance test on Windows')
    executable=Path(args.executable).resolve();binary=executable.read_bytes();pe=struct.unpack_from('<I',binary,0x3c)[0]
    assert struct.unpack_from('<H',binary,pe+92)[0]==2,'Packaged executable still uses the console subsystem'
    with tempfile.TemporaryDirectory(prefix='desktop-',ignore_cleanup_errors=True) as temp:
        root=Path(temp);profile=root/'משתמש עברי';state=root/'desktop.json'
        env={k:v for k,v in os.environ.items() if k not in ('INVOICE_DATA_DIR','INVOICE_ASSET_DIR','TESSDATA_PREFIX','INVOICE_TESSDATA_DIR')};env.update(LOCALAPPDATA=str(profile),INVOICE_DESKTOP_STATUS=str(state),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--disable-gpu')
        helper=None
        process=subprocess.Popen([str(executable),'--port','0'],env=env)
        try:
            info=None
            for _ in range(400):
                if process.poll() is not None:raise AssertionError('Native app exited before opening its window')
                try:info=json.loads(state.read_text(encoding='utf-8'))
                except (OSError,ValueError):pass
                if info and info.get('phase')=='ready':break
                time.sleep(.2)
            else:raise AssertionError('Native WebView2 window did not become ready')
            with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/api/health") as response:assert json.load(response)['ocr']['hebrew']
            second=subprocess.Popen([str(executable),'--port','0'],env=env)
            assert second.wait(timeout=20)==0 and process.poll() is None
            assert json.loads(state.read_text(encoding='utf-8'))['pid']==process.pid, 'Second launch replaced the active instance'
            user=ctypes.windll.user32;user.FindWindowW.restype=ctypes.c_void_p
            window=user.FindWindowW(None,TITLE);assert window,'Native application window not found'
            helper=None;company=None;installation=None
            if args.installer:
                url=f"http://127.0.0.1:{info['port']}"
                request=urllib.request.Request(url+'/api/companies',data=json.dumps({'name':'Native update preservation'}).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(request) as response:company=json.load(response)
                with urllib.request.urlopen(url+'/api/companies/'+company['id']+'/learning') as response:installation=json.load(response)['installation_id']
                marker=profile/'InvoiceAssistant/data/personal-update-marker.bin';marker.write_bytes(b'private personal learning must remain')
                sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
                from app.updates import helper_script
                script=helper_script(Path(args.installer).resolve(),executable.parent,process.pid,root/'update.log')
                encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
                helper=subprocess.Popen(['powershell','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',encoded],env=env,creationflags=0x08000000)
            user.PostMessageW(ctypes.c_void_p(window),0x0010,0,0)
            assert process.wait(timeout=30)==0,'Application did not exit cleanly with its native window'
            if helper:
                assert helper.wait(timeout=240)==0
                for _ in range(400):
                    try:new=json.loads(state.read_text(encoding='utf-8'))
                    except (OSError,ValueError):new={}
                    if new.get('pid')!=process.pid and new.get('phase')=='ready':break
                    time.sleep(.2)
                else:raise AssertionError('Updated native application did not reopen')
                url=f"http://127.0.0.1:{new['port']}"
                with urllib.request.urlopen(url+'/api/companies') as response:assert any(row['id']==company['id'] for row in json.load(response))
                with urllib.request.urlopen(url+'/api/companies/'+company['id']+'/learning') as response:assert json.load(response)['installation_id']==installation
                assert marker.read_bytes()==b'private personal learning must remain'
                kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=ctypes.c_void_p
                handle=kernel.OpenProcess(0x00100000,False,new['pid']);assert handle
                try:
                    window=user.FindWindowW(None,TITLE);assert window
                    user.PostMessageW(ctypes.c_void_p(window),0x0010,0,0)
                    assert kernel.WaitForSingleObject(ctypes.c_void_p(handle),30000)==0
                finally:kernel.CloseHandle(ctypes.c_void_p(handle))
                print('Silent update helper, wait-before-install, native relaunch and personal data/identity preservation: passed')
            assert json.loads(state.read_text(encoding='utf-8'))['phase']=='closed'
            for _ in range(150):
                try:shutil.rmtree(profile);break
                except PermissionError:time.sleep(.1)
            else:raise AssertionError('Native WebView2 processes did not release their user cache')
            print('Native WebView2 window, GUI subsystem, real Hebrew OCR and graceful owned-server shutdown: passed')
        except Exception:
            log=profile/'InvoiceAssistant/logs/application.log'
            if log.is_file():print(log.read_text(encoding='utf-8',errors='replace')[-10000:])
            raise
        finally:
            if helper and helper.poll() is None:helper.kill();helper.wait(timeout=10)
            if process.poll() is None:process.kill();process.wait(timeout=10)
if __name__=='__main__':main()
