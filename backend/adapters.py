"""Stage 11 (programmatic part): site adapters and the block type of a finding.

An adapter says where to look on a site and how to read what was found:
- `selector`  — blocks taken when the job has no own selector (the job selector always wins);
- `slots`     — containers that are expected to hold an ad: an empty page with such a slot is EMPTY_SLOT,
                a page without any is NO_BLOCKS; an access problem stays USER_ACTION / HTTP_* as before;
- `classify`  — block type with the reason of the decision:
                ad             — proven advertising: the «Реклама» marking, an erid token or an ad network;
                recommendation — a known own block of the site (carousel, shelf, the site's collection banner);
                unknown        — anything else: a candidate that a person has to check.
Without marking a block is never declared an ad. Russian law requires the «Реклама» label and an erid token on
online ads, so these are the main proof; ad network hosts and slot markers are the second line.

The Author.Today rules (v0) come from public pages without login on 14.10.2026 (docs/STAGE11.md). They have
not been checked on the client's examples of paid ads yet.
"""
import re
from urllib.parse import parse_qs, urlsplit

AD_LABEL = re.compile(r'^(реклама|рекламa|advertisement|sponsored|спонсорский материал|партн[её]рский материал|на правах рекламы)(?:\s*[•·|—-]?\s*\d{1,2}\+)?$', re.I)
ERID = re.compile(r'\berid[\s:=]+([A-Za-z0-9]{6,})', re.I)
AD_HOSTS = ('an.yandex.ru', 'yabs.yandex.ru', 'yandex.ru/ads', 'ads.adfox.ru', 'adfox.ru', 'googlesyndication.com', 'doubleclick.net', 'googleadservices.com',
            'ad.mail.ru', 'ads.vk.com', 'target.my.com', 'mytarget', 'adriver.ru', 'betweendigital.com', 'otm-r.com', 'buzzoola.com',
            'admitad.com', 'relap.io', 'smi2.ru', 'tgtrack', 'erid=')
AD_MARKERS = re.compile(r'(^|[-_])(adfox|yandex_rtb|adsbygoogle|advert|advertising|sponsor|ad-banner|ad_banner)([-_]|$)|^yandex_rtb', re.I)


def _hosts(signals):
    return [h.lower() for h in signals.get('hosts') or []]


def _erid(item, signals):
    if signals.get('erid'):
        return signals['erid']
    found = ERID.search(item.get('text') or '')
    if found:
        return found.group(1)
    for url in [item.get('destination') or ''] + list(signals.get('links') or []):
        try:
            values = parse_qs(urlsplit(url).query).get('erid')
        except ValueError:
            values = None
        if values:
            return values[0]
    return ''


def proof_of_ad(item, signals):
    """Reason string when the block carries an advertising proof, else ''."""
    label = signals.get('label') or next((line.strip() for line in (item.get('text') or '').splitlines() if AD_LABEL.match(line.strip())), '')
    erid = _erid(item, signals)
    if label and erid:
        return f'маркировка «{label}» и erid {erid}'
    if erid:
        return f'токен erid {erid}'
    if label:
        return f'маркировка «{label}»'
    network = next((h for h in _hosts(signals) + [urlsplit(item.get('frame_url') or '').netloc.lower()] if h and any(a in h for a in AD_HOSTS)), '')
    if network:
        return f'рекламная сеть {network}'
    marker = next((m for m in signals.get('markers') or [] if AD_MARKERS.search(m)), '')
    if marker:
        return f'рекламный контейнер «{marker}»'
    return ''


def self_promotion(item, signals):
    """A label-only block whose every link stays on the publisher's own site (no erid, no network, no AT target)."""
    page = urlsplit(item.get('frame_url') or item.get('source_url') or '').hostname or ''
    page = page.lower().removeprefix('www.')
    hosts = [h.split(':')[0].removeprefix('www.') for h in _hosts(signals)]
    if not page or not hosts or _erid(item, signals) or signals.get('target_domains'):
        return False
    if any(any(a in h for a in AD_HOSTS) for h in hosts) or any(AD_MARKERS.search(m) for m in signals.get('markers') or []):
        return False
    return all(h == page or h.endswith('.' + page) for h in hosts)


