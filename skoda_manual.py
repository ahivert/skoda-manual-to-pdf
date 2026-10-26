"""Export Škoda documentation using an ephemeral browser session."""

import argparse
import asyncio
import base64
import re
import sys
from getpass import getpass
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, Doctype, ProcessingInstruction
from playwright.async_api import Error as BrowserError
from playwright.async_api import TimeoutError as BrowserTimeout
from playwright.async_api import async_playwright
from pypdf import PdfReader, PdfWriter, Transformation

DIGITAL = 'https://digital-manual.skoda-auto.com'
CATALOGUE = 'https://www.skoda.fr/apps/manuals/995/fr-FR/api/'


def normalize_vin(value):
    vin = value.strip().upper()
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        raise argparse.ArgumentTypeError('VIN must contain 17 letters/digits (excluding I, O, Q).')
    return vin


def flatten_tree(trees):
    nodes = []

    def walk(items, depth):
        for item in items:
            nodes.append({
                'label': BeautifulSoup(item['label'], 'html.parser').get_text(' ', strip=True),
                'target': item.get('linkTarget'),
                'depth': depth, 'anchor': f's{len(nodes)}',
            })
            walk(item.get('children') or [], depth + 1)

    walk(trees, 0)
    return nodes


def safe_asset_url(value):
    url = urljoin(DIGITAL, value)
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'digital-manual.skoda-auto.com'
            or parts.path not in {'/public/media', '/default/public/media'}):
        raise ValueError('Unexpected manual image URL; refusing to fetch it.')
    return url


def clean_topic(html, images, remove_index=False):
    soup = BeautifulSoup(html, 'html.parser')
    if remove_index:
        # The root topic repeats the entire tree; preserve any actual introduction.
        for tag in soup.select('.tttitle, .ttchildren'):
            if tag.parent is not None:
                tag.decompose()
    for metadata in soup.find_all(string=lambda s: isinstance(s, (Comment, Doctype, ProcessingInstruction))):
        metadata.extract()
    for tag in soup.select('script, style, iframe, object, embed, form, link, meta, base'):
        tag.decompose()
    allowed = {
        'div', 'section', 'article', 'p', 'span', 'br', 'hr', 'strong', 'b', 'em', 'i',
        'u', 'small', 'sub', 'sup', 'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'table',
        'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'figure', 'figcaption',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'img', 'blockquote',
    }
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed:
            tag.unwrap()
            continue
        attributes = {k: v for k, v in tag.attrs.items() if k in {
            'id', 'class', 'lang', 'dir', 'colspan', 'rowspan', 'alt', 'title',
            'data-type', 'data-role',
        }}
        if tag.name == 'img':
            src = tag.get('data-src') or tag.get('src')
            if src not in images:
                raise ValueError('Missing manual image; refusing to produce an incomplete PDF.')
            attributes['src'] = images[src]
        if tag.name == 'a' and str(tag.get('href', '')).startswith('#'):
            attributes['href'] = tag['href']
        tag.attrs = attributes
    return str(soup)


async def request_json(request, url, params=None):
    response = await request.get(url, params=params, max_redirects=0, timeout=60000)
    try:
        if response.status != 200:
            raise ValueError(f'Manual service returned HTTP {response.status}. Please retry later.')
        if 'json' not in response.headers.get('content-type', ''):
            raise ValueError('Manual session was not accepted or has expired; please rerun the command.')
        data = await response.json()
        if data.get('errorCode') not in (None, 'NoError'):
            raise ValueError(data.get('errorMessage') or data['errorCode'])
        return data
    finally:
        await response.dispose()


