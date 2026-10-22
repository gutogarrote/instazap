import hashlib
import json
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager

from .media import validate_first_part


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
            if 'first_part_seconds' not in {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN first_part_seconds INTEGER NOT NULL DEFAULT 60 '
                           'CHECK(first_part_seconds IN (30,60))')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            # Keep the legacy column and its CHECK intact; additive snapshot for all four choices.
            if 'first_part_snapshot' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN first_part_snapshot INTEGER NOT NULL DEFAULT 60 '
                           'CHECK(first_part_snapshot IN (15,30,60,90))')
                db.execute('UPDATE jobs SET first_part_snapshot=first_part_seconds')
            if 'original_duration' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN original_duration REAL '
                           'CHECK(original_duration > 0 AND original_duration <= 240)')

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

    def create(self, owner, kind, url=None, first_part_seconds=60):
        validate_first_part(first_part_seconds)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM jobs WHERE owner=? AND state IN ('receiving','queued','processing')", (owner,)).fetchone()[0]:
                raise Busy('Você já tem um vídeo na fila. Aguarde ou exclua a tarefa pendente.')
            count = db.execute('SELECT count(*) FROM jobs').fetchone()[0]
            pending = db.execute("SELECT count(*) FROM jobs WHERE state IN ('receiving','queued','processing')").fetchone()[0]
            usage = sum(f.stat().st_size for f in self.files.rglob('*') if f.is_file())
            if (count >= self.settings.max_jobs or
                    usage + (pending + 1) * 450_000_000 > self.settings.disk_budget or
                    shutil.disk_usage(self.root).free < 950_000_000):
                raise Busy('Armazenamento ou fila cheia. Exclua arquivos antigos ou tente mais tarde.')
            job_id = secrets.token_hex(16)
            now = time.time()
            db.execute('INSERT INTO jobs(id,owner,state,created,expires,kind,url,first_part_seconds,first_part_snapshot) VALUES (?,?,?,?,?,?,?,?,?)',
                       (job_id, owner, 'receiving' if kind == 'upload' else 'queued', now,
                        now + self.settings.ttl, kind, url, first_part_seconds if first_part_seconds in (30, 60) else 60,
                        first_part_seconds))
            (self.files / job_id).mkdir(mode=0o700)
        return job_id

    def update(self, job_id, **fields):
        if 'first_part_seconds' in fields:
            value = validate_first_part(fields.pop('first_part_seconds'))
            fields['first_part_snapshot'] = value
            fields['first_part_seconds'] = value if value in (30, 60) else 60
        with self.db() as db:
            db.execute(f'UPDATE jobs SET {",".join(k + "=?" for k in fields)} WHERE id=?',
                       (*fields.values(), job_id))

    def get(self, job_id, owner):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?',
                             (job_id, owner, time.time())).fetchone()
        return self.snapshot(row) if row else None

    def listing(self, owner):
        with self.db() as db:
            return [self.snapshot(r) for r in db.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC',
                                               (owner, time.time()))]

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='queued' AND expires>? ORDER BY created LIMIT 1",
                             (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE jobs SET state='processing', progress=5 WHERE id=?", (row['id'],))
                return self.snapshot(row)

    def remove(self, job_id):
        # Worker deletion is serialized by caller; access disappears before filesystem removal.
        with self.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        shutil.rmtree(self.files / job_id, ignore_errors=True)

    def cleanup(self):
        now = time.time()
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM jobs WHERE expires<=?', (now,))]
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
    def snapshot(row):
        job = dict(row)
        job['first_part_seconds'] = job.pop('first_part_snapshot')
        return job

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k not in ('owner', 'url', 'kind')} | {'parts': json.loads(job['parts'])}
