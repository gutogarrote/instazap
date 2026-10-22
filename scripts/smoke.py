"""Real local HTTP + optional mobile browser smoke. All media are SYNTHETIC TEST fixtures.
Run: uv run python scripts/smoke.py [--browser] [--port 8766]
Owns only a temporary server at 127.0.0.1:<port>; never connects to social networks.
The server is scripts/smoke_server.py: the real app, with the link downloader replaced by
a copy of a synthetic local file. Link results prove local processing, never Instagram.
"""
import argparse
import array
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8766'
LINK_SECONDS = 140.6  # Synthetic "reel" returned by the test-only downloader.


def fixture(path):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=96x64:r=10:d=61.2', '-f', 'lavfi', '-i',
                    'sine=frequency=880:duration=61.2', '-threads', '1', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-c:a', 'aac', '-t', '61.2', str(path)], check=True)


def link_fixture(path):
    # A chirp: every instant has its own frequency, so misplaced audio cannot correlate.
    d = LINK_SECONDS
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=s=96x64:r=30:d={d}',
                    '-f', 'lavfi', '-i', f'aevalsrc=0.3*sin(2*PI*(300*t+3*t*t)):s=48000:d={d}',
                    '-threads', '1', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-t', str(d), str(path)], check=True)


def pcm(path):
    # Trim to the declared audio duration: AAC decoders also emit the final frame's padding.
    data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
    audio = next(s for s in data['streams'] if s['codec_type'] == 'audio')
    return array.array('h', subprocess.check_output([
        'ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-vn',
        '-af', f"atrim=duration={audio['duration']}", '-ac', '1', '-ar', '48000', '-f', 's16le', '-']))


def correlation(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def check_cut_outputs(source, files, cut, plan=None):
    """Content, not only duration sums: audio from the chosen point to the end, intro excluded."""
    start = math.floor(round(cut * 1e6) * 30 / 1e6) / 30
    remainder = LINK_SECONDS - start
    count = max(1, math.ceil((remainder - 1e-7) / 90))
    durations = []
    for path in files:
        data = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                                   '-of', 'json', str(path)]))
        durations.append(float(data['format']['duration']))
        assert all(float(s['duration']) <= 90 for s in data['streams']), data
    assert len(files) == count, (files, count)
    assert all(0 < d <= 90 for d in durations), durations
    assert all(abs(d - remainder / count) <= 1 / 30 + .002 for d in durations), durations
    assert abs(sum(durations) - remainder) <= 1 / 30 + .002, (durations, remainder)
    if plan:
        assert [round(p['duration'], 3) for p in plan] == [round(d, 3) for d in durations], (plan, durations)
    original, joined = pcm(source), array.array('h')
    for path in files:
        joined.extend(pcm(path))
    offset = round(start * 48000)
    for at in (0, round(durations[0] * 48000) - 960, len(original) - offset - 2400):
        assert correlation(original[offset + at:offset + at + 1920], joined[at:at + 1920]) > .95, at
    assert abs(correlation(original[:1920], joined[:1920])) < .3, 'intro was generated'
    return durations


