import asyncio
import base64
import json
import uuid
from playwright.async_api import async_playwright
from . import adapters, catalog, db, ocr
from .browser import open_context, close_context
from .identity import fingerprint
from .ad_network import ResourceFailures
from .errors import readable_error, error_code, UserActionNeeded, HttpStatusError, SelectorError

DEFAULT_SELECTOR = adapters.GENERIC.selector

# Stage 13: besides the 0.6 fields (unchanged: they form the fingerprint) every block reports the cleaned text,
# where each field came from, the kind of image (img, css, canvas, iframe, none) and its rectangle for a crop.
EXTRACT = r'''function(args) {
  const scope = this && this.nodeType === 11 ? this : document;
  // Stage 11: {selector, discover}. discover also takes the block around an «Реклама» label or an erid token.
  const selector = typeof args === 'string' ? args : args.selector;
  const discover = typeof args === 'string' ? false : !!args.discover;
  const visible = el => {
    let r = el.getBoundingClientRect();
    if (r.width <= 10 || r.height <= 10) return false;
    let left = r.left, right = r.right, top = r.top, bottom = r.bottom;
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      if (node !== el) {
        const parent = node.getBoundingClientRect();
        if (['hidden', 'clip'].includes(style.overflowX)) { left = Math.max(left, parent.left); right = Math.min(right, parent.right); }
        if (['hidden', 'clip'].includes(style.overflowY)) { top = Math.max(top, parent.top); bottom = Math.min(bottom, parent.bottom); }
      }
    }
    return right - left > 10 && bottom - top > 10;
  };
  if (scope !== document && !visible(scope.host)) return [];
  const elements = [...scope.querySelectorAll(selector)].filter(visible).map(el =>
    discover && args.site !== 'author_today' && el.matches('img[id^="yandex_rtb"]') ? el.closest('a[href]') || el : el);
  const adLabel = /^(реклама|advertisement|sponsored|спонсорский материал|партн[её]рский материал|на правах рекламы)(?:\s*[•·|—-]?\s*\d{1,2}\+)?$/i;
  const eridText = /\berid[\s:=]+([A-Za-z0-9]{6,})/i;
  // The client's crops show AT as the advertiser, not necessarily as the publisher.
  // Inspect URL data only: never click an ad or request its redirect chain.
  const targetOf = anchor => {
    const queue = [anchor.href], seen = new Set();
    for (let i = 0; i < queue.length && i < 16; i++) {
      const value = queue[i]; if (!value || seen.has(value)) continue; seen.add(value);
      try {
        const url = new URL(value, document.baseURI);
        if (!['http:', 'https:'].includes(url.protocol)) continue;
        if (url.hostname.toLowerCase() === 'author.today' && !url.username && !url.password)
          return {domain: 'author.today', url: url.href, via: i === 0 ? 'direct_url' : 'redirect_parameter'};
        if (i < 4) for (const key of ['url', 'redirect', 'redirect_url', 'target', 'to', 'u', 'adurl', 'link']) {
          const nested = url.searchParams.get(key); if (nested && /^https?:/i.test(nested)) queue.push(nested);
        }
      } catch {}
    }
    if ((anchor.innerText || '').split('\n').some(line => /^(?:https?:\/\/)?(?:www\.)?author\.today\/?$/i.test(line.trim())))
      return {domain: 'author.today', url: '', via: 'advertiser_label'};
    return null;
  };
  if (discover) {
    const leaves = [...scope.querySelectorAll('*')].filter(el => el.children.length === 0 && (adLabel.test((el.textContent || '').trim()) || eridText.test(el.textContent || '')));
    for (const leaf of leaves.slice(0, 50)) {
      // A publisher's footer/menu link «Реклама» sells placement, it is not a creative.
      if (leaf.closest('nav,header,footer,[role="navigation"],[role="contentinfo"]')) continue;
      let box = leaf.parentElement;
      for (let depth = 0; box && depth < 6; depth++, box = box.parentElement) {
        const r = box.getBoundingClientRect();
        if (r.width >= 120 && r.height >= 60) break;
      }
      // A big enough header (title + «Реклама» + age) is not yet the creative: live Librain overlays put the
      // image, the link and the advertiser «author.today» one to four levels higher. Grow while the box has
      // neither a link nor an image, but never across a second ad label or into a page-sized container.
      const labelsIn = node => [...node.querySelectorAll('*')].filter(n => n.children.length === 0 && adLabel.test((n.textContent || '').trim())).length;
      const media = 'img, picture, canvas, iframe';
      for (let depth = 0; box && depth < 4; depth++) {
        const linked = box.matches('a[href]') || box.querySelector('a[href]'), pictured = box.matches(media) || box.querySelector(media);
        if (linked && pictured) break;
        const up = box.parentElement;
        if (!up || up === document.body || up === document.documentElement) break;
        const r = up.getBoundingClientRect();
        if (r.width > 1500 || r.height > 1000 || labelsIn(up) > 1 || (up.innerText || '').length > 2500) break;
        // Linked text creative: climb only for its own picture, not for neighbouring text.
        if (linked && !up.querySelector(media)) break;
        box = up;
      }
      // A block already taken by the selector keeps its exact borders: its fingerprint must not change.
      if (box && box !== document.body && !box.closest('nav,header,footer,[role="navigation"],[role="contentinfo"]') && visible(box) && !elements.some(e => e.contains(box) || box.contains(e))) elements.push(box);
    }
    if (args.site !== 'author_today') {
      for (const link of [...scope.querySelectorAll('a[href]')].filter(a => targetOf(a)).slice(0, 100)) {
        if (link.closest('nav,header,footer') || !visible(link) || elements.some(e => e.contains(link))) continue;
        let box = link.closest('article,li,[data-ad],.ad-card,.advertisement') || link.parentElement;
        if (!box || box === document.body || box === document.documentElement) continue;
        // An inline link or a whole article is not a creative. Preserve a bounded card only.
        const r = box.getBoundingClientRect(), text = (box.innerText || '').trim();
        if (r.width < 120 || r.height < 60 || r.width > 1500 || r.height > 1000 || text.length > 2500 || !visible(box)) continue;
        if (!elements.some(e => e.contains(box) || box.contains(e))) elements.push(box);
      }
    }
  }
  const signalsOf = el => {
    const hosts = new Set(), links = [];
    for (const node of [el, ...el.querySelectorAll('a[href], iframe[src], img[src]')].slice(0, 60)) {
      const url = node.href || node.src || '';
      try { if (url && /^https?:/.test(url)) { hosts.add(new URL(url).host); if (node.href) links.push(url); } } catch {}
    }
    const markers = [];
    for (let node = el, depth = 0; node && node.nodeType === 1 && depth < 4; node = node.parentElement, depth++) {
      if (node.id) markers.push(node.id);
      if (typeof node.className === 'string') markers.push(...node.className.trim().split(/\s+/).filter(Boolean).slice(0, 6));
    }
    const leafLabel = [...el.querySelectorAll('*')].find(n => n.children.length === 0 && adLabel.test((n.textContent || '').trim()));
    const erid = (el.innerText || '').match(eridText)?.[1] || '';
    const targets = (el.matches('a[href]') ? [el] : [...el.querySelectorAll('a[href]')]).map(targetOf).filter(Boolean);
    const advertiser = [...el.querySelectorAll('*')].some(n => n.children.length === 0 && /^(?:https?:\/\/)?(?:www\.)?author\.today\/?$/i.test((n.textContent || '').trim()));
    const target = targets.find(t => t.url) || targets[0] || (advertiser ? {domain: 'author.today', url: '', via: 'advertiser_label'} : null);
    return {label: leafLabel ? leafLabel.textContent.trim() : '', erid, hosts: [...hosts].slice(0, 15), links: links.slice(0, 15),
            target_domains: target ? [target.domain] : [], target_url: target?.url || '', target_match: target?.via || '',
            markers: [...new Set(markers)].slice(0, 20), work_links: el.querySelectorAll('a[href*="/work/"]').length};
  };
  const serviceLine = value => adLabel.test(value) || /^[•·|—–-]+$/.test(value) || /^(?:author\.today(?:\s+реклама(?:\s*[•·|—-]?\s*\d{1,2}\+)?)?|\d{1,2}\+|обложка|читать|перейти(?: на сайт)?|узнать больше)$/i.test(value);
  // Network creatives print the advertiser's domain above the headline («belleyou.ru»). It stays in the text,
  // but becomes the title only when nothing else is readable.
  const domainLine = value => /^(?:https?:\/\/)?(?:www\.)?[\p{L}\p{N}-]+(?:\.[\p{L}\p{N}-]+)+\/?$/iu.test(value.trim());
  // Call-to-action buttons stay in the text but are never a headline.
  const ctaLine = value => /^(?:посмотреть(?: каталог)?|подробнее|купить|скачать|смотреть|заказать|в каталог|читать(?: далее| онлайн)?|начать читать|установить|записаться|получить|узнать)$/i.test(value);
  // Mandatory warnings that Yandex prints in capitals above or below the headline (credit, medicine, BAA).
  const disclaimer = value => /^(?:есть противопоказания|изучите все условия|соответствующем разделе|оценивайте свои финансовые|ознакомьтесь с условиями|проконсультируйтесь|посоветуйтесь с врачом|необходима консультация|не является лекарств)/i.test(value);
  const meaningful = value => value?.trim() && !serviceLine(value.trim()) && !domainLine(value.trim()) && !ctaLine(value.trim()) && !disclaimer(value.trim());
  const soft = value => (value || '').replace(/­/g, '');
  const label = /^(реклама|advertisement|sponsored|ad|промо|спонсор|партнёрский материал)$/i;
  const clean = value => {
    const lines = [];
    for (const raw of value.split('\n')) {
      const line = raw.replace(/\s+/g, ' ').trim();
      if (!line || label.test(line) || serviceLine(line) || lines[lines.length - 1] === line) continue;
      lines.push(line);
    }
    return lines.join('\n').slice(0, 6000);
  };
  return elements.filter(el => {
    if (!discover || args.site === 'author_today') return true;
    if (el.closest('nav,header,footer,[role="navigation"],[role="contentinfo"]')) return false;
    // Live CONT assigns RTB slot ids to ordinary article photos too. The id alone
    // cannot turn an unlinked editorial image into an ad. Explicit job selectors win.
    if (el.matches('img[id^="yandex_rtb"]') && !el.closest('a[href],[data-ad],.advertisement,.ad-banner,.ad_banner')) return false;
    if (el.matches('[class*="_placeholder_"]') && adLabel.test((el.innerText || '').trim()) && !el.querySelector('a[href],iframe,video,canvas')) return false;
    return true;
  }).filter(el => !elements.some(parent => parent !== el && parent.contains(el))).slice(0, 100).map(el => {
    // AT's control links are outside the creative. Keep the original node for signals and evidence,
    // but read fields from a clone without «Что это?» / «Скрыть». Never click the destination.
    const atBanner = args.site === 'author_today' && el.matches('.aside-banner');
    const content = atBanner ? el.cloneNode(true) : el;
    if (atBanner) content.querySelectorAll('.aside-banner-buttons, button, script, style, [hidden]').forEach(n => n.remove());
    const links = content.matches('a[href]') ? [content] : [...content.querySelectorAll('a[href]')];
    const targetLink = args.site === 'author_today' ? null : (links.find(link => targetOf(link)?.url) || links.find(link => targetOf(link)));
    // A whole-card link holds headline, label, body and CTA: its first meaningful line is the headline.
    const firstLine = value => soft(value).split('\n').map(line => line.trim()).find(meaningful) || '';
    const titleLink = links.find(a => firstLine(a.innerText));
    const a = (atBanner && links.find(a => a.querySelector('img, picture, canvas'))) || targetLink || titleLink || links[0];
    const img = el.matches('img') ? el : el.querySelector('img');
    const heading = content.matches('h1,h2,h3,h4,[data-title]') ? content : content.querySelector('h1,h2,h3,h4,[data-title]');
    const text = soft(content.innerText || (atBanner ? content.textContent : '') || '').trim().slice(0, 6000);
    let image = img?.currentSrc || img?.src || '';
    let imageSource = image ? 'img' : 'none';
    if (!image) {
      for (const node of [el, ...el.querySelectorAll('*')].slice(0, 100)) {
        const match = getComputedStyle(node).backgroundImage.match(/url\(["']?(.*?)["']?\)/);
        if (match) { try { image = new URL(match[1], document.baseURI).href; imageSource = 'css'; } catch {} break; }
      }
    }
    const canvas = el.matches('canvas') ? el : el.querySelector('canvas');
    const frame = el.matches('iframe') ? el : el.querySelector('iframe');
    if (!image && canvas) imageSource = 'canvas';
    else if (!image && frame) imageSource = 'iframe';
    const lineTitle = text.split('\n').find(meaningful);
    const preferredLine = targetLink && !atBanner ? lineTitle : '';
    const domainTitle = text.split('\n').map(line => line.trim()).find(line => domainLine(line) && !serviceLine(line));
    let titleSource = heading?.innerText ? 'heading' : preferredLine ? 'text' : titleLink?.innerText ? 'link' : lineTitle ? 'text' : img?.alt ? 'alt' : domainTitle ? 'domain' : 'fallback';
    let title = soft((heading && firstLine(heading.innerText)) || preferredLine || (titleLink ? firstLine(titleLink.innerText) : '') || lineTitle || img?.alt || domainTitle || 'Баннер без текста').trim().slice(0,300);
    const destination = a?.href || '';
    const frameSrc = frame ? (frame.src || '') : '';
    // A closed ad frame (nothing readable from outside) is kept: its content address identifies the creative.
    if (!text && !image && !destination && imageSource === 'iframe' && frameSrc) { image = frameSrc; title = 'Рекламный фрейм'; titleSource = 'fallback'; }
    // Read the already loaded same-origin creative, at native resolution. A cookie overlay can cover
    // a screenshot, but is not part of these pixels. Tainted/cross-origin canvases fall back to a crop.
    let imagePixels = '';
    if (atBanner && img?.complete && img.naturalWidth > 0 && img.naturalWidth <= 2400 && img.naturalHeight <= 2400) {
      try {
        const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext('2d').drawImage(img, 0, 0);
        const data = c.toDataURL('image/png'); if (data.length < 6000000) imagePixels = data;
      } catch {}
    }
    const r = (atBanner && img ? img : el).getBoundingClientRect();
    return {title, text, destination, image_url: image, clean_text: clean(text), image_source: imageSource,
      at_banner: atBanner,
      image_pixels: imagePixels,
      field_sources: {title: titleSource, text: text ? 'dom' : 'none', destination: destination ? (a === el ? 'self' : 'link') : 'none',
                      image: imageSource, frame_src: frameSrc},
      rect: {x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height}, signals: signalsOf(el)};
  }).filter(item => item.clean_text || item.image_url || item.destination || item.image_source === 'canvas');
}'''


