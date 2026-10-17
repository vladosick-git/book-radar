"""Resource TLS failures must not masquerade as a legitimately empty ad slot.

Keep hosts and error codes only: request URLs can contain cookies/auction identifiers.
"""
from urllib.parse import urlsplit

AD_DOMAINS = ('an.yandex.ru', 'yabs.yandex.ru', 'mdd.yandex.net', 'adfox.ru',
              'yandexadexchange.net', 'googlesyndication.com', 'doubleclick.net',
              's.advscdn.com', 'ad.mail.ru', 'ads.vk.com', 'adriver.ru')


class AdNetworkCertificateError(RuntimeError):
    pass


class ResourceFailures:
    def __init__(self):
        self.certificates = set()

    def reset(self):
        self.certificates.clear()

    def observe(self, request):
        host = (urlsplit(request.url).hostname or '').lower()
        if any(host == d or host.endswith('.' + d) for d in AD_DOMAINS):
            if 'ERR_CERT' in (request.failure or ''):
                self.certificates.add(host)

    def check(self):
        if self.certificates:
            hosts = ', '.join(sorted(self.certificates))
            raise AdNetworkCertificateError(
                f'Браузер отклонил сертификат рекламного ресурса: {hosts}. '
                'Показ может быть неполным. Проверьте доверие сертификатам выбранного браузера; '
                'пустое место не считается успешной проверкой рекламы.')
