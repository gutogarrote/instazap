"""Runs in a bounded child process; never invokes yt-dlp media downloaders."""
import sys
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.extractor.instagram import InstagramIE
from yt_dlp.extractor.twitter import TwitterIE
from yt_dlp.networking import Request

from .network import RestrictedRH, original_url


class SafeYDL(YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        # Replace, never supplement, the stock handlers. No fallback transport.
        return super().build_request_director([RestrictedRH])


def download(url, target):
    original_url(url)
    with SafeYDL({'quiet': True, 'no_warnings': True, 'proxy': '', 'socket_timeout': 10,
                  'retries': 0, 'extractor_retries': 0, 'noplaylist': True,
                  'cachedir': False, 'skip_download': True}, auto_init=False) as ydl:
        ydl.add_info_extractor(InstagramIE())
        ydl.add_info_extractor(TwitterIE())
        info = ydl.extract_info(url, download=False)
        if not info or info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
            raise ValueError('Publicação não suportada')
        if info.get('duration', 0) and info['duration'] > 240:
            raise ValueError('Vídeo acima de 240 segundos')
        formats = info.get('formats') or [info]
        formats = [f for f in formats if f.get('url', '').startswith('https://')
                   and f.get('ext') == 'mp4' and f.get('protocol', 'https') == 'https'
                   and f.get('vcodec') != 'none' and f.get('acodec') != 'none']
        if not formats:
            raise ValueError('MP4 direto indisponível; use upload')
        fmt = max(formats, key=lambda f: min(f.get('height') or 0, 720))
        with ydl.urlopen(Request(fmt['url'], headers=fmt.get('http_headers', {}))) as response:
            with Path(target).open('wb') as output:
                while chunk := response.read(64 * 1024):
                    output.write(chunk)


if __name__ == '__main__':
    try:
        download(sys.argv[1], sys.argv[2])
    except Exception:
        sys.exit(1)
