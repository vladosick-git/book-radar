import asyncio
from playwright.async_api import async_playwright

from backend import simulation as sim


def test_every_city_and_device_is_applied_in_a_real_browser():
    async def go():
        async with async_playwright() as pw:
            assert len(sim.CITIES) == 25 and len(sim.DEVICES) == 12
            browser = await pw.chromium.launch()
            device_keys = list(sim.DEVICES)
            for i, (city_key, city) in enumerate(sim.CITIES.items()):
                profile = {'city': city_key, 'device': device_keys[i % len(device_keys)]}
                options = sim.device_options(pw, profile)
                context = await browser.new_context(**options)
                async def fixture(route):
                    await route.fulfill(content_type='text/html', body='<meta name="viewport" content="width=device-width,initial-scale=1"><p>Preset fixture</p>')
                await context.route('**/*', fixture)
                page = await context.new_page()
                await page.goto('http://127.0.0.1:9989/preset-fixture')
                observed = await page.evaluate('''async () => { const p=await new Promise((ok,fail)=>navigator.geolocation.getCurrentPosition(ok,fail));
                  return {ua:navigator.userAgent,width:innerWidth,touch:navigator.maxTouchPoints,dpr:devicePixelRatio,
                    lat:p.coords.latitude,lon:p.coords.longitude,tz:Intl.DateTimeFormat().resolvedOptions().timeZone}; }''')
                assert observed['ua'] == options['user_agent']
                assert observed['width'] == options['viewport']['width']
                assert observed['dpr'] == options['device_scale_factor']
                assert (observed['touch'] > 0) == options['has_touch']
                assert observed['lat'] == city['latitude'] and observed['lon'] == city['longitude']
                assert observed['tz'] == city['timezone']
                await context.close()
            await browser.close()
    asyncio.run(go())
