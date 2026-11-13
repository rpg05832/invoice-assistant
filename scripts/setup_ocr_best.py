"""Optional full-precision Hebrew/English Tesseract models, CPU-compatible."""
from pathlib import Path
from setup_vision import download
REVISION='e12c65a915945e4c28e237a9b52bc4a8f39a0cec'
MODELS={'heb':'dbaa827aea6bc21215638447f17783a1004987c2d0bf5573d111fee397abdae5','eng':'8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba'}
if __name__=='__main__':
    for language,expected in MODELS.items():download(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{REVISION}/{language}.traineddata',Path(__file__).resolve().parents[1]/'data/tessdata_best'/f'{language}.traineddata',expected)
