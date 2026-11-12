"""Run the optional vision model on loopback, with bounded CPU/context usage."""
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    binary='llama-server.exe' if os.name=='nt' else 'llama-server'
    candidates=list((ROOT/'data/runtime').rglob(binary))
    if not candidates:raise SystemExit('Run scripts/setup_vision.py first')
    model=ROOT/'data/models/Qwen3VL-2B-Instruct-Q4_K_M.gguf';projector=ROOT/'data/models/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf'
    if not model.is_file() or not projector.is_file():raise SystemExit('Vision model missing. Run setup_vision.py')
    command=[str(candidates[0]),'-m',str(model),'--mmproj',str(projector),'--host','127.0.0.1','--port','8089','-c','4096','-np','1','-t','2','-tb','2','-ngl','0','--no-mmproj-offload','--image-max-tokens','1024']
    subprocess.run(command,cwd=candidates[0].parent,check=True)

if __name__=='__main__':main()
