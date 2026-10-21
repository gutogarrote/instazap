import json
import logging
import threading
import time
import zipfile

from .media import split
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
        stage = 'download' if job['kind'] == 'link' else 'split'
        try:
            if job['kind'] == 'link':
                # URL is not written to logs or retained after completion.
                import sys
                try:
                    run([sys.executable, '-m', 'instazap.download', job['url'], str(source)], timeout=210,
                        file_limit=self.store.settings.max_bytes, cancel=self.cancel)
                except Exception as exc:
                    raise ValueError('Não foi possível obter este link público (bloqueio, login ou formato indisponível). Use “Enviar arquivo”.') from exc
            stage = 'split'
            parts = split(source, folder, lambda i, n: self.store.update(job['id'], progress=10 + int(i / n * 80)), cancel=self.cancel,
                          first_part_seconds=job['first_part_seconds'])
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

    def loop(self):
        while not self.stop.is_set():
            try:
                worked = self.once()
            except Exception:
                # Infrastructure errors should not terminate the worker permanently.
                worked = False
            if not worked:
                self.stop.wait(1)
