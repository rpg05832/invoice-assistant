"""Package source and verified OCR models, excluding customer data and secrets."""
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from setup_ocr import MODELS, main as install_models


def main():
    install_models()
    output = ROOT / 'dist' / 'invoice-assistant-0.1.0-windows-source.zip'
    output.parent.mkdir(exist_ok=True)
    tracked = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')
    paths = [Path(p) for p in tracked if p and not p.startswith(('.github/', 'tests/'))]
    if not paths: raise RuntimeError('Stage or commit source files before packaging')
    paths += [Path('data/tessdata') / f'{lang}.traineddata' for lang in MODELS]
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for relative in sorted(paths):
            if relative.parts[0] in ('.git', '.venv', '.env') or ('data' in relative.parts and relative.parts[:2] != ('data', 'tessdata')):
                raise RuntimeError(f'Unexpected package path: {relative}')
            z.write(ROOT / relative, Path('invoice-assistant') / relative)
    print(output)
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__': main()