async def shadow_blocks(page, frame, args):
    """Read rendered open/closed shadow trees in our own browser through CDP.

    Yandex creatives can be visible in screenshots while the containing slot has
    empty innerText. No page hooks, ad clicks, redirects or browser security flags.
    """
    try:
        session = await page.context.new_cdp_session(page if frame == page.main_frame else frame)
    except Exception:
        session = await page.context.new_cdp_session(page)  # same-process child frame
    try:
        tree = await session.send('DOM.getDocument', {'depth': -1, 'pierce': True})
        stack, roots = [(tree['root'], [], tree['root'].get('documentURL', ''))], []
        while stack and len(roots) < 100:
            node, ancestors, document_url = stack.pop()
            document_url = node.get('documentURL', document_url)
            attributes = dict(zip(node.get('attributes', [])[::2], node.get('attributes', [])[1::2]))
            markers = ([attributes['id']] if attributes.get('id') else []) + attributes.get('class', '').split()[:6]
            lineage = (markers + ancestors)[:20]
            for root in node.get('shadowRoots', []):
                if root.get('shadowRootType') in ('open', 'closed') and document_url == frame.url:
                    roots.append((root, lineage))
                stack.append((root, lineage, document_url))
            stack.extend((child, lineage, document_url) for child in node.get('children', []))
            if node.get('contentDocument'):
                stack.append((node['contentDocument'], lineage, document_url))
        items = []
        for root, markers in roots:
            resolved = await session.send('DOM.resolveNode', {'backendNodeId': root['backendNodeId'], 'objectGroup': 'radar-shadow'})
            object_id = resolved.get('object', {}).get('objectId')
            if not object_id:
                continue
            result = await session.send('Runtime.callFunctionOn', {'objectId': object_id,
                'functionDeclaration': EXTRACT, 'arguments': [{'value': args}], 'returnByValue': True})
            if result.get('exceptionDetails'):
                continue
            for item in result.get('result', {}).get('value') or []:
                signals = item.setdefault('signals', {})
                signals['markers'] = list(dict.fromkeys(signals.get('markers', []) + markers))[:30]
                item.setdefault('field_sources', {})['dom_scope'] = root['shadowRootType'] + '_shadow_root'
                items.append(item)
        return items
    finally:
        await session.send('Runtime.releaseObjectGroup', {'objectGroup': 'radar-shadow'})
        await session.detach()

