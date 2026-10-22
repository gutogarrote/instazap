"""Manual cut mode. All media are synthetic TEST fixtures made by local ffmpeg; the link
downloader is replaced in-process, so nothing here is evidence of an Instagram download."""
import array
import asyncio
import json
import math
import random
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from instazap.app import create_app
from instazap.config import Settings
from instazap.media import FRAME, MediaError, cut_plan, presentation_duration, split, validate_cut
from instazap.store import Store

LINK = 'https://www.instagram.com/reel/MANUALTEST/'


def check_plan(cut, duration):
    plan = cut_plan(cut, duration)
    start, end = plan[0][0], plan[-1][0] + plan[-1][1]
    assert abs(end - Fraction(duration)) <= Fraction(1, 2 * 10**6)  # Measured end, at µs resolution.
    remainder = end - start
    count = math.ceil(remainder / 90)
    assert start <= Fraction(cut) + Fraction(1, 10**6) and Fraction(cut) - start < FRAME
    assert (start / FRAME).denominator == 1
    assert len(plan) == count  # Fewest parts that respect the ceiling.
    assert sum(span for _, span in plan) == remainder  # Exactly the remainder: nothing lost or repeated.
    for index, (offset, span) in enumerate(plan):
        assert 0 < span <= 90
        assert abs(span - remainder / count) < FRAME  # Equal within one output frame.
        if 60 * count <= remainder <= 90 * count:
            assert span >= 60
        if index:
            assert offset == plan[index - 1][0] + plan[index - 1][1]
            assert (offset / FRAME).denominator == 1
    return [float(span) for _, span in plan]


@pytest.mark.parametrize(('remainder', 'expected'), [
    (0.04, [0.04]), (29.9, [29.9]), (59.99, [59.99]), (60, [60]), (75.5, [75.5]), (90, [90]),
    (90.01, [45, 45.01]), (105.4, [52.7, 52.7]), (119.99, [59.966667, 60.023333]), (120, [60, 60]),
    (120.01, [60, 60.01]), (150, [75, 75]), (179.99, [90, 89.99]), (180, [90, 90]),
    (180.01, [60, 60, 60.01]), (225, [75, 75, 75]), (240, [80, 80, 80]),
])
def test_plan_boundaries_and_exact_multiples(remainder, expected):
    assert check_plan(0, remainder) == pytest.approx(expected, abs=1e-6)
    # Same remainder after a grid-aligned, exactly representable cut.
    assert check_plan(17.5, 17.5 + remainder) == pytest.approx(expected, abs=1e-6)


def test_plan_fractional_cuts_property():
    generator = random.Random(20261022)
    for _ in range(4000):
        duration = generator.uniform(1, 240)
        cut = generator.uniform(0, duration)
        if cut < duration:
            check_plan(cut, duration)
    # Binary noise in a measured duration must not move a boundary by a frame.
    assert [float(span) for _, span in cut_plan(17.4, 140.6)] == [61.6, 61.6]
    # Typed decimal is not binary-exact: 17.4 must start at 17.4, not one frame earlier.
    assert float(cut_plan(17.4, 45.12)[0][0]) == pytest.approx(17.4, abs=1e-12)
    # Rounding to microseconds never moves a valid cut onto the end.
    assert len(cut_plan(239.9999995, 240)) == 1
    assert len(cut_plan(math.nextafter(240, 0), 240)) == 1


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf, -0.001, 30, 30.5, True, False, '1', None,
                                   [], {}, 10**400])
def test_validate_cut_rejects(value):
    with pytest.raises(ValueError):
        validate_cut(value, 30)


def command(*args):
    return subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1',
                                    *map(str, args)])


def metadata(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                               '-of', 'json', str(path)]))


def pcm(path):
    audio = next(s for s in metadata(path)['streams'] if s['codec_type'] == 'audio')
    return array.array('h', command('-i', path, '-vn', '-af', f"atrim=duration={audio['duration']}",
                                    '-ar', '48000', '-ac', '1', '-f', 's16le', '-'))