class Adapter:
    id = 'generic'
    version = '5'
    domains = ()
    title = 'Универсальный'
    selector = '[data-ad], [data-ad-slot], .advertisement, .adsbygoogle, [id^="yandex_rtb"], .ad-banner, .ad_banner, .advertising'
    slots = '[data-ad-slot], .adsbygoogle, [id^="yandex_rtb"], [id*="adfox" i], [class*="adfox" i]'

    def matches(self, url):
        host = (urlsplit(url).hostname or '').lower()
        return any(host == d or host.endswith('.' + d) for d in self.domains)

    def classify(self, item, signals):
        proof = proof_of_ad(item, signals)
        if proof and self_promotion(item, signals):
            # Live Librain 16.10: its own «Быстрый старт» panel carries the word «реклама» and links only to /genres.
            return 'unknown', 'собственный блок площадки с подписью «реклама»: все ссылки ведут на тот же сайт, нет erid и рекламной сети'
        if proof:
            return 'ad', proof
        if signals.get('target_domains'):
            return 'unknown', 'кандидат по ссылке или подписи рекламодателя author.today; маркировка рекламы не подтверждена'
        return 'unknown', 'нет маркировки «Реклама», erid и признаков рекламной сети: нужна проверка по снимку'


class AuthorToday(Adapter):
    """Author.Today v0: public pages without login, 14.10.2026."""
    id = 'author_today'
    version = '0.2'
    domains = ('author.today',)
    title = 'Author.Today'
    # Own blocks of AT are collected too, so that a person sees them and they are never mixed with ads.
    selector = Adapter.selector + ', #atRecs .f-book-container, .aside-banner'
    slots = Adapter.slots + ', #atRecs, .aside-banner'
    RECOMMENDATION = (
        ('atrecs', 'карусель рекомендаций AT (#atRecs)'),
        ('f-book-container', 'карточка книги в карусели AT'),
        ('aside-banner', 'боковой баннер AT'),
        ('bookcard', 'карточка книги AT'),
        ('slick', 'карусель книг AT'),
    )

    def classify(self, item, signals):
        proof = proof_of_ad(item, signals)
        if proof:
            return 'ad', proof
        markers = ' '.join(signals.get('markers') or []).lower()
        destination = item.get('destination') or ''
        own_collection = '/collection/' in destination and 'utm_source=at' in destination.lower()
        for token, reason in self.RECOMMENDATION:
            if token in markers:
                extra = '; ведёт на собственную подборку AT' if own_collection else ''
                return 'recommendation', f'{reason}, маркировки «Реклама» и erid нет{extra}'
        if own_collection:
            return 'recommendation', 'ссылка на собственную подборку AT (utm_source=AT), маркировки нет'
        if signals.get('work_links', 0) >= 3:
            return 'recommendation', f'список книг AT ({signals["work_links"]} ссылок на /work/), маркировки нет'
        return 'unknown', 'блок AT без маркировки и без известного признака: нужна проверка по снимку'


ADAPTERS = (AuthorToday(),)
GENERIC = Adapter()
BLOCK_TYPES = {'ad': 'Реклама', 'recommendation': 'Рекомендация', 'unknown': 'Неясно'}


def for_url(url):
    return next((adapter for adapter in ADAPTERS if adapter.matches(url)), GENERIC)


def catalog():
    return [{'id': a.id, 'version': a.version, 'title': a.title, 'domains': list(a.domains)} for a in (*ADAPTERS, GENERIC)]


def classify_legacy(row):
    """Block type for a finding saved before stage 11 (no DOM signals kept): only text/link/frame proofs."""
    adapter = for_url(row.get('source_url') or '')
    signals = {'hosts': [urlsplit(u).netloc for u in (row.get('destination'), row.get('image_url')) if u], 'links': [row.get('destination') or '']}
    block_type, reason = adapter.classify({**row}, signals)
    return block_type, reason + ' (запись до этапа 11, классифицирована по сохранённым полям)'
