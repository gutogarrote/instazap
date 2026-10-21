"""Subprocesses have wall-time, address-space, file-size and CPU limits (Linux)."""
import os
import signal
import subprocess
import sys
import time


def run(argv, timeout=600, memory=1024 * 1024 * 1024, file_limit=450_000_000, cancel=None):
    # Limits are installed in a fresh interpreter, avoiding preexec_fn in a threaded server.
    command = [sys.executable, '-m', 'instazap.process', str(memory), str(file_limit), str(os.getpid()), *map(str, argv)]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Tarefa excluída')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Tempo limite excedido')
            try:
                out, err = proc.communicate(timeout=min(1, remaining))
                break
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    if proc.returncode:
        raise RuntimeError('O arquivo não pôde ser processado. Envie um vídeo MP4 válido.')
    return out


if __name__ == '__main__':
    import ctypes
    import resource
    # Linux: a killed/restarted API must not leave an orphan ffmpeg running beside recovery.
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        sys.exit(1)
    if os.getppid() != int(sys.argv[3]):
        sys.exit(1)
    resource.setrlimit(resource.RLIMIT_AS, (int(sys.argv[1]), int(sys.argv[1])))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[2]), int(sys.argv[2])))
    resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    os.execvp(sys.argv[4], sys.argv[4:])
