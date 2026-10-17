"""Owner pilot in real Chromium with synthetic AT pages and all network intercepted."""
import hashlib
import json
import sqlite3
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from backend import browser, config, db, main, onboarding, runs
from tools.history_sites import extract

EMAIL = 'owner@example.test'
PASSWORD = 'FIXTURE-PRIVATE-PASSWORD-123'
FORM = {'action': 'register', 'email': EMAIL, 'display_name': 'Проверка', 'password': PASSWORD}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    monkeypatch.setattr(config, 'SERVER', False)
    monkeypatch.setattr(config, 'AUTH', False)
    with TestClient(main.app) as instance:
        yield instance


@pytest.fixture
def site(monkeypatch):
    observed = {'posts': [], 'email': EMAIL, 'captcha': False, 'live_markup': False, 'unconfirmed': False}

    async def handler(route):
        request = route.request
        path = request.url.split('author.today', 1)[-1].split('?', 1)[0].lower()
        if not request.url.startswith(onboarding.ORIGIN + '/'):
            await route.abort(); return
        if request.method == 'POST':
            observed['posts'].append((path, parse_qs(request.post_data or '')))
            headers = {'Content-Type': 'text/html; charset=utf-8'}
            if path == '/account/login':
                headers['Set-Cookie'] = 'fixture_auth=owner; Path=/; Secure; HttpOnly; SameSite=Lax'
            await route.fulfill(status=200, headers=headers, body='<h1>Фикстура: проверьте результат и почту</h1>')
        elif path == '/account/register':
            await route.fulfill(content_type='text/html', body='''<h1>Фикстура регистрации AT</h1>
                <form action="/account/register" method="post">
                <input name="FIO"><input name="Email" type="email"><input name="Password" type="password">
                <input name="TermsAgree" type="checkbox"><input name="MarketingNewsletters" type="checkbox" checked>
                <button type="submit">Регистрация</button></form>''')
        elif path == '/account/login':
            await route.fulfill(content_type='text/html', body='''<h1>Фикстура входа AT</h1>
                <form id="loginForm" action="/account/login" method="post">
                <input name="Login"><input name="Password" type="password"><input name="RememberMe" type="checkbox">
                <button type="submit">Войти</button></form>
                <form id="logoffForm" hidden></form><a href="/u/admin" hidden>Шаблон гостя</a>''')
        elif path == '/account/settings':
            if observed['captcha']:
                await route.fulfill(status=403, body='<h1>Проверка владельца</h1>')
            elif 'fixture_auth=owner' in request.headers.get('cookie', ''):
                if observed['live_markup']:
                    body = f'''<h1>Настройки фикстуры</h1><div class="settings-row-middle">
                        <div class="row-title">Электронная почта</div><div class="row-content"><span id="masked">ow---@example.test
                        <a onclick="document.getElementById('masked').style.display='none';document.getElementById('revealed').style.display=''">показать</a></span>
                        <span id="revealed" style="display:none">{escape(observed['email'])}</span></div></div>'''
                    if observed['unconfirmed']:
                        body += '<a href="/account/security/email">Подтвердить</a>'
                else:
                    body = f'<h1>Настройки фикстуры</h1><input name="Email" type="email" value="{escape(observed["email"])}">'
                await route.fulfill(content_type='text/html; charset=utf-8', body=body)
            else:
                await route.fulfill(status=302, headers={'Location': '/account/login'})
        else:
            await route.fulfill(status=404, body='Fixture missing')

    original = browser.open_context

    async def open_fixture(pw, profile_id, headless):
        context = await original(pw, profile_id, headless=True)
        await context.route('**/*', handler)
        return context

    monkeypatch.setattr(main, 'open_context', open_fixture)
    monkeypatch.setattr(onboarding, 'open_context', open_fixture)
    return observed


def pilot(client):
    response = client.post('/api/onboarding/at/pilot')
    assert response.status_code == 201
    return response.json()['account_id']


