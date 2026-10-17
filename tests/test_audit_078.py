"""Claude audit 0.7.8: defects reproduced on the real 0.7.7 Librain/CONT capture.

1. Yandex click counters (yandex.ru/an/count/<token>) carry a fresh token per impression: the same creative
   became a new placement and a new ad on every repeat (real findings #1 and #9, «rutube.ru», same image).
2. A network creative's domain line («belleyou.ru», «joom.ru») was taken as the title instead of the headline.
3. Soft hyphens (U+00AD) inside the creative text split otherwise equal texts.
"""
import asyncio
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from backend import adapters, catalog, config, db, main
from backend.collector import EXTRACT, shadow_blocks
from backend.identity import click_counter, fingerprint, stable_url

COUNTER_A = 'https://yandex.ru/an/count/WpuejI_zOoVX2Lbl0uKP06Djhz0U55EfpnF5Ctz6fr2V51i4GCBH10WqGtAKIkK?test-tag=1'
COUNTER_B = 'https://yandex.ru/an/count/WpGejI_zOoVX2Lbm0pKP07Cigz0U55EfpnF5Ctz6fr2V51i4GC9n19CKOXKZ6iH'


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    return tmp_path


def make_job():
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Аудит', db.now()))
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                        ('Librain', profile, '["https://librain.ru/"]', '', 2, db.now()))
    return db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]


def test_click_counter_token_is_volatile_but_other_urls_are_kept():
    assert click_counter(COUNTER_A) and click_counter('https://yabs.yandex.ru/count/abc') and click_counter('https://an.yandex.ru/count/x')
    assert stable_url(COUNTER_A) == stable_url(COUNTER_B) == 'https://yandex.ru/an/count/'
    for kept in ('https://author.today/work/454126', 'https://yandex.ru/search/?text=book', 'https://notyandex.ru/an/count/abc',
                 'https://avatars.mds.yandex.net/get-direct/5235809/_29RwoIFv0wGErJRlQveGA/x180'):
        assert not click_counter(kept) and stable_url(kept) == kept


def test_repeat_of_the_same_network_creative_is_one_placement_and_one_ad(data):
    job = make_job()
    item = {'title': 'Боевое Фэнтези! Мастер-изобретатель очнулся в мире магов', 'text': 'author.today\nРЕКЛАМА\n•\n16+\nБоевое Фэнтези!',
            'image_url': 'https://avatars.mds.yandex.net/get-direct/5235809/_29RwoIFv0wGErJRlQveGA/x180'}
    first = catalog.save(job, {**item, 'destination': COUNTER_A}, 'https://librain.ru/', 'https://librain.ru/', 'a.png')
    second = catalog.save(job, {**item, 'destination': COUNTER_B}, 'https://librain.ru/', 'https://librain.ru/', 'b.png')
    assert first['fingerprint'] == second['fingerprint'] and not second['new']
    assert first['ad_id'] == second['ad_id']
    finding = db.rows('SELECT sightings, destination FROM findings')
    assert len(finding) == 1 and finding[0]['sightings'] == 2 and finding[0]['destination'] == COUNTER_B  # latest link kept
    assert db.rows('SELECT sightings, variant_no FROM ads') == [{'sightings': 2, 'variant_no': 1}]
    assert len(db.rows('SELECT id FROM observations')) == 2


def test_different_creatives_behind_counters_are_separate_ads_not_variants(data):
    job = make_job()
    a = catalog.save(job, {'title': 'Книга одна', 'text': 'x', 'destination': COUNTER_A, 'image_url': 'https://img.test/1.jpg'},
                     'https://librain.ru/', 'https://librain.ru/', 'a.png')
    b = catalog.save(job, {'title': 'Совсем другая книга', 'text': 'y', 'destination': COUNTER_B, 'image_url': 'https://img.test/2.jpg'},
                     'https://librain.ru/', 'https://librain.ru/', 'b.png')
    assert a['ad_id'] != b['ad_id']
    assert {r['variant_no'] for r in db.rows('SELECT variant_no FROM ads')} == {1}


