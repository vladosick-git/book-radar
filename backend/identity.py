import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Preserve unknown parameters: they may identify a creative, book, size or signature.
VOLATILE_PARAMETERS = {'rnd', 'cb', 'cachebuster', 'cache_buster', 'cachebust', '_cachebuster', '_cb', 'gclid', 'yclid', 'fbclid'}


# Ad network click counters carry a fresh opaque token per impression (yandex.ru/an/count/<token>).
# The token identifies the auction, not the creative: keeping it made every repeat a new placement and a new ad.
COUNTER_PATHS = ('/an/count/', '/count/')


def click_counter(value):
    try:
        parts = urlsplit(value or '')
        host = (parts.hostname or '').lower()
    except ValueError:
        return False
    yandex = any(host == f'{name}.{zone}' or host.endswith(f'.{name}.{zone}')
                 for name in ('yandex',) for zone in ('ru', 'com', 'by', 'kz', 'net'))
    return parts.scheme.lower() in ('http', 'https') and yandex and parts.path.startswith(COUNTER_PATHS)


def stable_url(value):
    parts = urlsplit(value)
    if parts.scheme.lower() not in ('http', 'https'):
        return value
    if click_counter(value):
        prefix = next(p for p in COUNTER_PATHS if parts.path.startswith(p))
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), prefix, '', ''))
    parameters = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                  if key.lower() not in VOLATILE_PARAMETERS and not key.lower().startswith('utm_')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(parameters), parts.fragment))


def fingerprint(profile_id, source, frame, item):
    signature = [profile_id, stable_url(source), stable_url(frame), item['title'], item['text'],
                 stable_url(item['destination']), stable_url(item['image_url'])]
    return hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
