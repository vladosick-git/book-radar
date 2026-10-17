"""Read-only preflight for the supplied Compose deployment. Never prints secrets or executes .env."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


def load_env(path):
    values, issues = {}, []
    for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            issues.append(f'Строка {number}: ожидается NAME=value.'); continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
            issues.append(f'Строка {number}: неверное имя переменной.'); continue
        if name in values:
            issues.append(f'Строка {number}: повтор переменной {name}.')
        if value.startswith(('"', "'")):
            quote = value[0]
            if len(value) < 2 or not value.endswith(quote):
                issues.append(f'Строка {number}: незакрытые кавычки.'); continue
            value = value[1:-1]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        if '$' in value or '`' in value:
            issues.append(f'Строка {number}: подстановка переменных не поддерживается проверкой; задайте буквальное значение.')
        values[name] = value
    return values, issues


def password_hash_valid(value):
    try:
        scheme, rounds, salt, digest = value.split(':')
        return (scheme == 'pbkdf2_sha256' and 100_000 <= int(rounds) <= 2_000_000
                and len(base64.b64decode(salt, validate=True)) == 16
                and len(base64.b64decode(digest, validate=True)) == 32)
    except (ValueError, TypeError):
        return False


def check(path, runtime=False):
    path = Path(path).resolve()
    checks = []
    def result(name, ok, detail, failure='FAIL'):
        checks.append({'check': name, 'status': 'PASS' if ok else failure, 'detail': detail})
    if not path.is_file():
        result('env_file', False, 'Файл .env не найден. Скопируйте .env.example и заполните его.')
        return {'scope': 'deploy-preflight', 'ready': False, 'checks': checks}
    try:
        values, issues = load_env(path)
    except (OSError, UnicodeError):
        result('env_file', False, 'Не удалось прочитать .env; значения не выводятся.')
        return {'scope': 'deploy-preflight', 'ready': False, 'checks': checks}
    result('env_syntax', not issues, '; '.join(issues) or 'Файл прочитан без выполнения команд.')
    on = lambda name, default='1': values.get(name, default).lower() in ('1', 'true', 'yes', 'on')
    result('server_mode', values.get('RADAR_MODE') == 'server', 'Нужен RADAR_MODE=server.')
    result('authentication', on('RADAR_AUTH'), 'Вход в серверную панель должен быть включён.')
    result('password_hash', password_hash_valid(values.get('RADAR_ADMIN_PASSWORD_HASH', '')),
           'Используйте хеш, созданный python -m backend.auth hash; значение не выводится.')
    result('secure_cookie', on('RADAR_COOKIE_SECURE'), 'Для публичной панели нужен RADAR_COOKIE_SECURE=1 и HTTPS.')
    domain = values.get('RADAR_DOMAIN', '').lower().rstrip('.')
    try:
        ipaddress.ip_address(domain); domain_ok = False
    except ValueError:
        domain_ok = bool(re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain))
    domain_ok = domain_ok and not (domain == 'example.org' or domain.endswith('.example.org'))
    result('domain', domain_ok, 'Этот Compose рассчитан на собственное DNS-имя и HTTPS; адрес example.org — заглушка.')
    hosts = [v.strip().lower().rstrip('.') for v in values.get('RADAR_ALLOWED_HOSTS', '').split(',') if v.strip()]
    result('allowed_hosts', domain_ok and domain in hosts and all('*' not in h for h in hosts),
           'Добавьте домен панели в RADAR_ALLOWED_HOSTS; wildcard не допускается.')
    key_setting = values.get('RADAR_VAULT_KEY_FILE', '')
    key_name = key_setting.removeprefix('/run/radar/')
    key_path = path.parent / 'secrets' / key_name
    key_ok = key_setting.startswith('/run/radar/') and bool(key_name) and '/' not in key_name and '\\' not in key_name and key_name not in ('.', '..')
    try:
        key_data = key_path.read_bytes().strip() if key_ok else b''
        key_ok = key_ok and len(key_data) == 44 and len(base64.b64decode(key_data, altchars=b'-_', validate=True)) == 32
    except (OSError, ValueError):
        key_ok = False
    result('vault_key', key_ok, 'Нужен корректный Fernet-ключ в deploy/secrets, доступный контейнеру; содержимое не выводится.')
    if key_ok and os.name != 'nt':
        result('vault_permissions', not (key_path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)),
               'Ключ должен иметь права 600 или строже. Владелец файла — UID контейнера.')
    elif key_ok:
        result('vault_permissions_linux', False, 'Права и UID ключа нужно проверить на Linux VPS.', 'NOT_TESTED')
    try:
        browsers = int(values.get('RADAR_MAX_BROWSERS', '1'))
    except ValueError:
        browsers = 0
    result('browser_limit', 1 <= browsers <= 8, 'Лимит Chromium должен быть целым числом 1–8.')
    if browsers > 2:
        result('pilot_capacity', False, 'Для пилота начните с 1–2 браузеров; больший лимит требует замера ресурсов.', 'WARN')
    result('ocr_mode', values.get('RADAR_OCR', 'off') in ('off', 'tesseract'), 'OCR: off или tesseract.')
    version = values.get('RADAR_VERSION', '')
    result('image_tag', bool(re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}', version)), 'Задайте явный тег образа RADAR_VERSION.')
    for key, default, maximum in [('RADAR_BACKUP_KEEP', '7', 365), ('RADAR_PREUPDATE_KEEP', '5', 100)]:
        try:
            good = 1 <= int(values.get(key, default)) <= maximum
        except ValueError:
            good = False
        result(key.lower(), good, f'{key}: целое число 1–{maximum}.')
    if runtime:
        docker = shutil.which('docker')
        result('docker_cli', bool(docker), 'На сервере нужны Docker и Compose.')
        if docker:
            # Do not print Compose output: it can contain the password hash and other configuration secrets.
            for name, args in [('docker_engine', ['info', '--format', '{{.ServerVersion}}']),
                               ('compose_config', ['compose', '--env-file', str(path), '-f', str(path.parent / 'docker-compose.yml'), 'config', '--quiet'])]:
                try:
                    completed = subprocess.run([docker, *args], capture_output=True, timeout=30, cwd=path.parent)
                    result(name, completed.returncode == 0, 'Проверка выполнена; вывод Docker скрыт. При ошибке проверьте Docker на сервере.')
                except (OSError, subprocess.TimeoutExpired):
                    result(name, False, 'Проверка Docker недоступна или истекло время ожидания.')
    else:
        result('docker_runtime', False, 'Запустите с --runtime на VPS: Docker/Compose здесь не проверены.', 'NOT_TESTED')
    result('live_deployment', False, 'DNS, сертификат, доступ извне, ресурсы VPS и длительная работа проверяются после развёртывания.', 'NOT_TESTED')
    return {'scope': 'deploy-preflight', 'ready': not any(c['status'] == 'FAIL' for c in checks),
            'ready_means': 'Только конфигурация; не подтверждение живого развёртывания.', 'checks': checks}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=Path(__file__).with_name('.env'))
    parser.add_argument('--runtime', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    report = check(args.env, args.runtime)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for entry in report['checks']:
            print(f"{entry['status']} {entry['check']}: {entry['detail']}")
        print('Конфигурация готова.' if report['ready'] else 'Конфигурация требует исправления.')
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