def correlation(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def chirp(tmp_path, video, audio=None, rate='30', marker=None):
    """Chirp audio (each instant has its own pitch); optional red frame index `marker`."""
    source = tmp_path / 'synthetic-manual.mp4'
    args = ['-f', 'lavfi', '-i', f'color=blue:s=64x48:r={rate}:d={video}']
    if audio:
        args += ['-f', 'lavfi', '-i', f'aevalsrc=0.3*sin(2*PI*(300*t+3*t*t)):s=48000:d={audio}', '-c:a', 'aac']
    vf = f"drawbox=color=red:t=fill:enable='eq(n,{marker})'" if marker is not None else 'null'
    command(*args, '-vf', vf, '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', source)
    return source


def red(frame):
    return sum(frame[::3]) / len(frame[::3]) > 200 and sum(frame[2::3]) / len(frame[2::3]) < 40


@pytest.mark.parametrize(('case', 'video', 'audio', 'rate', 'cut', 'count'), [
    # Shared reel ending mid-video: remainder < 60 s gives one shorter part.
    ('short_remainder', 47.3, 47.3, '30', 17.4, 1),
    # NTSC source, arbitrary fractional cut: two equal feasible parts.
    ('ntsc_feasible', 150.2, 150.2, '30000/1001', 17.43, 2),
    # (90, 120) s remainder: equal parts < 60 s are unavoidable; nothing exceeds 90.
    ('gap_remainder', 120.0, 120.0, '30', 14.6, 2),
    # Exact multiple: two parts of exactly 90 s (the ceiling itself).
    ('exact_multiple', 200.0, 200.0, '30', 20.0, 2),
    # Audio outlives video; video outlives audio (decoded ends, not packet metadata).
    ('audio_longer', 101.1, 101.192, '30', 3.3, 2),
    ('video_longer', 101.2, 101.108, '30', 3.3, 2),
])
def test_real_cut_preserves_content_from_point(tmp_path, case, video, audio, rate, cut, count):
    fps = Fraction(rate)
    start = math.floor(round(cut * 10**6) * 30 / 10**6) / 30
    marker = math.floor(start * fps)  # Source frame on screen at the (grid) cut.
    source = chirp(tmp_path, video, audio, rate, marker)
    plan = cut_plan(cut, presentation_duration(source))  # Decoded duration, not container metadata.
    assert len(plan) == count
    parts = split(source, tmp_path, cut_start=cut)
    assert [p['duration'] for p in parts] == pytest.approx([float(s) for _, s in plan], abs=1 / 30 + .002)
    if case == 'exact_multiple':
        assert [p['duration'] for p in parts] == [90, 90]
    original, joined, lengths = pcm(source), array.array('h'), []
    for part in parts:
        target = tmp_path / part['name']
        data = metadata(target)
        assert 0 < float(data['format']['duration']) <= 90
        assert all(float(s['duration']) <= 90 for s in data['streams'])
        assert all(abs(float(s['start_time'])) < 1 / 48000 for s in data['streams'])
        command('-i', target, '-f', 'null', '-')  # Independent full decode.
        lengths.append(len(pcm(target)))
        joined.extend(pcm(target))
    offset = round(start * 48000)
    remaining = len(original) - offset
    # Same bound as the full-video timeline tests; correlations below prove alignment.
    assert len(joined) >= remaining - 48, (len(joined), remaining)
    # Sample-exact inner boundaries (a 1/1000 s movie timescale used to drop 16 samples per join).
    for length, (_, span) in zip(lengths[:-1], plan[:-1], strict=True):
        assert length == span * 48000
    first = lengths[0]
    for at in (0, first - 2400, first, remaining - 2400):
        if 0 <= at <= remaining - 1920:
            assert correlation(original[offset + at:offset + at + 1920], joined[at:at + 1920]) > .95, at
    # The intro already shared is not generated: the first samples are from the cut, not the start.
    assert abs(correlation(original[:1920], joined[:1920])) < .3
    # The first frame is the source frame shown at the cut point.
    frame = command('-i', tmp_path / parts[0]['name'], '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-')
    assert red(frame)
    # And the final content survives.
    tail = command('-sseof', '-0.1', '-i', tmp_path / parts[-1]['name'], '-vn', '-f', 's16le', '-')
    assert max(abs(v) for v in array.array('h', tail)) > 1000


def test_real_cut_no_audio_and_tiny_remainder(tmp_path):
    source = chirp(tmp_path, 31.0, None, '30', 900)
    parts = split(source, tmp_path, cut_start=30.0)
    assert len(parts) == 1 and not parts[0]['audio']
    data = metadata(tmp_path / parts[0]['name'])
    assert [s['codec_type'] for s in data['streams']] == ['video']
    assert float(data['format']['duration']) == pytest.approx(1.0, abs=.034)
    frame = command('-i', tmp_path / parts[0]['name'], '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-')
    assert red(frame)
    for path in tmp_path.glob('video-parte-*'):
        path.unlink()
    # One frame before the end is still a valid, non-empty part.
    parts = split(source, tmp_path, cut_start=30.99)
    assert len(parts) == 1 and 0 < parts[0]['duration'] <= 1 / 30 + .002


def test_split_rejects_cut_outside_measured_duration(media, tmp_path):
    with pytest.raises(MediaError, match='fora da duração'):
        split(media(2), tmp_path, cut_start=2.0)
    assert not list(tmp_path.iterdir())


@pytest.fixture
def downloader(monkeypatch, media):
    """Replace only the network fetch with a synthetic local file (the bounded run wrapper is skipped)."""
    calls = []
    state = {'source': media(47.3), 'fail': False}

    def fetch(argv, **kwargs):
        calls.append(argv)
        if state['fail']:
            raise RuntimeError('simulated offline extractor')
        Path(argv[4]).write_bytes(state['source'].read_bytes())
    monkeypatch.setattr('instazap.worker.run', fetch)
    return calls, state


def session(app):
    client = TestClient(app)
    client.headers['x-csrf-token'] = client.get('/api/session').json()['csrf']
    return client


def prepare(client, app):
    job_id = client.post('/api/jobs/link', json={'url': LINK + '?igsh=x', 'mode': 'manual'}).json()['id']
    assert app.state.worker.once()
    return job_id


def test_manual_link_waits_for_explicit_cut_then_splits_remainder(site, downloader):
    client, app = site
    calls, _ = downloader
    job_id = prepare(client, app)
    assert calls[0][3] == LINK
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'awaiting_cut' and job['mode'] == 'manual' and job['parts'] == []
    assert job['source_duration'] == pytest.approx(47.3, abs=.05) and job['cut_start'] is None
    with app.state.store.db() as db:
        assert db.execute('SELECT url FROM jobs WHERE id=?', (job_id,)).fetchone()[0] is None
    # Preview readiness never starts a split.
    assert not app.state.worker.once()
    assert sorted(p.name for p in (app.state.store.files / job_id).iterdir()) == ['source']
    ranged = client.get(f'/api/jobs/{job_id}/source', headers={'Range': 'bytes=10-41'})
    assert ranged.status_code == 206 and len(ranged.content) == 32
    assert ranged.headers['content-type'] == 'video/mp4' and ranged.headers['cache-control'] == 'no-store'
    response = client.post(f'/api/jobs/{job_id}/cut', json={'start': 17.4})
    assert response.status_code == 202
    assert [p['duration'] for p in response.json()['plan']] == pytest.approx([29.9], abs=.05)
    assert client.get(f'/api/jobs/{job_id}/source').status_code == 404
    assert app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'ready' and job['cut_start'] == 17.4
    assert [p['duration'] for p in job['parts']] == pytest.approx([29.9], abs=1 / 30 + .002)
    assert len(calls) == 1  # The cut reuses the persisted source; no second download.
    assert not (app.state.store.files / job_id / 'source').exists()


def test_manual_upload_mode_and_full_mode_unchanged(site, media):
    client, app = site
    job_id = client.post('/api/jobs/upload', content=media(61.2).read_bytes(), headers={'x-mode': 'manual'}).json()['id']
    assert app.state.worker.once()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'awaiting_cut'
    assert client.delete(f'/api/jobs/{job_id}').status_code == 204
    job_id = client.post('/api/jobs/upload', content=media(61.2).read_bytes()).json()['id']
    assert app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['mode'] == 'full' and [p['duration'] for p in job['parts']] == pytest.approx([60, 1.2], abs=.001)
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 404


@pytest.mark.parametrize('body', ['{"start":NaN}', '{"start":Infinity}', '{"start":-Infinity}', '{"start":1e400}',
                                  '{"start":true}', '{"start":false}', '{"start":"17.4"}', '{"start":null}',
                                  '{"start":-0.001}', '{"start":47.3}', '{"start":1000}', '{"start":[1]}',
                                  '{"start":' + '9' * 400 + '}', '{}', '[]', '17', 'not json', '{"start":1,'])
def test_cut_strict_validation(site, downloader, body):
    client, app = site
    job_id = prepare(client, app)
    duration = client.get(f'/api/jobs/{job_id}').json()['source_duration']
    body = body.replace('47.3', repr(duration))
    response = client.post(f'/api/jobs/{job_id}/cut', content=body, headers={'content-type': 'application/json'})
    assert response.status_code == 400, response.text
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'awaiting_cut' and job['cut_start'] is None
    assert client.post(f'/api/jobs/{job_id}/cut', content='x' * 2049).status_code == 413


@pytest.mark.parametrize('value', ['manual ', 'MANUAL', 1, None, True, ['manual']])
def test_mode_validation(site, value):
    client, app = site
    assert client.post('/api/jobs/link', json={'url': LINK, 'mode': value}).status_code == 400
    assert client.post('/api/jobs/upload', content=b'x', headers={'x-mode': str(value)}).status_code == 400
    assert client.get('/api/jobs').json() == []
    assert not list(app.state.store.files.iterdir())


def test_cut_isolation_and_preparation_states(site, downloader):
    client, app = site
    job_id = client.post('/api/jobs/link', json={'url': LINK, 'mode': 'manual'}).json()['id']
    # Not measured yet: no preview, no cut.
    assert client.get(f'/api/jobs/{job_id}/source').status_code == 404
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 409
    assert app.state.worker.once()
    other = session(app)
    assert other.get(f'/api/jobs/{job_id}/source').status_code == 404
    assert other.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 404
    assert other.delete(f'/api/jobs/{job_id}').status_code == 404
    # CSRF and origin protections cover the new mutation.
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}, headers={'x-csrf-token': ''}).status_code == 403
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1},
                       headers={'origin': 'https://evil.test'}).status_code == 403
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'awaiting_cut'
    # A waiting preview holds the session's slot; deleting it frees the slot.
    assert client.post('/api/jobs/link', json={'url': LINK}).status_code == 429
    assert client.delete(f'/api/jobs/{job_id}').status_code == 204
    assert not (app.state.store.files / job_id).exists()
    assert client.post('/api/jobs/link', json={'url': LINK}).status_code == 202


