"""Offline checks for canonical downloader input and sanitized diagnostics."""
import sys
from unittest.mock import MagicMock

import pytest
from yt_dlp.utils import DownloadError

from instazap.download import download, failure_category
from instazap.network import NetworkBlocked
from scripts import check_instagram


def test_downloader_uses_canonical_url(monkeypatch, tmp_path):
    ydl = MagicMock()
    ydl.extract_info.return_value = None
    factory = MagicMock()
    factory.return_value.__enter__.return_value = ydl
    monkeypatch.setattr('instazap.download.SafeYDL', factory)
    with pytest.raises(ValueError, match='Publicação não suportada'):
        download('https://www.instagram.com/reel/ABCDE/?igsh=secret#fragment', tmp_path / 'source')
    ydl.extract_info.assert_called_once_with('https://www.instagram.com/reel/ABCDE/', download=False)


@pytest.mark.parametrize(('error', 'category'), [
    (DownloadError('The webpage request was redirected to the login page. You have exceeded the rate-limit '
                   'for accessing posts anonymously https://source.test/?secret=abc'),
     'LOGIN_REQUIRED_OR_ANONYMOUS_RATE_LIMIT'),
    (DownloadError('Login required https://source.test'), 'LOGIN_REQUIRED'),
    (DownloadError('HTTP Error 429: Too Many Requests'), 'RATE_LIMITED'),
    (NetworkBlocked('private destination https://cdn.test/secret'), 'NETWORK_BLOCKED'),
    (DownloadError('unknown https://cdn.test/?token=secret'), 'DOWNLOAD_FAILED'),
])
def test_failure_category(error, category):
    assert failure_category(error) == category


@pytest.mark.parametrize(('result', 'category'), [
    (b'LOGIN_REQUIRED_OR_ANONYMOUS_RATE_LIMIT\n', 'LOGIN_REQUIRED_OR_ANONYMOUS_RATE_LIMIT'),
    (b'https://cdn.test/?token=secret\nTraceback', 'PROCESS_FAILED'),
    (TimeoutError('https://source.test/secret'), 'TIMEOUT'),
    (RuntimeError('https://cdn.test/secret'), 'PROCESS_FAILED'),
])
def test_diagnostic_prints_only_fixed_category(monkeypatch, capsys, result, category):
    monkeypatch.setattr(sys, 'argv', ['check_instagram.py',
                                    'https://www.instagram.com/reel/ABCDE/?igsh=secret'])
    def run(argv, **kwargs):
        assert argv[3] == 'https://www.instagram.com/reel/ABCDE/'
        assert argv[-1] == '--diagnostic'
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(check_instagram, 'run', run)
    assert check_instagram.main() == 1
    captured = capsys.readouterr()
    assert captured.out == f'NOT VERIFIED: {category}. Use upload.\n'
    assert captured.err == ''


def test_diagnostic_invalid_url_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['check_instagram.py', 'https://instagram.com:secret/reel/ABCDE/'])
    assert check_instagram.main() == 1
    assert capsys.readouterr().out == 'NOT VERIFIED: INVALID_URL. Use upload.\n'
