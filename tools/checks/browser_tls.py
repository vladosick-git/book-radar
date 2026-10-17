"""Strict TLS preflight in a new disposable browser profile. No cookies are imported."""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from playwright.async_api import async_playwright
from backend import browser, config, db


async def check():
    rows = []
    with tempfile.TemporaryDirectory(prefix='radar-tls-') as directory:
        db.DATA = Path(directory)
        async with async_playwright() as pw:
            context = await browser.open_context(pw, 1, headless=True)
            try:
                page = context.pages[0]
                for url, should_trust in [('https://example.com/', True), ('https://mdd.yandex.net/', True),
                                          ('https://self-signed.badssl.com/', False)]:
                    error, status = '', None
                    try:
                        response = await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                        status = response.status if response else None
                    except Exception as exc:
                        error = 'CERTIFICATE' if 'ERR_CERT' in str(exc) else type(exc).__name__
                    passed = bool(status) if should_trust else error == 'CERTIFICATE'
                    rows.append({'url': url, 'expected': 'trusted' if should_trust else 'rejected',
                                 'status': status, 'error': error, 'result': 'PASS' if passed else 'FAIL'})
            finally:
                await context.close()
    return {'browser': Path(config.BROWSER_EXECUTABLE).name if config.BROWSER_EXECUTABLE else 'playwright-chromium',
            'checks': rows, 'passed': all(r['result'] == 'PASS' for r in rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = asyncio.run(check())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding='utf-8')
    print(text)
    raise SystemExit(0 if result['passed'] else 1)
