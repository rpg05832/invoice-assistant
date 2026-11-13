"""Install small, pinned CPU sequence recognizers for numeric annotations."""
import json
import os
from pathlib import Path
try:
    from .setup_vision import download
except ImportError:
    from setup_vision import download


def main(directory=None):
    directory=Path(directory or os.environ.get('INVOICE_ASSET_DIR',Path(__file__).resolve().parents[1]/'data'))
    manifest=json.loads((Path(__file__).parent/'sequence-models.json').read_text())
    for model in manifest.values():
        for name,sha in model['files'].items():
            download(f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{name}?download=true",directory/'models'/model['directory']/name,sha)

if __name__=='__main__':main()
