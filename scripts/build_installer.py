"""Build the single-file per-user Windows installer with Inno Setup."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    if os.name!='nt':raise SystemExit('Build the Windows installer on Windows.')
    if not (ROOT/'dist/InvoiceAssistant/InvoiceAssistant.exe').is_file():raise SystemExit('Build the standalone application first.')
    found=shutil.which('ISCC.exe') or shutil.which('ISCC')
    candidates=[]
    for variable in ('ProgramFiles(x86)','ProgramFiles'):
        directory=Path(os.environ.get(variable,'C:/Program Files'))
        candidates.extend(directory.glob('Inno Setup*/ISCC.exe'))
    compiler=Path(found) if found else next(iter(sorted(candidates,reverse=True)),None)
    if not compiler:raise SystemExit('Install Inno Setup before building the installer.')
    command=[str(compiler),'/DProjectRoot='+str(ROOT)]
    hebrew=compiler.parent/'Languages/Hebrew.isl'
    if hebrew.is_file():command.append('/DHebrewLanguageFile='+str(hebrew))
    command.append(str(ROOT/'scripts/windows_installer.iss'))
    subprocess.run(command,cwd=ROOT,check=True)
    output=ROOT/'dist/InvoiceAssistant-Setup-0.1.0-x64.exe'
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.exe.sha256').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    print(output);print('SHA256:',digest)


if __name__=='__main__':main()