def test_repeated_cut_is_idempotent_and_never_enqueues_twice(site, downloader):
    client, app = site
    job_id = prepare(client, app)
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 202
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 202  # Network retry.
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1.0}).status_code == 202  # Same JSON number.
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 11}).status_code == 409
    for invalid in (True, '10.5', None, math.nan):  # Strict even after the cut was chosen.
        assert client.post(f'/api/jobs/{job_id}/cut', content=json.dumps({'start': invalid}),
                           headers={'content-type': 'application/json'}).status_code == 400
    assert len(client.get('/api/jobs').json()) == 1
    assert app.state.worker.once() and not app.state.worker.once()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'ready'
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 11}).status_code == 409


def test_concurrent_cuts_race_one_winner(site, downloader):
    client, app = site
    job_id = prepare(client, app)
    owner = app.state.store.digest(client.cookies['instazap_session'])
    results, barrier = [], threading.Barrier(8)

    def attempt(value):
        store = Store(app.state.store.settings)  # Separate SQLite connection per thread.
        barrier.wait()
        results.append(store.choose_cut(job_id, owner, value))
    threads = [threading.Thread(target=attempt, args=(1 + i / 10,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(results).count('queued') == 1 and sorted(results).count('conflict') == 7

    async def http_race():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                                     cookies=dict(client.cookies)) as other:
            other.headers['x-csrf-token'] = client.headers['x-csrf-token']
            return await asyncio.gather(*[other.post(f'/api/jobs/{job_id}/cut', json={'start': 5})
                                          for _ in range(5)])
    assert {r.status_code for r in asyncio.run(http_race())} == {409}
    with app.state.store.db() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 1


def test_preview_ttl_touch_cap_and_expiry(site, downloader):
    client, app = site
    store = app.state.store
    store.settings.preview_ttl, store.settings.preview_max = 100, 1000
    job_id = prepare(client, app)
    job = store.get(job_id, store.digest(client.cookies['instazap_session']))
    assert job['expires'] == pytest.approx(time.time() + 100, abs=5)
    # Polling extends only when less than half remains, and never beyond created + max.
    store.update(job_id, expires=time.time() + 30)
    client.get('/api/jobs')
    assert store.get(job_id, job['owner'])['expires'] == pytest.approx(time.time() + 100, abs=5)
    store.update(job_id, created=time.time() - 990, expires=time.time() + 5)
    client.get('/api/jobs')
    assert store.get(job_id, job['owner'])['expires'] == pytest.approx(time.time() + 10, abs=5)
    # Expired: inaccessible at once, physically removed by cleanup, cut refused.
    store.update(job_id, expires=time.time() - 1)
    assert client.get(f'/api/jobs/{job_id}/source').status_code == 404
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 404
    store.cleanup()
    assert not (store.files / job_id).exists()
    assert client.get('/api/jobs').json() == []


def test_cut_extends_expiry_so_cleanup_cannot_remove_queued_source(site, downloader):
    client, app = site
    job_id = prepare(client, app)
    app.state.store.update(job_id, expires=time.time() + .5)
    assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 1}).status_code == 202
    time.sleep(.6)
    app.state.store.cleanup()
    assert (app.state.store.files / job_id / 'source').exists()
    assert app.state.worker.once()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'ready'