def manual_http(client, source):
    response = client.post('/api/jobs/link', json={'url': 'https://www.instagram.com/reel/SYNTHETICHTTP/',
                                                   'mode': 'manual'})
    assert response.status_code == 202, response.text
    job_id = response.json()['id']
    for _ in range(240):
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['state'] in ('awaiting_cut', 'error'):
            break
        time.sleep(.25)
    assert job['state'] == 'awaiting_cut' and job['parts'] == [], job
    assert abs(job['source_duration'] - LINK_SECONDS) < .05, job
    time.sleep(2.5)  # The worker idles: no split before the user's explicit cut.
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'awaiting_cut'
    ranged = client.get(f'/api/jobs/{job_id}/source', headers={'Range': 'bytes=0-99'})
    assert ranged.status_code == 206 and len(ranged.content) == 100, ranged.status_code
    assert ranged.headers['content-type'] == 'video/mp4'
    for bad in ['{"start":NaN}', '{"start":Infinity}', '{"start":true}', '{"start":"17.4"}', '{"start":-0.5}',
                json.dumps({'start': job['source_duration']}), '{"start":null}', '[]']:
        response = client.post(f'/api/jobs/{job_id}/cut', content=bad, headers={'content-type': 'application/json'})
        assert response.status_code == 400, (bad, response.status_code)
    first = client.post(f'/api/jobs/{job_id}/cut', json={'start': 17.4})
    assert first.status_code == 202, first.text
    plan = first.json()['plan']
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 17.4}).status_code == 202  # Retry: same job.
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 20}).status_code == 409
    assert len(client.get('/api/jobs').json()) == 1
    for _ in range(480):
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['state'] in ('ready', 'error'):
            break
        time.sleep(.25)
    assert job['state'] == 'ready', job
    assert client.get(f'/api/jobs/{job_id}/source').status_code == 404
    files = []
    for part in job['parts']:
        target = source.parent / f'http-{part["name"]}'
        target.write_bytes(client.get(f"/api/jobs/{job_id}/files/{part['name']}").content)
        files.append(target)
    durations = check_cut_outputs(source, files, 17.4, plan)
    assert client.delete(f'/api/jobs/{job_id}').status_code == 204
    print(f'HTTP PASS: synthetic link → awaiting cut (no auto split) → Range preview → strict cut validation '
          f'→ cut 17.4 s → equal parts {durations} → content from cut point, intro excluded')


def browser_smoke(source, link):
    from playwright.sync_api import sync_playwright
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with sync_playwright() as p:
        # Reuse an existing browser when available; no heavyweight browser install in this script.
        existing = sorted((Path.home() / '.cache/ms-playwright').glob('chromium-*/chrome-linux*/chrome'))
        executable = os.getenv('INSTAZAP_TEST_CHROME') or (str(existing[-1]) if existing else None)
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
                for first in [30, 60]:
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
                'state':'queued', 'progress':0, 'error':None, 'parts':[]}
        page.route('**/api/jobs', lambda route: route.fulfill(json=[fake]))
        page.route('**/api/jobs/link', lambda route: route.fulfill(status=202, json={'id':'synthetic'}))
        page.get_by_role('button', name='Preparar vídeo pelo link').click()
        expect(page.locator('.job-status')).to_have_text('Na fila')
        fake.update(state='processing', progress=25)
        expect(page.locator('.job-status')).to_have_text('Preparando as partes · 25%', timeout=10000)
        fake.update(state='error', error='Link de teste indisponível. Envie o arquivo.')
        expect(page.locator('.job .error')).to_contain_text('Envie o arquivo', timeout=10000)
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
        assert page.locator('.part').count() == 2
        assert page.locator('.part p').all_text_contents() == ['Parte 1 de 2 · 30.0 s', 'Parte 2 de 2 · 31.2 s']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator(".part video").first.evaluate('(v) => { window.originalPreview=v; v.preload="auto"; v.load(); }')
        page.wait_for_function('() => document.querySelector(".part video").readyState >= 2')
        # Polling another job must retain the existing preview node and playback state.
        jobs = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')
        fake.update(state='processing', error=None)
        page.route('**/api/jobs', lambda route: route.fulfill(json=jobs+[fake]))
        expect(page.locator('.job-status').last).to_contain_text('Preparando', timeout=10000)
        scroll_before_poll = page.evaluate('scrollY')
        fake['progress'] = 75
        expect(page.locator('.job-status').last).to_contain_text('75%', timeout=10000)
        assert page.evaluate('scrollY') == scroll_before_poll
        assert page.evaluate('window.originalPreview === document.querySelector(".part video") && window.originalPreview.readyState >= 2')
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
        context.close()
        manual_browser(browser, errors, artifacts, link)
        timeline_browser(browser, errors, link.parent)
        assert errors == [], errors
        browser.close()
    print('BROWSER PASS: mobile/desktop order, delayed link/upload busy feedback, duplicate guard, 30/60 transmission, rejection recovery, synthetic link stages, real 30-first upload/downloads, stable previews, reload/delete')