def test_pilot_is_local_atomic_and_repeatable(client):
    aid = pilot(client)
    assert client.post('/api/onboarding/at/pilot').json() == {'account_id': aid, 'created': False}
    assert len(db.rows('SELECT * FROM profiles')) == len(db.rows('SELECT * FROM accounts')) == 1
    assert not main.open_profiles
    assert db.rows('SELECT status FROM accounts')[0]['status'] == 'planned'
    onboarding.record(aid, 'register', 'awaiting_owner', 'fixture', EMAIL)
    db.init(); db.init()
    assert client.get(f'/api/onboarding/at/{aid}').json()['status'] == 'awaiting_owner'
    assert client.get('/api/onboarding/at/999').status_code == 404
    with db.connect() as c:
        c.execute('DELETE FROM onboarding_at'); c.execute('DELETE FROM accounts')
    assert client.post('/api/onboarding/at/pilot').status_code == 409
    assert len(db.rows('SELECT * FROM services')) == 1


def test_pilot_info_is_reachable_before_the_frontend_mount(client):
    from tools.at_onboarding import application
    with TestClient(application()) as preview:
        response = preview.get('/api/pilot/info')
        assert response.status_code == 200
        assert response.json()['purpose'] == 'at-owner-onboarding'
        assert response.json()['data'] == str(db.DATA.resolve())
        assert response.headers['cache-control'] == 'no-store'
        assert preview.get('/api/health').json()['version'] == config.VERSION


def test_private_validation_and_foreign_service_rejected(client):
    aid = pilot(client)
    for extra in [{'email': 'bad'}, {'password': PASSWORD * 30}, {'display_name': ' '}, {'submit': True}]:
        response = client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, **extra})
        assert response.status_code == 422
        assert PASSWORD not in response.text and 'input' not in response.json()['detail'][0]
    sid = db.rows('SELECT service_id FROM accounts')[0]['service_id']
    for url in ['https://author.today.evil.test/register', 'http://author.today/account/register', 'https://x@author.today/account/register', 'https://author.today:444/account/register']:
        db.execute('UPDATE services SET registration_url=? WHERE id=?', (url, sid))
        assert client.get(f'/api/onboarding/at/{aid}').status_code == 400
    assert not main.open_profiles


def test_fill_then_submit_is_not_a_verified_account(client, site):
    aid = pilot(client)
    response = client.post(f'/api/onboarding/at/{aid}/form', json=FORM)
    assert response.json()['status'] == 'form_ready', response.text
    assert not site['posts']

    async def fields():
        page = main.profile_contexts[1].pages[0]
        return await page.locator('form input').evaluate_all('(els)=>Object.fromEntries(els.map(e=>[e.name,e.type === "checkbox" ? e.checked : e.value]))')
    filled = client.portal.call(fields)
    assert filled == {'FIO': FORM['display_name'], 'Email': EMAIL, 'Password': PASSWORD, 'TermsAgree': False, 'MarketingNewsletters': False}
    sent = client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'submit': True, 'accept_terms': True})
    assert sent.json()['status'] == 'awaiting_owner', sent.text
    assert len(site['posts']) == 1
    assert 'MarketingNewsletters' not in site['posts'][0][1]
    assert db.rows('SELECT * FROM accounts')[0]['confirmed_at'] is None
    assert client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'submit': True, 'accept_terms': True}).status_code == 409
    # A fill-only operation and restart cannot clear the persistent single-submit guard.
    client.post(f'/api/onboarding/at/{aid}/form', json=FORM)
    db.init()
    assert client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'submit': True, 'accept_terms': True}).status_code == 409
    checked = client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True})
    assert checked.json()['status'] == 'signed_out'
    assert db.rows('SELECT * FROM accounts')[0]['confirmed_at'] is None
    assert PASSWORD not in json.dumps(db.rows('SELECT * FROM onboarding_at') + db.rows('SELECT * FROM logs') + db.rows('SELECT * FROM audit'))


def test_login_and_cold_session_prove_same_account_and_isolation(client, site):
    aid = pilot(client)
    submitted = client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login', 'submit': True})
    assert submitted.json()['status'] == 'awaiting_owner', submitted.text
    assert client.post(f'/api/onboarding/at/{aid}/check', json={}).json()['status'] == 'signed_in'
    cold = client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True})
    assert cold.json()['status'] == 'session_verified', cold.text
    assert not main.open_profiles and not runs.leased_profiles()
    assert db.rows('SELECT * FROM accounts')[0]['confirmed_at']
    assert client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login', 'email': 'other@example.test'}).status_code == 409
    second = client.post('/api/profiles', json={'name': 'Другой профиль'}).json()['id']
    sid = db.rows('SELECT service_id FROM accounts')[0]['service_id']
    client.post('/api/accounts/assign', json={'profile_ids': [second], 'service_ids': [sid]})
    other = db.rows('SELECT id FROM accounts WHERE profile_id=?', (second,))[0]['id']
    client.post(f'/api/accounts/{other}', json={'status': 'in_progress', 'identifier': EMAIL})
    assert client.post(f'/api/onboarding/at/{other}/check', json={'reopen': True}).json()['status'] == 'signed_out'
    site['email'] = 'wrong@example.test'
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).json()['status'] == 'needs_owner'
    site['email'], site['captcha'] = EMAIL, True
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).json()['status'] == 'needs_owner'