def test_domain_line_is_not_the_title_and_soft_hyphens_are_removed():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('<div id="yandex_rtb_a"></div><div id="yandex_rtb_b"></div>')
            create = '''({id, html}) => {
                const root = document.getElementById(id).attachShadow({mode: 'closed'});
                root.innerHTML = `<style>:host{display:block;width:360px;height:220px} article{width:340px;min-height:180px} a{display:block}</style><article>${html}</article>`;
            }'''
            await page.evaluate(create, {'id': 'yandex_rtb_a', 'html':
                '<a href="https://yandex.ru/an/count/T1">belleyou.ru</a><span>РЕКЛАМА</span><p>Эф­фект­ное белье из кружева Шантильи</p><a href="https://yandex.ru/an/count/T1">Посмотреть каталог</a>'})
            await page.evaluate(create, {'id': 'yandex_rtb_b', 'html':
                '<a href="https://yandex.ru/an/count/T2"><img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="300" height="150"></a><span>Реклама</span><a href="https://yandex.ru/an/count/T2">joom.ru</a>'})
            items = await shadow_blocks(page, page.main_frame, {'selector': adapters.GENERIC.selector, 'discover': True, 'site': 'generic'})
            await browser.close()
            return items
    items = {i['destination'][-2:]: i for i in asyncio.run(run())}
    assert items['T1']['title'] == 'Эффектное белье из кружева Шантильи' and '­' not in items['T1']['text']
    assert 'belleyou.ru' in items['T1']['text']  # the advertiser stays visible in the text
    assert items['T2']['title'] == 'joom.ru' and items['T2']['field_sources']['title'] == 'domain'  # nothing else readable


def test_label_header_grows_to_the_whole_creative_but_not_across_two_creatives():
    """Live Librain 16.10 (overlay format): only the header «title + Реклама + 16+» was taken, without image,
    link and the «author.today» advertiser line (block_evidence had no target)."""
    unit = '''<div class="ya-unit-item" style="width:340px">
      <a href="https://yandex.ru/an/count/{t}"><img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="320" height="160"></a>
      <div class="banner-content"><div class="header" style="width:320px;height:70px">
        <div>{title}</div><span>Реклама</span><span>•</span><span>16+</span></div>
        <a href="https://yandex.ru/an/count/{t}">author.today</a><a href="https://yandex.ru/an/count/{t}">Скачать</a></div></div>'''

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('<div id="yandex_rtb_x"></div>')
            await page.evaluate('''html => { const root = document.getElementById('yandex_rtb_x').attachShadow({mode: 'closed'});
                root.innerHTML = `<div class="unit-container" style="width:720px;display:flex">${html}</div>`; }''',
                unit.format(t='A1', title='Боевое Фэнтези! Мастер-изобретатель очнулся в мире магов') + unit.format(t='B2', title='Попаданец в мир магии'))
            items = await shadow_blocks(page, page.main_frame, {'selector': adapters.GENERIC.selector, 'discover': True, 'site': 'generic'})
            await browser.close()
            return items
    items = {i['title']: i for i in asyncio.run(run())}
    assert set(items) == {'Боевое Фэнтези! Мастер-изобретатель очнулся в мире магов', 'Попаданец в мир магии'}
    for title, suffix in (('Боевое Фэнтези! Мастер-изобретатель очнулся в мире магов', 'A1'), ('Попаданец в мир магии', 'B2')):
        item = items[title]
        assert item['destination'].endswith(suffix) and item['image_source'] == 'img'
        assert item['signals']['target_domains'] == ['author.today'] and adapters.GENERIC.classify(item, item['signals'])[0] == 'ad'