def manual_browser(browser, errors, artifacts, link):
    from playwright.sync_api import expect

    def position(page):
        return page.evaluate('() => Number(document.querySelector("#cut-range").value)')

    def seeked(page):
        page.wait_for_function('() => { const v = document.querySelector("#cutter-video"); '
                               'return !v.seeking && Math.abs(v.currentTime - Number(document.querySelector("#cut-range").value)) < .02; }')

    def range_x(page, fraction):
        # Native range: the thumb centre travels between half-thumb insets on both ends.
        box = page.locator('#cut-range').bounding_box()
        inset = 8
        return box['x'] + inset + (box['width'] - 2 * inset) * fraction, box['y'] + box['height'] / 2

    # Phone: explicit mode choice, delayed feedback, touch timeline, exact controls, reload, cut.
    context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True,
                                  accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(BASE)
    page.get_by_text('Continuar de onde parei').tap()
    expect(page.locator('#first-part-control')).to_be_hidden()
    button = page.get_by_role('button', name='Carregar vídeo pelo link para escolher o corte')
    held = []
    page.route('**/api/jobs/link', lambda route: held.append(route))
    page.locator('#url').fill('https://www.instagram.com/reel/SLOWSYNTH1/?igsh=test')
    button.tap()
    expect(page.locator('#submission-message')).to_contain_text('Aguarde… recebendo o link')
    expect(button).to_be_disabled()
    page.wait_for_timeout(300)
    assert len(held) == 1 and held[0].request.post_data_json == {
        'url': 'https://www.instagram.com/reel/SLOWSYNTH1/?igsh=test', 'mode': 'manual'}
    page.locator('#link-form').evaluate('(f) => f.dispatchEvent(new Event("submit", {cancelable:true}))')
    page.wait_for_timeout(100)
    assert len(held) == 1, 'Duplicate load during pending request'
    held[0].continue_()
    page.unroute('**/api/jobs/link')
    expect(page.locator('#cutter-message')).to_contain_text('preparando a prévia')
    assert page.locator('#cutter').evaluate('(e) => e.getBoundingClientRect().top < innerHeight')
    expect(page.locator('#cutter-message')).to_contain_text('Prévia pronta', timeout=60000)
    expect(button).to_be_enabled()
    duration = page.evaluate('() => Number(document.querySelector("#cut-range").max)')
    assert abs(duration - LINK_SECONDS) < .06, duration
    video = page.locator('#cutter-video').bounding_box()
    assert video['width'] >= 390 - 16 and video['x'] >= 0 and video['x'] + video['width'] <= 390, video
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    for selector in ['#cut-range', '#cut-button', '#cutter-discard', '#cut-goto button',
                     '.nudges button >> nth=0', '.nudges button >> nth=3']:
        box = page.locator(selector).bounding_box()
        assert box['height'] >= 44 and box['width'] >= 44, (selector, box)

    # Real touch: tap, then a dragged touch sequence through the Chrome DevTools input pipeline.
    page.touchscreen.tap(*range_x(page, .5))
    assert abs(position(page) / duration - .5) < .06, position(page)
    seeked(page)
    cdp = context.new_cdp_session(page)
    x, y = range_x(page, .2)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    for step in range(1, 11):
        x, y = range_x(page, .2 + .5 * step / 10)
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x, 'y': y}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    assert abs(position(page) / duration - .7) < .06, position(page)
    seeked(page)
    expect(page.locator('#cut-time')).not_to_have_text('0:00,000')

    # Exact typed time and frame steps; the visible timestamp and the player agree.
    page.locator('#cut-input').fill('17,4')
    page.get_by_role('button', name='Ir', exact=True).tap()
    expect(page.locator('#cut-time')).to_have_text('0:17,400')
    seeked(page)
    expect(page.locator('#cut-plan')).to_contain_text('entre 60 e 90 s')
    expect(page.locator('#cut-button')).to_have_text('Gerar partes a partir de 0:17,400')
    page.get_by_role('button', name='Avançar um quadro').tap()
    expect(page.locator('#cut-time')).to_have_text('0:17,433')

    # Polling must not reset the player, its source or its position.
    page.evaluate('() => { window.keptVideo = document.querySelector("#cutter-video"); '
                  'window.keptSrc = window.keptVideo.currentSrc; }')
    before = page.evaluate('() => document.querySelector("#cutter-video").currentTime')
    page.wait_for_timeout(4500)
    assert page.evaluate('() => window.keptVideo === document.querySelector("#cutter-video") && '
                         'window.keptVideo.currentSrc === window.keptSrc')
    assert abs(page.evaluate('() => document.querySelector("#cutter-video").currentTime') - before) < .001

    # Reload: the waiting preview and chosen point come back; nothing was split meanwhile.
    page.reload()
    expect(page.locator('#cutter-message')).to_contain_text('Prévia pronta', timeout=30000)
    expect(page.locator('#cut-time')).to_have_text('0:17,433')
    seeked(page)
    expect(page.locator('input[name="mode"][value="manual"]')).to_be_checked()
    assert page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]['state'] == 'awaiting_cut'

    # Arbitrary fractional point by touch drag: about 25 % leaves 90-120 s, an honest <60 s split.
    x, y = range_x(page, .4)
    cdp = context.new_cdp_session(page)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
    for step in range(1, 9):
        x, y = range_x(page, .4 - .15 * step / 8)
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x, 'y': y}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    assert abs(position(page) / duration - .25) < .06, position(page)
    page.get_by_role('button', name='Avançar um quadro').tap()
    seeked(page)
    remainder = duration - position(page)
    assert 90 < remainder < 120, remainder
    expect(page.locator('#cut-plan')).to_contain_text('menos de 60 s cada')
    page.locator('#cutter').scroll_into_view_if_needed()
    page.screenshot(path=str(artifacts / 'manual-mobile-cutter.png'))

    # Rejection and network failure restore the controls on the same preview.
    cut_button = page.locator('#cut-button')
    page.route('**/api/jobs/*/cut', lambda route: route.fulfill(
        status=400, content_type='application/json', body=json.dumps({'detail': 'Corte de teste rejeitado.'})))
    cut_button.tap()
    expect(page.locator('#cutter-error')).to_contain_text('Corte de teste rejeitado')
    expect(cut_button).to_be_enabled()
    page.unroute('**/api/jobs/*/cut')
    page.route('**/api/jobs/*/cut', lambda route: route.abort('failed'))
    cut_button.tap()
    expect(page.locator('#cutter-error')).to_contain_text('Conexão interrompida')
    expect(cut_button).to_be_enabled()
    page.unroute('**/api/jobs/*/cut')

    # Stale poll + repeated clicks: hold one listing and the cut, then answer the poll with old data.
    snapshot = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')
    stale, cuts = [], []

    def hold_first(route):
        if not stale and route.request.method == 'GET':
            stale.append(route)
        else:
            route.continue_()
    page.route('**/api/jobs', hold_first)
    page.wait_for_timeout(2500)
    assert stale, 'No poll was held'
    page.route('**/api/jobs/*/cut', lambda route: cuts.append(route))
    chosen = position(page)
    cut_button.tap()
    expect(page.locator('#cutter-message')).to_contain_text('Enviando o corte')
    expect(cut_button).to_be_disabled()
    cut_button.evaluate('(b) => { b.disabled = false; b.click(); b.click(); }')
    page.wait_for_timeout(300)
    assert len(cuts) == 1, 'Repeated clicks sent another cut'
    assert cuts[0].request.post_data_json == {'start': chosen}
    cuts[0].continue_()
    expect(page.locator('#cutter')).to_be_hidden(timeout=10000)
    stale[0].fulfill(json=snapshot)
    page.wait_for_timeout(500)
    expect(page.locator('#cutter')).to_be_hidden()
    assert page.evaluate('() => !document.querySelector("#cutter-video").getAttribute("src")')
    page.unroute('**/api/jobs')
    page.unroute('**/api/jobs/*/cut')
    expect(page.locator('.job').first).to_be_focused()
    page.get_by_text('Pronto para o Status', exact=True).wait_for(timeout=180000)
    expect(page.locator('.job').first).to_contain_text('somente dali até o fim')
    expect(page.locator('.job').first).to_contain_text('Partes com menos de 60 s')
    files = []
    count = page.locator('.part').count()
    for index in range(1, count + 1):
        with page.expect_download() as pending:
            page.get_by_role('link', name=f'Baixar parte {index}', exact=True).tap()
        target = link.parent / f'browser-{index}.mp4'
        pending.value.save_as(target)
        files.append(target)
    durations = check_cut_outputs(link, files, chosen)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(artifacts / 'manual-mobile.png'), full_page=True)
    page.get_by_role('button', name='Excluir agora').tap()
    page.wait_for_function('() => document.querySelectorAll(".job").length === 0')
    context.close()

    # Desktop: responsive width, mouse drag, preview failure, link replacement, discard, downloader failure.
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(BASE)
    page.get_by_text('Continuar de onde parei').click()
    page.route('**/api/jobs/*/source', lambda route: route.fulfill(status=200, content_type='video/mp4',
                                                                     body=b'not a video'))
    page.locator('#url').fill('https://www.instagram.com/reel/SYNTHA1/')
    page.get_by_role('button', name='Carregar vídeo pelo link para escolher o corte').click()
    expect(page.locator('#cutter-error')).to_contain_text('Não foi possível reproduzir a prévia', timeout=60000)
    expect(page.locator('#cut-button')).to_be_enabled()
    page.unroute('**/api/jobs/*/source')
    old = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]['id']
    page.locator('#url').fill('https://www.instagram.com/reel/SYNTHB2/')
    replace = page.get_by_role('button', name='Carregar vídeo pelo link para escolher o corte')
    expect(replace).to_have_text('Trocar vídeo →')
    replace.click()
    expect(page.locator('#cutter-message')).to_contain_text('Prévia pronta', timeout=60000)
    jobs = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')
    assert len(jobs) == 1 and jobs[0]['id'] != old and jobs[0]['state'] == 'awaiting_cut', jobs
    assert jobs[0]['id'] in page.evaluate('() => document.querySelector("#cutter-video").currentSrc')
    expect(page.locator('#cutter-error')).to_be_hidden()
    video = page.locator('#cutter-video').bounding_box()
    panel = page.locator('.panel').bounding_box()
    assert 600 < video['width'] <= panel['width'] and video['height'] <= 1000 * .7 + 1, (video, panel)
    x, y = range_x(page, .1)
    page.mouse.move(x, y)
    page.mouse.down()
    x, y = range_x(page, .6)
    page.mouse.move(x, y, steps=12)
    page.mouse.up()
    assert abs(position(page) / LINK_SECONDS - .6) < .05, position(page)
    seeked(page)
    page.locator('#cut-range').focus()
    before = position(page)
    page.keyboard.press('ArrowRight')
    assert abs(position(page) - before - 1 / 30) < 1e-6
    page.keyboard.press('PageDown')
    assert abs(position(page) - before + 1 + 1e-9 - 1 / 30) < 1e-6
    page.screenshot(path=str(artifacts / 'manual-desktop.png'), full_page=True)
    page.get_by_role('button', name='Descartar vídeo').click()
    expect(page.locator('#cutter')).to_be_hidden()
    page.wait_for_function('() => document.querySelectorAll(".job").length === 0')
    page.locator('#url').fill('https://www.instagram.com/reel/FAILSYNTH/')
    page.get_by_role('button', name='Carregar vídeo pelo link para escolher o corte').click()
    expect(page.locator('.job .error')).to_contain_text('Não foi possível obter este link público', timeout=30000)
    expect(page.locator('#cutter')).to_be_hidden()
    page.get_by_role('button', name='Excluir agora').click()
    page.wait_for_function('() => document.querySelectorAll(".job").length === 0')
    context.close()
    print(f'BROWSER MANUAL PASS: touch tap/drag + mouse drag + keys/frames/typed time, phone-width player and '
          f'44 px targets, delayed load feedback, stable player while polling, reload recovery, rejection/network '
          f'retry, one cut despite repeated clicks, stale poll ignored, cut {chosen:.6f} s → {durations}, '
          f'preview failure, link replacement, discard, downloader failure')


