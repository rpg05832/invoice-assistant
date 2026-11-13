"""Install, run real OCR/training checks, uninstall and retain personal data."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('installer');args=parser.parse_args()
    if os.name!='nt':raise SystemExit('Test installation on Windows.')
    installer=Path(args.installer).resolve()
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);target=root/'installed';personal=root/'profile';personal.mkdir()
        env={**os.environ,'LOCALAPPDATA':str(personal)}
        subprocess.run([str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS','/TASKS=','/DIR='+str(target),'/LOG='+str(root/'install.log')],env=env,check=True,timeout=180)
        executable=target/'InvoiceAssistant.exe';assert executable.is_file(),'Installed executable missing'
        script=Path(__file__).resolve().with_name('smoke_standalone.py')
        subprocess.run([sys.executable,str(script),str(executable)],env=env,check=True,timeout=240)
        # Data is intentionally outside {app}; uninstall must never remove it.
        marker=personal/'InvoiceAssistant/data/preservation-marker.txt';marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('private data must remain',encoding='utf-8')
        uninstaller=target/'unins000.exe';assert uninstaller.is_file()
        subprocess.run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG='+str(root/'uninstall.log')],env=env,check=True,timeout=90)
        for _ in range(100):
            if not executable.exists():break
            time.sleep(.1)
        assert not executable.exists(),'Uninstall did not remove application files'
        assert marker.read_text(encoding='utf-8')=='private data must remain'
    print('Per-user Windows installer, OCR, CTC training, learning transfer and data-preserving uninstall: passed')


if __name__=='__main__':main()
