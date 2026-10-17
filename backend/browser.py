import json
import time
import os
from pathlib import Path
from . import config, db
from .protection import protect, unprotect
from playwright.async_api import Error as PlaywrightError


def state_path(profile_id):
    name = 'radar-state.vault' if config.VAULT_KEY_FILE else 'radar-state.dpapi' if os.name == 'nt' else 'radar-state.json'
    return db.DATA / 'profiles' / str(profile_id) / name


def write_state(profile_id, state):
    path = state_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(protect(json.dumps(state, ensure_ascii=False).encode('utf-8')))
    if os.name != 'nt':
        temporary.chmod(0o600)
    temporary.replace(path)
    # Older snapshots are removed only after the new protected file was written.
    for legacy in ('radar-state.json', 'radar-state.dpapi'):
        old = path.with_name(legacy)
        if old != path and old.is_file() and (legacy == 'radar-state.json' and (os.name == 'nt' or config.VAULT_KEY_FILE) or legacy == 'radar-state.dpapi' and config.VAULT_KEY_FILE):
            old.unlink()


def read_state(profile_id):
    path = state_path(profile_id)
    if path.exists():
        return json.loads(unprotect(path.read_bytes()).decode('utf-8'))
    for legacy in ('radar-state.dpapi', 'radar-state.json'):
        old = path.with_name(legacy)
        if old == path or not old.is_file():
            continue
        raw = old.read_bytes()
        state = json.loads((unprotect(raw) if legacy.endswith('.dpapi') else raw).decode('utf-8'))
        write_state(profile_id, state)
        return state
    return None


async def open_context(pw, profile_id, headless):
    options = {}
    if config.BROWSER_EXECUTABLE:
        executable = Path(config.BROWSER_EXECUTABLE)
        if not executable.is_absolute() or not executable.is_file():
            raise RuntimeError('Настроенный браузер не найден. Проверьте RADAR_BROWSER_EXECUTABLE; автоматическая подмена отключена.')
        options['executable_path'] = str(executable)
    context = await pw.chromium.launch_persistent_context(
        str(db.DATA / 'profiles' / str(profile_id)), headless=headless,
        viewport={'width': 1440, 'height': 1000}, locale='ru-RU', accept_downloads=False, **options)
    try:
        state = read_state(profile_id)
        if state is not None:
            cookies = [c for c in state.get('cookies', []) if c.get('expires', -1) <= 0 or c['expires'] > time.time()]
            if cookies:
                await context.add_cookies(cookies)
            # localStorage from an imported session: filled only where the key is still missing.
            storage = {o['origin']: {i['name']: i['value'] for i in o.get('localStorage', []) if 'name' in i and 'value' in i}
                       for o in state.get('origins', []) if isinstance(o, dict) and o.get('origin')}
            storage = {origin: items for origin, items in storage.items() if items}
            if storage:
                await context.add_init_script('(() => { const data = %s; const items = data[location.origin]; if (!items) return;'
                                              ' for (const [k, v] of Object.entries(items)) { try { if (localStorage.getItem(k) === null) localStorage.setItem(k, v); } catch (e) {} } })();'
                                              % json.dumps(storage, ensure_ascii=False))
        return context
    except Exception:
        await context.close()
        raise


async def save_context(context, profile_id):
    state = await context.storage_state()
    write_state(profile_id, state)


async def close_context(context, profile_id):
    try:
        await save_context(context, profile_id)
    except PlaywrightError:
        # A manually closed browser is already gone; use its most recent snapshot.
        pass
    finally:
        await context.close()
