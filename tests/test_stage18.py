"""Stage 18 (rest): automatic verified backup before an update, dry-run of migrations on a copy, rollback
to the pre-update copy without deleting anything, one migrating process at a time."""
import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from backend import backup, catalog, config, db, upgrade

ROOT = Path(__file__).parents[1]
LEGACY = Path(__file__).parent / 'fixtures' / 'radar-0.6.2-fixture.db'
SHOT = 'job-1-' + 'c' * 32 + '.png'


def tables(path):
    connection = sqlite3.connect(path)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    shutil.copy(LEGACY, data / 'radar.sqlite')
    (data / 'evidence').mkdir()
    (data / 'evidence' / SHOT).write_bytes(b'png-bytes')
    monkeypatch.setattr(db, 'DATA', data)
    monkeypatch.delenv('RADAR_BACKUP_DIR', raising=False)
    return data


def test_first_start_of_new_version_backs_up_the_database_once(legacy):
    db.init()
    archives = sorted((legacy / 'backups').glob('radar-preupdate-legacy-to-*.zip'))
    assert len(archives) == 1 and archives[0].name.startswith(f'radar-preupdate-legacy-to-{config.VERSION}-')
    manifest = backup.verify(archives[0])
    assert manifest['files_included'] is False and set(manifest['files']) == {'radar.sqlite'}
    assert manifest['note'] == {'kind': 'pre-update', 'from': 'legacy', 'to': config.VERSION}
    assert manifest['counts']['findings'] == 6 and 'ads' not in manifest['counts']  # the copy is the data before migration
    db.init()
    assert len(list((legacy / 'backups').glob('*.zip'))) == 1  # same version: no new copy


def test_fresh_installation_makes_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    assert not (tmp_path / 'backups').exists()


def test_next_version_backs_up_again_and_keeps_five(legacy, monkeypatch):
    # Fix the starting version of this upgrade chain; the application version changes between releases.
    monkeypatch.setattr(config, 'VERSION', '0.7.0')
    db.init()
    for minor in range(1, 8):
        monkeypatch.setattr(config, 'VERSION', f'0.7.{minor}')
        db.init()
    names = sorted(path.name for path in (legacy / 'backups').glob('*.zip'))
    assert len([n for n in names if n.startswith('radar-preupdate-legacy-')]) == 1
    assert [n.split('-to-')[0] for n in names if not n.startswith('radar-preupdate-legacy-')] == \
           [f'radar-preupdate-0.7.{m}' for m in range(0, 7)]


