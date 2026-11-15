"""Bundle Microsoft's signed offline WebView2 installer for disconnected PCs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
URL='https://go.microsoft.com/fwlink/?linkid=2124701'

def main():
    if os.name!='nt':raise SystemExit('Prepare the Windows runtime on Windows')
    target=ROOT/'build/webview/MicrosoftEdgeWebView2RuntimeInstallerX64.exe';target.parent.mkdir(parents=True,exist_ok=True)
    if not target.is_file():
        temp=target.with_suffix('.download');total=0
        with urllib.request.urlopen(URL,timeout=120) as source,temp.open('wb') as output:
            while True:
                chunk=source.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>450_000_000:raise ValueError('Unexpected WebView2 installer size')
                output.write(chunk)
        temp.replace(target)
    if not 50_000_000<target.stat().st_size<450_000_000:raise ValueError('Expected the full offline WebView2 installer, not a network bootstrapper')
    # Keep the original Microsoft binary and validate its publisher before bundling.
    literal="'"+str(target).replace("'","''")+"'"
    script=f"$ErrorActionPreference='Stop'; $env:PSModulePath=$PSHOME+'\\Modules'; $s=Get-AuthenticodeSignature -LiteralPath {literal}; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'CN=Microsoft Corporation'){{throw 'Invalid WebView2 Microsoft signature'}}"
    subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],check=True)
    print('Verified Microsoft offline WebView2 runtime:',target.stat().st_size,'bytes; SHA256',hashlib.sha256(target.read_bytes()).hexdigest())
if __name__=='__main__':main()
