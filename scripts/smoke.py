"""Real local HTTP + optional mobile browser smoke. All media are SYNTHETIC TEST fixtures.
Run: uv run python scripts/smoke.py [--browser]
Owns only a temporary server at 127.0.0.1:8765; never connects to social networks.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8765'


def fixture(path):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=96x64:r=10:d=61.2', '-f', 'lavfi', '-i',
                    'sine=frequency=880:duration=61.2', '-threads', '1', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-c:a', 'aac', '-t', '61.2', str(path)], check=True)


def browser_smoke(source):
    from playwright.sync_api import sync_playwright
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with sync_playwright() as p:
        # Reuse an existing browser when available; no heavyweight browser install in this script.
        existing = sorted((Path.home() / '.cache/ms-playwright').glob('chromium-*/chrome-linux*/chrome'))
        executable = os.getenv('INSTAZAP_TEST_CHROME') or (str(existing[-1]) if existing else None)
        browser = p.chromium.launch(executable_path=executable, headless=True, args=['--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True, has_touch=True,
                                      accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(BASE)
        page.locator('#video-file').set_input_files(str(source))
        page.get_by_role('button', name='Dividir meu vídeo').click()
        page.get_by_text('Pronto para o Status', exact=True).wait_for(timeout=120000)
        assert page.locator('.part').count() == 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Real preview loads from authorized HTTP route and decodes.
        page.locator('video').first.evaluate('(v) => { v.preload="auto"; v.load(); }')
        page.wait_for_function('() => document.querySelector("video").readyState >= 2')
        with page.expect_download() as pending:
            page.get_by_role('link', name='Baixar parte 1', exact=True).click()
        download = pending.value
        assert download.suggested_filename == 'video-parte-01-de-02.mp4'
        assert Path(download.path()).stat().st_size > 0
        page.reload()
        page.get_by_text('Pronto para o Status', exact=True).wait_for()
        page.screenshot(path=str(artifacts / 'mobile.png'), full_page=True)
        page.set_viewport_size({'width':1440, 'height':1000})
        page.screenshot(path=str(artifacts / 'desktop.png'), full_page=True)
        page.get_by_role('button', name='Excluir agora').click()
        page.wait_for_function('() => document.querySelectorAll(".job").length === 0')
        assert errors == [], errors
        browser.close()
    print('BROWSER PASS: 390×844, upload, 2 previews, video decode, download, reload, delete; no JS errors/overflow')


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--browser', action='store_true')
    options = args.parse_args()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 8765))  # Refuse to reuse an unrelated server.
    with tempfile.TemporaryDirectory(prefix='instazap-e2e-') as temporary:
        source = Path(temporary) / 'SYNTHETIC-TEST-61.2s.mp4'
        fixture(source)
        env = dict(os.environ, INSTAZAP_DATA=str(Path(temporary) / 'data'),
                   INSTAZAP_ACCESS_SECRET='', INSTAZAP_ORIGIN='', INSTAZAP_SECURE_COOKIE='0',
                   INSTAZAP_BIND='127.0.0.1', INSTAZAP_PORT='8765')
        proc = subprocess.Popen([sys.executable, 'scripts/serve.py'],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            with httpx.Client(base_url=BASE, timeout=30) as client:
                for _ in range(100):
                    try:
                        if client.get('/healthz').status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if proc.poll() is not None:
                        raise RuntimeError(proc.stderr.read().decode())
                    time.sleep(.1)
                session = client.get('/api/session').json()
                client.headers['x-csrf-token'] = session['csrf']
                with source.open('rb') as stream:
                    response = client.post('/api/jobs/upload', content=stream)
                assert response.status_code == 202, response.text
                job_id = response.json()['id']
                for _ in range(240):
                    job = client.get(f'/api/jobs/{job_id}').json()
                    if job['state'] in ('ready', 'error'):
                        break
                    time.sleep(.25)
                assert job['state'] == 'ready', job
                assert len(job['parts']) == 2
                durations = []
                for part in job['parts']:
                    response = client.get(f"/api/jobs/{job_id}/files/{part['name']}")
                    assert response.status_code == 200
                    downloaded = Path(temporary) / part['name']
                    downloaded.write_bytes(response.content)
                    data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format',
                                                               '-of','json',str(downloaded)]))
                    duration = float(data['format']['duration'])
                    assert duration <= 60
                    durations.append(duration)
                assert durations == [60.0, 1.2], durations
                assert client.delete(f'/api/jobs/{job_id}').status_code == 204
                assert client.get(f'/api/jobs/{job_id}').status_code == 404
                print(f'HTTP PASS: upload → persisted queue → worker → downloads {durations} → delete → 404')
                if options.browser:
                    browser_smoke(source)
        finally:
            proc.terminate()
            try:
                _, stderr = proc.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate()
            assert proc.returncode in (0, -15), stderr.decode()
            print('SERVER STOPPED: 127.0.0.1:8765')


if __name__ == '__main__':
    main()
