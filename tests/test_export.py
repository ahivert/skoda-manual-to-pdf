import asyncio

import pytest
from bs4 import BeautifulSoup

from skoda_manual import make_document, request_json, save_output


def test_document_has_clickable_toc_and_each_topic_once():
    nodes = [
        {'label': 'Manual <name>', 'anchor': 's0', 'depth': 0, 'target': None},
        {'label': 'Chapter', 'anchor': 's1', 'depth': 1, 'target': 'a'},
        {'label': 'Repeated reference', 'anchor': 's2', 'depth': 2, 'target': 'a'},
    ]
    html = make_document('Owner’s manual', 'fr', nodes, {'a': '<p>Unique content</p>'})
    assert 'href="#s1"' in html
    assert html.count('Unique content') == 1
    assert 'Manual &lt;name&gt;' in html
    assert '<html lang="fr"' in html


def test_contents_is_localized_and_omits_deep_topics_without_losing_them():
    nodes = [
        {'label': 'Notice', 'anchor': 's0', 'depth': 0, 'target': None},
        {'label': 'Major chapter', 'anchor': 's1', 'depth': 1, 'target': None},
        {'label': 'Section', 'anchor': 's2', 'depth': 2, 'target': None},
        {'label': 'Detailed topic', 'anchor': 's3', 'depth': 3, 'target': 'a'},
    ]
    soup = BeautifulSoup(make_document('Manual', 'fr', nodes, {'a': '<p>Complete content</p>'}), 'html.parser')
    assert soup.nav.h2.get_text() == 'Sommaire'
    assert len(soup.nav.select('a')) == 2
    assert soup.nav.select_one('a[href="#s3"]') is None
    assert soup.select_one('#s3').get_text().endswith('Complete content')
    assert soup.select_one('#s1')['class'] == ['chapter']
    assert soup.select_one('#s0') is None


def test_saving_never_overwrites_existing_files(tmp_path):
    path = tmp_path / 'manual.pdf'
    path.write_bytes(b'existing')
    with pytest.raises(FileExistsError):
        save_output(path, b'%PDF-new')
    assert path.read_bytes() == b'existing'


def test_saving_new_pdf(tmp_path):
    path = tmp_path / 'manual.pdf'
    save_output(path, b'%PDF-test')
    assert path.read_bytes() == b'%PDF-test'


class HtmlResponse:
    status = 200

    def __init__(self):
        self.headers = {'content-type': 'text/html'}

    async def dispose(self):
        pass


class ExpiredSession:
    async def get(self, *args, **kwargs):
        return HtmlResponse()


def test_expired_session_is_a_clear_error_not_json_decode_noise():
    with pytest.raises(ValueError, match='session'):
        asyncio.run(request_json(ExpiredSession(), 'https://digital-manual.skoda-auto.com/api/topic'))
