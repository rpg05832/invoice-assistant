"""Download a 26 KB, pinned ONNX digit classifier for CPU-only computers."""
import hashlib
from pathlib import Path
from urllib.request import urlopen
try:
    from .public_models import download_model
except ImportError:
    from public_models import download_model

REVISION='4c46cd00fbdb7cd30b6c1c17ab54f2e1f4f7b177'
SHA256='5c688690f8bacf667d4c2074af5ad0646ca328d7ab03eccf944a65b320171bdd'
URL=f'https://media.githubusercontent.com/media/onnx/models/{REVISION}/validated/vision/classification/mnist/model/mnist-12.onnx'


def main():
    target=Path(__file__).resolve().parents[1]/'data/models/mnist-12.onnx';target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==SHA256:print('Digit model: verified');return
    download_model(SHA256,target);print('Digit model: installed from GitHub and verified')

if __name__=='__main__':main()
