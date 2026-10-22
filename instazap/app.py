import asyncio
import fcntl
import hashlib
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from .config import Settings
from .network import original_url
from .media import validate_first_part
from .store import Busy, Store
from .worker import Worker

STATIC = Path(__file__).parent / 'static'


def create_app(settings=None):
    settings = settings or Settings()
    store = Store(settings)
    worker = Worker(store)

    @asynccontextmanager
    async def lifespan(app):
        # One app process owns recovery and worker, even across accidental multiple launches.
        lock = (store.root / 'worker.lock').open('w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            store.recover()
            if settings.worker:
                worker.thread.start()
            yield
        finally:
            worker.stop.set()
            worker.cancel.set()
            if worker.thread.is_alive():
                await asyncio.to_thread(worker.thread.join, 35)
            lock.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.worker = store, worker

    def token(request):
        value = request.cookies.get('instazap_session', '')
        if not store.session(value):
            raise HTTPException(401, 'Sessão expirada. Recarregue a página para começar novamente.')
        return value

    def csrf(value):
        return hashlib.sha256(('csrf:' + value).encode()).hexdigest()

    def owner(request):
        return store.digest(token(request))

    def find(request, job_id):
        job = store.get(job_id, owner(request))
        if not job:
            raise HTTPException(404, 'Tarefa não encontrada ou expirada.')
        return job

    @app.middleware('http')
    async def protections(request, call_next):
        if request.url.path.startswith('/api/'):
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Origem não permitida.'}, 403)
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                expected = settings.public_origin or str(request.base_url).rstrip('/')
                origin = request.headers.get('origin')
                if origin and origin != expected:
                    return JSONResponse({'detail': 'Origem não permitida.'}, 403)
                try:
                    value = token(request)
                except HTTPException as exc:
                    return JSONResponse({'detail': exc.detail}, exc.status_code)
                if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), csrf(value)):
                    return JSONResponse({'detail': 'Proteção CSRF: recarregue a página.'}, 403)
        response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.get('/api/session')
    def session(request: Request, response: Response):
        value = request.cookies.get('instazap_session', '')
        if not store.session(value):
            if settings.access_secret and not secrets.compare_digest(request.headers.get('x-access-secret', ''), settings.access_secret):
                raise HTTPException(401, 'Digite a chave de acesso deste Instazap.')
            value = store.new_session()
            response.set_cookie('instazap_session', value, max_age=7 * 86400, httponly=True,
                                secure=settings.secure_cookie, samesite='strict', path='/')
        return {'csrf': csrf(value), 'max_bytes': settings.max_bytes}

    @app.get('/api/jobs')
    def jobs(request: Request):
        return [store.public(j) for j in store.listing(owner(request))]

    @app.post('/api/jobs/link', status_code=202)
    async def link(request: Request):
        # Bound request body before JSON parsing; stream enforcement also for chunked requests.
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 2048:
                        raise HTTPException(413, 'Link muito grande.')
        except TimeoutError as exc:
            raise HTTPException(408, 'Tempo limite ao receber o link. Tente novamente.') from exc
        import json
        try:
            data = json.loads(body)
            url = original_url(data.get('url'))
            first_part_seconds = validate_first_part(data.get('first_part_seconds', 60))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            job_id = store.create(owner(request), 'link', url, first_part_seconds=first_part_seconds)
        except Busy as exc:
            raise HTTPException(429, str(exc)) from exc
        return {'id': job_id}

    @app.post('/api/jobs/upload', status_code=202)
    async def upload(request: Request):
        raw = request.headers.get('x-first-part-seconds', '60')
        if raw not in ('15', '30', '60', '90'):
            raise HTTPException(400, 'A primeira parte deve ter 15, 30, 60 ou 90 segundos.')
        try:
            job_id = store.create(owner(request), 'upload', first_part_seconds=int(raw))
        except Busy as exc:
            raise HTTPException(429, str(exc)) from exc
        source = store.files / job_id / 'source'
        size = 0
        try:
            async with asyncio.timeout(120):
                with source.open('wb') as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > settings.max_bytes:
                            raise HTTPException(413, 'O arquivo ultrapassa 200 MB. Envie um arquivo menor.')
                        output.write(chunk)
            if size == 0:
                raise HTTPException(400, 'O arquivo está vazio.')
            store.update(job_id, state='queued')
        except (Exception, asyncio.CancelledError) as exc:
            store.remove(job_id)
            if isinstance(exc, (TimeoutError, ClientDisconnect)):
                raise HTTPException(408, 'Upload interrompido ou tempo limite excedido. Tente novamente.') from exc
            raise
        return {'id': job_id}

    @app.get('/api/jobs/{job_id}')
    def detail(request: Request, job_id: str):
        return store.public(find(request, job_id))

    @app.delete('/api/jobs/{job_id}', status_code=204)
    def delete(request: Request, job_id: str):
        with worker.lock:
            find(request, job_id)
            if worker.active == job_id:
                worker.cancel.set()
            store.remove(job_id)
        return Response(status_code=204)

    @app.get('/api/jobs/{job_id}/files/{name}')
    def file(request: Request, job_id: str, name: str):
        import json
        job = find(request, job_id)
        allowed = [p['name'] for p in json.loads(job['parts'])] + ['partes.zip']
        if job['state'] != 'ready' or name not in allowed:
            raise HTTPException(404, 'Arquivo indisponível.')
        return FileResponse(store.files / job_id / name,
                            media_type='application/zip' if name.endswith('.zip') else 'video/mp4',
                            filename=name, content_disposition_type='inline' if request.query_params.get('preview') == '1' else 'attachment')

    @app.get('/')
    def home():
        return FileResponse(STATIC / 'index.html')

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app


app = create_app()
