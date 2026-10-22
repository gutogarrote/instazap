import hashlib
import json
import math
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager

from .media import validate_cut, validate_first_part

PENDING = "('receiving','queued','processing','awaiting_cut')"


class Busy(ValueError):
    pass


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.data.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files = self.root / 'jobs'
        self.files.mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
              expires REAL NOT NULL, kind TEXT NOT NULL, url TEXT, progress INTEGER DEFAULT 0,
              error TEXT, parts TEXT DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS owner_index ON jobs(owner);
            ''')

            # Additive migration: old jobs keep the original 60-second behavior.
            db.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'first_part_seconds' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN first_part_seconds INTEGER NOT NULL DEFAULT 60 '
                           'CHECK(first_part_seconds IN (30,60))')
            # Manual mode: old jobs are 'full'; cut/duration stay NULL until measured/chosen.
            if 'mode' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'full' "
                           "CHECK(mode IN ('full','manual'))")
            if 'source_duration' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN source_duration REAL')
            if 'cut_start' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN cut_start REAL')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'queue.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, token):
        if not token or len(token) > 100:
            return False
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?',
                                  (self.digest(token), time.time())).fetchone())

    def new_session(self):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('INSERT INTO sessions VALUES (?,?)', (self.digest(token), time.time() + 7 * 86400))
        return token

    def create(self, owner, kind, url=None, first_part_seconds=60, mode='full'):
        validate_first_part(first_part_seconds)
        if mode not in ('full', 'manual'):
            raise ValueError('Modo inválido.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # A preview awaiting its cut holds a source and a future output reservation.
            if db.execute(f"SELECT count(*) FROM jobs WHERE owner=? AND state IN {PENDING}", (owner,)).fetchone()[0]:
                raise Busy('Você já tem um vídeo na fila ou aguardando o corte. Conclua ou exclua essa tarefa.')
            count = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
            pending = db.execute(f'SELECT count(*) FROM jobs WHERE state IN {PENDING}').fetchone()[0]
            usage = sum(f.stat().st_size for f in self.files.rglob('*') if f.is_file())
            if (count >= self.settings.max_jobs or
                    usage + (pending + 1) * 450_000_000 > self.settings.disk_budget or
                    shutil.disk_usage(self.root).free < 950_000_000):
                raise Busy('Armazenamento ou fila cheia. Exclua arquivos antigos ou tente mais tarde.')
            job_id = secrets.token_hex(16)
            now = time.time()
            db.execute('INSERT INTO jobs(id,owner,state,created,expires,kind,url,first_part_seconds,mode) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (job_id, owner, 'receiving' if kind == 'upload' else 'queued', now,
                        now + self.settings.ttl, kind, url, first_part_seconds, mode))
            (self.files / job_id).mkdir(mode=0o700)
        return job_id

    def update(self, job_id, **fields):
        with self.db() as db:
            db.execute(f'UPDATE jobs SET {",".join(k + "=?" for k in fields)} WHERE id=?',
                       (*fields.values(), job_id))

    def get(self, job_id, owner):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?',
                             (job_id, owner, time.time())).fetchone()
        return dict(row) if row else None

    def listing(self, owner):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC',
                                               (owner, time.time()))]

    def preview_expiry(self, created):
        return min(time.time() + self.settings.preview_ttl, created + self.settings.preview_max)

    def touch_previews(self, owner):
        # An open page keeps its source; refresh rarely, so expiry is not new data every poll.
        now = time.time()
        with self.db() as db:
            db.execute("UPDATE jobs SET expires=min(?+?, created+?) WHERE owner=? AND state='awaiting_cut' "
                       'AND expires>? AND expires<?',
                       (now, self.settings.preview_ttl, self.settings.preview_max, owner, now,
                        now + self.settings.preview_ttl / 2))

    def choose_cut(self, job_id, owner, start):
        """Atomically queue one cut: 'queued', 'same' (idempotent retry), 'pending', 'conflict' or None."""
        start = validate_cut(start, math.inf)  # Type first: True must not equal a stored 1.0.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state, cut_start, source_duration FROM jobs WHERE id=? AND owner=? '
                             "AND mode='manual' AND expires>?", (job_id, owner, time.time())).fetchone()
            if not row:
                return None
            if row['state'] != 'awaiting_cut':
                if row['cut_start'] is None:
                    return 'pending'
                return 'same' if row['cut_start'] == start else 'conflict'
            validate_cut(start, row['source_duration'])
            db.execute("UPDATE jobs SET state='queued', progress=0, cut_start=?, expires=? WHERE id=?",
                       (start, time.time() + self.settings.ttl, job_id))
            return 'queued'

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='queued' AND expires>? ORDER BY created LIMIT 1",
                             (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE jobs SET state='processing', progress=5 WHERE id=?", (row['id'],))
                return dict(row)

    def remove(self, job_id):
        # Worker deletion is serialized by caller; access disappears before filesystem removal.
        with self.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        shutil.rmtree(self.files / job_id, ignore_errors=True)

    def cleanup(self):
        now = time.time()
        with self.db() as db:
            # Select and delete together: a cut or touch that extends a row first keeps it.
            db.execute('BEGIN IMMEDIATE')
            ids = [r[0] for r in db.execute('SELECT id FROM jobs WHERE expires<=?', (now,))]
            db.execute('DELETE FROM jobs WHERE expires<=?', (now,))
            db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
        for job_id in ids:
            self.remove(job_id)

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE jobs SET state='queued',progress=0 WHERE state='processing'")
            db.execute("UPDATE jobs SET state='error',error='Upload interrompido. Envie o arquivo novamente.' WHERE state='receiving'")
            rows = db.execute('SELECT id,state FROM jobs').fetchall()
        for row in rows:
            folder = self.files / row['id']
            for path in folder.glob('*'):
                if row['state'] != 'ready' and path.name != 'source':
                    path.unlink(missing_ok=True)
                if row['state'] == 'error':
                    path.unlink(missing_ok=True)
        known = {r['id'] for r in rows}
        for folder in self.files.iterdir():
            if folder.name not in known:
                shutil.rmtree(folder, ignore_errors=True)
        self.cleanup()

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k not in ('owner', 'url', 'kind')} | {'parts': json.loads(job['parts'])}
