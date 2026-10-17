"""Stage 18: safe updates of an existing installation.

    python -m backend.upgrade status   [--data DIR]
    python -m backend.upgrade check    [--data DIR] [--json]      dry-run of the migrations on a copy; the data is not written
    python -m backend.upgrade backup   [--data DIR] [--to DIR]    verified backup of the database (before a manual update)
    python -m backend.upgrade rollback <backup.zip> [--data DIR] [--force]

Automatic part: the first start of a new version on existing data (API or worker, whichever is first)
makes a verified database backup before any migration. Migrations of 0.6.x-0.7.x only add tables, columns
and indexes, so the previous version keeps working on migrated data; a rollback to the pre-update copy is
needed only if the data itself must return to the moment of the update.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

KEEP_PREUPDATE = int(os.environ.get('RADAR_PREUPDATE_KEEP', '5') or 5)


def backup_dir(data_dir):
    return Path(os.environ.get('RADAR_BACKUP_DIR') or Path(data_dir) / 'backups')


@contextmanager
def migration_lock(data_dir, timeout=600):
    """Cross-process lock around migrations: the API and the worker may start at the same moment."""
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    handle = open(data / '.migrate.lock', 'a+b')
    deadline = time.monotonic() + timeout
    try:
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Другой процесс слишком долго обновляет базу. Проверьте, не завис ли он.')
                    time.sleep(0.2)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def reader(database, timeout=30):
    """A connection that cannot write. Not `mode=ro`: on Windows a read-only open of a WAL database that another
    process (API or worker) holds open fails with «disk I/O error»; query_only keeps the same guarantee."""
    connection = sqlite3.connect(database, timeout=timeout)
    connection.execute('PRAGMA query_only=ON')
    return connection


def _read_version(database):
    """(has_data, stored_version) without creating anything. A process that is closing the database at this
    moment (a worker restarted right after a stop) can make the first attempt fail: retry briefly."""
    for attempt in range(5):
        try:
            return _probe_version(database)
        except sqlite3.OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.5)


def _probe_version(database):
    if not database.is_file() or database.stat().st_size == 0:
        return False, None
    connection = reader(database)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'jobs' not in tables:
            return False, None
        version = None
        if 'settings' in tables:
            row = connection.execute("SELECT value FROM settings WHERE key='app_version'").fetchone()
            version = row[0] if row else None
        return True, version
    finally:
        connection.close()


def before_migrations(data_dir):
    """Backup once per version change. Fresh installations and same-version restarts do nothing."""
    data = Path(data_dir)
    has_data, version = _read_version(data / 'radar.sqlite')
    if not has_data or version == config.VERSION or os.environ.get('RADAR_PREUPDATE_BACKUP', '1') == '0':
        return None
    from . import backup
    old = version or 'legacy'
    path, manifest = backup.create(backup_dir(data), data, keep=KEEP_PREUPDATE, prefix=f'radar-preupdate-{old}-to-{config.VERSION}-',
                                   files=False, note={'kind': 'pre-update', 'from': old, 'to': config.VERSION})
    say(f'Книжный радар: перед обновлением {old} -> {config.VERSION} создана проверенная копия базы {path}')
    return path


def say(text):
    # A service console may use a legacy code page: never let a message stop the start-up.
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode(), flush=True)


def after_migrations(data_dir):
    connection = sqlite3.connect(Path(data_dir) / 'radar.sqlite', timeout=30)
    try:
        with connection:
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('app_version',?)", (config.VERSION,))
    finally:
        connection.close()


# ---------- dry run ----------

def describe(database):
    connection = sqlite3.connect(database)
    try:
        tables = sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        return {table: {'rows': connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
                        'columns': [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]} for table in tables}
    finally:
        connection.close()


def _snapshot(source, target):
    origin = reader(source)
    copy = sqlite3.connect(target)
    try:
        origin.backup(copy)
    finally:
        copy.close()
        origin.close()


def check(data_dir):
    """Run every migration of this version on a copy of the database. The original is opened read-only."""
    from . import db
    data = Path(data_dir)
    source = data / 'radar.sqlite'
    if not source.is_file():
        raise RuntimeError(f'База не найдена: {source}')
    before = describe_readonly(source)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temporary:
        copy_dir = Path(temporary)
        _snapshot(source, copy_dir / 'radar.sqlite')
        snapshot_seconds = time.perf_counter() - started
        previous = db.DATA
        db.DATA = copy_dir
        try:
            migrate_started = time.perf_counter()
            db._migrate()
            migrate_seconds = time.perf_counter() - migrate_started
        finally:
            db.DATA = previous
            import gc
            gc.collect()  # the migration leaves idle connections to the copy; Windows cannot delete an open file
        after = describe(copy_dir / 'radar.sqlite')
        connection = sqlite3.connect(copy_dir / 'radar.sqlite')
        try:
            integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
            foreign = connection.execute('PRAGMA foreign_key_check').fetchall()
        finally:
            connection.close()
    unchanged = describe_readonly(source) == before
    added_tables = sorted(set(after) - set(before))
    added_columns = {t: [c for c in after[t]['columns'] if c not in before[t]['columns']] for t in before if t in after}
    lost = [t for t in before if t not in after or after[t]['rows'] < before[t]['rows']]
    return {'version': config.VERSION, 'stored_version': _read_version(source)[1], 'integrity': integrity, 'foreign_key_errors': len(foreign),
            'added_tables': added_tables, 'added_columns': {t: c for t, c in added_columns.items() if c},
            'rows_before': {t: v['rows'] for t, v in before.items()}, 'rows_after': {t: v['rows'] for t, v in after.items()},
            'tables_with_fewer_rows': lost, 'original_unchanged': unchanged,
            'seconds': {'snapshot': round(snapshot_seconds, 2), 'migrate': round(migrate_seconds, 2)},
            'ok': integrity == 'ok' and not foreign and not lost and unchanged}


def describe_readonly(database):
    temporary = tempfile.mkdtemp()
    try:
        copy = Path(temporary) / 'probe.sqlite'
        _snapshot(database, copy)
        return describe(copy)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


# ---------- rollback ----------

def _alive_worker(database, stale_seconds):
    try:
        connection = reader(database, timeout=5)
        try:
            rows = connection.execute('SELECT heartbeat_at FROM workers WHERE stopped_at IS NULL').fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    now = datetime.now(timezone.utc)
    return any((now - datetime.fromisoformat(row[0])).total_seconds() <= stale_seconds for row in rows if row[0])


def rollback(archive, data_dir, force=False):
    """Return the data directory to a backup. Nothing is deleted: the current content moves to
    .before-rollback-<time>/ inside the same directory (works for a Docker volume mount point too)."""
    from . import backup
    data = Path(data_dir)
    manifest = backup.verify(archive)
    if not force and _alive_worker(data / 'radar.sqlite', config.STALE_SECONDS):
        raise RuntimeError('Worker ещё отправляет сигналы. Остановите API и worker (docker compose stop api worker), затем повторите.')
    database = data / 'radar.sqlite'
    if database.exists():
        # Windows cannot move an open database; probe before anything is touched.
        probe = data / 'radar.sqlite.rollback-probe'
        try:
            os.replace(database, probe)
            os.replace(probe, database)
        except OSError as exc:
            raise RuntimeError('База открыта другим процессом. Остановите API и worker, затем повторите. Ничего не изменено.') from exc
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    staging, aside = data / f'.rollback-staging-{stamp}', data / f'.before-rollback-{stamp}'
    started = time.perf_counter()
    backup.restore(archive, staging)
    aside.mkdir()
    keep = {staging.name, aside.name, '.migrate.lock'}
    moved, placed = [], []
    try:
        for entry in list(data.iterdir()):
            if entry.name in keep or entry.name.startswith(('.before-rollback-', '.rollback-staging-')) or entry.resolve() == backup_dir(data).resolve():
                continue
            shutil.move(str(entry), str(aside / entry.name))
            moved.append(entry.name)
        for entry in list(staging.iterdir()):
            shutil.move(str(entry), str(data / entry.name))
            placed.append(entry.name)
    except BaseException:
        # Put the current data back exactly as it was; the restored pieces are only copies from the archive.
        for name in placed:
            target = data / name
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        for name in moved:
            shutil.move(str(aside / name), str(data / name))
        shutil.rmtree(staging, ignore_errors=True)
        aside.rmdir()
        raise
    staging.rmdir()
    if not manifest.get('files_included', True):
        # A pre-update copy holds only the database: screenshots and profile sessions are copied back from the current data.
        for name in ('evidence', 'profiles'):
            if (aside / name).is_dir():
                shutil.rmtree(data / name, ignore_errors=True)
                shutil.copytree(aside / name, data / name)
    return {'restored': str(archive), 'previous_data': str(aside), 'counts': manifest['counts'], 'seconds': round(time.perf_counter() - started, 2),
            'version_in_copy': manifest.get('note', {}).get('from')}


def main(argv):
    args = list(argv)
    def option(name, default=None):
        if name in args:
            index = args.index(name)
            value = args[index + 1]
            del args[index:index + 2]
            return value
        return default
    data = Path(option('--data') or os.environ.get('RADAR_DATA_DIR') or Path(__file__).resolve().parents[1] / 'data')
    as_json = '--json' in args and not args.remove('--json')
    force = '--force' in args and not args.remove('--force')
    target = option('--to')
    if not args:
        say(__doc__)
        return 2
    command = args[0]
    if command == 'status':
        has_data, version = _read_version(data / 'radar.sqlite')
        say(f'Данные: {data} · база {"есть" if has_data else "не найдена"} · версия данных {version or "до 0.7.0"} · версия программы {config.VERSION}')
        return 0
    if command == 'check':
        report = check(data)
        if as_json:
            say(json.dumps(report, indent=1))
        else:
            say(f'Проверка миграций {report["stored_version"] or "до 0.7.0"} -> {report["version"]} на копии: {"ГОТОВО" if report["ok"] else "ЕСТЬ ПРОБЛЕМЫ"}')
            say(f'  целостность: {report["integrity"]}, ошибок внешних ключей: {report["foreign_key_errors"]}, исходная база не изменена: {report["original_unchanged"]}')
            say(f'  новые таблицы: {", ".join(report["added_tables"]) or "нет"}')
            for table, columns in report['added_columns'].items():
                say(f'  {table}: + {", ".join(columns)}')
            say(f'  время: копия {report["seconds"]["snapshot"]} с, миграция {report["seconds"]["migrate"]} с')
        return 0 if report['ok'] else 1
    if command == 'backup':
        from . import backup
        started = time.perf_counter()
        path, manifest = backup.create(Path(target) if target else backup_dir(data), data, keep=KEEP_PREUPDATE,
                                       prefix=f'radar-preupdate-{config.VERSION}-manual-', files=False, note={'kind': 'pre-update', 'from': config.VERSION})
        say(f'Копия базы создана и проверена за {time.perf_counter() - started:.1f} с: {path} · {manifest["counts"]}')
        return 0
    if command == 'rollback' and len(args) == 2:
        result = rollback(args[1], data, force=force)
        say(f'Данные возвращены к копии {result["restored"]} за {result["seconds"]} с · {result["counts"]}')
        say(f'Прежнее содержимое сохранено в {result["previous_data"]}. Установите прежнюю версию программы и запустите службы.')
        return 0
    say(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