def test_restart_keeps_waiting_preview_and_interrupted_cut(tmp_path, downloader):
    config = Settings(data=tmp_path, worker=False)
    first = create_app(config)
    with TestClient(first) as client:
        client.headers['x-csrf-token'] = client.get('/api/session').json()['csrf']
        job_id = prepare(client, first)
        cookies, csrf = dict(client.cookies), client.headers['x-csrf-token']
    second = create_app(config)  # Restart: recovery must keep the source while the user decides.
    with TestClient(second, cookies=cookies) as client:
        client.headers['x-csrf-token'] = csrf
        assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'awaiting_cut'
        assert client.get(f'/api/jobs/{job_id}/source', headers={'Range': 'bytes=0-9'}).status_code == 206
        assert client.post(f'/api/jobs/{job_id}/cut', json={'start': 20.25}).status_code == 202
        assert second.state.store.claim()['id'] == job_id  # Interrupted mid-split.
        (second.state.store.files / job_id / 'video-parte-01-de-01.mp4').write_bytes(b'partial')
    third = create_app(config)
    with TestClient(third, cookies=cookies) as client:
        job = client.get(f'/api/jobs/{job_id}').json()
        assert job['state'] == 'queued' and job['cut_start'] == 20.25
        assert sorted(p.name for p in (third.state.store.files / job_id).iterdir()) == ['source']
        assert third.state.worker.once()
        job = client.get(f'/api/jobs/{job_id}').json()
        assert job['state'] == 'ready'
        assert [p['duration'] for p in job['parts']] == pytest.approx([47.3 - 20.233333], abs=1 / 30 + .002)


