import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as BrowserError

from skoda_manual import launch_browser


def runtime(*results):
    return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(side_effect=results)))


def test_existing_browser_does_not_prompt_or_install(monkeypatch):
    browser = object()
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Unexpected prompt'))
    assert asyncio.run(launch_browser(runtime(browser))) is browser


def test_missing_browser_is_installed_with_the_current_python_when_accepted(monkeypatch):
    browser = object()
    playwright = runtime(BrowserError("Executable doesn't exist at /missing/chromium"), browser)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: 'y')
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    install = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', install)
    assert asyncio.run(launch_browser(playwright)) is browser
    install.assert_awaited_once_with(sys.executable, '-m', 'playwright', 'install', 'chromium')


def test_declining_install_does_not_download(monkeypatch):
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: '')
    install = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', install)
    with pytest.raises(ValueError, match='cancelled'):
        asyncio.run(launch_browser(runtime(BrowserError("Executable doesn't exist at /missing"))))
    install.assert_not_awaited()


def test_noninteractive_run_explains_how_to_install_without_prompting(monkeypatch):
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Unexpected prompt'))
    with pytest.raises(ValueError, match='playwright install chromium'):
        asyncio.run(launch_browser(runtime(BrowserError("Executable doesn't exist at /missing"))))


def test_other_browser_errors_are_not_misdiagnosed_as_missing_chromium(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Unexpected prompt'))
    with pytest.raises(BrowserError, match='missing system libraries'):
        asyncio.run(launch_browser(runtime(BrowserError('missing system libraries'))))


def test_failed_download_is_reported_without_retrying_browser_launch(monkeypatch):
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: 'yes')
    install = AsyncMock(return_value=SimpleNamespace(wait=AsyncMock(return_value=1)))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', install)
    playwright = runtime(BrowserError("Executable doesn't exist at /missing"))
    with pytest.raises(ValueError, match='installation failed'):
        asyncio.run(launch_browser(playwright))
    assert playwright.chromium.launch.await_count == 1
