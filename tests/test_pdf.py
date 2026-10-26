import asyncio
import base64
from io import BytesIO

from playwright.async_api import async_playwright
from pypdf import PdfReader, PdfWriter

from skoda_manual import launch_browser, make_document, render_pdf


def test_offline_pdf_has_text_images_outline_and_clickable_toc():
    async def render():
        async with async_playwright() as playwright:
            browser = await launch_browser(playwright)
            try:
                nodes = [
                    {'label': 'Notice', 'anchor': 'root', 'target': None, 'depth': 0},
                    {'label': 'Chapitre illustré', 'anchor': 's0', 'target': 'a', 'depth': 1},
                ]
                symbol = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" '
                                          b'width="512" height="512">'
                                          b'<rect width="512" height="512"/></svg>').decode()
                icons = f'<img class="symbol" data-role="symbol" src="data:image/svg+xml;base64,{symbol}">'
                html = make_document('Notice de test', 'fr', nodes, {'a': (icons * 30) + '''
                    <p>Contenu vérifiable : café.</p>
                    <img alt="test" src="data:image/svg+xml;base64,
                    PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCI+
                    PHJlY3Qgd2lkdGg9IjEwIiBoZWlnaHQ9IjEwIiBmaWxsPSJyZWQiLz48L3N2Zz4=">
                '''})
                return await render_pdf(browser, html)
            finally:
                await browser.close()

    pdf = asyncio.run(render())
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(pdf)), outline_item='Documentation')
    merged = BytesIO()
    writer.write(merged)
    reader = PdfReader(merged)
    assert 2 <= len(reader.pages) <= 3
    assert 'café' in ''.join(page.extract_text() for page in reader.pages)
    assert reader.outline
    assert any(page.get('/Annots') for page in reader.pages)
    # SVG is rendered as vector drawing instructions rather than a raster XObject.
    assert any(b' re' in page.get_contents().get_data() for page in reader.pages)
