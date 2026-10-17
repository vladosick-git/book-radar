"""The handoff must not recursively include earlier releases or overwrite a delivered ZIP."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def test_clean_package_and_no_overwrite(tmp_path):
    root = tmp_path / 'project'
    (root / 'tools').mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[1] / 'tools/release.py', root / 'tools/release.py')
    for relative in ('backend/public.py', 'deploy/.env.example', 'outputs/old.zip', 'work/private.json',
                     'data/radar.sqlite', 'deploy/.env', 'deploy/secrets/vault.key', 'deploy/secrets/README.txt'):
        target = root / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture')
    command = [sys.executable, str(root / 'tools/release.py'), 'fixture', str(root / 'outputs')]
    subprocess.run(command, check=True, capture_output=True)
    archive = root / 'outputs/Книжный_радар_fixture.zip'
    with zipfile.ZipFile(archive) as package:
        files = package.namelist()
    assert any(v.endswith('backend/public.py') for v in files)
    assert any(v.endswith('deploy/.env.example') for v in files)
    assert any(v.endswith('deploy/secrets/README.txt') for v in files)
    assert not any('/outputs/' in v or '/work/' in v or '/data/' in v or v.endswith(('.key', '/.env')) for v in files)
    original = archive.read_bytes()
    retry = subprocess.run(command, capture_output=True)
    assert retry.returncode != 0
    assert archive.read_bytes() == original
