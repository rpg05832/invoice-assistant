"""Install optional CPU-only Qwen3-VL Q4 and a pinned llama.cpp runtime."""
import hashlib
import os
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
REVISION='52d6c8ffea26cc873ac5ad116f8631268d7eb503'
MODELS={
 'Qwen3VL-2B-Instruct-Q4_K_M.gguf':'089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae',
 'mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf':'f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82',
}
RUNTIMES={
 'Windows':('llama-b12322-bin-win-cpu-x64.zip','35b6b69e69ae1a313768feb70bc1c4504bf070f7015704aaa88543812bcc5dd2'),
 'Linux':('llama-b12322-bin-ubuntu-x64.tar.gz','735a12ee7711afc75d994a7b9fac654a7f8718d084d33cf001910f0d16c5a7d0'),
}


def download(url,target,expected):
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    if target.is_file() and digest(target)==expected:print(target.name+': verified');return
    target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_suffix(target.suffix+'.download')
    with urlopen(url,timeout=120) as response,temporary.open('wb') as output:shutil.copyfileobj(response,output,1024*1024)
    if digest(temporary)!=expected:temporary.unlink(missing_ok=True);raise RuntimeError('Integrity verification failed: '+target.name)
    temporary.replace(target);print(target.name+': installed and verified')


def main():
    if platform.machine().lower() not in ('x86_64','amd64'):raise RuntimeError('This optional runtime targets x64 CPUs')
    for name,expected in MODELS.items():download(f'https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/{REVISION}/{name}?download=true',ROOT/'data/models'/name,expected)
    filename,expected=RUNTIMES[platform.system()];archive=ROOT/'data/runtime'/filename
    download(f'https://github.com/ggml-org/llama.cpp/releases/download/b12322/{filename}',archive,expected)
    output=ROOT/'data/runtime/vision';output.mkdir(parents=True,exist_ok=True)
    if archive.suffix=='.zip':
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                dest=(output/member.filename).resolve()
                if not dest.is_relative_to(output.resolve()):raise ValueError('Unsafe runtime archive')
            z.extractall(output)
    else:
        with tarfile.open(archive) as t:t.extractall(output,filter='data')
    print('Optional vision engine installed. Start scripts/run_vision.py when needed; allow about 3 GB RAM for the engine.')

if __name__=='__main__':main()
