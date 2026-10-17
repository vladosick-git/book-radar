from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from .ad_network import AdNetworkCertificateError


class UserActionNeeded(RuntimeError):
    """The site asks for a human check; retrying automatically would only loop."""


class HttpStatusError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class SelectorError(RuntimeError):
    """The job's CSS selector is invalid: every page would fail the same way."""


def readable_error(exc):
    message = str(exc)
    if isinstance(exc, RuntimeError):
        return message[:400]
    if isinstance(exc, PlaywrightTimeoutError):
        return 'Страница или браузер не ответили вовремя. Проверьте адрес и соединение.'
    if 'ERR_UNSAFE_PORT' in message:
        return 'Браузер не разрешает этот порт. Укажите другой адрес страницы.'
    if 'ERR_NAME_NOT_RESOLVED' in message:
        return 'Не удалось найти сайт. Проверьте адрес.'
    if any(code in message for code in ['ERR_CONNECTION', 'ERR_INTERNET_DISCONNECTED', 'ERR_NETWORK']):
        return 'Не удалось подключиться к странице. Проверьте адрес и соединение.'
    if 'ERR_CERT' in message:
        return 'Браузер отклонил сертификат сайта. Проверьте адрес и сертификат.'
    if 'has been closed' in message:
        return 'Окно браузера закрыто до завершения сбора.'
    if 'Executable doesn' in message or 'Executable not found' in message:
        return 'Chromium не установлен. Повторите подготовку программы.'
    return 'Не удалось обработать страницу или профиль. Проверьте адрес, селектор и доступ к папке данных.'


# Stable machine-readable codes for runs, pages and the journal (stage 08/17).
ERROR_CODES = {
    'USER_ACTION': 'Сайт запросил проверку или вход',
    'HTTP_4XX': 'Страница вернула ошибку клиента (4xx)',
    'HTTP_5XX': 'Ошибка на стороне сайта (5xx)',
    'TIMEOUT': 'Страница не ответила вовремя',
    'DNS': 'Сайт не найден',
    'CONNECTION': 'Нет соединения со страницей',
    'CERTIFICATE': 'Сертификат сайта отклонён',
    'AD_CERTIFICATE': 'Сертификат рекламного ресурса отклонён',
    'UNSAFE_PORT': 'Браузер запрещает этот порт',
    'BAD_SELECTOR': 'Некорректный CSS-селектор',
    'BROWSER_CLOSED': 'Браузер закрылся во время сбора',
    'BROWSER_MISSING': 'Chromium не установлен',
    'RUN_TIMEOUT': 'Превышено время запуска',
    'WORKER_LOST': 'Worker перестал отвечать',
    'WORKER_STOPPED': 'Worker остановлен во время запуска',
    'STOPPED': 'Остановлено пользователем',
    'UNKNOWN': 'Неизвестная ошибка',
    # Journal warnings (stage 17): not run failures, but searchable by code.
    'SESSION_EXPIRED': 'Признак входа не найден: сессия истекла',
    'NO_BLOCKS': 'На странице не найдено блоков',
    'EMPTY_SLOT': 'Рекламное место есть, но не заполнено',
    'FRAMES_UNAVAILABLE': 'Часть фреймов страницы недоступна',
    'NOTIFY_FAILED': 'Уведомление не доставлено',
}


def error_code(exc):
    message = str(exc)
    if isinstance(exc, AdNetworkCertificateError):
        return 'AD_CERTIFICATE'
    if isinstance(exc, UserActionNeeded):
        return 'USER_ACTION'
    if isinstance(exc, HttpStatusError):
        return 'HTTP_5XX' if exc.status >= 500 else 'HTTP_4XX'
    if isinstance(exc, SelectorError):
        return 'BAD_SELECTOR'
    if isinstance(exc, PlaywrightTimeoutError):
        return 'TIMEOUT'
    if 'ERR_NAME_NOT_RESOLVED' in message:
        return 'DNS'
    if 'ERR_UNSAFE_PORT' in message:
        return 'UNSAFE_PORT'
    if any(code in message for code in ['ERR_CONNECTION', 'ERR_INTERNET_DISCONNECTED', 'ERR_NETWORK']):
        return 'CONNECTION'
    if 'ERR_CERT' in message:
        return 'CERTIFICATE'
    if any(marker in message for marker in ('has been closed', 'Target closed', 'Connection closed', 'Browser closed')):
        return 'BROWSER_CLOSED'
    if 'Executable doesn' in message or 'Executable not found' in message:
        return 'BROWSER_MISSING'
    return 'UNKNOWN'
