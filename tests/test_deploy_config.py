import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

spec = importlib.util.spec_from_file_location('deploy_check', Path(__file__).parents[1] / 'deploy/check_config.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture
def env(tmp_path):
    (tmp_path / 'secrets').mkdir()
    (tmp_path / 'secrets/vault.key').write_bytes(Fernet.generate_key())
    (tmp_path / 'secrets/vault.key').chmod(0o600)
    password = 'pbkdf2_sha256:390000:' + base64.b64encode(b'a' * 16).decode() + ':' + base64.b64encode(b'b' * 32).decode()
    path = tmp_path / '.env'
    path.write_text(f'RADAR_MODE=server\nRADAR_DOMAIN=radar.owner.test\nRADAR_ALLOWED_HOSTS=radar.owner.test\nRADAR_ADMIN_PASSWORD_HASH={password}\nRADAR_VAULT_KEY_FILE=/run/radar/vault.key\nRADAR_VERSION=0.7.0\n', encoding='utf-8')
    return path


def test_valid_configuration_is_not_live_acceptance(env):
    report = preflight.check(env)
    assert report['ready']
    assert next(c for c in report['checks'] if c['check'] == 'live_deployment')['status'] == 'NOT_TESTED'


@pytest.mark.parametrize('line', ['RADAR_AUTH=0', 'RADAR_COOKIE_SECURE=0', 'RADAR_DOMAIN=radar.example.org',
    'RADAR_ALLOWED_HOSTS=*', 'RADAR_MAX_BROWSERS=garbage', 'RADAR_ADMIN_PASSWORD_HASH=plaintext',
    'RADAR_VAULT_KEY_FILE=/run/radar/../../stolen.key', 'RADAR_BACKUP_KEEP=0', 'RADAR_OCR=paid-api'])
def test_rejects_unsafe_or_invalid_configuration(env, line):
    # Replace the variable rather than relying on duplicate-key failure.
    name = line.split('=')[0]
    lines = [v for v in env.read_text().splitlines() if not v.startswith(name + '=')]
    env.write_text('\n'.join(lines + [line]))
    assert not preflight.check(env)['ready']


def test_does_not_leak_secrets_or_execute_commands(env, tmp_path):
    marker = tmp_path / 'executed'
    secret = f'$(touch {marker})-SECRET-STRING'
    with env.open('a') as output:
        output.write(f'PRIVATE_EXTRA={secret}\nRADAR_AUTH=0\nRADAR_AUTH=1\n')
    report = preflight.check(env)
    assert not report['ready']
    assert secret not in json.dumps(report)
    assert not marker.exists()


def test_invalid_key_missing_env_and_runtime_failure(env, monkeypatch):
    (env.parent / 'secrets/vault.key').write_bytes(b'not-a-key')
    assert not preflight.check(env)['ready']
    assert not preflight.check(env.parent / 'missing')['ready']
    monkeypatch.setattr(preflight.shutil, 'which', lambda _: None)
    assert not preflight.check(env, runtime=True)['ready']
