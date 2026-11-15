"""Release identity shared by the application, installer and updater."""
import json
import sys
from pathlib import Path
VERSION='0.3.0'

def build_info():
    path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'build-info.json'
    try:return {'version':VERSION,**json.loads(path.read_text(encoding='utf-8'))}
    except (OSError,ValueError):return {'version':VERSION,'commit':'development','built_at':''}