# Stage 11: slots of the site adapter on a page without blocks - an empty slot is not an access problem.
SLOTS = r'''selector => {
  const all = [...document.querySelectorAll(selector)];
  const shown = all.filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 5 && r.height > 5 && s.display !== 'none' && s.visibility !== 'hidden'; });
  return {total: all.length, visible: shown.length};
}'''

CHALLENGE = r'''() => {
  const marker = document.querySelector('#challenge-form, #cf-challenge-running, [data-browser-challenge]');
  const visible = marker && marker.getBoundingClientRect().width > 0 && marker.getBoundingClientRect().height > 0;
  const title = document.title.trim();
  return Boolean(visible || /^(just a moment[.!…]*|verify you are human[.!…]*|подтвердите, что вы не робот[.!…]*)$/i.test(title));
}'''


def save_finding(job, item, source, frame, screenshot, run_id=None, extra=None):
    """0.6 signature kept: returns the fingerprint. Stage 14 stores the sighting through the catalog."""
    return catalog.save(job, item, source, frame, screenshot, run_id=run_id, extra=extra)['fingerprint']


def needs_crop(digest):
    rows = db.rows('SELECT crop FROM findings WHERE fingerprint=?', (digest,))
    return not rows or not rows[0]['crop']


async def crop_block(page, job, item, offset):
    """Stage 13: picture of the block alone (OCR input and a precise preview). None when it cannot be cut."""
    pixels = item.get('image_pixels') or ''
    if item.get('at_banner') and pixels.startswith('data:image/png;base64,'):
        try:
            raw = base64.b64decode(pixels.split(',', 1)[1], validate=True)
            if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) < 4_500_000:
                name = f"job-{job['id']}-{uuid.uuid4().hex}.png"
                (db.DATA / 'evidence' / name).write_bytes(raw)
                item['field_sources']['crop'] = 'loaded_image_pixels'
                return name
        except (ValueError, OSError):
            pass
    rect = item.get('rect') or {}
    width, height = min(float(rect.get('w') or 0), 2400), min(float(rect.get('h') or 0), 2400)
    if width < 8 or height < 8:
        return None
    clip = {'x': max(0, float(rect.get('x') or 0) + offset[0]), 'y': max(0, float(rect.get('y') or 0) + offset[1]), 'width': width, 'height': height}
    name = f"job-{job['id']}-{uuid.uuid4().hex}.png"
    try:
        await page.screenshot(path=str(db.DATA / 'evidence' / name), clip=clip, full_page=True, timeout=15000)
    except Exception:
        return None
    return name


