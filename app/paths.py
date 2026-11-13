"""User-writable model cache and bundled source assets."""
import os
from pathlib import Path


def assets():
    return Path(os.environ.get('INVOICE_ASSET_DIR',Path(__file__).resolve().parents[1]/'data')).resolve()
