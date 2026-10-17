"""Runtime configuration from environment variables. Defaults keep the local Windows behaviour of 0.5."""
import os


def _flag(name, default):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ('1', 'true', 'yes', 'on')


def _int(name, default, low, high):
    try:
        return min(high, max(low, int(os.environ.get(name, default))))
    except ValueError:
        return default


MODE = 'server' if os.environ.get('RADAR_MODE', 'local').strip().lower() == 'server' else 'local'
SERVER = MODE == 'server'
# Local mode runs the worker inside the API process; on a server it is a separate process.
EMBEDDED_WORKER = _flag('RADAR_EMBEDDED_WORKER', not SERVER)
MAX_BROWSERS = _int('RADAR_MAX_BROWSERS', 1, 1, 8)
HEARTBEAT_SECONDS = _int('RADAR_HEARTBEAT_SECONDS', 5, 1, 60)
STALE_SECONDS = _int('RADAR_STALE_SECONDS', 60, 5, 3600)
SCHEDULER_SECONDS = _int('RADAR_SCHEDULER_SECONDS', 15, 1, 600)
RETRY_DELAYS = [int(x) for x in os.environ.get('RADAR_RETRY_DELAYS', '60,300,900').split(',') if x.strip().isdigit()] or [60, 300, 900]
AUTH = _flag('RADAR_AUTH', SERVER)
ADMIN_PASSWORD_HASH = os.environ.get('RADAR_ADMIN_PASSWORD_HASH', '')
SESSION_HOURS = _int('RADAR_SESSION_HOURS', 12, 1, 24 * 30)
COOKIE_SECURE = _flag('RADAR_COOKIE_SECURE', SERVER)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('RADAR_ALLOWED_HOSTS', '').split(',') if h.strip()] or ['127.0.0.1', 'localhost', 'testserver']
VAULT_KEY_FILE = os.environ.get('RADAR_VAULT_KEY_FILE', '')
# Stage 17: Telegram API base (a fake server in tests). The bot token is never configured here: it is kept in the vault.
TELEGRAM_API = os.environ.get('RADAR_TELEGRAM_API', 'https://api.telegram.org').rstrip('/')
# Stage 13: text recognition of ad images. 'off' or 'tesseract' (a local binary, no network).
OCR = os.environ.get('RADAR_OCR', 'off').strip().lower()
TESSERACT_CMD = os.environ.get('RADAR_TESSERACT_CMD', '')
OCR_LANGS = os.environ.get('RADAR_OCR_LANGS', 'rus+eng')
# Optional folder with *.traineddata (e.g. a user folder on Windows when Program Files is not writable).
TESSDATA_DIR = os.environ.get('RADAR_TESSDATA_DIR', '')
OCR_TIMEOUT = _int('RADAR_OCR_TIMEOUT', 30, 5, 300)
# A dedicated vendor browser may provide a required CA chain (e.g. Yandex NCA).
# Its profile still belongs to RADAR_DATA_DIR; never use the owner's regular profile.
BROWSER_EXECUTABLE = os.environ.get('RADAR_BROWSER_EXECUTABLE', '').strip()
SIMULATION = _flag('RADAR_SIMULATION', False)
VISUAL_PREVIEW = _flag('RADAR_VISUAL_PREVIEW', False)
ALLOW_TEST_COLLECTION = _flag('RADAR_ALLOW_TEST_COLLECTION', False)
VERSION = '0.8.2'
