import asyncio
import time
import zipfile
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient

from instazap.app import create_app
from instazap.config import Settings


@pytest.mark.parametrize(('url', 'canonical'), [
    ('https://www.instagram.com/reel/ABCDE/?igsh=secret&utm_source=share#fragment',
     'https://www.instagram.com/reel/ABCDE/'),
    ('https://x.com/user/status/123456?s=20&t=secret#fragment', 'https://x.com/user/status/123456'),
])
def test_post_link_stores_and_downloads_canonical_url(site, monkeypatch, url, canonical, caplog):
    client, app = site
    response = client.post('/api/jobs/link', json={'url': url})
    assert response.status_code == 202
    job_id = response.json()['id']
    with app.state.store.db() as db:
        row = db.execute('SELECT state, url FROM jobs WHERE id=?', (job_id,)).fetchone()
    assert tuple(row) == ('queued', canonical)
    calls = []
    def unavailable(argv, **kwargs):
        calls.append(argv)
        raise RuntimeError('offline')
    monkeypatch.setattr('instazap.worker.run', unavailable)
    app.state.worker.once()
    assert calls[0][3] == canonical
    assert 'secret' not in caplog.text


def test_upload_job_download_ownership_delete(site, media):
    client, app = site
    response = client.post('/api/jobs/upload', content=media(1.2).read_bytes())
    assert response.status_code == 202
    job_id = response.json()['id']
    assert client.post('/api/jobs/upload', content=b'x').status_code == 429
    assert app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'ready', job
    assert job['first_part_seconds'] == 60
    assert len(job['parts']) == 1
    path = f"/api/jobs/{job_id}/files/{job['parts'][0]['name']}"
    file = client.get(path)
    assert file.status_code == 200 and b'ftyp' in file.content[:40]
    assert file.headers['cache-control'] == 'no-store'
    assert client.get(path, headers={'Range': 'bytes=0-63'}).status_code == 206
    assert client.get(path+'?preview=1').headers['content-disposition'].startswith('inline')
    archive = client.get(f'/api/jobs/{job_id}/files/partes.zip')
    with zipfile.ZipFile(BytesIO(archive.content)) as z:
        assert z.namelist() == [job['parts'][0]['name']]
        assert z.read(z.namelist()[0]) == file.content
    other = TestClient(app)
    other.headers['x-csrf-token'] = other.get('/api/session').json()['csrf']
    assert other.get('/api/jobs').json() == []
    assert other.get(f'/api/jobs/{job_id}').status_code == 404
    assert other.get(path).status_code == 404
    assert other.delete(f'/api/jobs/{job_id}').status_code == 404
    assert client.delete(f'/api/jobs/{job_id}').status_code == 204
    assert client.get(path).status_code == 404
    assert not (app.state.store.files / job_id).exists()


def test_expiry(site, media):
    client, app = site
    job_id = client.post('/api/jobs/upload', content=media(1).read_bytes()).json()['id']
    app.state.worker.once()
    app.state.store.update(job_id, expires=time.time()-1)
    assert client.get(f'/api/jobs/{job_id}').status_code == 404
    app.state.store.cleanup()
    assert not (app.state.store.files / job_id).exists()


def test_errors_and_csrf(site):
    client, app = site
    assert client.post('/api/jobs/upload', content=b'').status_code == 400
    job_id = client.post('/api/jobs/upload', content=b'invalid video').json()['id']
    app.state.worker.once()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'error'
    assert list((app.state.store.files / job_id).iterdir()) == []
    assert client.post('/api/jobs/upload', content=b'x', headers={'x-csrf-token': ''}).status_code == 403
    assert client.post('/api/jobs/link', json={'url':'https://127.0.0.1/'}, headers={'origin':'https://evil.test'}).status_code == 403
    assert client.get('/api/session', headers={'sec-fetch-site':'cross-site'}).status_code == 403
    assert client.post('/api/jobs/link', content='x'*2049).status_code == 413
    assert client.post('/api/jobs/link', json=[]).status_code == 400
    assert client.get('/api/jobs/bad/files/../../queue.sqlite3').status_code == 404


def test_access_gate_and_session_expiry(tmp_path):
    app = create_app(Settings(data=tmp_path, worker=False, access_secret='test-only-secret'))
    with TestClient(app) as client:
        assert client.get('/api/session').status_code == 401
        assert client.get('/api/jobs').status_code == 401
        assert client.get('/api/session', headers={'x-access-secret':'wrong'}).status_code == 401
        response = client.get('/api/session', headers={'x-access-secret':'test-only-secret'})
        assert response.status_code == 200
        cookie = response.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
        assert client.get('/api/jobs').status_code == 200
        with app.state.store.db() as db:
            db.execute('UPDATE sessions SET expires=0')
        assert client.get('/api/jobs').status_code == 401


