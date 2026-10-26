from pathlib import Path

import pytest

from skoda_manual import default_output


@pytest.mark.parametrize(('model', 'language', 'expected'), [
    ('Škoda Enyaq Coupé', 'fr', 'skoda-enyaq-coupe-fr.pdf'),
    ('Enyaq', 'en', 'skoda-enyaq-en.pdf'),
    ('Škoda Octavia Combi', 'de', 'skoda-octavia-combi-de.pdf'),
    ('', 'fr', 'skoda-manual-fr.pdf'),
    ('../../Enyaq / Coupé', 'fr', 'skoda-enyaq-coupe-fr.pdf'),
    ('Škoda', 'en', 'skoda-en.pdf'),
])
def test_default_filename_contains_a_safe_model_and_language(model, language, expected):
    output = default_output(model, language)
    assert output == Path(expected)
    assert output.parent == Path('.')
