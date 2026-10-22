"""Port numbers do not scope cookies: previews need independent names and stores."""
from contextlib import ExitStack
from http.cookiejar import CookieJar

import pytest
from fastapi.testclient import TestClient

from instazap.app import create_app
from instazap.config import Settings


def test_default_cookie_retains_existing_session_and_results(tmp_path, monkeypatch):
    monkeypatch.delenv('INSTAZAP_COOKIE_NAME', raising=False)
    settings = Settings(data=tmp_path, worker=False)
    assert settings.cookie_name == 'instazap_session'
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.get('/api/session')
        assert first.headers['set-cookie'].startswith('instazap_session=')
        old_token = client.cookies['instazap_session']
        client.headers['x-csrf-token'] = first.json()['csrf']
        job_id = client.post('/api/jobs/upload', content=b'fixture').json()['id']
        second = client.get('/api/session')
        assert 'set-cookie' not in second.headers
        assert second.json()['csrf'] == first.json()['csrf']
        assert client.cookies['instazap_session'] == old_token
        assert client.get(f'/api/jobs/{job_id}').status_code == 200


def test_named_instances_share_host_cookie_jar_without_overwriting(tmp_path, monkeypatch):
    jar = CookieJar()
    clients, tokens, csrf, jobs = [], [], [], []
    with ExitStack() as stack:
        for index, name in enumerate(['instazap_session', 'instazap_preview_a', 'instazap_preview_b']):
            monkeypatch.setenv('INSTAZAP_COOKIE_NAME', name)
            settings = Settings(data=tmp_path / str(index), worker=False)
            assert settings.cookie_name == name
            app = create_app(settings)
            client = stack.enter_context(TestClient(
                app, base_url=f'http://100.64.0.1:{8871 + index}', cookies=jar))
            clients.append(client)
            response = client.get('/api/session')
            assert response.headers['set-cookie'].startswith(f'{name}=')
            assert 'HttpOnly' in response.headers['set-cookie']
            assert 'SameSite=strict' in response.headers['set-cookie']
            tokens.append(client.cookies[name])
            csrf.append(response.json()['csrf'])
            client.headers['x-csrf-token'] = csrf[-1]
            jobs.append(client.post('/api/jobs/upload', content=b'fixture').json()['id'])
        assert len(list(jar)) == 3  # One shared browser cookie jar, all for the same host/path.
        assert len(set(tokens)) == len(set(csrf)) == 3
        for index, client in enumerate(clients):
            # All instance cookies are sent to every port, just like a browser.
            response = client.get('/api/session')
            for name in ['instazap_session', 'instazap_preview_a', 'instazap_preview_b']:
                assert f'{name}=' in response.request.headers['cookie']
            assert 'set-cookie' not in response.headers
            assert response.json()['csrf'] == csrf[index]
            assert [job['id'] for job in client.get('/api/jobs').json()] == [jobs[index]]
            other = (index + 1) % len(clients)
            assert client.get(f'/api/jobs/{jobs[other]}').status_code == 404
            assert client.delete(f'/api/jobs/{jobs[index]}',
                                 headers={'x-csrf-token':csrf[other]}).status_code == 403
            assert client.delete(f'/api/jobs/{jobs[other]}').status_code == 404
            assert client.get(f'/api/jobs/{jobs[index]}').status_code == 200
            # A token from another store cannot become this instance's owner.
            original_header = response.request.headers['cookie']
            name = client.app.state.store.settings.cookie_name
            wrong_cookie = original_header.replace(f'{name}={tokens[index]}', f'{name}={tokens[other]}')
            assert client.get('/api/jobs', headers={'cookie':wrong_cookie}).status_code == 401
            assert client.delete(f'/api/jobs/{jobs[index]}').status_code == 204


@pytest.mark.parametrize('name', ['', 'with space', 'a\tb', 'a\rb', 'a\nb', 'a\0b', 'a\x7fb',
    'a;b', 'a=b', 'a,b', 'a:b', 'a/b', 'a\\b', 'a"b', 'a(b)', 'a[b]', 'a{b}', 'café',
    'path', 'HttpOnly', None, 123])
def test_invalid_cookie_config_rejected_at_startup(tmp_path, name):
    with pytest.raises(ValueError, match='INSTAZAP_COOKIE_NAME'):
        create_app(Settings(data=tmp_path / 'unused', worker=False, cookie_name=name))
    assert not (tmp_path / 'unused').exists()


def test_invalid_cookie_name_from_environment_fails_clearly(monkeypatch):
    monkeypatch.setenv('INSTAZAP_COOKIE_NAME', 'bad;name')
    with pytest.raises(ValueError, match='INSTAZAP_COOKIE_NAME'):
        Settings()


def test_cookie_name_accepts_ascii_token_punctuation(tmp_path):
    name = "preview!#$%&'*+-.^_`|~0123"
    app = create_app(Settings(data=tmp_path, worker=False, cookie_name=name))
    with TestClient(app) as client:
        assert client.get('/api/session').headers['set-cookie'].startswith(f'{name}=')
        assert client.get('/api/jobs').status_code == 200