def test_downloader_failure_is_distinct_from_media_failure(site, downloader, tmp_path):
    client, app = site
    _, state = downloader
    state['fail'] = True
    failed = prepare(client, app)
    job = client.get(f'/api/jobs/{failed}').json()
    assert job['state'] == 'error' and 'Não foi possível obter este link público' in job['error']
    assert not (app.state.store.files / failed / 'source').exists()
    state['fail'], state['source'] = False, tmp_path / 'not-media'
    state['source'].write_bytes(b'downloaded bytes that are not a video')
    broken = prepare(client, app)
    job = client.get(f'/api/jobs/{broken}').json()
    assert job['state'] == 'error' and 'link público' not in job['error']
    assert not (app.state.store.files / broken / 'source').exists()


def test_old_database_jobs_get_full_mode(tmp_path, media):
    import sqlite3
    with sqlite3.connect(tmp_path / 'queue.sqlite3') as db:
        db.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                   state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                   kind TEXT NOT NULL, url TEXT, progress INTEGER DEFAULT 0,
                   error TEXT, parts TEXT DEFAULT '[]', first_part_seconds INTEGER NOT NULL DEFAULT 60)""")
        db.execute("INSERT INTO jobs(id,owner,state,created,expires,kind,first_part_seconds) "
                   "VALUES ('old','owner','queued',1,?,'upload',30)", (time.time() + 1000,))
        db.execute("INSERT INTO jobs(id,owner,state,created,expires,kind,parts) VALUES "
                   """('done','owner','ready',1,?,'upload','[{"name":"existing.mp4"}]')""", (time.time() + 1000,))
    (tmp_path / 'jobs' / 'old').mkdir(parents=True)
    (tmp_path / 'jobs' / 'old' / 'source').write_bytes(media(61.2).read_bytes())
    (tmp_path / 'jobs' / 'done').mkdir()
    (tmp_path / 'jobs' / 'done' / 'existing.mp4').write_bytes(b'kept')
    app = create_app(Settings(data=tmp_path, worker=False))
    with TestClient(app):
        store = app.state.store
        for job_id in ('old', 'done'):
            job = store.get(job_id, 'owner')
            assert job['mode'] == 'full' and job['cut_start'] is None and job['source_duration'] is None
        assert (tmp_path / 'jobs' / 'done' / 'existing.mp4').read_bytes() == b'kept'
        assert app.state.worker.once()
        job = store.get('old', 'owner')
        assert job['state'] == 'ready'
        assert [p['duration'] for p in json.loads(job['parts'])] == pytest.approx([30, 31.2], abs=.001)
    Store(Settings(data=tmp_path, worker=False))  # Migration is idempotent.
