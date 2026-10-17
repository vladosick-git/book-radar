"""Owner-only local desktop control; token is encrypted by the application's vault."""
import secrets


def write_token(data):
    from backend.protection import protect
    data.mkdir(parents=True, exist_ok=True)
    (data / 'panel-control.dpapi').write_bytes(protect(secrets.token_bytes(32)))


def read_token(data):
    from backend.protection import unprotect
    return unprotect((data / 'panel-control.dpapi').read_bytes()).hex()


def allowed(request, data):
    if request.client is None or request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        return False
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        return False
    try:
        return secrets.compare_digest(request.headers.get('x-radar-control', ''), read_token(data))
    except (OSError, ValueError):
        return False
