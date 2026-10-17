"""Consistent backups of the radar data directory.

    python -m backend.backup create <dir> [--keep 7]
    python -m backend.backup verify <file.zip>
    python -m backend.backup restore <file.zip> <empty data dir>

The archive holds a SQLite online-backup copy, evidence screenshots and the protected session
snapshot of each profile (not Chromium caches). It is written as *.partial and renamed only after
verification, so an unfinished copy is never offered for restore. Session snapshots are secrets:
store archives like the vault key, and never next to it.
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import db

TABLES = ('profiles', 'jobs', 'findings', 'runs', 'logs', 'ads', 'observations')


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _counts(database):
    connection = sqlite3.connect(database)
    try:
        existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        return integrity, {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES if table in existing}
    finally:
        connection.close()


def create(target_dir, data_dir=None, keep=7, prefix='radar-backup-', files=True, note=None):
    """files=False keeps only the database (pre-update copies: migrations never touch screenshots or sessions)."""
    data = Path(data_dir or db.DATA)
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    final = target / f'{prefix}{stamp}.zip'
    partial = final.with_suffix('.zip.partial')
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'radar.sqlite'
        source = sqlite3.connect(data / 'radar.sqlite')
        copy = sqlite3.connect(snapshot)
        try:
            source.backup(copy)  # consistent even while the worker writes
        finally:
            copy.close(); source.close()
        integrity, counts = _counts(snapshot)
        if integrity != 'ok':
            raise RuntimeError(f'Копия базы не прошла integrity_check: {integrity}')
        entries = {'radar.sqlite': snapshot}
        if files:
            for path in sorted((data / 'evidence').glob('*.png')):
                entries[f'evidence/{path.name}'] = path
            for path in sorted((data / 'profiles').glob('*/radar-state.*')):
                if path.suffix in ('.json', '.dpapi', '.vault'):
                    entries[f'profiles/{path.parent.name}/{path.name}'] = path
        manifest = {'format': 1, 'created_at': stamp, 'counts': counts, 'files_included': files, 'note': note or {},
                    'files': {name: _sha(path) for name, path in entries.items()}}
        files = entries
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, path in files.items():
                archive.write(path, name)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1))
    verify(partial)
    partial.replace(final)
    prune(target, keep, prefix)
    return final, manifest


def verify(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        for name, expected in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise RuntimeError(f'Контрольная сумма не совпала: {name}')
        with tempfile.TemporaryDirectory() as temporary:
            archive.extract('radar.sqlite', temporary)
            integrity, counts = _counts(Path(temporary) / 'radar.sqlite')
    if integrity != 'ok' or counts != manifest['counts']:
        raise RuntimeError('База в копии повреждена или не совпадает с манифестом.')
    return manifest


def restore(archive_path, data_dir):
    manifest = verify(archive_path)
    target = Path(data_dir)
    if target.exists() and any(target.iterdir()):
        raise RuntimeError('Каталог восстановления не пуст. Укажите пустой каталог; текущие данные не трогаются.')
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for name in manifest['files']:
            destination = (target / name).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise RuntimeError(f'Недопустимый путь в архиве: {name}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))
    integrity, counts = _counts(target / 'radar.sqlite')
    if integrity != 'ok' or counts != manifest['counts']:
        raise RuntimeError('Восстановленная база не совпадает с копией.')
    (target / 'evidence').mkdir(exist_ok=True)
    (target / 'profiles').mkdir(exist_ok=True)
    return manifest


def prune(target_dir, keep, prefix='radar-backup-'):
    if os.environ.get('RADAR_PRESERVE_DATA') == '1':
        return
    archives = sorted(Path(target_dir).glob(f'{prefix}*.zip'))
    for old in archives[:-keep] if keep > 0 else []:
        old.unlink()
    for stale in Path(target_dir).glob('*.zip.partial'):
        stale.unlink()


def main(argv):
    if len(argv) >= 2 and argv[0] == 'create':
        keep = int(argv[argv.index('--keep') + 1]) if '--keep' in argv else 7
        path, manifest = create(argv[1], keep=keep)
        print(f'Копия создана и проверена: {path} · {manifest["counts"]}')
    elif len(argv) == 2 and argv[0] == 'verify':
        print(f'Копия цела: {verify(argv[1])["counts"]}')
    elif len(argv) == 3 and argv[0] == 'restore':
        print(f'Восстановлено в {argv[2]}: {restore(argv[1], argv[2])["counts"]}')
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
