import sys
import threading

import pytest

from instazap.process import run


def test_wall_timeout_kills_child():
    with pytest.raises(TimeoutError):
        run([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.15)


def test_cancel_kills_child():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match='excluída'):
        run([sys.executable, '-c', 'import time; time.sleep(10)'], cancel=cancel)


def test_parent_death_does_not_leave_orphan_worker(tmp_path):
    import subprocess
    import time
    from pathlib import Path
    pidfile = tmp_path / 'test-child.pid'
    child = 'import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)'
    code = f'from instazap.process import run; import sys; run([sys.executable, "-c", {child!r}, {str(pidfile)!r}])'
    parent = subprocess.Popen([sys.executable, '-c', code])
    try:
        for _ in range(200):
            if pidfile.exists():
                break
            time.sleep(.01)
        assert pidfile.exists()
        child_pid = int(pidfile.read_text())
        parent.kill()
        parent.wait(timeout=5)
        for _ in range(200):
            status = Path(f'/proc/{child_pid}/stat')
            try:
                state = status.read_text().split()[2]
            except FileNotFoundError:
                break  # The child exited before or during the read.
            if state == 'Z':
                break
            time.sleep(.01)
        else:
            pytest.fail('Child survived the server process')
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
