"""Protection of browser-session snapshots and stored passwords.

Formats (detected by prefix, so old data keeps working):
- RADAR-DPAPI-1  — Windows DPAPI (local Windows default; readable only by the same Windows user);
- RADAR-VAULT-1  — Fernet with a key file outside the database (server; RADAR_VAULT_KEY_FILE);
- no prefix      — plain bytes on non-Windows without a key file (legacy 0.4 behaviour, owner-only file).
Create a key:  python -m backend.protection keygen /secure/radar-vault.key
"""
import ctypes
import os
import sys

from . import config

PREFIX = b'RADAR-DPAPI-1\x00'
VAULT_PREFIX = b'RADAR-VAULT-1\x00'


def _crypt(payload, decrypt=False):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                         ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('Не удалось прочитать или защитить сессию Windows. Используйте ту же учётную запись Windows и компьютер.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free(ctypes.cast(target.data, ctypes.c_void_p))


def _vault():
    from cryptography.fernet import Fernet
    try:
        with open(config.VAULT_KEY_FILE, 'rb') as handle:
            return Fernet(handle.read().strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError('Ключ хранилища недоступен или повреждён. Проверьте RADAR_VAULT_KEY_FILE; данные не изменены.') from exc


def protect(payload):
    if config.VAULT_KEY_FILE:
        return VAULT_PREFIX + _vault().encrypt(payload)
    return PREFIX + _crypt(payload) if os.name == 'nt' else payload


def unprotect(payload):
    if payload.startswith(VAULT_PREFIX):
        if not config.VAULT_KEY_FILE:
            raise RuntimeError('Данные зашифрованы ключом хранилища, но RADAR_VAULT_KEY_FILE не задан.')
        from cryptography.fernet import InvalidToken
        try:
            return _vault().decrypt(payload[len(VAULT_PREFIX):])
        except InvalidToken as exc:
            raise RuntimeError('Ключ хранилища не подходит к этим данным. Используйте исходный ключ; данные не изменены.') from exc
    if payload.startswith(PREFIX):
        if os.name != 'nt':
            raise RuntimeError('Сессия защищена Windows DPAPI и не переносится на другую ОС. Войдите в профиль заново.')
        return _crypt(payload[len(PREFIX):], decrypt=True)
    if os.name == 'nt' and not config.VAULT_KEY_FILE:
        raise RuntimeError('Неверный формат защищённой сессии. Откройте профиль вручную для повторного входа.')
    return payload


def rotate(old_key_file, new_key_file, data_dir):
    """Re-encrypt every RADAR-VAULT-1 blob (profile sessions, mailbox password ciphers, the notification bot token) with a new key.

    All blobs are decrypted and re-encrypted in memory first; nothing is written if any blob fails.
    Files are swapped with .bak copies and the database is updated in one transaction; on error
    the copies are restored. Stop API and worker and make a backup before rotating.
    """
    import base64
    import shutil
    import sqlite3
    from pathlib import Path
    from cryptography.fernet import Fernet, InvalidToken
    read = lambda path: Fernet(Path(path).read_bytes().strip())
    old, new = read(old_key_file), read(new_key_file)
    data = Path(data_dir)
    swap = lambda blob: VAULT_PREFIX + new.encrypt(old.decrypt(blob[len(VAULT_PREFIX):]))
    files = {}
    for path in sorted((data / 'profiles').glob('*/radar-state.vault')):
        blob = path.read_bytes()
        if blob.startswith(VAULT_PREFIX):
            try:
                files[path] = swap(blob)
            except InvalidToken as exc:
                raise RuntimeError(f'Старый ключ не подходит к {path.parent.name}/{path.name}. Ничего не изменено.') from exc
    connection = sqlite3.connect(data / 'radar.sqlite', timeout=30)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = []
        if 'mailboxes' in tables:
            for mailbox_id, cipher in connection.execute('SELECT id,password_cipher FROM mailboxes'):
                if cipher and bytes(cipher).startswith(VAULT_PREFIX):
                    try:
                        rows.append((swap(bytes(cipher)), mailbox_id))
                    except InvalidToken as exc:
                        raise RuntimeError(f'Старый ключ не подходит к паролю ящика #{mailbox_id}. Ничего не изменено.') from exc
        settings = []
        permanent = []
        for table, column in (('permanent_mail_config', 'key_cipher'), ('permanent_mail_accounts', 'secret_cipher')):
            if table in tables:
                for item_id, cipher in connection.execute(f'SELECT id,{column} FROM {table}'):
                    if cipher and bytes(cipher).startswith(VAULT_PREFIX):
                        try:
                            permanent.append((table, column, swap(bytes(cipher)), item_id))
                        except InvalidToken as exc:
                            raise RuntimeError(f'Старый ключ не подходит к постоянной почте #{item_id}. Ничего не изменено.') from exc
        if 'settings' in tables:
            for key, value in connection.execute("SELECT key,value FROM settings WHERE key='notify.telegram_token'"):
                blob = base64.b64decode(value)
                if blob.startswith(VAULT_PREFIX):
                    try:
                        settings.append((base64.b64encode(swap(blob)).decode(), key))
                    except InvalidToken as exc:
                        raise RuntimeError('Старый ключ не подходит к токену уведомлений. Ничего не изменено.') from exc
        backups = []
        try:
            for path, blob in files.items():
                backup = path.with_suffix('.vault.bak')
                shutil.copy2(path, backup)
                backups.append((path, backup))
                temporary = path.with_suffix('.vault.tmp')
                temporary.write_bytes(blob)
                temporary.replace(path)
            with connection:
                connection.executemany('UPDATE mailboxes SET password_cipher=? WHERE id=?', rows)
                connection.executemany('UPDATE settings SET value=? WHERE key=?', settings)
                for table, column, cipher, item_id in permanent:
                    connection.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (cipher, item_id))
        except BaseException:
            for path, backup in backups:
                backup.replace(path)
            raise
        for _, backup in backups:
            backup.unlink()
    finally:
        connection.close()
    return {'sessions': len(files), 'mailboxes': len(rows) + sum(t == 'permanent_mail_accounts' for t, _, _, _ in permanent),
            'settings': len(settings) + sum(t == 'permanent_mail_config' for t, _, _, _ in permanent)}


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == 'rotate':
        result = rotate(sys.argv[2], sys.argv[3], sys.argv[4])
        print(f'Ключ заменён: сессий {result["sessions"]}, паролей ящиков {result["mailboxes"]}. '
              'Укажите новый файл в RADAR_VAULT_KEY_FILE и запустите службы. Старый ключ храните до проверки.')
        sys.exit(0)
    if len(sys.argv) != 3 or sys.argv[1] != 'keygen':
        print('Использование: python -m backend.protection keygen <путь к файлу ключа>\n'
              '               python -m backend.protection rotate <старый ключ> <новый ключ> <каталог data>')
        sys.exit(2)
    from cryptography.fernet import Fernet
    path = sys.argv[2]
    if os.path.exists(path):
        print('Файл уже существует: ключ не перезаписан.')
        sys.exit(1)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(Fernet.generate_key())
    print(f'Ключ создан: {path}. Храните копию отдельно от резервных копий базы.')