def make_document(title, language, nodes, topics):
    first = {}
    for node in nodes:
        if node['target']:
            first.setdefault(node['target'], node['anchor'])
    toc, sections, seen = [], [], set()
    for node in nodes:
        label = escape(node['label'])
        target = node['target']
        anchor = first.get(target, node['anchor'])
        if 1 <= node['depth'] <= 2:
            toc.append(f'<li class="toc-depth-{node["depth"]}">'
                       f'<a href="#{anchor}">{label}</a></li>')
        if target and target in seen:
            continue
        if target:
            seen.add(target)
        level = min(node['depth'] + 1, 6)
        body = topics[target] if target else ''
        if node['depth'] == 0:
            content = BeautifulSoup(body, 'html.parser')
            if not content.get_text(strip=True) and not content.find('img'):
                continue
        section_class = ' class="chapter"' if node['depth'] == 1 else ''
        sections.append(f'<section id="{anchor}"{section_class}>'
                        f'<h{level}>{label}</h{level}>{body}</section>')
    contents_title = 'Sommaire' if language == 'fr' else 'Contents'
    subtitle = escape(' — '.join(n['label'] for n in nodes if n['depth'] == 0))
    direction = 'rtl' if language in {'ar', 'he'} else 'ltr'
    return f'''<!doctype html><html lang="{escape(language)}" dir="{direction}">
<head><meta charset="utf-8"><title>{escape(title)}</title><style>
@page {{ size: A4; margin: 18mm; }}
body {{ font: 10pt/1.45 Arial, sans-serif; color: #151515; }}
h1,h2,h3,h4,h5,h6 {{ break-after: avoid; color: #123d32; }}
h1 {{ font-size: 22pt; }} h2 {{ font-size: 17pt; }} h3 {{ font-size: 14pt; }}
p {{ orphans: 3; widows: 3; }}
img {{ max-width: 100%; max-height: 225mm; object-fit: contain; }}
.icon, .symbol, img[data-role="symbol"], img[data-role="icon"],
img[data-role="safety-alert-symbol"] {{
    width: 1.2em; height: 1.2em; vertical-align: middle;
}}
figure, tr {{ break-inside: avoid; }}
table {{ border-collapse: collapse; width: 100%; }}
td,th {{ border: 1px solid #aaa; padding: 4px; }}
a {{ color: #174f40; }} nav ul {{ list-style: none; padding: 0; }}
nav {{ break-after: page; }}
.toc-depth-1 {{ font-weight: bold; margin-top: 6pt; }}
.toc-depth-2 {{ margin-inline-start: 12pt; }}
.chapter {{ break-before: page; }}
[data-role="bridgehead"], [data-type="titel"] {{ font-weight: bold; }}
[data-type*="warn"], [data-type*="gefahr"] {{ border-inline-start: 3px solid #b33; padding: 6px; }}
</style></head><body><h1>{escape(title)}</h1><p>{subtitle}</p>
<nav aria-label="{contents_title}"><h2>{contents_title}</h2><ul>{''.join(toc)}</ul></nav>
{''.join(sections)}</body></html>'''


def save_output(path, data):
    # Exclusive creation protects existing files even if they appear during the export.
    with path.open('xb') as stream:
        try:
            stream.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise


async def open_manuals(page, vin, language_title):
    await page.goto('https://www.skoda.fr/apps/manuals/Models', wait_until='domcontentloaded')
    try:
        await page.get_by_role('button', name='Refuser les cookies optionnels').click(timeout=15000)
    except BrowserTimeout:
        # Some regions do not display the optional-cookie dialog.
        if await page.get_by_role('dialog').count():
            raise
    await page.get_by_role('textbox').first.fill(vin)
    await page.locator('main [aria-expanded]').click()
    await page.get_by_text(language_title, exact=True).click()
    await page.get_by_role('button', name='Afficher', exact=True).click()
    await page.wait_for_url(f'{DIGITAL}/w/**/welcome', timeout=60000)
    links = page.locator('a[href*="/show/"]')
    await links.first.wait_for(timeout=60000)
    return await links.evaluate_all('(els) => els.map(e => ({url:e.href, title:e.innerText}))')


async def collect_topics(request, nodes, locale):
    keys = list(dict.fromkeys(n['target'] for n in nodes if n['target']))
    semaphore = asyncio.Semaphore(4)
    completed = 0

    async def fetch(key):
        nonlocal completed
        async with semaphore:
            data = await request_json(request, DIGITAL + '/api/vw-topic/V1/topic', {
                'key': key, 'displaytype': 'desktop', 'language': locale,
            })
            body = data.get('bodyHtml')
            if not isinstance(body, str) or not body.strip():
                raise ValueError(f'Empty chapter {key}; export aborted.')
            completed += 1
            if completed % 25 == 0 or completed == len(keys):
                print(f'  Chapters: {completed}/{len(keys)}', file=sys.stderr)
            return key, body

    return dict(await asyncio.gather(*(fetch(key) for key in keys)))


async def collect_images(request, topics):
    sources = set()
    for html in topics.values():
        for image in BeautifulSoup(html, 'html.parser').find_all('img'):
            source = image.get('data-src') or image.get('src')
            if not source:
                raise ValueError('An image has no source; export aborted.')
            sources.add(source)
    semaphore = asyncio.Semaphore(4)
    completed = 0

    async def fetch(source):
        nonlocal completed
        url = safe_asset_url(source)
        async with semaphore:
            response = await request.get(url, max_redirects=0, timeout=60000)
            try:
                mime = response.headers.get('content-type', '').split(';')[0]
                if response.status != 200 or mime not in {
                    'image/png', 'image/jpeg', 'image/gif', 'image/svg+xml', 'image/webp',
                }:
                    raise ValueError('A manual image could not be retrieved; export aborted.')
                body = await response.body()
                if not body:
                    raise ValueError('An image is empty; export aborted.')
                completed += 1
                if completed % 50 == 0 or completed == len(sources):
                    print(f'  Images: {completed}/{len(sources)}', file=sys.stderr)
                return source, f'data:{mime};base64,' + base64.b64encode(body).decode('ascii')
            finally:
                await response.dispose()

    return dict(await asyncio.gather(*(fetch(source) for source in sorted(sources))))


