"""Install optional Hebrew-focused Tesseract models without installing extra code."""
from pathlib import Path
try:
    from .setup_vision import download
except ImportError:
    from setup_vision import download

ROOT=Path(__file__).resolve().parents[1]
REVISION='a99d4d6bb91aa946554ad22a29ae5e637675361d'
MODELS={
    'heb_rivok':'dd9ba506b4ec6e62119a1334949824137692eada686687e51ac10dbb2f7a3df8',
    'heb_rivok_lite':'a92584816adf5c9ae38afc255e3530477516318c21c2e711fd57b05535239360',
}


def main():
    target=ROOT/'data/tessdata_rivok'
    for name,expected in MODELS.items():
        download(f'https://huggingface.co/Rivok/tesseract-heb-rivok/resolve/{REVISION}/tessdata/{name}.traineddata',target/f'{name}.traineddata',expected)
    print('Experimental printed Hebrew models installed. They are not a handwritten check-number guarantee.')
    print('Compare with: tesseract page.png stdout --tessdata-dir "'+str(target)+'" -l heb_rivok')

if __name__=='__main__':main()
