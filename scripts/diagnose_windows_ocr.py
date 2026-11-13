"""Check a published install in Hebrew paths without a system OCR installation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

INSTALLER='https://github.com/rpg05832/invoice-assistant/releases/download/windows-0.1.0-f97b36b0/InvoiceAssistant-Setup-0.1.0-x64.exe'
EXPECTED='3d23c4610d0d4985c1f64a4d7eca89524e2b7dcf8718702dbf708148d9ab4bf6'


def invoke(command,env,cwd=None):
    try:
        p=subprocess.run(command,env=env,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
        return {'returncode':p.returncode,'stdout':p.stdout[:2500],'stderr':p.stderr[:2500]}
    except (OSError,subprocess.TimeoutExpired) as exc:return {'exception':type(exc).__name__,'detail':str(exc)[:1000]}


def main():
    if os.name!='nt':raise SystemExit('Run on Windows')
    # Keep logs outside the diagnostic install so Inno's final cleanup cannot lock them.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        root=Path(temp);setup=root/'setup.exe';digest=hashlib.sha256()
        with urllib.request.urlopen(INSTALLER,timeout=90) as source,setup.open('wb') as output:
            while True:
                chunk=source.read(1024*1024)
                if not chunk:break
                output.write(chunk);digest.update(chunk)
        assert digest.hexdigest()==EXPECTED
        target=root/'תוכנת חשבוניות';profile=root/'משתמש עברי';profile.mkdir()
        env={k:v for k,v in os.environ.items() if k not in ('INVOICE_DATA_DIR','INVOICE_ASSET_DIR','INVOICE_TESSDATA_DIR','TESSDATA_PREFIX')}
        system=Path(os.environ.get('SystemRoot','C:/Windows'));env['PATH']=str(system/'System32')+os.pathsep+str(system)
        env['LOCALAPPDATA']=str(profile)
        subprocess.run([str(setup),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS','/TASKS=','/DIR='+str(target)],env=env,check=True,timeout=180)
        log=(root/'server.log').open('w',encoding='utf-8')
        process=subprocess.Popen([str(target/'InvoiceAssistant.exe'),'--no-browser','--port','18977'],env=env,stdout=log,stderr=log)
        try:
            health=None
            for _ in range(180):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:18977/api/health',timeout=1) as response:health=json.load(response)
                    break
                except OSError:
                    if process.poll() is not None:break
                    time.sleep(.25)
            engine=target/'_internal/tesseract/tesseract.exe';models=profile/'InvoiceAssistant/data/tessdata'
            report={'health':health,'bundled_binary_exists':engine.is_file(),'model_files':[p.name for p in models.glob('*.traineddata')],'absolute_arguments':invoke([str(engine),'--tessdata-dir',str(models),'--list-langs'],env),'relative_arguments':invoke([str(engine),'--tessdata-dir','.','--list-langs'],env,cwd=models),'version':invoke([str(engine),'--version'],env)}
            print(json.dumps(report,ensure_ascii=True,indent=2),flush=True)
            output=Path('test-results/windows-ocr-diagnostic.json');output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
            log.close()
            uninstaller=target/'unins000.exe'
            if uninstaller.exists():subprocess.run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],env=env,check=True,timeout=90)


if __name__=='__main__':main()