def timeline_fixture(path, offset=None, video=12.0, audio=12.0):
    """SYNTHETIC: testsrc2 (a distinct picture per frame) + chirp; container picks the start time."""
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-f', 'lavfi', '-i',
            f'testsrc2=s=96x128:r=30:d={video}', '-f', 'lavfi', '-i',
            f'aevalsrc=0.3*sin(2*PI*(300*t+3*t*t)):s=48000:d={audio}', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac']
    if offset is not None:
        args += ['-output_ts_offset', str(offset)]
    subprocess.run([*args, str(path)], check=True)
    return float(json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json',
                                                      str(path)]))['format']['start_time'])


def source_frames(path):
    """Every decoded source frame (96x128 RGB) with its time on the demuxer's common origin."""
    origin = float(json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json',
                                                        str(path)]))['format']['start_time'])
    times = [float(f['best_effort_timestamp_time']) - origin for f in json.loads(subprocess.check_output(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames', '-show_entries',
         'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))['frames']]
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-threads', '1', '-i', str(path), '-map', '0:v:0',
                                   '-fps_mode', 'passthrough', '-s', '96x128', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    size = 96 * 128 * 3
    return times, [raw[i:i + size] for i in range(0, len(raw), size)]


def matching_frame(picture, pictures):
    scores = [sum(abs(a - b) for a, b in zip(picture[::5], p[::5], strict=True)) / len(picture[::5]) for p in pictures]
    index = min(range(len(scores)), key=scores.__getitem__)
    return index, scores[index]


CANVAS = """() => { const v = document.querySelector('#cutter-video'); const c = document.createElement('canvas');
    c.width = 96; c.height = 128; const g = c.getContext('2d'); g.drawImage(v, 0, 0, 96, 128);
    const d = g.getImageData(0, 0, 96, 128).data, out = [];
    for (let i = 0; i < d.length; i += 4) out.push(d[i], d[i + 1], d[i + 2]);
    return out; }"""


def timeline_browser(browser, errors, folder):
    """Real Chromium on the authorized Range preview: what is displayed at t is where the part starts.
    c843c0b served the raw source: Chromium showed a +5 s file at 4.978 s and the cut jumped ~5 s."""
    from playwright.sync_api import expect
    cases = []
    for name, offset, video, audio in [('positive-offset.mp4', 5, 12.0, 12.0), ('negative-start.mkv', None, 12.0, 12.0),
                                       ('transport.ts', None, 12.0, 12.0), ('audio-tail.mp4', None, 1.0, 4.0)]:
        path = folder / f'SYNTHETIC-TEST-{name}'
        start = timeline_fixture(path, offset, video, audio)
        cases.append((path, start))
    assert cases[0][1] > 4.9 and cases[1][1] < 0 and cases[2][1] > 1, cases
    context = browser.new_context(viewport={'width': 1000, 'height': 1000})
    page = context.new_page()
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(BASE)
    page.get_by_text('Continuar de onde parei').click()

    def load(path):
        previous = page.evaluate('() => cutter.id')
        page.locator('#video-file').set_input_files(str(path))
        page.locator('#upload-button').click()
        page.wait_for_function('(old) => cutter.id && cutter.id !== old && cutter.loaded', arg=previous, timeout=120000)
        expect(page.locator('#cutter-message')).to_contain_text('Prévia pronta', timeout=120000)
        expect(page.locator('#cutter-error')).to_be_hidden()
        return page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]

    def go(seconds):
        page.locator('#cut-input').fill(f'{seconds:.3f}'.replace('.', ','))
        page.get_by_role('button', name='Ir', exact=True).click()
        page.wait_for_function('() => { const v = document.querySelector("#cutter-video"); '
                               'return !v.seeking && v.readyState >= 2; }')
        page.wait_for_function('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))')
        return page.evaluate('() => document.querySelector("#cutter-video").currentTime')

    report = []
    for number, (path, _start) in enumerate(cases):
        if number:
            # (a) A file upload in manual mode replaces the waiting preview, like the link does.
            expect(page.locator('#upload-button')).to_contain_text('Trocar pelo vídeo do aparelho')
            old = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]['id']
        job = load(path)
        if number:
            assert job['id'] != old and len(page.evaluate('() => fetch("/api/jobs").then(r => r.json())')) == 1
        times, pictures = source_frames(path)
        clock = page.evaluate('() => { const v = document.querySelector("#cutter-video"); return [v.currentTime, v.duration]; }')
        assert clock[0] == 0 and abs(clock[1] - job['source_duration']) <= 1 / 30 + .002, (path.name, clock, job)
        duration = job['source_duration']
        positions = [0.0, 1.2345, duration * .45, duration - .2] if duration > 2 else [0.0, .5]
        if 'audio-tail' in path.name:
            positions = [0.4, 2.0, 3.0, 3.7]  # Video ends at 1 s: the held last picture is what parts start with.
        for position in positions:
            current = go(position)
            assert abs(current - position) < 1e-3, (path.name, position, current)
            expect(page.locator('#cut-time')).to_have_text(clock_text(position))
            index, score = matching_frame(bytes(page.evaluate(CANVAS)), pictures)
            # 30 fps re-timing: the picture is the source frame on screen within ±1/60 s of the point.
            end = times[index + 1] if index + 1 < len(times) else math.inf
            # Before the first video frame (A/V offset), players and parts show the first frame.
            assert times[index] <= max(position, times[0]) + 1 / 60 + 1e-3 and end > position - 1 / 60 - 1e-3 \
                and score < 8, (
                path.name, position, index, times[index], score)
            report.append(f'{path.name}@{position:.3f}→#{index}')
        # Keyboard frame step moves the real player by one 30 fps frame too.
        page.locator('#cut-range').focus()
        page.keyboard.press('ArrowLeft')
        page.wait_for_function('() => !document.querySelector("#cutter-video").seeking')
        cut = 3.0 if 'audio-tail' in path.name else positions[1]
        go(cut)
        displayed, _ = matching_frame(bytes(page.evaluate(CANVAS)), pictures)
        if number == 0:
            # (b) Switching to full mode while a preview waits keeps it (and its player) intact.
            page.evaluate('() => { window.keptVideo = document.querySelector("#cutter-video"); '
                          'window.keptSrc = window.keptVideo.currentSrc; }')
            page.get_by_text('Dividir o vídeo inteiro').click()
            page.locator('#url').fill('https://www.instagram.com/reel/SYNTHFULL/')
            page.get_by_role('button', name='Preparar vídeo pelo link').click()
            expect(page.locator('#error')).to_contain_text('aguardando o corte')
            expect(page.locator('#cutter')).to_be_visible()
            assert page.evaluate('() => window.keptVideo === document.querySelector("#cutter-video") && '
                                 'window.keptVideo.currentSrc === window.keptSrc')
            expect(page.locator('#cut-time')).to_have_text(clock_text(cut))
            assert page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]['state'] == 'awaiting_cut'
            page.get_by_text('Continuar de onde parei').click()
            page.locator('#url').fill('')
        page.get_by_role('button', name='Gerar partes', exact=False).click()
        page.get_by_text('Pronto para o Status', exact=True).first.wait_for(timeout=120000)
        ready = page.evaluate('() => fetch("/api/jobs").then(r => r.json())')[0]
        part = folder / f'timeline-{number}.mp4'
        part.write_bytes(bytes(page.evaluate(
            f'() => fetch("/api/jobs/{ready["id"]}/files/{ready["parts"][0]["name"]}").then(r => r.arrayBuffer())'
            '.then(b => Array.from(new Uint8Array(b)))')))
        first = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(part), '-frames:v', '1', '-s', '96x128',
                                         '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        index, _ = matching_frame(first, pictures)
        assert index == displayed, (path.name, cut, index, displayed)
        # Audio starts at the same instant (chirp): no intro, and the whole tail is there.
        grid = math.floor(round(cut * 1e6) * 30 / 1e6) * 1600
        source_audio, joined = pcm(path) if not path.name.endswith('.mkv') else pcm_raw(path), array.array('h')
        for item in ready['parts']:
            target = folder / f'timeline-{number}-{item["name"]}'
            target.write_bytes(bytes(page.evaluate(
                f'() => fetch("/api/jobs/{ready["id"]}/files/{item["name"]}").then(r => r.arrayBuffer())'
                '.then(b => Array.from(new Uint8Array(b)))')))
            joined.extend(pcm(target))
        assert correlation(source_audio[grid:grid + 1920], joined[:1920]) > .95, (path.name, cut)
        assert len(joined) >= len(source_audio) - grid - 48, (path.name, len(joined), len(source_audio) - grid)
        assert abs(sum(p['duration'] for p in ready['parts']) - (duration - grid / 48000)) <= 1 / 30 + .002
        report.append(f'cut {cut:.4f} {path.name}: first frame #{index} = displayed #{displayed}')
        page.get_by_role('button', name='Excluir agora').first.click()
        page.wait_for_function('() => document.querySelectorAll(".job").length === 0')
        if number == len(cases) - 1:
            break
        # Load a preview to be replaced by the next fixture's upload.
        load(cases[number][0])
        if number == 1:
            # (d) Replace while the previous preview is playing: its late events must not leak.
            page.evaluate('() => { const v = document.querySelector("#cutter-video"); v.muted = true; return v.play(); }')
            page.wait_for_timeout(600)
            assert page.evaluate('() => !document.querySelector("#cutter-video").paused')
    context.close()
    print('BROWSER TIMELINE PASS: real Range preview in Chromium; displayed frame = source frame on screen within ±1/60 s and '
          '= first output frame, audio from the same instant: ' + '; '.join(report))


def pcm_raw(path):
    return array.array('h', subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i',
                                                     str(path), '-vn', '-ac', '1', '-ar', '48000', '-f', 's16le', '-']))


def clock_text(value):
    ms = round(value * 1000)
    return f'{ms // 60000}:{ms % 60000 // 1000:02d},{ms % 1000:03d}'


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
        link = Path(temporary) / f'SYNTHETIC-TEST-link-{LINK_SECONDS}s.mp4'
        link_fixture(link)
        env = dict(os.environ, INSTAZAP_DATA=str(Path(temporary) / 'data'),
                   INSTAZAP_ACCESS_SECRET='', INSTAZAP_ORIGIN='', INSTAZAP_SECURE_COOKIE='0',
                   INSTAZAP_BIND='127.0.0.1', INSTAZAP_PORT=str(options.port), INSTAZAP_SYNTHETIC_LINK=str(link))
        proc = subprocess.Popen([sys.executable, 'scripts/smoke_server.py'],
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
                manual_http(client, link)
                if options.browser:
                    browser_smoke(source, link)
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
