"""Download pinned official Tesseract models with integrity verification."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

REVISION = '87416418657359cb625c412a48b6e1d6d41c29bd'
MODELS = {
    'osd': '9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff',
    'heb': '11f9e43ab227f786352a50f75c94c2e9906f1baba86d93276da19da7ce0904db',
    'eng': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
}

def main():
    directory = Path(__file__).resolve().parents[1] / 'data' / 'tessdata'
    directory.mkdir(parents=True, exist_ok=True)
    for language, expected in MODELS.items():
        path = directory / f'{language}.traineddata'
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            print(f'{language}: verified'); continue
        url = f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{REVISION}/{language}.traineddata'
        with urlopen(url, timeout=60) as response:
            content = response.read(20_000_000)
        if hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError(f'Integrity check failed for {language}; installation stopped')
        temporary = path.with_suffix('.download')
        temporary.write_bytes(content); temporary.replace(path)
        print(f'{language}: installed and verified')

if __name__ == '__main__': main()