def test_csv_carries_the_advertiser_shown_on_the_card(data, monkeypatch):
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    job = make_job()
    catalog.save(job, {'title': 'Мастер-изобретатель', 'text': 'author.today\nРеклама', 'destination': COUNTER_A, 'image_url': '',
                       'block_type': 'ad', 'block_reason': 'маркировка', 'adapter': 'generic@5',
                       'block_evidence': {'target_domains': ['author.today'], 'target_match': 'advertiser_label'}},
                 'https://librain.ru/', 'https://librain.ru/', 'a.png')
    with TestClient(main.app) as client:
        table = list(csv.reader(io.StringIO(client.get('/api/export.csv').text.lstrip('﻿'))))
        document = client.get('/api/export.json').json()
    header = table[0]
    # 0.8.1 appends provenance fields; the original advertiser columns keep their positions.
    assert header[24:26] == ['advertiser', 'target_url'] and table[1][header.index('advertiser')] == 'author.today'
    assert document['findings'][0]['block_evidence']['target_domains'] == ['author.today']


def test_whole_card_link_gives_a_one_line_headline():
    """Live Librain 16.10: «Эффектное белье…\nРеклама\nУтонченность…\nbelleyou.ru\nПосмотреть каталог» became the title."""
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('<div id="yandex_rtb_c"></div>')
            await page.evaluate('''() => { const root = document.getElementById('yandex_rtb_c').attachShadow({mode: 'closed'});
                root.innerHTML = `<a href="https://yandex.ru/an/count/C3" style="display:block;width:340px;height:200px">
                  <div>Эффектное белье из кружева Шантильи</div><div>Реклама</div><div>Утонченность кружева и комфорт посадки.</div>
                  <div>belleyou.ru</div><div>Посмотреть каталог</div></a>
                  <a href="https://yandex.ru/an/count/D4" style="display:block;width:340px;height:200px"><div>ИЗУЧИТЕ ВСЕ УСЛОВИЯ КРЕДИТА (ЗАЙМА) НА САЙТЕ</div>
                  <div>Кредит наличными до 5 млн</div><div>Реклама</div><div>bank.example</div></a>`; }''')
            items = await shadow_blocks(page, page.main_frame, {'selector': adapters.GENERIC.selector, 'discover': True, 'site': 'generic'})
            await browser.close()
            return items
    items = asyncio.run(run())
    assert [i['title'] for i in items] == ['Эффектное белье из кружева Шантильи', 'Кредит наличными до 5 млн']
    assert 'belleyou.ru' in items[0]['text'] and 'Посмотреть каталог' in items[0]['text']
    assert 'ИЗУЧИТЕ ВСЕ УСЛОВИЯ' in items[1]['text']  # the warning stays in the text


def test_publishers_own_panel_with_the_word_reklama_is_not_a_proven_ad():
    page = 'https://librain.ru/'
    own = {'title': 'Выбери, с чего начать', 'text': 'БЫСТРЫЙ СТАРТ\nреклама\nВыбери, с чего начать', 'frame_url': page}
    own_signals = {'label': 'реклама', 'hosts': ['librain.ru'], 'markers': ['home-quick-start__panel', 'home-quick-start']}
    block_type, reason = adapters.GENERIC.classify(own, own_signals)
    assert block_type == 'unknown' and 'тот же сайт' in reason
    network = {'title': 'Роман! Хакер с ИИ в голове', 'text': 'author.today\nРеклама\nРоман!', 'frame_url': page}
    assert adapters.GENERIC.classify(network, {'label': 'Реклама', 'hosts': ['yandex.ru', 'avatars.mds.yandex.net'],
                                               'target_domains': ['author.today']})[0] == 'ad'
    # the same panel with an erid or a network slot marker stays an ad
    assert adapters.GENERIC.classify({**own, 'text': own['text'] + '\nerid: 2VtzqwTESTab12'}, own_signals)[0] == 'ad'
    assert adapters.GENERIC.classify(own, {**own_signals, 'markers': ['yandex_rtb_R-A-1-1']})[0] == 'ad'
