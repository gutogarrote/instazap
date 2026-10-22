"""Real local HTTP + optional mobile browser smoke. All media are SYNTHETIC TEST fixtures.
Run: uv run python scripts/smoke.py [--browser]
Owns only a temporary server at 127.0.0.1:8766; never connects to social networks.
"""
import argparse
import json
import os
import socket
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8766'


def fixture(path, duration=61.2):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    f'testsrc2=s=96x64:r=10:d={duration}', '-f', 'lavfi', '-i',
                    f'sine=frequency=880:duration={duration}', '-threads', '1', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-c:a', 'aac', '-t', str(duration), str(path)], check=True)


def browser_smoke(source):
    from playwright.sync_api import sync_playwright
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with sync_playwright() as p:
        # Reuse an existing browser when available; no heavyweight browser install in this script.
        existing = sorted((Path.home() / '.cache/ms-playwright').glob('chromium-*/chrome-linux*/chrome'))
        executable = os.getenv('INSTAZAP_TEST_CHROME') or (str(existing[-1]) if existing else shutil.which('chromium') or shutil.which('chromium-browser'))
        browser = p.chromium.launch(executable_path=executable, headless=True, args=['--disable-dev-shm-usage'])
        errors = []
        from playwright.sync_api import expect
        for width, height in [(390, 844), (1440, 1000)]:
            context = browser.new_context(viewport={'width':width, 'height':height},
                                          is_mobile=width == 390, has_touch=width == 390,
                                          accept_downloads=True)
            page = context.new_page()
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(BASE)
            expect(page.locator('#input-area')).to_be_visible()
            expect(page.locator('#first-part-control input[value="60"]')).to_be_checked()
            assert page.evaluate("""() => {
                const before = (a,b) => !!(document.querySelector(a).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
                return before('#link-form','#upload-form') && before('#upload-form','.results') && before('.results','.how');
            }""")
            assert page.locator('.results').bounding_box()['y'] < page.locator('.how').bounding_box()['y']
            # Hold each POST: assert visible busy feedback while NO response exists.
            for kind in ['link', 'upload']:
                for first in [15, 30, 60, 90]:
                    page.locator(f'input[name="first-part"][value="{first}"]').check()
                    held = []
                    pattern = f'**/api/jobs/{kind}'
                    page.route(pattern, lambda route, request, pending=held: pending.append(route))
                    if kind == 'link':
                        page.locator('#url').fill('https://www.instagram.com/reel/SYNTHETIC/')
                        button = page.get_by_role('button', name='Preparar vídeo pelo link')
                    else:
                        page.locator('#video-file').set_input_files(str(source))
                        button = page.get_by_role('button', name='Dividir meu vídeo')
                    button.click()
                    expect(page.locator('#submission-status')).to_be_visible()
                    expect(page.locator('#submission-message')).to_contain_text('Aguarde… recebendo')
                    expect(button).to_be_disabled()
                    expect(page.locator('#input-area')).to_have_attribute('aria-busy','true')
                    expect(page.locator('#submission-status')).to_be_focused()
                    assert page.locator('#submission-status').evaluate('(e) => { const r=e.getBoundingClientRect(); return r.top>=0 && r.bottom<=innerHeight; }'), (width, kind, first, page.locator('#submission-status').bounding_box(), page.evaluate('innerHeight'))
                    page.wait_for_timeout(350)
                    assert len(held) == 1
                    if kind == 'link':
                        assert held[0].request.post_data_json['first_part_seconds'] == first
                    else:
                        assert held[0].request.headers['x-first-part-seconds'] == str(first)
                    page.locator(f'#{kind}-form').evaluate('(f) => f.dispatchEvent(new Event("submit", {cancelable:true}))')
                    page.wait_for_timeout(100)
                    assert len(held) == 1, 'Duplicate POST during pending request'
                    held[0].fulfill(status=429, content_type='application/json',
                                    body=json.dumps({'detail':'Fila de teste cheia. Tente novamente.'}))
                    expect(page.locator('#error')).to_contain_text('Fila de teste cheia')
                    expect(button).to_be_enabled()
                    expect(page.locator('#submission-status')).to_be_hidden()
                    expect(page.locator('#input-area')).to_have_attribute('aria-busy','false')
                    if first == 30:
                        button.click()
                        expect(page.locator('#submission-status')).to_be_visible()
                        page.wait_for_timeout(100)
                        assert len(held) == 2
                        held[1].abort('failed')
                        expect(page.locator('#error')).to_contain_text('Conexão interrompida')
                        expect(button).to_be_enabled()
                        expect(page.locator('#submission-status')).to_be_hidden()
                    page.unroute(pattern)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if width == 390:
                context.close()

        # Successful link submission with explicit synthetic queue/worker states.
        fake = {'id':'synthetic', 'created':time.time(), 'expires':time.time()+3600,
                'first_part_seconds':90, 'original_duration':None, 'state':'queued', 'progress':0, 'error':None, 'parts':[]}
        page.route('**/api/jobs', lambda route: route.fulfill(json=[fake]))
        page.route('**/api/jobs/link', lambda route: route.fulfill(status=202, json={'id':'synthetic'}))
        page.get_by_role('button', name='Preparar vídeo pelo link').click()
        expect(page.locator('.job-status')).to_have_text('Na fila')
        expect(page.get_by_text('A duração original aparecerá após obter e medir o vídeo.')).to_be_visible()
        fake.update(state='processing', progress=25, original_duration=61.2)
        expect(page.locator('.job-status')).to_have_text('Preparando as partes · 25%', timeout=10000)
        expect(page.locator('.original-duration')).to_have_text('Duração original: 61.20 s')
        page.reload()
        expect(page.locator('.original-duration')).to_have_text('Duração original: 61.20 s')
        fake.update(state='error', error='Link de teste indisponível. Envie o arquivo.')
        expect(page.locator('.job .error')).to_contain_text('Envie o arquivo', timeout=10000)
        # UI boundaries: short selections and feasible/infeasible equal remainders.
        fake.update(state='ready', error=None)
        for first in [15, 30, 60, 90]:
            for remainder in [0, 1.2, 59.9, 60, 90, 90.1, 119.9, 120, 180, 180.1, 225]:
                if first + remainder > 240:
                    continue
                fake.update(first_part_seconds=first, original_duration=first+remainder if remainder else first/2)
                page.reload()
                expect(page.locator('.original-duration')).to_have_text(f'Duração original: {fake["original_duration"]:.2f} s')
                expect(page.get_by_text(f'Primeira parte: {first} s', exact=True)).to_be_visible()
                explanation = page.get_by_text('Para preservar tudo sem ultrapassar 90 s, o restante fica em partes iguais menores que 60 s.')
                if 0 < remainder < 60 or 90 < remainder < 120:
                    expect(explanation).to_be_visible()
                else:
                    expect(explanation).to_have_count(0)
        page.unroute('**/api/jobs')
        page.unroute('**/api/jobs/link')
        context.close()
        context = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True,
                                      has_touch=True, accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(BASE)
        page.locator('input[name="first-part"][value="30"]').check()
        page.locator('#video-file').set_input_files(str(source))
        page.get_by_role('button', name='Dividir meu vídeo').click()
        page.get_by_text('Pronto para o Status', exact=True).wait_for(timeout=120000)
        expect(page.locator('.original-duration')).to_have_text('Duração original: 61.20 s')
        expect(page.get_by_text('Para preservar tudo sem ultrapassar 90 s, o restante fica em partes iguais menores que 60 s.')).to_be_visible()
        assert page.locator('.part').count() == 2
        assert page.locator('.part p').all_text_contents() == ['Parte 1 de 2 · 30.0 s', 'Parte 2 de 2 · 31.2 s']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('video').first.evaluate('(v) => { window.originalPreview=v; v.preload="auto"; v.load(); }')
        page.wait_for_function('() => document.querySelector("video").readyState >= 2')
        # Polling another job must retain the existing preview node and playback state.
        jobs = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')
        fake.update(state='processing', error=None)
        page.route('**/api/jobs', lambda route: route.fulfill(json=jobs+[fake]))
        expect(page.locator('.job-status').last).to_contain_text('Preparando', timeout=10000)
        scroll_before_poll = page.evaluate('scrollY')
        fake['progress'] = 75
        expect(page.locator('.job-status').last).to_contain_text('75%', timeout=10000)
        assert page.evaluate('scrollY') == scroll_before_poll
        assert page.evaluate('window.originalPreview === document.querySelector("video") && window.originalPreview.readyState >= 2')
        page.unroute('**/api/jobs')
        for index, duration in [(1, 30), (2, 31.2)]:
            with page.expect_download() as pending:
                page.get_by_role('link', name=f'Baixar parte {index}', exact=True).click()
            download = pending.value
            assert download.suggested_filename == f'video-parte-{index:02}-de-02.mp4'
            data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format',
                                                       '-of','json',download.path()]))
            assert float(data['format']['duration']) == duration
        page.reload()
        page.get_by_text('Pronto para o Status', exact=True).wait_for()
        page.screenshot(path=str(artifacts / 'mobile.png'), full_page=True)
        saved_session = context.storage_state()
        context.close()
        context = browser.new_context(viewport={'width':1440, 'height':1000}, storage_state=saved_session)
        page = context.new_page()
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(BASE)
        page.get_by_text('Pronto para o Status', exact=True).wait_for()
        page.screenshot(path=str(artifacts / 'desktop.png'), full_page=True)
        page.get_by_role('button', name='Excluir agora').click()
        page.wait_for_function('() => document.querySelectorAll(".job").length === 0')
        # Real upload/worker/result for every other choice, including selection > source.
        for first, wanted in [(15, [15, 46.2]), (60, [60, 1.2]), (90, [61.2])]:
            page.locator(f'input[name="first-part"][value="{first}"]').check()
            page.locator('#video-file').set_input_files(str(source))
            page.get_by_role('button', name='Dividir meu vídeo').click()
            page.get_by_text('Pronto para o Status', exact=True).wait_for(timeout=120000)
            actual = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]
            assert actual['first_part_seconds'] == first
            assert [part['duration'] for part in actual['parts']] == wanted
            expect(page.locator('.original-duration')).to_have_text('Duração original: 61.20 s')
            page.get_by_role('button', name='Excluir agora').click()
            expect(page.locator('.job')).to_have_count(0)
        balanced = source.parent / 'SYNTHETIC-TEST-150.1s.mp4'
        fixture(balanced, 150.1)
        page.locator('input[name="first-part"][value="30"]').check()
        page.locator('#video-file').set_input_files(str(balanced))
        page.get_by_role('button', name='Dividir meu vídeo').click()
        page.get_by_text('Pronto para o Status', exact=True).wait_for(timeout=120000)
        actual = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]
        assert len(actual['parts']) == 3
        assert all(abs(part['duration'] - 60.05) <= 1/30+.001 for part in actual['parts'][1:])
        expect(page.get_by_text('Para preservar tudo sem ultrapassar 90 s, o restante fica em partes iguais menores que 60 s.')).to_have_count(0)
        page.get_by_role('button', name='Excluir agora').click()
        expect(page.locator('.job')).to_have_count(0)
        assert errors == [], errors
        browser.close()
    print('BROWSER PASS: mobile/desktop order, delayed link/upload busy feedback, duplicate guard, 15/30/60/90 transmission, rejection recovery, synthetic link duration before completion/recovery/stages, real uploads for all four choices and fractional equal remainder/downloads, stable previews, reload/delete')


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--browser', action='store_true')
    args.add_argument('--port', type=int, default=8766)
    options = args.parse_args()
    global BASE
    BASE = f'http://127.0.0.1:{options.port}'
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', options.port))  # Refuse to reuse an unrelated server.
    with tempfile.TemporaryDirectory(prefix='instazap-e2e-') as temporary:
        source = Path(temporary) / 'SYNTHETIC-TEST-61.2s.mp4'
        fixture(source)
        env = dict(os.environ, INSTAZAP_DATA=str(Path(temporary) / 'data'),
                   INSTAZAP_ACCESS_SECRET='', INSTAZAP_ORIGIN='', INSTAZAP_SECURE_COOKIE='0',
                   INSTAZAP_BIND='127.0.0.1', INSTAZAP_PORT=str(options.port))
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
            print(f'SERVER STOPPED: {BASE}')


if __name__ == '__main__':
    main()