def test_streaming_real_200mb_limit(site):
    _, app = site
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            session = (await client.get('/api/session')).json()
            sent = 0
            async def chunks():
                nonlocal sent
                chunk = b'x' * 1_000_000
                for _ in range(201):
                    sent += len(chunk)
                    yield chunk
                    await asyncio.sleep(0)
            response = await client.post('/api/jobs/upload', content=chunks(), headers={'x-csrf-token':session['csrf']})
            assert response.status_code == 413
            assert sent == 201_000_000
            assert (await client.get('/api/jobs')).json() == []
            assert not list(app.state.store.files.iterdir())
    asyncio.run(check())


def test_restart_recovery(site, media):
    client, app = site
    job_id = client.post('/api/jobs/upload', content=media(1).read_bytes()).json()['id']
    interrupted = app.state.store.claim()
    assert interrupted['id'] == job_id
    (app.state.store.files / job_id / 'partial.mp4').write_bytes(b'incomplete')
    app.state.store.recover()
    assert not (app.state.store.files / job_id / 'partial.mp4').exists()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'queued'
    assert app.state.worker.once()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'ready'
    # Interrupted uploads are never queued as if complete.
    owner = app.state.store.digest(client.cookies['instazap_session'])
    receiving = app.state.store.create(owner, 'upload')
    (app.state.store.files / receiving / 'source').write_bytes(b'partial')
    app.state.store.recover()
    assert client.get(f'/api/jobs/{receiving}').json()['state'] == 'error'
    assert not (app.state.store.files / receiving / 'source').exists()


def test_queue_bound(site):
    client, app = site
    app.state.store.settings.max_jobs = 1
    assert client.post('/api/jobs/upload', content=b'bad').status_code == 202
    other = TestClient(app)
    other.headers['x-csrf-token'] = other.get('/api/session').json()['csrf']
    assert other.post('/api/jobs/upload', content=b'bad').status_code == 429


def test_real_new_app_restart(tmp_path, media):
    config = Settings(data=tmp_path, worker=False)
    first = create_app(config)
    with TestClient(first) as client:
        csrf = client.get('/api/session').json()['csrf']
        cookies = dict(client.cookies)
        job_id = client.post('/api/jobs/upload', content=media(60).read_bytes(),
                             headers={'x-csrf-token':csrf, 'x-first-part-seconds':'30'}).json()['id']
        assert first.state.store.claim()['id'] == job_id
    second = create_app(config)
    with TestClient(second, cookies=cookies) as client:
        assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'queued'
        second.state.worker.once()
        job = client.get(f'/api/jobs/{job_id}').json()
        assert job['state'] == 'ready'
        assert job['first_part_seconds'] == 30
        assert [p['duration'] for p in job['parts']] == [30, 30]
    third = create_app(config)
    with TestClient(third, cookies=cookies) as client:
        job = client.get(f'/api/jobs/{job_id}').json()
        assert job['state'] == 'ready'
        assert client.get(f"/api/jobs/{job_id}/files/{job['parts'][0]['name']}").status_code == 200


def test_link_failure_offers_upload(site, monkeypatch):
    client, app = site
    job_id = client.post('/api/jobs/link', json={'url':'https://www.instagram.com/reel/ABCDE/'}).json()['id']
    def unavailable(*args, **kwargs):
        raise RuntimeError('simulated offline extractor')
    monkeypatch.setattr('instazap.worker.run', unavailable)
    app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'error'
    assert 'Enviar arquivo' in job['error']
    assert 'url' not in job


def test_delete_processing_cancels_and_leaves_no_files(site, media, monkeypatch):
    import threading
    client, app = site
    job_id = client.post('/api/jobs/upload', content=media(1).read_bytes()).json()['id']
    started = threading.Event()
    def long_split(source, folder, progress, cancel, first_part_seconds):
        started.set()
        assert cancel.wait(5)
        raise RuntimeError('cancelled')
    monkeypatch.setattr('instazap.worker.split', long_split)
    task = threading.Thread(target=app.state.worker.once)
    task.start()
    assert started.wait(5)
    assert client.delete(f'/api/jobs/{job_id}').status_code == 204
    task.join(5)
    assert not task.is_alive()
    assert client.get(f'/api/jobs/{job_id}').status_code == 404
    assert not (app.state.store.files / job_id).exists()


def test_graceful_shutdown_requeues_instead_of_failing(site, media, monkeypatch):
    import threading
    client, app = site
    job_id = client.post('/api/jobs/upload', content=media(1).read_bytes()).json()['id']
    started = threading.Event()
    def long_split(source, folder, progress, cancel, first_part_seconds):
        started.set()
        assert cancel.wait(5)
        raise RuntimeError('shutdown')
    monkeypatch.setattr('instazap.worker.split', long_split)
    task = threading.Thread(target=app.state.worker.once)
    task.start()
    assert started.wait(5)
    app.state.worker.stop.set()
    app.state.worker.cancel.set()
    task.join(5)
    assert not task.is_alive()
    assert client.get(f'/api/jobs/{job_id}').json()['state'] == 'queued'
    assert (app.state.store.files / job_id / 'source').exists()


