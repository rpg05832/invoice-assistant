"""Package source and verified OCR models, excluding customer data and secrets."""
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from setup_ocr import MODELS, main as install_models


def validate_distribution_paths(paths,public_assets):
    """A distributable is code plus an exact allowlist of generic model files."""
    for path in paths:
        parts=path.parts
        if path.is_absolute() or '..' in parts or any(p in ('.git','.venv','.env','test-results') for p in parts) or path.suffix in ('.sqlite3','.sqlite','.db','.key','.npz') or ('data' in parts and path not in public_assets):
            raise RuntimeError(f'Private or unexpected distribution path: {path}')


def main():
    install_models()
    from setup_digits import main as install_digits
    install_digits()
    from setup_sequences import main as install_sequences
    install_sequences()
    output = ROOT / 'dist' / 'invoice-assistant-0.1.0-windows-source.zip'
    output.parent.mkdir(exist_ok=True)
    tracked = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')
    paths = [Path(p) for p in tracked if p and not p.startswith(('.github/', 'tests/'))]
    if not paths: raise RuntimeError('Stage or commit source files before packaging')
    paths += [Path('data/models/mnist-12.onnx')]
    import json
    for model in json.loads((ROOT/'scripts/sequence-models.json').read_text()).values():
        paths += [Path('data/models')/model['directory']/name for name in model['files']]
    paths += [Path('data/tessdata') / f'{lang}.traineddata' for lang in MODELS]
    # Only the fixed files explicitly appended above may be distributed.
    public_assets={Path('data/models/mnist-12.onnx')}|{Path('data/tessdata')/f'{lang}.traineddata' for lang in MODELS}|{Path('data/models')/model['directory']/name for model in json.loads((ROOT/'scripts/sequence-models.json').read_text()).values() for name in model['files']}
    validate_distribution_paths(paths,public_assets)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for relative in sorted(paths):
            z.write(ROOT / relative, Path('invoice-assistant') / relative)
    print(output)
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__': main()
