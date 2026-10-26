import argparse

import pytest

from skoda_manual import clean_topic, flatten_tree, normalize_vin, safe_asset_url


def test_vin_is_normalized():
    assert normalize_vin(' tmbjb9ny6rf999999 ') == 'TMBJB9NY6RF999999'


@pytest.mark.parametrize('value', ['', 'short', 'TMBIB9NY6RF999999', '../etc/passwd!!!!'])
def test_invalid_vin_is_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        normalize_vin(value)


def test_tree_keeps_all_siblings_in_order_and_unique_anchors():
    tree = [{'label': 'Manual', 'children': [
        {'label': str(i), 'linkTarget': 'topic' if i < 2 else str(i)}
        for i in range(120)
    ]}]
    nodes = flatten_tree(tree)
    assert len(nodes) == 121
    assert nodes[-1]['label'] == '119'
    assert len({n['anchor'] for n in nodes}) == 121
    assert nodes[1]['depth'] == 1
    assert nodes[1]['target'] == nodes[2]['target'] == 'topic'


def test_html_in_toc_labels_becomes_plain_text():
    nodes = flatten_tree([{'label': '<span data-type="eigenname">SMS</span>'}])
    assert nodes[0]['label'] == 'SMS'


def test_current_tenant_media_path_is_accepted():
    url = 'https://digital-manual.skoda-auto.com/default/public/media?lang=fr_FR&key=ansi-s1_3x.svg'
    assert safe_asset_url(url) == url


def test_media_url_preserves_query():
    assert safe_asset_url('/public/media?name=test&v=2') == (
        'https://digital-manual.skoda-auto.com/public/media?name=test&v=2'
    )


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'https://evil.example/a.png',
    '//127.0.0.1/a', '/api/users/V1/getuser',
    'https://digital-manual.skoda-auto.com.evil.example/public/media?x=1',
])
def test_untrusted_assets_are_rejected(url):
    with pytest.raises(ValueError):
        safe_asset_url(url)


def test_chapter_sanitization_preserves_content_and_embeds_images():
    html = '''<html><body><script>alert(1)</script><iframe src="file:///etc/passwd"></iframe>
    <p onclick="bad()" style="background:url(file:///etc/passwd)">Warning: café</p>
    <img data-src="/public/media?name=test&amp;v=2" onerror="bad()">
    <a href="javascript:bad()">unsafe</a><a href="https://example.com">external</a>
    </body></html>'''
    result = clean_topic(html, {'/public/media?name=test&v=2': 'data:image/png;base64,aGVsbG8='})
    assert 'Warning: café' in result
    assert 'data:image/png;base64,aGVsbG8=' in result
    for forbidden in ['<script', '<iframe', 'onclick', 'onerror', 'javascript:', 'file:', 'https://']:
        assert forbidden not in result


def test_root_index_is_removed_without_dropping_introductory_content():
    html = '''<div class="topic-content"><p>Important introduction.</p>
        <div class="tttitle"><a>Duplicate navigation</a></div>
        <div class="ttchildren"><div class="tttitle">Nested navigation</div></div>
        <p>Actual documentation.</p></div>'''
    cleaned = clean_topic(html, {}, remove_index=True)
    assert 'Duplicate navigation' not in cleaned
    assert 'Nested navigation' not in cleaned
    assert 'Important introduction.' in cleaned
    assert 'Actual documentation.' in cleaned
    assert 'Duplicate navigation' in clean_topic(html, {})


def test_html_fragment_does_not_retain_xml_prologue_or_document_metadata():
    html = '<?xml version="1.0"?><!DOCTYPE html><html><!-- metadata --><p>Actual content</p></html>'
    cleaned = clean_topic(html, {})
    assert 'Actual content' in cleaned
    for metadata in ['<?xml', '<!DOCTYPE', '<!--']:
        assert metadata not in cleaned


def test_missing_image_is_not_silently_dropped():
    with pytest.raises(ValueError, match='image'):
        clean_topic('<img data-src="/public/media?missing">', {})
