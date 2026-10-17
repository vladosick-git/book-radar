import asyncio

from playwright.async_api import async_playwright

from backend import adapters
from backend.collector import EXTRACT, shadow_blocks


def test_closed_open_and_child_frame_creatives_keep_fields_and_do_not_mix_documents():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('<div id="yandex_rtb_a"></div><div id="yandex_rtb_b"></div><iframe srcdoc="<div id=inside></div>"></iframe>')
            create = '''({id,mode,title}) => {
                const root = document.getElementById(id).attachShadow({mode});
                root.innerHTML = `<style>:host {display:block;width:360px;height:220px} article{width:340px;min-height:180px} a{display:block}</style>
                  <article><a href="https://author.today/work/123">author.today</a><span>Реклама · 16+</span><div>•</div><div>16+</div>
                  <h2>${title}</h2><p>Настоящие поля синтетической карточки для регрессии</p></article>`;
            }'''
            await page.evaluate(create, {'id': 'yandex_rtb_a', 'mode': 'closed', 'title': 'Цикл книг! Инженер нашёл симбиота'})
            await page.evaluate(create, {'id': 'yandex_rtb_b', 'mode': 'open', 'title': 'Маг в новом мире'})
            child = page.frames[1]
            await child.evaluate(create, {'id': 'inside', 'mode': 'closed', 'title': 'Книга внутри фрейма'})
            args = {'selector': adapters.GENERIC.selector, 'discover': True, 'site': 'generic'}
            assert await page.evaluate(EXTRACT, args) == []
            items = await shadow_blocks(page, page.main_frame, args)
            assert {i['title'] for i in items} == {'Цикл книг! Инженер нашёл симбиота', 'Маг в новом мире'}
            assert {i['field_sources']['dom_scope'] for i in items} == {'closed_shadow_root', 'open_shadow_root'}
            nested = await shadow_blocks(page, child, args)
            assert len(nested) == 1 and nested[0]['title'] == 'Книга внутри фрейма'
            for item in items + nested:
                assert item['destination'] == 'https://author.today/work/123'
                assert 'Реклама' not in item['clean_text']
                assert item['signals']['target_domains'] == ['author.today']
                assert adapters.GENERIC.classify(item, item['signals'])[0] == 'ad'
            await browser.close()
    asyncio.run(run())
