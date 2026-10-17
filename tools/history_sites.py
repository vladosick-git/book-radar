"""Extract a domain-only candidate list; never import browser history into a profile.

python tools/history_sites.py SOURCE_HISTORY --out PRIVATE_OUTPUT.csv
"""
import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

BOOK_DOMAINS = ('author.today', 'litnet.com', 'litnet.ru', 'litmarket.ru', 'litgorod.ru', 'litfan.ru',
    'librain.ru', 'readtoday.ru', 'proza.ru', 'skanbook.ru', 'bookce.online', 'pro-books.ru',
    'readli.net', 'readboom.net', 'novkniga.ru', 'kniganet.ru', 'ficbook.net', 'litres.ru')
PUBLISHER_DOMAINS = ('cont.ws', 'dzen.ru', 'kino-teatr.ru', 'alternathistory.ru')


def extract(source, domains=BOOK_DOMAINS):
    counters, ids = {}, {}
    with sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True) as c:
        for uid, url in c.execute('SELECT id,url FROM urls'):
            try:
                u = urlsplit(url)
                if u.scheme not in ('http', 'https'):
                    continue
                host = (u.hostname or '').lower().rstrip('.')
            except ValueError:
                continue
            domain = next((d for d in domains if host == d or host.endswith('.' + d)), None)
            if domain:
                ids[uid] = domain
                counters.setdefault(domain, Counter())['url_records'] += 1
        for uid, count in c.execute('SELECT url,COUNT(*) FROM visits GROUP BY url'):
            if uid in ids:
                counters[ids[uid]]['visit_records'] += count
    return [{'domain': domain, **dict(counts), 'visit_records': counts['visit_records'],
             'status': 'candidate_not_approved'}
            for domain, counts in sorted(counters.items(), key=lambda pair: (-pair[1]['visit_records'], pair[0]))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--include-publishers', action='store_true', help='Include a small list of publisher-domain candidates; not proof of ad placement.')
    args = parser.parse_args()
    if args.source.resolve() == args.out.resolve():
        parser.error('Output must differ from the source database.')
    rows = extract(args.source, BOOK_DOMAINS + PUBLISHER_DOMAINS if args.include_publishers else BOOK_DOMAINS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['domain', 'url_records', 'visit_records', 'status'])
        writer.writeheader(); writer.writerows(rows)
    print(f'Candidates: {len(rows)}. Domain-only output: {args.out}')


if __name__ == '__main__':
    main()
