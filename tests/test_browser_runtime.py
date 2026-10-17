import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend import browser, config, db
from backend.ad_network import ResourceFailures, AdNetworkCertificateError
from backend.errors import error_code


def test_resource_certificate_failure_is_host_only_and_not_an_empty_slot():
    monitor = ResourceFailures()
    monitor.observe(SimpleNamespace(url='https://mdd.yandex.net/x?private=DO_NOT_ECHO', failure='net::ERR_CERT_AUTHORITY_INVALID'))
    monitor.observe(SimpleNamespace(url='https://mdd.yandex.net.evil.example/', failure='net::ERR_CERT_AUTHORITY_INVALID'))
    monitor.observe(SimpleNamespace(url='https://an.yandex.ru/', failure='net::ERR_TIMED_OUT'))
    with pytest.raises(AdNetworkCertificateError) as err:
        monitor.check()
    assert error_code(err.value) == 'AD_CERTIFICATE'
    assert 'mdd.yandex.net' in str(err.value)
    assert 'DO_NOT_ECHO' not in str(err.value) and 'evil.example' not in str(err.value)
    monitor.reset()
    monitor.check()


@pytest.mark.parametrize('override', [False, True])
def test_browser_override_keeps_private_profile_and_tls_validation(tmp_path, monkeypatch, override):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'isolated')
    executable = tmp_path / 'vendor-browser.exe'
    executable.write_bytes(b'not executed in this unit test')
    monkeypatch.setattr(config, 'BROWSER_EXECUTABLE', str(executable) if override else '')
    context = SimpleNamespace()
    launch = AsyncMock(return_value=context)
    pw = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    assert asyncio.run(browser.open_context(pw, 7, True)) is context
    args, kwargs = launch.call_args
    assert args == (str(db.DATA / 'profiles' / '7'),)
    assert kwargs['headless'] is True
    assert 'ignore_https_errors' not in kwargs and 'args' not in kwargs
    assert kwargs.get('executable_path') == (str(executable) if override else None)


def test_missing_configured_browser_does_not_silently_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'BROWSER_EXECUTABLE', str(tmp_path / 'missing.exe'))
    launch = AsyncMock()
    pw = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    with pytest.raises(RuntimeError, match='RADAR_BROWSER_EXECUTABLE'):
        asyncio.run(browser.open_context(pw, 1, True))
    launch.assert_not_called()
