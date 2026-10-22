"""TEST-ONLY server for scripts/smoke.py. Never use it to serve real users.

Same app, worker, limits and uvicorn options as scripts/serve.py, except the link
downloader: it copies the SYNTHETIC local fixture named by INSTAZAP_SYNTHETIC_LINK.
It never contacts Instagram/X, so it proves local processing only, not downloads.
Reel codes select test behavior: ...SLOW... delays the copy, ...FAIL... fails it.
"""
import os
import shutil
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from instazap import worker  # noqa: E402


def synthetic_fetch(self, url, source):
    if 'FAIL' in url:
        raise RuntimeError('synthetic downloader failure')
    if 'SLOW' in url:
        for _ in range(30):  # 3 s, still cancellable like the real bounded subprocess.
            if self.cancel.wait(.1):
                raise RuntimeError('cancelled')
    shutil.copyfile(os.environ['INSTAZAP_SYNTHETIC_LINK'], source)


worker.Worker.fetch = synthetic_fetch

if __name__ == '__main__':
    uvicorn.run('instazap.app:app', host='127.0.0.1', port=int(os.environ['INSTAZAP_PORT']), workers=1,
                limit_concurrency=16, timeout_keep_alive=5, access_log=False, proxy_headers=False)