def test_busy_server_and_missing_identity_never_open_a_second_browser(client, monkeypatch):
    aid = pilot(client)
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).status_code == 409
    monkeypatch.setattr(config, 'SERVER', True)
    # Generic visible windows remain disabled on the server; AT has its own remote flow.
    assert client.post('/api/profiles/1/open', json={'url': onboarding.LOGIN}).status_code == 409
    monkeypatch.setattr(config, 'SERVER', False)
    client.post(f'/api/accounts/{aid}', json={'status': 'in_progress', 'identifier': EMAIL})
    assert runs.acquire_lease(1, 'fixture-worker', 'collect', 60)
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).status_code == 409
    runs.release_lease(1, 'fixture-worker')


def test_real_settings_markup_reveal_and_unconfirmed_email_are_not_a_complete_login(client, site):
    aid = pilot(client)
    site['live_markup'], site['unconfirmed'] = True, True
    client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login', 'submit': True})
    response = client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True})
    assert response.json()['status'] == 'email_unconfirmed', response.text
    assert db.rows('SELECT * FROM accounts')[0]['confirmed_at'] is None
    site['unconfirmed'] = False
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).json()['status'] == 'session_verified'
    site['email'] = 'different@example.test'
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).json()['status'] == 'needs_owner'


def test_history_export_is_domain_only_read_only_and_counts_actual_visits(tmp_path):
    source = tmp_path / 'history'
    with sqlite3.connect(source) as c:
        c.executescript('CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT); CREATE TABLE visits(url INTEGER);')
        c.executemany('INSERT INTO urls VALUES (?,?)', [(1, 'https://author.today/work/1?access=PRIVATE'),
            (2, 'https://www.litnet.com/u/private'), (3, 'https://author.today.evil.test/'),
            (4, 'file:///C:/private'), (5, 'https://[broken'), (6, 'https://mail.example.test/inbox')])
        c.executemany('INSERT INTO visits VALUES (?)', [(1,), (1,), (2,), (3,)])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = extract(source)
    assert result == [dict(domain='author.today', url_records=1, visit_records=2, status='candidate_not_approved'),
        dict(domain='litnet.com', url_records=1, visit_records=1, status='candidate_not_approved')]
    assert 'PRIVATE' not in json.dumps(result)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_lost_login_removes_confirmation_and_public_at_state_matches(client, site):
    aid = pilot(client)
    client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login', 'submit': True})
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).json()['status'] == 'session_verified'
    # Expire the stored login using the existing fixture's response, without a real website.
    site['email'] = 'another@example.test'
    assert client.post(f'/api/onboarding/at/{aid}/check', json={'reopen': True}).json()['status'] == 'needs_owner'
    public = client.get('/api/state').json()
    assert public['accounts'][0]['status'] == 'in_progress'
    assert public['profiles'][0]['at_session']['status'] == 'needs_owner'
    assert public['profiles'][0]['at_session']['checked_at']


def test_filling_and_current_check_preserve_existing_owner_tab(client, site):
    aid = pilot(client)
    client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login', 'submit': True})

    async def owner_tab():
        page = main.profile_contexts[1].pages[0]
        await page.goto(onboarding.ORIGIN + '/account/login?owner-tab=1')
        return page.url
    original = client.portal.call(owner_tab)
    client.post(f'/api/onboarding/at/{aid}/form', json={**FORM, 'action': 'login'})

    async def first_url():
        return main.profile_contexts[1].pages[0].url
    assert client.portal.call(first_url) == original
    client.post(f'/api/onboarding/at/{aid}/check', json={})
    assert client.portal.call(first_url) == original
