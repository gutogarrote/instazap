import json
import logging
import threading
import time
import zipfile

from .media import prepare_preview, split
from .process import run


logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.active = None
        self.thread = threading.Thread(target=self.loop, name='instazap-worker', daemon=True)

    def once(self):
        with self.lock:
            self.store.cleanup()
            job = self.store.claim()
            if not job:
                return False
            self.cancel.clear()
            self.active = job['id']
        folder = self.store.files / job['id']
        source = folder / 'source'
        terminal = False
        # A manual job runs twice: prepare (fetch + measure, then wait for the user), then cut.
        preparing = job['mode'] == 'manual' and job['cut_start'] is None
        # No URL left on a manual job means its source is already here (re-preparing an old preview).
        fetch = job['kind'] == 'link' and job['url'] is not None and (job['mode'] == 'full' or preparing)
        stage = 'download' if fetch else 'split'
        preview = folder / 'preview.mp4'
        try:
            if fetch:
                # URL is not written to logs or retained after completion.
                try:
                    self.fetch(job['url'], source)
                except Exception as exc:
                    raise ValueError('Não foi possível obter este link público (bloqueio, login ou formato indisponível). Use “Enviar arquivo”.') from exc
            if preparing:
                stage = 'preview'
                duration = prepare_preview(source, preview, cancel=self.cancel)
                # Never split here: the source waits for the user's explicit cut.
                self.store.update(job['id'], state='awaiting_cut', progress=100, source_duration=duration,
                                  url=None, expires=self.store.preview_expiry(job['created']))
                return True
            stage = 'split'
            preview.unlink(missing_ok=True)  # The cut is chosen; only the source is needed now.
            parts = split(source, folder, lambda i, n: self.store.update(job['id'], progress=10 + int(i / n * 80)), cancel=self.cancel,
                          first_part_seconds=job['first_part_seconds'], cut_start=job['cut_start'])
            stage = 'archive'
            with zipfile.ZipFile(folder / 'partes.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
                for part in parts:
                    archive.write(folder / part['name'], part['name'])
            self.store.update(job['id'], state='ready', progress=100, parts=json.dumps(parts), url=None,
                              expires=time.time() + self.store.settings.ttl)
            terminal = True
        except Exception as exc:
            # Only controlled stage/type fields: never exception text, URLs or paths.
            logger.warning('worker stage=%s failure=%s', stage, type(exc).__name__)
            message = str(exc) if isinstance(exc, ValueError) else 'Não foi possível processar este vídeo. Tente enviar outro arquivo.'
            if self.stop.is_set():
                self.store.update(job['id'], state='queued', progress=0)
                for path in folder.glob('*'):
                    if path.name != 'source':
                        path.unlink(missing_ok=True)
            else:
                self.store.update(job['id'], state='error', error=message, url=None,
                                  expires=time.time() + self.store.settings.ttl)
                terminal = True
                for path in folder.glob('*'):
                    path.unlink(missing_ok=True)
        finally:
            if terminal:
                source.unlink(missing_ok=True)
            with self.lock:
                self.active = None
        return True

    def fetch(self, url, source):
        import sys
        run([sys.executable, '-m', 'instazap.download', url, str(source)], timeout=210,
            file_limit=self.store.settings.max_bytes, cancel=self.cancel)

    def loop(self):
        while not self.stop.is_set():
            try:
                worked = self.once()
            except Exception:
                # Infrastructure errors should not terminate the worker permanently.
                worked = False
            if not worked:
                self.stop.wait(1)
