import asyncio
from io import BytesIO

from playwright.async_api import async_playwright
from pypdf import PdfReader, PdfWriter

from skoda_manual import add_page_furniture, make_document, render_pdf, running_headers


def test_running_headers_follow_bookmark_page_boundaries():
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=595, height=842)
    writer.add_outline_item('Chapter one', 1)
    subsection = writer.add_outline_item('Unrelated subsection', 2)
    writer.add_outline_item('Chapter two', 2, parent=subsection)
    # Chromium may omit whitespace where a long heading wraps onto another line.
    writer.add_outline_item('Chaptertwo', 3)
    buffer = BytesIO()
    writer.write(buffer)
    reader = PdfReader(buffer)
    assert running_headers(reader, ['Chapter one', 'Chapter two'], 'Sommaire') == [
        'Sommaire', 'Chapter one', 'Chapter one', 'Chapter two',
    ]


def test_global_page_numbers_and_chapter_headers_preserve_pdf_navigation():
    async def build():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                nodes = [
                    {'label': 'Notice', 'anchor': 'root', 'target': None, 'depth': 0},
                    {'label': 'Premier chapitre', 'anchor': 'a', 'target': 'a', 'depth': 1},
                    {'label': 'Deuxième chapitre', 'anchor': 'b', 'target': 'b', 'depth': 1},
                ]
                html = make_document('Notice test', 'fr', nodes, {'a': '<p>First.</p>', 'b': '<p>Second.</p>'})
                pdf = await render_pdf(browser, html)
                writer = PdfWriter()
                writer.append(PdfReader(BytesIO(pdf)), outline_item='Manual')
                combined = BytesIO()
                writer.write(combined)
                return await add_page_furniture(
                    browser, combined.getvalue(), 'Notice test',
                    ['Premier chapitre', 'Deuxième chapitre'], 'fr',
                )
            finally:
                await browser.close()

    reader = PdfReader(BytesIO(asyncio.run(build())))
    assert len(reader.pages) == 3
    for index, page in enumerate(reader.pages):
        assert f'{index + 1} / 3' in page.extract_text()
    assert reader.pages[1].extract_text().count('Premier chapitre') == 2
    assert reader.pages[2].extract_text().count('Deuxième chapitre') == 2
    assert reader.outline
    assert reader.pages[1]['/Contents'].get_object()['/Filter'] == '/FlateDecode'
    assert reader.pages[0].get('/Annots')
    for ref, expected_page in zip(reader.pages[0]['/Annots'], [1, 2], strict=True):
        anchor = str(ref.get_object()['/Dest'])
        assert reader.get_destination_page_number(reader.named_destinations[anchor]) == expected_page
