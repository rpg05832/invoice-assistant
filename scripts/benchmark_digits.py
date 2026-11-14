"""Compare installed digit engines on local crops; all results stay on disk."""
import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images',type=Path,help='Folder of already cropped check-number images')
    parser.add_argument('--engine',choices=['paddle-v5','paddle-v6-small','glm-ocr','paddle-vl16','qwen35-08','lighton','digits'],default='paddle-v6-small')
    parser.add_argument('--truth',type=Path,help='Optional JSON: image filename -> exact digit STRING, including leading zeros')
    parser.add_argument('--output',type=Path,default=ROOT/'data/model-benchmarks/latest.json')
    args=parser.parse_args()
    paths=sorted(p for p in args.images.iterdir() if p.suffix.lower() in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff'))
    if not paths:parser.error('No crop images found')
    truth=json.loads(args.truth.read_text(encoding='utf-8')) if args.truth else {}
    if not isinstance(truth,dict) or any(not isinstance(v,str) or not v.isascii() or not v.isdigit() for v in truth.values()):parser.error('Truth values must be digit strings, never JSON numbers')
    report={'engine':args.engine,'hardware':{'system':platform.platform(),'processor':platform.processor(),'architecture':platform.machine()},'samples':[],'warning':'Small local trial. Not a claim about 99% accuracy, Hebrew text or full-document correctness.'}
    for path in paths:
        started=time.perf_counter();sample={'file':path.name}
        if path.name in truth:sample.update(expected=truth[path.name],exact_match=False)
        try:
            if args.engine.startswith('paddle-v'):
                from app.sequence_digits import recognize
                result=recognize(path,args.engine)
                if not result.get('available'):raise RuntimeError(result.get('warning','Model unavailable'))
                predictions=[result['number']] if result.get('number') else []
            else:
                from app.cloud import provider_call
                result=provider_call({'provider':'local_digits','action':'check','model':args.engine},'',path.read_bytes());predictions=result['checks']
            sample.update(predictions=predictions,seconds=round(time.perf_counter()-started,4))
            if path.name in truth:sample['exact_match']=predictions==[truth[path.name]]
        except Exception as exc:sample.update(error=type(exc).__name__,seconds=round(time.perf_counter()-started,4))
        report['samples'].append(sample)
    labelled=[s for s in report['samples'] if 'expected' in s]
    report['summary']={'crops':len(paths),'errors':sum('error' in s for s in report['samples']),'labelled':len(labelled),'exact_matches':sum(s.get('exact_match',False) for s in labelled),'median_seconds':round(statistics.median(s['seconds'] for s in report['samples']),4)}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report['summary']));print('Private local result:',args.output)
    if report['summary']['errors']:raise SystemExit(1)

if __name__=='__main__':main()
