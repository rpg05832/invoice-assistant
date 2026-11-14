"""Fixed public model inventory; no customer or personal training files."""
import json
from pathlib import Path
try:
    from . import setup_ocr,setup_digits,setup_vision,setup_tesseract_rivok
except ImportError:
    import setup_ocr,setup_digits,setup_vision,setup_tesseract_rivok

ROOT=Path(__file__).resolve().parents[1]
REPOSITORY='rpg05832/invoice-assistant'
TAG='public-models-v1'
PROFILES=['base','best','rivok','indra',*setup_vision.PROFILES,'runtimes']


def catalog():
    rows=[]
    def add(profile,name,sha,url,license):rows.append({'profile':profile,'name':name,'sha256':sha,'url':url,'license':license})
    for name,sha in setup_ocr.MODELS.items():add('base',name+'.traineddata',sha,f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{setup_ocr.REVISION}/{name}.traineddata','Apache-2.0')
    add('base','mnist-12.onnx',setup_digits.SHA256,setup_digits.URL,'Apache-2.0')
    for key,spec in json.loads((ROOT/'scripts/sequence-models.json').read_text()).items():
        for name,sha in spec['files'].items():add('base',key+'-'+name,sha,f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}?download=true",'Apache-2.0')
    best={'heb':'dbaa827aea6bc21215638447f17783a1004987c2d0bf5573d111fee397abdae5','eng':'8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba'}
    for name,sha in best.items():add('best',name+'.traineddata',sha,f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/{name}.traineddata','Apache-2.0')
    for name,sha in setup_tesseract_rivok.MODELS.items():add('rivok',name+'.traineddata',sha,f'https://huggingface.co/Rivok/tesseract-heb-rivok/resolve/{setup_tesseract_rivok.REVISION}/tessdata/{name}.traineddata','Apache-2.0')
    spec=json.loads((ROOT/'scripts/indra-models.json').read_text())
    for name,sha in spec['files'].items():add('indra',name,sha,f"https://huggingface.co/datalab-to/indra-ocr/resolve/{spec['revision']}/{name}?download=true",'AI PUBS Open RAIL-M (modified); original LICENSE included')
    for key,spec in setup_vision.PROFILES.items():
        for name,sha in spec.get('models',spec.get('files',{})).items():add(key,name,sha,f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}?download=true",spec.get('license','Apache-2.0'))
    for system,(name,sha) in setup_vision.RUNTIMES.items():add('runtimes',name,sha,f'https://github.com/ggml-org/llama.cpp/releases/download/b12322/{name}','MIT; runtime notices retained in archive')
    unique={}
    for row in rows:
        if row['sha256'] not in unique:unique[row['sha256']]={**row,'profiles':[row['profile']]}
        elif row['profile'] not in unique[row['sha256']]['profiles']:unique[row['sha256']]['profiles'].append(row['profile'])
    return list(unique.values())