def test_two_processes_starting_together_migrate_once(legacy):
    env = {**os.environ, 'RADAR_DATA_DIR': str(legacy), 'PYTHONDONTWRITEBYTECODE': '1'}
    code = 'from backend import db; db.init(); print("ok")'
    workers = [subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    results = [worker.communicate(timeout=120) for worker in workers]
    assert all(worker.returncode == 0 for worker in workers), results
    assert len(list((legacy / 'backups').glob('*.zip'))) == 1
    assert db.rows('SELECT COUNT(*) AS n FROM observations')[0]['n'] == sum(
        2 if row['sightings'] > 1 else 1 for row in db.rows('SELECT sightings FROM findings'))


def test_worker_starts_while_the_api_holds_the_database(legacy):
    """Guard for the server layout: a second process starts while the first holds the WAL database open. The original
    failure («disk I/O error» of a mode=ro probe during a worker restart) is reproduced by server_acceptance 2b."""
    db.init()
    holder = db.connect()
    holder.execute("INSERT INTO settings(key,value) VALUES ('probe','1') ON CONFLICT(key) DO UPDATE SET value='2'")
    holder.commit()  # the API keeps its connection: -wal and -shm exist
    try:
        env = {**os.environ, 'RADAR_DATA_DIR': str(legacy), 'PYTHONDONTWRITEBYTECODE': '1'}
        started = subprocess.run([sys.executable, '-c', 'from backend import db; db.init(); print("ok")'], cwd=ROOT, env=env, capture_output=True, timeout=120)
        assert started.returncode == 0, started.stderr.decode('utf-8', 'replace')[-800:]
        report = upgrade.check(legacy)
        assert report['integrity'] == 'ok' and report['original_unchanged']
    finally:
        holder.close()


def test_dry_run_checks_migrations_on_a_copy_only(legacy):
    before = (legacy / 'radar.sqlite').read_bytes()
    report = upgrade.check(legacy)
    assert report['ok'] and report['integrity'] == 'ok' and report['original_unchanged']
    assert {'ads', 'observations', 'notifications'} <= set(report['added_tables'])
    assert 'ad_id' in report['added_columns']['findings'] and 'error_code' in report['added_columns']['logs']
    assert report['rows_after']['findings'] == report['rows_before']['findings'] == 6
    assert report['rows_after']['ads'] == 4 and report['rows_after']['observations'] == 10
    assert 'ads' not in tables(legacy / 'radar.sqlite') and (legacy / 'radar.sqlite').read_bytes() == before
    assert not (legacy / 'backups').exists()
    cli = subprocess.run([sys.executable, '-m', 'backend.upgrade', 'check', '--data', str(legacy), '--json'], cwd=ROOT,
                         capture_output=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert cli.returncode == 0 and json.loads(cli.stdout.decode('utf-8'))['ok']


def test_rollback_returns_data_to_the_pre_update_copy_and_deletes_nothing(legacy):
    db.init()
    job = db.rows('SELECT * FROM jobs WHERE profile_id=1')[0]
    catalog.save(job, {'title': 'Появилось после обновления', 'text': '', 'destination': 'https://author.test/new', 'image_url': ''},
                 'https://author.test/', 'https://author.test/', SHOT)
    archive = next((legacy / 'backups').glob('radar-preupdate-*.zip'))
    gc.collect()  # the app leaves idle connections to the garbage collector; a stopped service has none
    result = upgrade.rollback(archive, legacy)
    assert result['counts']['findings'] == 6
    assert 'ads' not in tables(legacy / 'radar.sqlite')
    connection = sqlite3.connect(legacy / 'radar.sqlite')
    assert connection.execute('SELECT COUNT(*) FROM findings').fetchone()[0] == 6
    connection.close()
    # Screenshots come back from the current data (the pre-update copy holds only the database).
    assert (legacy / 'evidence' / SHOT).read_bytes() == b'png-bytes'
    aside = Path(result['previous_data'])
    assert aside.parent == legacy and 'ads' in tables(aside / 'radar.sqlite')
    assert (legacy / 'backups' / archive.name).is_file()
    # The rolled-back data can be upgraded again.
    db.init()
    assert db.rows('SELECT COUNT(*) AS n FROM findings')[0]['n'] == 6


def test_rollback_refuses_while_a_worker_is_alive(legacy):
    db.init()
    db.execute("INSERT INTO workers(id,pid,host,mode,version,started_at,heartbeat_at,max_browsers) VALUES ('w',1,'h','process','x',?,?,1)",
               (db.now(), db.now()))
    archive = next((legacy / 'backups').glob('*.zip'))
    gc.collect()
    with pytest.raises(RuntimeError, match='Worker'):
        upgrade.rollback(archive, legacy)
    assert 'ads' in tables(legacy / 'radar.sqlite') and not list(legacy.glob('.before-rollback-*'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows refuses to move an open file')
def test_rollback_refuses_while_the_database_is_open(legacy):
    db.init()
    archive = next((legacy / 'backups').glob('*.zip'))
    holder = sqlite3.connect(legacy / 'radar.sqlite')
    holder.execute('SELECT 1').fetchone()
    try:
        with pytest.raises(RuntimeError, match='открыта'):
            upgrade.rollback(archive, legacy, force=True)
    finally:
        holder.close()
    assert 'ads' in tables(legacy / 'radar.sqlite')
