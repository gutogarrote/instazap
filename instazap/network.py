"""The ONLY yt-dlp transport: public DNS pinned to TLS sockets, bounded all-response bytes.
No proxy, external downloader, manifest fetch or remote ffmpeg is permitted.
"""
import http.client
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

from yt_dlp.networking.common import RequestHandler, Response
from yt_dlp.networking.exceptions import HTTPError, RequestError


class NetworkBlocked(RequestError):
    pass


def original_url(url):
    if not isinstance(url, str) or len(url) > 500:
        raise ValueError('Cole um link de publicação do Instagram ou X.')
    # urlsplit silently strips some whitespace/control characters. Reject the raw
    # input first, rather than turning a malformed URL into an allowed one.
    if re.search(r'[\s\x00-\x1f\x7f\\]', url) or re.search(r'%(?![0-9A-Fa-f]{2})', url):
        raise ValueError('Link malformado.')
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        raise ValueError('Link malformado.') from None
    if (p.scheme != 'https' or not p.hostname or p.username is not None
            or p.password is not None or port is not None or p.netloc.lower() != p.hostname):
        raise ValueError('Use o link HTTPS da publicação, sem credenciais ou porta.')
    instagram = p.hostname in ('instagram.com', 'www.instagram.com') and re.fullmatch(r'/(?:p|reel|reels)/[A-Za-z0-9_-]{5,64}/?', p.path)
    twitter = p.hostname in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com') and re.fullmatch(r'/[A-Za-z0-9_]{1,15}/status/[0-9]{5,25}/?', p.path)
    if not (instagram or twitter):
        raise ValueError('Aceitamos apenas links de uma publicação /reel/ ou /p/ do Instagram ou /usuário/status/id do X.')
    # Only discard tracking parameters/fragments after validating the original
    # authority and exact publication path; never unquote or normalize the path.
    return f'https://{p.hostname}{p.path}'


def destination(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise NetworkBlocked('Destino de rede não permitido')
    host = p.hostname.lower()
    # Secondary destinations are also allowlisted; suffix matching requires a dot.
    domains = ('instagram.com', 'cdninstagram.com', 'fbcdn.net', 'facebook.com',
               'x.com', 'twitter.com', 'twimg.com')
    if not any(host == d or host.endswith('.' + d) for d in domains):
        raise NetworkBlocked('Domínio secundário não permitido')
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = [r[4][0] for r in records]
    def public(ip):
        addr = ipaddress.ip_address(ip)
        blocked_v6 = ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32')
        return (addr.is_global and not addr.is_multicast and not addr.is_reserved
                and not any(addr in ipaddress.ip_network(net) for net in blocked_v6))
    if not addresses or not all(public(ip) for ip in addresses):
        raise NetworkBlocked('Endereço privado, reservado ou de metadados bloqueado')
    return host, addresses[0], p.path + ('?' + p.query if p.query else '') or '/'


class Budget:
    def __init__(self, maximum=200_000_000):
        self.remaining = maximum
        self.deadline = time.monotonic() + 180
        self.requests = 0

    def check(self):
        if time.monotonic() > self.deadline or self.remaining < 0 or self.requests > 80:
            raise NetworkBlocked('Limite de tempo, tamanho ou requisições atingido')


class BoundedResponse(Response):
    def __init__(self, *args, budget, connection, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget, self.connection = budget, connection

    def read(self, amt=None):
        self.budget.check()
        # Extractor read-all remains bounded; no unbounded HTTPResponse.read().
        if amt is None or amt < 0:
            chunks = []
            while chunk := self.read(64 * 1024):
                chunks.append(chunk)
            return b''.join(chunks)
        chunk = super().read(min(amt, 64 * 1024, self.budget.remaining + 1))
        self.budget.remaining -= len(chunk)
        self.budget.check()
        return chunk

    def close(self):
        super().close()
        self.connection.close()


class RestrictedRH(RequestHandler):
    _SUPPORTED_URL_SCHEMES = ('https',)

    def __init__(self, *args, budget=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = budget or Budget()

    def _check_extensions(self, extensions):
        for key in ('cookiejar', 'timeout', 'legacy_ssl', 'keep_header_casing'):
            extensions.pop(key, None)

    def _send(self, request):
        url, method, body = request.url, request.method, request.data
        headers = self._get_headers(request)
        # Ignore extractor attempts to override the destination or transport.
        headers = {k: v for k, v in headers.items() if k.lower() not in
                   ('host', 'authorization', 'proxy-authorization', 'cookie', 'accept-encoding')}
        headers['Accept-Encoding'] = 'identity'
        for _ in range(6):
            self.budget.requests += 1
            self.budget.check()
            host, address, path = destination(url)
            conn = http.client.HTTPSConnection(host, timeout=10, context=ssl.create_default_context())
            # Pin IP at connect: no second DNS lookup and TLS still verifies the original hostname.
            conn._create_connection = lambda addr, timeout, source=None, ip=address: socket.create_connection((ip, 443), timeout)
            try:
                conn.request(method, path, body=body, headers=headers)
                raw = conn.getresponse()
                if raw.status in (301, 302, 303, 307, 308):
                    location = raw.getheader('Location')
                    raw.close()
                    conn.close()
                    if not location:
                        raise NetworkBlocked('Redirecionamento inválido')
                    url = urljoin(url, location)
                    if raw.status in (301, 302, 303):
                        method, body = 'GET', None
                    continue
                if raw.getheader('Content-Encoding', 'identity') != 'identity':
                    raise NetworkBlocked('Resposta comprimida não suportada')
                length = raw.getheader('Content-Length')
                if length and int(length) > self.budget.remaining:
                    raise NetworkBlocked('Resposta maior que 200 MB')
                response = BoundedResponse(raw, url, dict(raw.headers), raw.status,
                                           budget=self.budget, connection=conn)
                if raw.status >= 400:
                    raise HTTPError(response)
                return response
            except Exception:
                conn.close()
                raise
        raise NetworkBlocked('Excesso de redirecionamentos')
