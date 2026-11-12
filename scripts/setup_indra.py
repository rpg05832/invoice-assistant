"""Optional Hebrew RTL OCR models, pinned to the tested Indra package model tag."""
from pathlib import Path
import hashlib
import json


def main():
    from huggingface_hub import snapshot_download
    root=Path(__file__).resolve().parents[1]
    target=root/'data/models/indra'
    manifest=json.loads((root/'scripts/indra-models.json').read_text())
    snapshot_download('datalab-to/indra-ocr',revision=manifest['revision'],local_dir=str(target),allow_patterns=['*.onnx','meta.json','lm*.npz','LICENSE'],ignore_patterns=['math*'])
    for name,expected in manifest['files'].items():
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Indra integrity check failed: '+name)
    print('Indra RTL installed. Model terms: modified OpenRAIL-M; see data/models/indra/LICENSE before commercial activation.')

if __name__=='__main__':main()