async def render_pdf(browser, html):
    context = await browser.new_context(java_script_enabled=False, offline=True)
    try:
        page = await context.new_page()
        await page.route('**/*', lambda route: route.abort())
        await page.set_content(html, wait_until='load')
        failures = await page.locator('img').evaluate_all(
            '(imgs) => imgs.filter(img => !img.complete || img.naturalWidth === 0).length'
        )
        if failures:
            raise ValueError(f'{failures} images failed to render; export aborted.')
        return await page.pdf(format='A4', print_background=True, outline=True, tagged=True)
    finally:
        await context.close()


def running_headers(reader, chapters, default):
    candidates = []
    chapter_titles = {''.join(title.split()): title
                      for title in [*chapters, 'Sommaire', 'Contents']}

    def walk(outline, depth=0):
        for entry in outline:
            if isinstance(entry, list):
                walk(entry, depth + 1)
            elif ''.join(entry['/Title'].split()) in chapter_titles:
                page = reader.get_destination_page_number(entry)
                if page is not None and page >= 0:
                    title = chapter_titles[''.join(entry['/Title'].split())]
                    candidates.append((page, title, depth))

    walk(reader.outline)
    # A subsection may share a chapter's title; only its shallowest bookmarks count.
    depths = {}
    for _, title, depth in candidates:
        depths[title] = min(depths.get(title, depth), depth)
    starts = {page: title for page, title, depth in candidates if depth == depths[title]}
    headers = []
    current = default
    for index in range(len(reader.pages)):
        current = starts.get(index, current)
        headers.append(current)
    return headers


async def add_page_furniture(browser, pdf, title, chapters, language):
    reader = PdfReader(BytesIO(pdf))
    contents_title = 'Sommaire' if language == 'fr' else 'Contents'
    headers = running_headers(reader, chapters, contents_title)
    sheets = ''.join(
        f'<section class="sheet"><header><span>{escape(title)}</span>'
        f'<span>{escape(chapter)}</span></header>'
        f'<footer>{index + 1} / {len(headers)}</footer></section>'
        for index, chapter in enumerate(headers)
    )
    overlays = await render_pdf(browser, f'''<!doctype html><html lang="{escape(language)}">
<head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 0; }}
body {{ margin: 0; font: 8pt Arial, sans-serif; color: #555; }}
.sheet {{ position: relative; width: 210mm; height: 297mm; break-after: page; }}
.sheet:last-child {{ break-after: auto; }}
header {{ position: absolute; top: 7mm; left: 18mm; right: 18mm;
          display: flex; justify-content: space-between; gap: 8mm;
          border-bottom: 0.5pt solid #bbb; padding-bottom: 2mm; }}
header span {{ max-width: 48%; }} header span:last-child {{ text-align: right; }}
footer {{ position: absolute; bottom: 7mm; width: 100%; text-align: center; }}
</style></head><body>{sheets}</body></html>''')
    overlay_reader = PdfReader(BytesIO(overlays))
    if len(overlay_reader.pages) != len(reader.pages):
        raise ValueError('Page header/footer pagination mismatch; export aborted.')
    writer = PdfWriter(clone_from=reader)
    for page, overlay in zip(writer.pages, overlay_reader.pages, strict=True):
        if page.rotation:
            page.transfer_rotation_to_content()
        scale = Transformation().scale(
            float(page.mediabox.width) / float(overlay.mediabox.width),
            float(page.mediabox.height) / float(overlay.mediabox.height),
        )
        page.merge_transformed_page(overlay, scale)
        page.compress_content_streams()
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


async def launch_browser(playwright):
    try:
        return await playwright.chromium.launch()
    except BrowserError as error:
        if "Executable doesn't exist at" not in str(error):
            raise
    if not sys.stdin.isatty():
        raise ValueError(
            'Chromium is missing. Run interactively to install it, or run '
            'python -m playwright install chromium in the CLI environment.'
        )
    answer = input('Chromium is missing. Download and install it now? [y/N] ').strip().lower()
    if answer not in {'y', 'yes'}:
        raise ValueError('Chromium installation cancelled; no manual was downloaded.')
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'playwright', 'install', 'chromium',
    )
    if await process.wait() != 0:
        raise ValueError('Chromium installation failed; check network access and available disk space.')
    return await playwright.chromium.launch()