async def frame_offset(page, frame):
    if frame == page.main_frame:
        return (0, 0)
    try:
        box = await (await frame.frame_element()).bounding_box()
    except Exception:
        box = None
    return (box['x'], box['y']) if box else None


def browser_gone(exc):
    text = str(exc)
    return any(marker in text for marker in ('has been closed', 'Target closed', 'Connection closed', 'Browser closed', 'Browser has been closed'))


async def collect(job, *, start=0, on_page=None, headless=None, run_id=None):
    """Collect ad candidates from job URLs. `start` resumes after already completed pages.

    on_page(done, count, page) receives a per-URL result: index, url, status (ok/empty/error/user_action),
    found, error_code, message, screenshot, login (True/False/None when no login marker is configured).
    Returns {'status', 'count', 'errors', 'user_action', 'session', 'error_code'}; the job row and log are updated as before.
    """
    count, errors, user_action = 0, [], False
    login_selector = (job.get('login_selector') or '').strip() if hasattr(job, 'get') else ''
    login_seen = []
    codes = []
    headed = bool(job.get('headed', False)) if headless is None else not headless
    log = lambda level, message, code=None: db.log(job['id'], level, message, run_id=run_id, error_code=code)
    urls = json.loads(job['urls'])
    log('info', f'Сбор начат. Режим: {"видимый браузер" if headed else "фоновый"}.' + (f' Продолжение со страницы {start + 1} из {len(urls)}.' if start else ''))
    try:
        async with async_playwright() as pw:
            context = await open_context(pw, job['profile_id'], headless=not headed)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                resource_failures = ResourceFailures()
                page.on('requestfailed', resource_failures.observe)
                for index, url in enumerate(urls):
                    if index < start:
                        continue
                    result = {'index': index, 'url': url, 'status': 'ok', 'found': 0, 'error_code': None, 'message': None,
                              'screenshot': None, 'login': None, 'started_at': db.now()}
                    try:
                        resource_failures.reset()
                        response = await page.goto(url, wait_until='domcontentloaded', timeout=40000)
                        await page.wait_for_timeout(job['wait_seconds'] * 1000)
                        await page.evaluate('window.scrollTo(0, Math.min(500, document.body.scrollHeight))')
                        await page.wait_for_timeout(500)
                        await page.evaluate('window.scrollTo(0,0)')
                        screenshot = f"job-{job['id']}-{uuid.uuid4().hex}.png"
                        await page.screenshot(path=str(db.DATA / 'evidence' / screenshot), full_page=True, timeout=20000)
                        result['screenshot'] = screenshot
                        log('info', f'Скриншот страницы: {screenshot}')
                        if login_selector:
                            try:
                                result['login'] = await page.query_selector(login_selector) is not None
                            except Exception as exc:
                                raise SelectorError('Некорректный CSS-селектор признака входа.') from exc
                            login_seen.append(result['login'])
                            if not result['login']:
                                log('warning', 'Признак входа не найден: сессия профиля не активна или истекла. Импортируйте сессию заново или войдите в профиль.', 'SESSION_EXPIRED')
                        page_count, failed_frames = 0, 0
                        seen = set()
                        extracted = []
                        # Stage 11: the site adapter by domain; a selector of the job always wins over the adapter's.
                        adapter = adapters.for_url(url)
                        own_selector = (job['selector'] or '').strip()
                        extract_args = {'selector': own_selector or adapter.selector, 'discover': not own_selector, 'site': adapter.id}
                        for frame in page.frames:
                            try:
                                if frame != page.main_frame and not await (await frame.frame_element()).is_visible():
                                    continue
                                items = await frame.evaluate(EXTRACT, extract_args)
                                try:
                                    items.extend(await shadow_blocks(page, frame, extract_args))
                                except Exception:
                                    failed_frames += 1
                            except Exception as exc:
                                if 'not a valid selector' in str(exc) or 'not a valid' in str(exc):
                                    raise SelectorError('Некорректный CSS-селектор рекламного блока.') from exc
                                failed_frames += 1
                                continue
                            extracted.append((frame, items))
                        read_frames = {frame.url for frame, items in extracted if items and frame != page.main_frame}
                        for frame, items in extracted:
                            offset = None
                            for item in items:
                                sources = item.get('field_sources') or {}
                                if item.get('title') == 'Рекламный фрейм' and item.get('image_source') == 'iframe' and sources.get('frame_src') in read_frames:
                                    continue  # the frame itself was read: its blocks are the finding, not the closed placeholder
                                digest = fingerprint(job['profile_id'], url, frame.url, item)
                                if digest in seen:
                                    continue
                                extra = {}
                                previous = db.rows('SELECT id,crop,ocr_text,ocr_status,field_sources FROM findings WHERE fingerprint=?', (digest,))
                                previous = previous[0] if previous else None
                                if needs_crop(digest):
                                    offset = offset or await frame_offset(page, frame)
                                    crop = await crop_block(page, job, item, offset) if offset is not None else None
                                    if crop:
                                        extra['crop'] = crop
                                        if item.get('image_source') in ocr.IMAGE_SOURCES and ocr.enabled():
                                            if item.get('at_banner') and not item.get('text'):
                                                extra['ocr_text'], extra['ocr_status'] = await asyncio.to_thread(ocr.recognize, db.DATA / 'evidence' / crop, psm=11)
                                            else:
                                                extra['ocr_text'], extra['ocr_status'] = await asyncio.to_thread(ocr.recognize, db.DATA / 'evidence' / crop)
                                        elif item.get('image_source') in ocr.IMAGE_SOURCES:
                                            extra['ocr_status'] = 'off'
                                if item.get('at_banner') and not item.get('text'):
                                    # OCR enriches display text, never the DOM identity. Reuse it on a repeat;
                                    # also allow enabling OCR after the first (OCR-off) collection.
                                    try:
                                        cached_sources = json.loads(previous['field_sources']) if previous else {}
                                        if not isinstance(cached_sources, dict):
                                            cached_sources = {}
                                    except (ValueError, TypeError):
                                        cached_sources = {}
                                    refreshed = 'ocr_text' in extra
                                    if 'ocr_text' not in extra and previous:
                                        extra.update(ocr_text=previous['ocr_text'], ocr_status=previous['ocr_status'])
                                        if ocr.enabled() and previous['crop'] and (not extra['ocr_text'] or cached_sources.get('ocr_pipeline') != ocr.SPARSE_REVISION):
                                            extra['ocr_text'], extra['ocr_status'] = await asyncio.to_thread(ocr.recognize, db.DATA / 'evidence' / previous['crop'], psm=11)
                                            refreshed = True
                                    extra['field_sources'] = {**cached_sources, **sources}
                                    if refreshed and extra.get('ocr_status') in ('done', 'empty'):
                                        extra['field_sources']['ocr_pipeline'] = ocr.SPARSE_REVISION
                                    if extra.get('ocr_text'):
                                        extra['clean_text'] = extra['ocr_text']
                                        extra['field_sources']['clean_text'] = 'ocr'
                                signals = item.get('signals') or {}
                                block_type, block_reason = adapter.classify({**item, 'frame_url': frame.url}, signals)
                                extra.update(block_type=block_type, block_reason=block_reason, adapter=f'{adapter.id}@{adapter.version}',
                                             block_evidence={k: signals.get(k) for k in ('label', 'erid', 'hosts', 'markers', 'work_links', 'target_domains', 'target_url', 'target_match') if signals.get(k)})
                                item = {key: value for key, value in item.items() if key not in ('rect', 'signals', 'at_banner', 'image_pixels')}
                                save_finding(job, item, url, frame.url, screenshot, run_id=run_id, extra=extra)
                                if previous and 'ocr_text' in extra:
                                    catalog.set_ocr(previous['id'], extra['ocr_text'], extra.get('ocr_status') or '')
                                seen.add(digest)
                                page_count += 1
                                count += 1
                        result['found'] = page_count
                        log('info', f'{url}: найдено блоков {page_count}. Скриншот: {screenshot}')
                        if failed_frames:
                            log('warning', f'Недоступных фреймов: {failed_frames}. Результат может быть неполным.', 'FRAMES_UNAVAILABLE')
                        # Already extracted blocks are retained even on an HTTP/challenge error page.
                        blocked = response and response.status in (401, 403, 429)
                        challenge = await page.evaluate(CHALLENGE)
                        if blocked or challenge:
                            raise UserActionNeeded('Доступ ограничен или требуется проверка. Найденные блоки сохранены. Откройте профиль вручную.')
                        if response and response.status >= 400:
                            raise HttpStatusError(response.status, f'Страница вернула HTTP {response.status}. Найденные блоки сохранены.')
                        resource_failures.check()
                        if not page_count:
                            result['status'] = 'empty'
                            slots = await page.evaluate(SLOTS, adapter.slots) if not own_selector else {'total': 0, 'visible': 0}
                            if slots['total']:
                                result['error_code'] = 'EMPTY_SLOT'
                                result['message'] = (f'Рекламных мест на странице: {slots["total"]} (видимых {slots["visible"]}), заполненных блоков нет. '
                                                     f'Адаптер {adapter.title} {adapter.version}. Это пустой слот, не ошибка доступа.')
                            else:
                                result['error_code'] = 'NO_BLOCKS'
                                result['message'] = (f'Блоки не найдены, известных рекламных мест на странице нет (адаптер {adapter.title} {adapter.version}). '
                                                     'Проверьте скриншот и CSS-селектор: это не доказывает отсутствие рекламы.')
                            log('warning', result['message'], result['error_code'])
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if browser_gone(exc):
                            raise  # the browser is gone: later pages cannot succeed, the run is retried from here
                        message = readable_error(exc)
                        errors.append(message)
                        user_action = user_action or isinstance(exc, UserActionNeeded)
                        result.update(status='user_action' if isinstance(exc, UserActionNeeded) else 'error',
                                      error_code=error_code(exc), message=message)
                        codes.append(result['error_code'])
                        log('error', f'{url}: {message}', result['error_code'])
                    result['finished_at'] = db.now()
                    if on_page:
                        on_page(index + 1, count, result)
            finally:
                await close_context(context, job['profile_id'])
        status = 'partial' if errors and count else 'error' if errors else 'done'
        db.execute('UPDATE jobs SET status=?,last_count=?,last_error=? WHERE id=?',
                   (status, count, '\n'.join(errors) or None, job['id']))
        log('info', f'Сбор завершён. Найдено блоков: {count}. Ошибок: {len(errors)}.')
        session = None if not login_seen else 'active' if any(login_seen) else 'expired'
        return {'status': status, 'count': count, 'errors': errors, 'user_action': user_action, 'session': session,
                'error_code': codes[0] if codes else None}
    except asyncio.CancelledError:
        db.execute("UPDATE jobs SET status='stopped',last_count=? WHERE id=?", (count, job['id']))
        log('warning', 'Сбор остановлен. Сохранённые находки остаются в базе.')
        raise
    except Exception as exc:
        message = readable_error(exc)
        db.execute("UPDATE jobs SET status='error',last_error=?,last_count=? WHERE id=?", (message, count, job['id']))
        log('error', message, error_code(exc))
        return {'status': 'failed', 'count': count, 'errors': [message], 'user_action': False, 'session': None,
                'error_code': error_code(exc)}
