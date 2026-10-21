"""Read only the explicitly local .env configuration; never discover credential stores."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
settings = root / '.env'
allowed = {'INSTAZAP_BIND', 'INSTAZAP_PORT', 'INSTAZAP_DATA', 'INSTAZAP_ACCESS_SECRET',
           'INSTAZAP_SECURE_COOKIE', 'INSTAZAP_ORIGIN'}
if settings.exists():
    for raw in settings.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator and key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
args = [sys.executable, '-m', 'uvicorn', 'instazap.app:app', '--host',
        os.getenv('INSTAZAP_BIND', '127.0.0.1'), '--port', os.getenv('INSTAZAP_PORT', '8765'),
        '--workers', '1', '--limit-concurrency', '16', '--timeout-keep-alive', '5',
        '--no-access-log', '--no-proxy-headers']
os.execv(sys.executable, args)
