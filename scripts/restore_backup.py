"""Validate and restore a backup into a NEW directory, preserving existing data."""
import argparse
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

def restore(archive, target):
    target = Path(target).resolve()
    if target.exists(): raise ValueError('Target already exists. Choose a NEW directory; existing data is never overwritten.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staging = Path(temp) / 'restore'; staging.mkdir()
        with zipfile.ZipFile(archive) as z:
            if sum(m.file_size for m in z.infolist()) > 2_000_000_000: raise ValueError('Backup too large')
            names = set()
            for member in z.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in member.filename or ':' in member.filename or member.filename in names:
                    raise ValueError('Unsafe or duplicate archive entry')
                names.add(member.filename)
                if member.filename != 'invoices.sqlite3' and not member.filename.startswith('files/'):
                    raise ValueError('Unexpected archive entry')
                if member.is_dir(): continue
                destination = staging.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output)
        db_path = staging / 'invoices.sqlite3'
        if not db_path.exists(): raise ValueError('Database missing')
        conn = sqlite3.connect(f'{db_path.as_uri()}?mode=ro', uri=True)
        try:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('Invalid database')
            if conn.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Broken database references')
            paths = {r[0] for r in conn.execute('SELECT path FROM sources')} | {r[0] for r in conn.execute('SELECT image FROM documents')}
            for relative in paths:
                path = (staging / relative).resolve()
                if not path.is_relative_to(staging) or not path.is_file(): raise ValueError('Missing or unsafe source file')
        finally: conn.close()
        staging.rename(target)
    return target

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive'); parser.add_argument('new_directory')
    args = parser.parse_args()
    print('Restored:', restore(args.archive, args.new_directory))

