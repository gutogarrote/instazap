"""Optional live diagnostic, no login/cookies. Not part of offline tests.
Only pass a public publication you own or have permission to use.
"""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from instazap.network import original_url
from instazap.process import run

parser = argparse.ArgumentParser()
parser.add_argument('url')
args = parser.parse_args()
original_url(args.url)
with tempfile.TemporaryDirectory(prefix='instazap-public-check-') as temporary:
    target = Path(temporary) / 'source'
    try:
        run([sys.executable, '-m', 'instazap.download', args.url, str(target)], timeout=210,
            file_limit=200_000_000)
    except Exception:
        print('NOT VERIFIED: link unavailable, network restricted, login required, or extractor blocked. Use upload.')
        sys.exit(1)
    print(f'DOWNLOAD VERIFIED: {target.stat().st_size} bytes. File deleted after this diagnostic.')