@pytest.mark.parametrize('value', [0, -30, 30.5, 30.0, '30', None, True, 90, {}, []])
def test_link_first_part_validation(site, value):
    client, app = site
    response = client.post('/api/jobs/link', json={
        'url':'https://www.instagram.com/reel/ABCDE/', 'first_part_seconds':value})
    assert response.status_code == 400
    assert '30 ou 60' in response.json()['detail']
    assert client.get('/api/jobs').json() == []


@pytest.mark.parametrize('value', ['0', '-30', '30.5', '30.0', 'invalid', '', '90', ' 30'])
def test_upload_first_part_validation(site, value):
    client, app = site
    response = client.post('/api/jobs/upload', content=b'x', headers={'x-first-part-seconds':value})
    assert response.status_code == 400
    assert '30 ou 60' in response.json()['detail']
    assert client.get('/api/jobs').json() == []
    assert not list(app.state.store.files.iterdir())


@pytest.mark.parametrize('first', [30, 60])
def test_link_choice_reaches_real_worker(site, media, monkeypatch, first):
    client, app = site
    def synthetic_download(argv, **kwargs):
        # Test fixture replaces only the network operation, never extraction evidence.
        from pathlib import Path
        Path(argv[4]).write_bytes(media(60).read_bytes())
    monkeypatch.setattr('instazap.worker.run', synthetic_download)
    job_id = client.post('/api/jobs/link', json={
        'url':'https://x.com/user/status/123456', 'first_part_seconds':first}).json()['id']
    app.state.store.recover()
    assert app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'ready', job
    assert job['first_part_seconds'] == first
    assert [p['duration'] for p in job['parts']] == ([30, 30] if first == 30 else [60])


def test_old_database_migration_preserves_jobs_and_defaults(tmp_path):
    import sqlite3
    from instazap.store import Store
    with sqlite3.connect(tmp_path / 'queue.sqlite3') as db:
        db.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                   state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                   kind TEXT NOT NULL, url TEXT, progress INTEGER DEFAULT 0,
                   error TEXT, parts TEXT DEFAULT '[]')""")
        db.execute("INSERT INTO jobs(id,owner,state,created,expires,kind) VALUES ('old','owner','queued',1,?,'link')",
                   (time.time() + 1000,))
        db.execute('''INSERT INTO jobs(id,owner,state,created,expires,kind,parts)
                   VALUES ('ready','owner','ready',1,?,'upload','[{"name":"existing.mp4"}]')''',
                   (time.time() + 1000,))
    config = Settings(data=tmp_path, worker=False)
    store = Store(config)
    assert store.get('old', 'owner')['first_part_seconds'] == 60
    ready = store.get('ready', 'owner')
    assert ready['state'] == 'ready' and ready['first_part_seconds'] == 60
    assert ready['parts'] == '[{"name":"existing.mp4"}]'
    assert Store(config).claim()['first_part_seconds'] == 60  # Migration is idempotent.
    store.recover()
    assert store.claim()['id'] == 'old'


def test_queued_jobs_keep_independent_choices(site, media):
    client, app = site
    other = TestClient(app)
    other.headers['x-csrf-token'] = other.get('/api/session').json()['csrf']
    ids = []
    for session, first in [(client, 30), (other, 60)]:
        ids.append(session.post('/api/jobs/upload', content=media(60).read_bytes(),
                                headers={'x-first-part-seconds':str(first)}).json()['id'])
    app.state.store.recover()
    assert app.state.worker.once() and app.state.worker.once()
    for session, job_id, durations in [(client, ids[0], [30, 30]), (other, ids[1], [60])]:
        job = session.get(f'/api/jobs/{job_id}').json()
        assert job['state'] == 'ready'
        assert [p['duration'] for p in job['parts']] == durations


def test_processing_failure_logs_stage_and_removes_media(site, media, monkeypatch, caplog):
    client, app = site
    job_id = client.post('/api/jobs/upload', content=media(1).read_bytes()).json()['id']

    def failure(*args, **kwargs):
        raise RuntimeError('https://private.invalid/?token=secret /private/source.mp4')

    monkeypatch.setattr('instazap.worker.split', failure)
    assert app.state.worker.once()
    job = client.get(f'/api/jobs/{job_id}').json()
    assert job['state'] == 'error'
    assert not list((app.state.store.files / job_id).iterdir())
    assert 'worker stage=split failure=RuntimeError' in caplog.text
    assert 'private' not in caplog.text and 'secret' not in caplog.text
    assert 'private' not in job['error']
