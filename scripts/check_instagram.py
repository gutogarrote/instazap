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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    args = parser.parse_args()
    try:
        url = original_url(args.url)
    except ValueError:
        print('NOT VERIFIED: INVALID_URL. Use upload.')
        return 1
    with tempfile.TemporaryDirectory(prefix='instazap-public-check-') as temporary:
        target = Path(temporary) / 'source'
        try:
            result = run([sys.executable, '-m', 'instazap.download', url, str(target), '--diagnostic'],
                         timeout=210, file_limit=200_000_000).decode().strip()
        except TimeoutError:
            result = 'TIMEOUT'
        except Exception:
            result = 'PROCESS_FAILED'
        if result != 'OK':
            allowed = {'LOGIN_REQUIRED_OR_ANONYMOUS_RATE_LIMIT', 'LOGIN_REQUIRED', 'RATE_LIMITED',
                       'NETWORK_BLOCKED', 'DOWNLOAD_FAILED', 'TIMEOUT', 'PROCESS_FAILED'}
            category = result if result in allowed else 'PROCESS_FAILED'
            print(f'NOT VERIFIED: {category}. Use upload.')
            return 1
        print(f'DOWNLOAD VERIFIED: {target.stat().st_size} bytes. File deleted after this diagnostic.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
