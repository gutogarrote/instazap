"""All generated media are synthetic TEST fixtures, never evidence of remote downloads."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from instazap.app import create_app
from instazap.config import Settings


@pytest.fixture(scope='session')
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp('synthetic-test-media')
    cache = {}

    def make(duration, audio=True, size='96x64', rotation=0, sar=None):
        key = (duration, audio, size, rotation, sar)
        if key in cache:
            return cache[key]
        path = root / f'{len(cache)}.mp4'
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1',
                '-f', 'lavfi', '-i', f'color=c=blue:s={size}:r=10:d={duration}']
        if audio:
            args += ['-f', 'lavfi', '-i', f'sine=frequency=880:sample_rate=48000:duration={duration}']
        filters = f"drawbox=color=red:t=fill:enable='gte(t,{float(duration)-.3})'"
        if sar:
            filters += f',setsar={sar}'
        args += ['-vf', filters, '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                 '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', str(duration), str(path)]
        subprocess.run(args, check=True)
        if rotation:
            rotated = root / f'{len(cache)}-rotated.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-display_rotation:v:0', str(rotation), '-i', str(path),
                            '-c', 'copy', str(rotated)], check=True)
            path = rotated
        cache[key] = path
        return path
    return make


@pytest.fixture
def site(tmp_path):
    app = create_app(Settings(data=tmp_path, worker=False))
    with TestClient(app) as client:
        session = client.get('/api/session').json()
        client.headers['x-csrf-token'] = session['csrf']
        yield client, app
