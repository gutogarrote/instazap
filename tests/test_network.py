import io
import socket
from unittest.mock import Mock

import pytest
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError

from instazap.download import SafeYDL
from instazap.network import (BoundedResponse, Budget, NetworkBlocked, RestrictedRH,
                              destination, original_url)


@pytest.mark.parametrize('url', [
    'http://www.instagram.com/reel/ABCDE/', 'https://127.0.0.1/reel/ABCDE/',
    'https://instagram.com.evil.test/reel/ABCDE/', 'https://instagram.com@127.0.0.1/reel/ABCDE/',
    'https://www.instagram.com:443/reel/ABCDE/',
    'https://www.instagram.com/stories/ABCDE/', 'https://www.instagram.com/user/',
    'https://x.com/user/status/notanumber',
    'https://www.instagram.com/reel/../../etc/passwd', 'file:///etc/passwd',
    'https://[::1]/', 'https://169.254.169.254/', 'https://2130706433/',
    'https://www.instagram.com\\@evil.test/reel/ABCDE/', '', None,
    'https://www.instagram.com:bad/reel/ABCDE/?igsh=123',
    'https://www.instagram.com:99999/reel/ABCDE/?igsh=123',
    'https://www.instagram.com:/reel/ABCDE/?igsh=123',
    'https://www.instagram.com:0/reel/ABCDE/?igsh=123',
    'https://@www.instagram.com/reel/ABCDE/?igsh=123',
    'https://user:secret@www.instagram.com/reel/ABCDE/?igsh=123',
    'https://10.0.0.1/reel/ABCDE/?igsh=123',
    'https://www.instagram.com.evil.test/reel/ABCDE/?igsh=123',
    'https://www.instagram.com/reel/%41BCDE/?igsh=123',
    'https://www.instagram.com/reel/ABCDE/%2e%2e/?igsh=123',
    'https://www.instagram.com/reel/ABCDE%2f..%2f/?igsh=123',
    'https://x.com/user/status/12345%3f?s=20',
    'https://www.insta\ngram.com/reel/ABCDE/?igsh=123',
    ' https://www.instagram.com/reel/ABCDE/?igsh=123',
    'https://www.instagram.com/reel/ABCDE/?igsh=%ZZ',
])
def test_original_allowlist(url):
    with pytest.raises(ValueError):
        original_url(url)


@pytest.mark.parametrize('url', ['https://www.instagram.com/reel/ABCDE123/',
                                'https://instagram.com/p/ABCDE123', 'https://x.com/user/status/123456'])
def test_valid_original(url):
    assert original_url(url) == url


@pytest.mark.parametrize(('url', 'canonical'), [
    ('https://www.instagram.com/reel/ABCDE/?igsh=abc&utm_source=share#fragment',
     'https://www.instagram.com/reel/ABCDE/'),
    ('https://instagram.com/p/ABCDE?x=1', 'https://instagram.com/p/ABCDE'),
    ('https://www.instagram.com/reel/ABCDE/#fragment', 'https://www.instagram.com/reel/ABCDE/'),
    ('https://X.COM/user/status/123456?s=20&t=abc#fragment', 'https://x.com/user/status/123456'),
    ('https://twitter.com/user/status/123456/?utm_medium=social', 'https://twitter.com/user/status/123456/'),
])
def test_share_url_normalized(url, canonical):
    # Tracking queries/fragments are ordinary share links, not SSRF destinations.
    # They used to be rejected; discard them only after authority/path validation.
    assert original_url(url) == canonical


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '100.100.100.200',
                               '100.64.0.1', '0.0.0.0', '192.168.1.1', '::1', 'fc00::1',
                               'fe80::1', '::ffff:127.0.0.1', '192.0.2.1', '224.0.0.1'])
def test_nested_dns_private(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', (ip, 443))])
    with pytest.raises(NetworkBlocked):
        destination('https://scontent.cdninstagram.com/video.mp4')


def test_mixed_dns_rejects(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2,1,6,'',('8.8.8.8',443)), (2,1,6,'',('10.0.0.1',443))])
    with pytest.raises(NetworkBlocked):
        destination('https://www.instagram.com/')


@pytest.mark.parametrize('url', ['http://www.instagram.com/', 'file:///tmp/a', 'ftp://cdninstagram.com/a',
                                'https://evil.test/video.mp4', 'https://cdninstagram.com.evil.test/',
                                'https://cdninstagram.com:8443/', 'https://user:password@cdninstagram.com/'])
def test_nested_url_rejected(url):
    with pytest.raises(NetworkBlocked):
        destination(url)


def test_response_streaming_budget():
    response = BoundedResponse(io.BytesIO(b'123456'), 'https://instagram.com/', {},
                               budget=Budget(5), connection=Mock())
    assert response.read(3) == b'123'
    with pytest.raises(NetworkBlocked):
        response.read()
    response.close()


def test_only_restricted_handler():
    with SafeYDL({'proxy':'', 'quiet':True}, auto_init=False) as ydl:
        assert list(ydl._request_director.handlers) == ['Restricted']
        with pytest.raises(RequestError):
            ydl.urlopen('http://127.0.0.1/')
        with pytest.raises(RequestError):
            ydl.urlopen('file:///etc/passwd')


def test_real_transport_pins_dns_and_redirects(monkeypatch):
    dns_calls, sockets, tls_hosts = [], [], []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **kw:
                        dns_calls.append(host) or [(2,1,6,'',('8.8.8.8',443))])
    monkeypatch.setattr(socket, 'create_connection', lambda address, timeout:
                        sockets.append(address) or Mock())
    class FakeConnection:
        def __init__(self, host, **kwargs):
            tls_hosts.append(host)
        def request(self, *a, **kw):
            self._create_connection(('untrusted-second-dns',443),10)
            assert 'Host' not in kw['headers']
        def getresponse(self):
            raw = io.BytesIO(b'data')
            raw.status = 200
            raw.headers = {}
            raw.getheader = lambda key, default=None: default
            return raw
        def close(self):
            pass
    monkeypatch.setattr('instazap.network.http.client.HTTPSConnection', FakeConnection)
    handler = RestrictedRH(logger=Mock())
    assert handler.send(Request('https://scontent.cdninstagram.com/video.mp4', headers={'Host':'evil.test'})).read() == b'data'
    assert dns_calls == ['scontent.cdninstagram.com']
    assert sockets == [('8.8.8.8',443)]
    assert tls_hosts == dns_calls

    # A redirect from an allowed public host cannot reach metadata, arbitrary domains, or private DNS.
    class RedirectConnection(FakeConnection):
        def getresponse(self):
            raw = super().getresponse()
            raw.status = 302
            raw.getheader = lambda key, default=None: 'https://169.254.169.254/latest/meta-data/' if key == 'Location' else default
            return raw
    monkeypatch.setattr('instazap.network.http.client.HTTPSConnection', RedirectConnection)
    with pytest.raises(NetworkBlocked):
        handler.send(Request('https://www.instagram.com/reel/ABCDE/'))
    assert len(sockets) == 2  # The redirected destination never gets a socket.
