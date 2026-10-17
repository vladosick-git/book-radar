"""Synthetic publisher cards based on the client's 16.10 crop formats; no live ad clicks."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright
from backend import adapters, catalog, db
from backend.collector import EXTRACT, collect

CSS = '<style>article,.card {width:360px;min-height:120px;padding:10px;margin:10px;border:1px solid} a{display:inline-block} h2{font-size:24px}</style>'


def extract(html, selector=None):
    async def run():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page()
            await page.set_content(CSS + html)
            items = await page.evaluate(EXTRACT, {'selector': selector or adapters.GENERIC.selector, 'discover': not selector, 'site': 'generic'})
            await b.close()
            return items
    return asyncio.run(run())


@pytest.mark.parametrize('label', ['РЕКЛАМА · 16+', 'Реклама •16+', 'Реклама 16+', 'Реклама'])
def test_client_label_formats_and_advertiser_are_not_the_title(label):
    items = extract(f'''<article><a href="https://author.today/work/1">author.today</a>
        <span>{label}</span><h2>Цикл книг! Матерый программист очнулся в 1982 году в США</h2>
        <p>Альтернативная история! Попаданец меняет путь развития компьютеров и игр…</p>
        <a href="https://publisher.example/menu">Подробнее о сайте</a></article>''')
    assert len(items) == 1
    item = items[0]
    assert item['title'].startswith('Цикл книг!') and item['destination'] == 'https://author.today/work/1'
    assert label not in item['clean_text'] and 'author.today' not in item['clean_text']
    assert item['signals']['target_match'] == 'direct_url'
    assert adapters.GENERIC.classify(item, item['signals'])[0] == 'ad'


def test_unmarked_book_link_stays_a_candidate_and_sibling_variants_survive():
    items = extract('''<article><a href="https://author.today/work/2">Такие книги делят жанр на «до» и «после»</a>
        <p>Новая история для тех, кто любит читать с азартом.</p></article>
        <article><a href="https://author.today/work/2">Такие книги делят жанр на «до» и «после»</a>
        <p>Книга, в которой герой знает, чем всё кончится, и всё равно спешит.</p></article>''')
    assert len(items) == 2 and items[0]['text'] != items[1]['text']
    for item in items:
        kind, reason = adapters.GENERIC.classify(item, item['signals'])
        assert kind == 'unknown' and 'кандидат' in reason


def test_redirect_parameter_and_visible_advertiser_keep_original_click_url():
    url = 'https://yabs.yandex.ru/count?url=https%3A%2F%2Fauthor.today%2Fwork%2F5'
    item = extract(f'<article><span>Реклама ·16+</span><h2>Попаданец! Ветеран боевых действий в мире магии. Читать!</h2><a href="{url}">Читать первую книгу цикла</a></article>')[0]
    assert item['destination'] == url
    assert item['signals']['target_url'] == 'https://author.today/work/5'
    assert item['signals']['target_match'] == 'redirect_parameter'
    opaque = extract('<article><h2>Городское фэнтези! Попаданец стал неубиваемым клерком…</h2><a href="https://yabs.yandex.ru/count/opaque">author.today</a><p>Читать первый роман цикла</p></article>')[0]
    assert opaque['signals']['target_match'] == 'advertiser_label' and opaque['signals']['target_url'] == ''
    assert opaque['destination'].endswith('/opaque')


def test_lookalike_host_menus_hidden_blocks_large_articles_and_own_selector():
    assert extract('<article><h2>Не AT</h2><a href="https://author.today.evil.example/work/1">Книга</a></article>') == []
    assert extract('<nav><article><a href="https://author.today/work/1">Меню</a></article></nav>') == []
    assert extract('<article style="display:none"><a href="https://author.today/work/1">Скрытая книга</a></article>') == []
    assert extract('<article style="height:1600px"><a href="https://author.today/work/1">Ссылка в большой статье</a></article>') == []
    items = extract('<article><a href="https://author.today/work/1">Не выбран</a></article><div class="wanted">Только свой блок</div>', selector='.wanted')
    assert len(items) == 1 and items[0]['text'] == 'Только свой блок'


def test_publisher_and_frame_collection_keep_source_target_and_evidence(tmp_path, monkeypatch):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/':
                body = CSS + '''<h1>Сторонний сайт: синтетическая фикстура</h1>
                    <article><span>Реклама ·16+</span><h2>Попаданец с ИИ в голове против магов-биоинженеров…</h2>
                    <p>Чтобы выбраться из мира-аномалии, попаданец должен получить сверхспособности…</p>
                    <a href="https://author.today/work/101">author.today</a></article>
                    <iframe src="/frame" width="450" height="320"></iframe>'''
            elif self.path == '/frame':
                body = CSS + '''<article><span>Реклама •16+</span><h2>Новинка, написанная руками, а не алгоритмом</h2>
                    <p>Ни строчки нейрослопа. От первой сцены до финала писал человек</p>
                    <a href="https://yabs.yandex.ru/count?url=https%3A%2F%2Fauthor.today%2Fwork%2F102">Читать</a></article>'''
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers()
            self.wfile.write(body.encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    source = f'http://127.0.0.1:{server.server_port}/'
    try:
        monkeypatch.setattr(db, 'DATA', tmp_path)
        db.init()
        pid = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Фикстура', db.now()))
        jid = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
            ('Сторонний сайт', pid, json.dumps([source]), '', 2, db.now()))
        job = db.rows('SELECT * FROM jobs WHERE id=?', (jid,))[0]
        asyncio.run(collect(job))
        items = catalog.search({}, limit=10)['items']
        assert len(items) == 2, items
        assert all(r['block_type'] == 'ad' and r['source_url'] == source for r in items)
        assert all(r['block_evidence']['target_domains'] == ['author.today'] for r in items)
        assert any(r['frame_url'].endswith('/frame') for r in items)
        assert any(r['block_evidence']['target_url'] == 'https://author.today/work/102' for r in items)
        assert all((tmp_path / 'evidence' / r['screenshot']).is_file() for r in items)
        assert set(requests).issubset({'/', '/frame', '/favicon.ico'})
    finally:
        server.shutdown(); thread.join()


def test_live_publisher_editorial_photo_footer_and_empty_slot_are_not_creatives():
    # Reduced live structures from CONT and Kino-Teatr, without article/user data.
    photo = '<img id="yandex_rtb_R-A-1-57-photo" src="data:image/svg+xml,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'680\' height=\'382\'/>">'
    footer = '<footer><div style="width:240px;height:140px"><a href="/app/">Приложение</a><br><a href="/adv/">Реклама</a><br><a href="/contact/">Контакты</a></div></footer>'
    empty = '<div id="yandex_rtb_R-A-1-2" style="width:300px;height:250px">Реклама</div>'
    assert extract(photo + footer + empty) == []
    # Explicit selector retains the operator's choice; a linked banner remains collectible.
    assert len(extract(photo, selector='img')) == 1
    linked = extract('<a href="https://author.today/work/8">' + photo + '</a>')
    assert len(linked) == 1 and linked[0]['signals']['target_url'].endswith('/8')
    placeholder = '<div class="_placeholder_1fcyn_1" style="width:400px;height:300px;background-image:url(https://example.org/loader.svg)">реклама</div>'
    assert extract(placeholder) == []


def test_advertiser_outside_the_link_and_full_card_link_are_read_without_resolving_redirects():
    item = extract('<article><span>author.today</span><span>Реклама ·16+</span><h2>Попаданец — программист</h2><a href="https://yabs.yandex.ru/opaque">Читать</a></article>')[0]
    assert item['signals']['target_domains'] == ['author.today']
    assert item['signals']['target_url'] == ''
    assert item['destination'] == 'https://yabs.yandex.ru/opaque'
