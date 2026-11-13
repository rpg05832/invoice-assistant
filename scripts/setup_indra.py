"""Optional Hebrew RTL OCR models, pinned to the tested Indra package model tag."""
from pathlib import Path
import hashlib
import json


def main():
    import os
    try:from .public_models import download_model
    except ImportError:from public_models import download_model
    root=Path(__file__).resolve().parents[1]
    target=Path(os.environ.get('INVOICE_ASSET_DIR',root/'data'))/'models/indra'
    manifest=json.loads((root/'scripts/indra-models.json').read_text())
    for name,expected in sorted(manifest['files'].items(),key=lambda row:row[0]!='LICENSE'):download_model(expected,target/name)
    for name,expected in manifest['files'].items():
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Indra integrity check failed: '+name)
    print('Indra RTL installed. Model terms: modified OpenRAIL-M; see data/models/indra/LICENSE before commercial activation.')

if __name__=='__main__':main()
