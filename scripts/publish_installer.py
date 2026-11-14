"""Publish only the tested installer artifact to a public GitHub release."""
import argparse
from pathlib import Path
import os
import re
import urllib.error
from publish_model_mirrors import api,upload


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directory');args=parser.parse_args()
    directory=Path(args.directory);sha=os.environ['GITHUB_SHA']
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Invalid build revision')
    files=list(directory.rglob('InvoiceAssistant-Setup-0.1.0-x64.exe'))
    if len(files)!=1:raise ValueError('Expected one tested installer')
    installer=files[0];checksum=installer.with_suffix('.exe.sha256')
    if not checksum.is_file():raise ValueError('Installer checksum missing')
    import hashlib
    with installer.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if checksum.read_text(encoding='utf-8').split()[0]!=digest:raise ValueError('Installer checksum mismatch')
    tag='windows-0.1.0-'+sha[:8]
    try:rel=api('/releases/tags/'+tag)
    except urllib.error.HTTPError as exc:
        if exc.code!=404:raise
        rel=api('/releases',method='POST',payload={'tag_name':tag,'target_commitish':sha,'name':'Invoice Assistant · Windows 0.1.0 · '+sha[:8],'draft':True,'prerelease':True,'body':'Single-file per-user Windows installer, including Python, Hebrew/English OCR and light digit models. Personal data is stored separately. Additional public models download through this repository’s GitHub release mirrors. Validated on Windows and Linux, including actual OCR/training, installation and data-preserving uninstall. Build: '+sha})
    upload(rel,installer);upload(rel,checksum)
    api('/releases/'+str(rel['id']),method='PATCH',payload={'draft':False,'prerelease':True})
    print('Installer:',rel['html_url'])


if __name__=='__main__':main()
