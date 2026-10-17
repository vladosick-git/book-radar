"""Clean release ZIP of the shared folder with a SHA-256 manifest.

usage: python tools/release.py <version> <output_dir>
Excludes data, caches, dependencies, secrets and the git directory. The manifest lists every file in the archive.
"""
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version, out_dir = sys.argv[1], Path(sys.argv[2])
EXCLUDE_DIRS = {'.git', '.venv', 'node_modules', 'data', 'review-data', '__pycache__', '.pytest_cache', '.vite', 'secrets-local', 'outputs', 'work'}
EXCLUDE_FILES = re.compile(r'(^\.env$|^\.env\.(?!example$).*|\.pyc$|\.log$|\.sqlite.*$|\.db(?:-wal|-shm)?$|radar-state\..*|\.dpapi$|\.vault$|\.key$|\.partial$|\.radar-session\.json$)')
SECRETS = [r'Zoho-oauthtoken [A-Za-z0-9.]{20,}', r'1000\.[a-f0-9]{32}\.[a-f0-9]{32}', r'sk-ant-[A-Za-z0-9-]{10,}', r'-----BEGIN [A-Z ]*PRIVATE KEY-----']

files = []
for path in sorted(root.rglob('*')):
    rel = path.relative_to(root)
    if not path.is_file() or any(part in EXCLUDE_DIRS for part in rel.parts) or EXCLUDE_FILES.search(path.name):
        continue
    if rel.parts[0] == 'deploy' and len(rel.parts) > 1 and rel.parts[1] == 'secrets' and path.name != 'README.txt':
        continue
    files.append(path)

suspicious = []
for path in files:
    if path.suffix.lower() in {'.py', '.js', '.jsx', '.json', '.md', '.txt', '.css', '.html', '.ps1', '.cmd', '.yml', '.example'}:
        text = path.read_text(encoding='utf-8', errors='ignore')
        suspicious += [f'{path.relative_to(root)}: {pattern}' for pattern in SECRETS if re.search(pattern, text)]
if suspicious:
    print('Похоже на секреты, релиз не собран:\n' + '\n'.join(suspicious))
    sys.exit(1)

name = f'Книжный_радар_{version}'
manifest = {'name': name, 'version': version, 'created': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            'files': {path.relative_to(root).as_posix(): {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size} for path in files}}
out_dir.mkdir(parents=True, exist_ok=True)
archive = out_dir / f'{name}.zip'
if archive.exists():
    raise SystemExit('Архив уже существует: используйте новую редакцию или другую выходную папку.')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for path in files:
        zf.write(path, f'{name}/{path.relative_to(root).as_posix()}')
    zf.writestr(f'{name}/RELEASE_MANIFEST_SHA256.json', json.dumps(manifest, ensure_ascii=False, indent=1))
with zipfile.ZipFile(archive) as zf:
    bad = [rel for rel, meta in manifest['files'].items() if hashlib.sha256(zf.read(f'{name}/{rel}')).hexdigest() != meta['sha256']]
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(out_dir / f'{name}.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
print(f'{archive} · файлов {len(files)} · {archive.stat().st_size // 1024} КБ · sha256 {digest}')
print('сверка манифеста:', 'ok' if not bad else bad[:5])