async def export(vin, language, output, list_languages=False):
    async with async_playwright() as playwright:
        browser = await launch_browser(playwright)
        try:
            context = await browser.new_context()
            request = context.request
            data = await request_json(request, CATALOGUE + 'Languages', {'vin': vin})
            languages = data.get('results', [])
            if not languages:
                raise ValueError('No documentation languages were returned for this VIN.')
            if list_languages or not language:
                for item in languages:
                    print(f'{item["code"]:5} {item["title"]}')
            if list_languages:
                return
            language = language or input('Manual language code: ').strip()
            selected = next((item for item in languages if item['code'] == language), None)
            if not selected:
                raise ValueError('Choose a language code returned by --list-languages.')
            output = output or Path(f'manual-{language}.pdf')
            if output.exists():
                raise FileExistsError(f'{output} already exists; choose another output path.')
            if not output.parent.is_dir():
                raise ValueError('The output directory must already exist.')
            data = await request_json(request, CATALOGUE + 'Manuals', {'vin': vin, 'language': language})
            documents = data.get('results', [])
            if not documents:
                raise ValueError('No documents were returned for this VIN and language.')
            writer = PdfWriter()
            header_chapters, document_titles = [], []
            if any(doc.get('manualType') == 'Digital' for doc in documents):
                print('Opening an ephemeral manual session…', file=sys.stderr)
                page = await context.new_page()
                manuals = await open_manuals(page, vin, selected['title'])
                for manual in manuals:
                    parts = urlsplit(manual['url'])
                    if parts.scheme != 'https' or parts.netloc != 'digital-manual.skoda-auto.com':
                        raise ValueError('Unexpected digital manual host.')
                    root = parse_qs(parts.query).get('ct', [parts.path.rsplit('/', 1)[-1]])[0]
                    locale = parts.path.split('/')[2]
                    title = manual['title'].splitlines()[0]
                    document_titles.append(title)
                    header_chapters.append(title)
                    print(f'Fetching {title}…', file=sys.stderr)
                    tree = await request_json(request, DIGITAL + '/api/web/V6/topic', {
                        'key': root, 'displaytype': 'topic', 'language': locale, 'query': 'undefined',
                    })
                    nodes = flatten_tree(tree.get('trees') or [])
                    if not nodes:
                        raise ValueError('The manual table of contents is empty.')
                    header_chapters.extend(n['label'] for n in nodes if n['depth'] == 1)
                    topics = await collect_topics(request, nodes, locale)
                    images = await collect_images(request, topics)
                    roots = {n['target'] for n in nodes if n['depth'] == 0}
                    topics = {key: clean_topic(body, images, remove_index=key in roots)
                              for key, body in topics.items()}
                    pdf = await render_pdf(browser, make_document(title, language, nodes, topics))
                    writer.append(PdfReader(BytesIO(pdf)), outline_item=title)
            for doc in documents:
                if doc.get('manualType') == 'Digital':
                    continue
                manual = doc.get('pdfManual') or {}
                url = manual.get('pdfFileUrl', '')
                parts = urlsplit(url)
                if parts.scheme != 'https' or parts.netloc != 'ownersmanuals.blob.core.windows.net':
                    raise ValueError('Unexpected PDF source; refusing to download it.')
                response = await request.get(url, max_redirects=0, timeout=60000)
                try:
                    pdf = await response.body()
                    if response.status != 200 or not pdf.startswith(b'%PDF-'):
                        raise ValueError('A source PDF could not be retrieved.')
                    title = manual.get('manualFamily', 'Manual')
                    document_titles.append(title)
                    header_chapters.append(title)
                    writer.append(PdfReader(BytesIO(pdf)), outline_item=title)
                finally:
                    await response.dispose()
            if not writer.pages:
                raise ValueError('No pages were generated.')
            result = BytesIO()
            writer.write(result)
            print('Adding chapter headers and global page numbers…', file=sys.stderr)
            title = document_titles[0] if len(document_titles) == 1 else 'Škoda'
            pdf = await add_page_furniture(browser, result.getvalue(), title, header_chapters, language)
            save_output(output, pdf)
            print(f'Saved {output} ({len(writer.pages)} pages).')
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vin', nargs='?', type=normalize_vin, help='VIN (prompted privately when omitted)')
    parser.add_argument('-l', '--language', help='Manual language code, e.g. fr or en')
    parser.add_argument('-o', '--output', type=Path, help='Output PDF (default: manual-LANGUAGE.pdf)')
    parser.add_argument('--list-languages', action='store_true', help='List languages without downloading manuals')
    args = parser.parse_args()
    try:
        vin = args.vin or normalize_vin(getpass('VIN: '))
        asyncio.run(export(vin, args.language, args.output, args.list_languages))
    except (ValueError, OSError, BrowserError, argparse.ArgumentTypeError) as error:
        # Request errors may contain the VIN in a URL; redact it in terminal diagnostics.
        message = str(error).splitlines()[0]
        private_vin = args.vin or locals().get('vin')
        if private_vin:
            message = message.replace(private_vin, '[VIN]')
        print(f'Error: {message}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled; no completed PDF was written.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
