"""Run the optional vision model on loopback, with bounded CPU/context usage."""
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
try:
    from .setup_vision import PROFILES
except ImportError:
    from setup_vision import PROFILES


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=PROFILES,default='digits');args=parser.parse_args()
    profile=PROFILES[args.profile];files=profile.get('models',profile.get('files',{}));port='8089' if args.profile=='multilingual' else '8090'
    binary='llama-server.exe' if os.name=='nt' else 'llama-server'
    candidates=list((ROOT/'data/runtime').rglob(binary))
    if not candidates:raise SystemExit('Run scripts/setup_vision.py first')
    model=ROOT/'data/models'/next(name for name in files if 'mmproj' not in name);projector=ROOT/'data/models'/next(name for name in files if 'mmproj' in name)
    if not model.is_file() or not projector.is_file():raise SystemExit('Vision model missing. Run setup_vision.py')
    command=[str(candidates[0]),'-m',str(model),'--mmproj',str(projector),'--alias',args.profile,'--host','127.0.0.1','--port',port,'-c','4096','-np','1','-t','2','-tb','2','-ngl','0','--no-mmproj-offload','--image-max-tokens','1024']
    subprocess.run(command,cwd=candidates[0].parent,check=True)

if __name__=='__main__':main()
