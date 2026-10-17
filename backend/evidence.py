import re
import time
from . import db

FILENAME = re.compile(r'job-\d+-[0-9a-f]{32}\.png')


def evidence_path(filename):
    if not FILENAME.fullmatch(filename):
        return None
    base = (db.DATA / 'evidence').resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


def cleanup(min_age_days=7, dry_run=False):
    protected = set()
    for row in db.rows('SELECT screenshot,first_screenshot,crop FROM findings'):
        protected.update(filter(None, row.values()))
    for row in db.rows('SELECT message FROM logs ORDER BY id DESC LIMIT 300'):
        protected.update(FILENAME.findall(row['message']))
    threshold = time.time() - min_age_days * 86400
    removed = 0
    for entry in (db.DATA / 'evidence').glob('*.png'):
        if entry.is_symlink() or not FILENAME.fullmatch(entry.name) or entry.name in protected:
            continue
        if entry.stat().st_mtime < threshold:
            if not dry_run:
                entry.unlink()
            removed += 1
    return removed
