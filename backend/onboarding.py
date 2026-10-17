"""One owner-operated AT pilot. Passwords exist only during a form operation."""
import asyncio
import re
import uuid
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from typing import Literal
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from playwright.async_api import async_playwright

from . import audit, config, db, preparation, runs
from .browser import open_context, close_context, save_context

router = APIRouter(prefix='/api/onboarding/at')
ORIGIN = 'https://author.today'
REGISTER = ORIGIN + '/account/register'
LOGIN = ORIGIN + '/account/login'
SETTINGS = ORIGIN + '/account/settings'
_locks = {}


def migrate(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS onboarding_at (
        account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
        stage TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'not_started',
        message TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, checked_at TEXT, registration_submitted_at TEXT)''')


def is_at(value):
    try:
        u = urlsplit(value)
        return u.scheme == 'https' and u.hostname == 'author.today' and u.port in (None, 443) and not u.username and not u.password
    except ValueError:
        return False


def account_for(account_id):
    account = preparation.get_account(account_id)
    if account['service_kind'] != 'site' or not is_at(account['registration_url']):
        raise HTTPException(400, 'Этот сценарий предназначен для площадки Author.Today.')
    return account


def state(account_id):
    from . import main
    account_for(account_id)
    rows = db.rows('SELECT * FROM onboarding_at WHERE account_id=?', (account_id,))
    result = rows[0] if rows else {'account_id': account_id, 'stage': '', 'status': 'not_started',
        'email': '', 'message': 'Начните с регистрации или входа в существующий аккаунт.',
        'updated_at': None, 'checked_at': None, 'registration_submitted_at': None}
    account = account_for(account_id)
    result['remote_available'] = config.SERVER
    result['remote_open'] = config.SERVER and account['profile_id'] in main.profile_contexts
    return result


def record(account_id, stage, status, message, email='', checked=False):
    db.execute('''INSERT INTO onboarding_at(account_id,stage,status,message,email,updated_at,checked_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
        stage=excluded.stage,status=excluded.status,message=excluded.message,email=excluded.email,
        updated_at=excluded.updated_at,checked_at=excluded.checked_at''',
        (account_id, stage, status, message, email, db.now(), db.now() if checked else None))
    return state(account_id)


class FormInput(BaseModel):
    action: str = Field(pattern='^(register|login)$')
    email: str = Field(min_length=3, max_length=160)
    display_name: str = Field(default='', max_length=80)
    password: SecretStr = Field(min_length=1, max_length=200)
    submit: bool = False
    accept_terms: bool = False

    @field_validator('email')
    @classmethod
    def email_address(cls, value):
        value = value.strip()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Укажите действующий адрес своей почты.')
        return value

    @field_validator('display_name')
    @classmethod
    def clean_name(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def requirements(self):
        if self.action == 'register' and not self.display_name:
            raise ValueError('Укажите имя для регистрации.')
        if self.action == 'register' and self.submit and not self.accept_terms:
            raise ValueError('Перед отправкой регистрации подтвердите согласие с условиями Author.Today.')
        return self


def lock_for(profile_id):
    key = (str(db.DATA.resolve()), profile_id)
    lock = _locks.get(key)
    if lock is None or getattr(lock, '_loop', None) not in (None, asyncio.get_running_loop()):
        lock = _locks[key] = asyncio.Lock()
    if lock.locked():
        raise HTTPException(409, 'Дождитесь завершения операции с этим профилем.')
    return lock


@router.post('/pilot', status_code=201)
def create_pilot():
    """Create local preparation records, never an account on the external site."""
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        existing = c.execute('''SELECT a.id FROM accounts a JOIN services s ON s.id=a.service_id
            JOIN profiles p ON p.id=a.profile_id WHERE p.name=? AND s.kind='site' AND s.registration_url=?''',
            ('AT · первый вход', REGISTER)).fetchone()
        if existing:
            return {'account_id': existing['id'], 'created': False}
        profile = c.execute('SELECT id FROM profiles WHERE name=?', ('AT · первый вход',)).fetchone()
        if profile:
            raise HTTPException(409, 'Имя пилотного профиля уже занято. Используйте существующую запись AT в подготовке.')
        pid = c.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('AT · первый вход', db.now())).lastrowid
        service = c.execute("SELECT id FROM services WHERE kind='site' AND registration_url=? ORDER BY id LIMIT 1", (REGISTER,)).fetchone()
        if service:
            sid = service['id']
        else:
            name = 'Author.Today · первый вход'
            if c.execute('SELECT 1 FROM services WHERE name_key=?', (name.casefold(),)).fetchone():
                raise HTTPException(409, 'Название сервиса пилота уже занято другой записью.')
            sid = c.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
                (name, name.casefold(), 'site', REGISTER, db.now())).lastrowid
        aid = c.execute('INSERT INTO accounts(profile_id,service_id,created_at,updated_at) VALUES (?,?,?,?)',
            (pid, sid, db.now(), db.now())).lastrowid
    audit.record('at_pilot_prepare', 'profile', pid)
    return {'account_id': aid, 'created': True}


@router.get('/{account_id}')
def get_state(account_id: int):
    return state(account_id)


async def fill_form(page, payload):
    """Strict selectors observed on the official forms. No click or retry loop."""
    if not is_at(page.url):
        raise HTTPException(409, 'Страница ушла с Author.Today. Данные не отправлены.')
    form = page.locator('form[action="/account/register"]' if payload.action == 'register' else 'form#loginForm').filter(visible=True)
    if await form.count() != 1:
        raise HTTPException(409, 'Форма сайта изменилась или требует проверки. Продолжите в открытом окне.')
    if payload.action == 'register':
        await form.locator('input[name="FIO"]').fill(payload.display_name)
        await form.locator('input[name="Email"]').fill(payload.email)
        await form.locator('input[name="TermsAgree"][type="checkbox"]').set_checked(payload.accept_terms)
        await form.locator('input[name="MarketingNewsletters"][type="checkbox"]').uncheck()
    else:
        await form.locator('input[name="Login"]').fill(payload.email)
        await form.locator('input[name="RememberMe"][type="checkbox"]').check()
    await form.locator('input[name="Password"]').fill(payload.password.get_secret_value())
    return form


@router.post('/{account_id}/form')
async def prepare_form(account_id: int, payload: FormInput):
    from . import main
    account = account_for(account_id)
    async with lock_for(account['profile_id']):
        if account['status'] == 'confirmed' and '@' in account['identifier'] and account['identifier'].casefold() != payload.email.casefold():
            raise HTTPException(409, 'Для другого аккаунта используйте отдельный профиль.')
        prior = state(account_id)
        if payload.action == 'register' and payload.submit and prior.get('registration_submitted_at'):
            raise HTTPException(409, 'Регистрацию уже отправляли. Проверьте почту и окно сайта; повторная автоматическая отправка отключена.')
        url = REGISTER if payload.action == 'register' else LOGIN
        try:
            already_open = account['profile_id'] in main.open_profiles
            if not already_open:
                result = await main.start_owner_browser(account['profile_id'], url, headless=config.SERVER)
                if not result['loaded']:
                    raise HTTPException(409, 'Страница сайта не загрузилась. Проверьте открытое окно.')
            context = main.profile_contexts.get(account['profile_id'])
            if context is None:
                raise HTTPException(409, 'Окно ещё открывается. Повторите после загрузки.')
            # Keep the owner's current tab intact when a window was already open.
            page = await context.new_page() if already_open else (context.pages[0] if context.pages else await context.new_page())
            # The remote panel always controls the latest owner-operated tab.
            if config.SERVER:
                await page.bring_to_front()
            response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            if response is None or response.status >= 400:
                raise HTTPException(409, 'Сайт не открыл форму. Продолжите в окне браузера.')
            form = await fill_form(page, payload)
            # Do not bind the preparation record until the intended form was found and filled.
            db.execute('UPDATE accounts SET identifier=?,status=CASE WHEN status=\'planned\' THEN \'in_progress\' ELSE status END,opened_at=?,updated_at=? WHERE id=?',
                (payload.email, db.now(), db.now(), account_id))
            record(account_id, payload.action, 'form_ready', 'Форма заполнена в окне браузера. Отправка ещё не выполнена.', payload.email)
            if payload.submit:
                if payload.action == 'register':
                    # Persist before the request: a timeout must never cause a duplicate registration.
                    db.execute('UPDATE onboarding_at SET registration_submitted_at=? WHERE account_id=?', (db.now(), account_id))
                await form.locator('button[type="submit"]').filter(visible=True).first.click(timeout=15000)
                message = 'Отправка выполнена. Проверьте результат в окне сайта, почту и возможный код или CAPTCHA. Успешный вход ещё не проверен.'
                status = 'awaiting_owner'
            else:
                message, status = 'Форма заполнена. Проверьте её в окне сайта и отправьте вручную либо включите отправку в этой форме.', 'form_ready'
            await save_context(context, account['profile_id'])
            audit.record('at_form_submit' if payload.submit else 'at_form_fill', 'profile', account['profile_id'], form_action=payload.action)
            return record(account_id, payload.action, status, message, payload.email)
        except HTTPException:
            raise
        except Exception:
            # Playwright exceptions can include field values. Keep them out of responses and logs.
            return record(account_id, payload.action, 'needs_owner', 'Операция остановлена. Проверьте форму и проверки сайта в открытом окне. Повторная отправка автоматически не выполняется.', payload.email)


def owner_page(account_id):
    from . import main
    account = account_for(account_id)
    context = main.profile_contexts.get(account['profile_id'])
    if not config.SERVER or context is None or not context.pages:
        raise HTTPException(409, 'Сначала откройте форму AT из этой панели.')
    page = context.pages[-1]
    if not is_at(page.url):
        raise HTTPException(409, 'Текущая вкладка вышла с Author.Today. Откройте форму заново.')
    main.remote_activity[account['profile_id']] = time.monotonic()
    return account, page


@router.post('/{account_id}/remote-open')
async def remote_open(account_id: int):
    from . import main
    if not config.SERVER:
        raise HTTPException(409, 'В локальной версии используйте обычное окно профиля.')
    account = account_for(account_id)
    async with lock_for(account['profile_id']):
        if account['profile_id'] not in main.profile_contexts:
            await main.start_owner_browser(account['profile_id'], SETTINGS if account['identifier'] else REGISTER, headless=True)
    return state(account_id)


@router.get('/{account_id}/screen')
async def remote_screen(account_id: int):
    account, page = owner_page(account_id)
    try:
        # A read-only image refresh must not make the owner's next command report "busy".
        image = await page.screenshot(type='png', timeout=10000)
    except Exception:
        raise HTTPException(409, 'Снимок недоступен: браузер закрывается или перешёл на другую страницу.') from None
    return Response(image, media_type='image/png', headers={'Cache-Control': 'no-store'})


class ControlInput(BaseModel):
    action: Literal['click', 'type', 'key', 'scroll', 'refresh', 'navigate']
    x: float = Field(default=0, ge=0, le=1440)
    y: float = Field(default=0, ge=0, le=1000)
    text: SecretStr = Field(default='', max_length=2000)
    key: Literal['Tab', 'Enter', 'Backspace', 'Escape', 'ArrowDown', 'ArrowUp', 'Control+A'] = 'Tab'
    delta: int = Field(default=0, ge=-1000, le=1000)
    url: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def allowed_navigation(self):
        if self.action == 'navigate' and not is_at(self.url):
            raise ValueError('Ссылка подтверждения должна вести на https://author.today/.')
        return self


@router.post('/{account_id}/control')
async def remote_control(account_id: int, payload: ControlInput):
    account, page = owner_page(account_id)
    async with lock_for(account['profile_id']):
        try:
            if payload.action == 'click':
                await page.mouse.click(payload.x, payload.y)
            elif payload.action == 'type':
                await page.keyboard.insert_text(payload.text.get_secret_value())
            elif payload.action == 'key':
                await page.keyboard.press(payload.key)
            elif payload.action == 'scroll':
                await page.mouse.wheel(0, payload.delta)
            elif payload.action == 'refresh':
                await page.reload(wait_until='domcontentloaded', timeout=30000)
            elif payload.action == 'navigate':
                await page.goto(payload.url, wait_until='domcontentloaded', timeout=30000)
            # A manual submit may navigate after the click. Never infer registration from it.
            await page.wait_for_timeout(300)
            await save_context(page.context, account['profile_id'])
            audit.record('at_remote_control', 'profile', account['profile_id'], control=payload.action)
            return {'performed': True, 'registration_verified': False}
        except Exception:
            raise HTTPException(409, 'Действие не подтверждено. Обновите снимок перед следующим действием; автоматического повтора нет.') from None


@router.post('/{account_id}/remote-close')
async def remote_close(account_id: int):
    from . import main
    account = account_for(account_id)
    async with lock_for(account['profile_id']):
        await main.close_profile(account['profile_id'])
    return state(account_id)


async def inspect_session(page, email):
    response = await page.goto(SETTINGS, wait_until='domcontentloaded', timeout=30000)
    if response is None or response.status >= 400:
        return 'needs_owner', 'Проверка недоступна: сайт не вернул страницу настроек.'
    if not is_at(page.url) or urlsplit(page.url).path.casefold().rstrip('/') != '/account/settings':
        return 'signed_out', 'Сайт не открыл защищённые настройки. Завершите вход и подтверждения в окне браузера.'
    # AT includes hidden logout and user-link templates even for guests. Those are not proof of login.
    values = await page.locator('input[name="Email"], input[type="email"]').evaluate_all('(els) => els.map(e => e.value)')
    # Live AT settings show the address as text, masked until the owner clicks «показать».
    email_row = page.locator('.settings-row-middle').filter(has=page.get_by_text('Электронная почта', exact=True))
    if await email_row.count() == 1:
        # Read text already present in the DOM, including the concealed address.
        # Session verification never clicks controls on the real site.
        text = await email_row.locator('.row-content').text_content(timeout=5000) or ''
        values.extend(re.findall(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', text))
    if email.casefold() not in [v.strip().casefold() for v in values if isinstance(v, str)]:
        return 'needs_owner', 'Настройки открыты, но принадлежность сессии указанной почте не подтверждена. Проверьте аккаунт вручную.'
    confirm = page.locator('a[href="/account/security/email"]').filter(has_text=re.compile(r'^Подтвердить$'))
    if await confirm.count() and await confirm.first.is_visible():
        return 'email_unconfirmed', 'Вход выполнен, адрес аккаунта совпадает, но AT ещё просит подтвердить почту. Откройте ссылку из письма и повторите проверку.'
    return 'signed_in', 'Доступ к защищённым настройкам и почта аккаунта проверены.'


class CheckInput(BaseModel):
    reopen: bool = False


@router.post('/{account_id}/check')
async def check_session(account_id: int, payload: CheckInput):
    from . import main
    account = account_for(account_id)
    email = state(account_id).get('email') or account['identifier']
    if not email:
        raise HTTPException(409, 'Сначала укажите почту через форму регистрации или входа.')
    async with lock_for(account['profile_id']):
        owner = 'at-check:' + uuid.uuid4().hex
        acquired = False
        try:
            if payload.reopen:
                await main.close_profile(account['profile_id'])
                acquired = runs.acquire_lease(account['profile_id'], owner, 'check', 120)
                if not acquired:
                    raise HTTPException(409, 'Профиль занят. Дождитесь завершения работы.')
                async with async_playwright() as pw:
                    context = await open_context(pw, account['profile_id'], headless=True)
                    try:
                        page = context.pages[0] if context.pages else await context.new_page()
                        status, message = await inspect_session(page, email)
                    finally:
                        await close_context(context, account['profile_id'])
            else:
                context = main.profile_contexts.get(account['profile_id'])
                if context is None or not context.pages:
                    raise HTTPException(409, 'Сначала откройте регистрацию или вход, либо выберите проверку после перезапуска.')
                page = await context.new_page()
                try:
                    status, message = await inspect_session(page, email)
                finally:
                    await page.close()
                await save_context(context, account['profile_id'])
            if status == 'signed_in' and payload.reopen:
                status = 'session_verified'
                message = 'Вход сохранился после закрытия и повторного запуска браузера. Почта аккаунта совпадает.'
            if status in ('signed_in', 'session_verified'):
                db.execute("UPDATE accounts SET status='confirmed',identifier=?,confirmed_at=COALESCE(confirmed_at,?),updated_at=? WHERE id=?",
                    (email, db.now(), db.now(), account_id))
            else:
                db.execute("UPDATE accounts SET status='in_progress',updated_at=? WHERE id=?", (db.now(), account_id))
            audit.record('at_session_check', 'profile', account['profile_id'], result=status, reopened=payload.reopen)
            return record(account_id, 'check', status, message, email, checked=True)
        except HTTPException:
            raise
        except Exception:
            db.execute("UPDATE accounts SET status='in_progress',updated_at=? WHERE id=?", (db.now(), account_id))
            return record(account_id, 'check', 'needs_owner', 'Не удалось проверить вход. Проверьте доступ к сайту и повторите вручную.', email, checked=True)
        finally:
            if acquired:
                runs.release_lease(account['profile_id'], owner)
