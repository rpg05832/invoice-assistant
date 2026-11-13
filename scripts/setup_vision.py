"""Install optional verified CPU vision profiles and a pinned llama.cpp runtime."""
import hashlib
import json
import os
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
PROFILES={
 'digits':{'revision':'52d6c8ffea26cc873ac5ad116f8631268d7eb503','repo':'Qwen/Qwen3-VL-2B-Instruct-GGUF','models':{
 'Qwen3VL-2B-Instruct-Q4_K_M.gguf':'089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae',
 'mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf':'f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82'}},
 'multilingual':{'revision':'1cd86afb9a95c410a6038ab3b40d8b578c892266','repo':'Qwen/Qwen3-VL-4B-Instruct-GGUF','models':{
 'Qwen3VL-4B-Instruct-Q4_K_M.gguf':'66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a',
 'mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf':'30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d'}}}
PROFILES.update(json.loads((ROOT/'scripts/vision-models.json').read_text()))
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
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=PROFILES,default='digits');args=parser.parse_args();profile=PROFILES[args.profile]
    if platform.machine().lower() not in ('x86_64','amd64'):raise RuntimeError('This optional runtime targets x64 CPUs')
    for name,expected in profile.get('models',profile.get('files',{})).items():download(f"https://huggingface.co/{profile['repo']}/resolve/{profile['revision']}/{name}?download=true",ROOT/'data/models'/name,expected)
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
    print('Verified model installed. Start scripts/run_vision.py --profile '+args.profile+'. Only the multilingual profile may propose Hebrew text; other profiles are restricted to digit crops.')

if __name__=='__main__':main()
