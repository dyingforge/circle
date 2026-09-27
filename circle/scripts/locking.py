"""Stable, process-safe locks for one user's local checkout (not distributed locks)."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
from pathlib import Path
import tempfile
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextlib.contextmanager
def project_lock(root: Path):
    # Never lock a fact file: atomic replacement would change its inode, and
    # Windows cannot replace a document while another process keeps it open.
    key = os.path.normcase(str(root.resolve()))
    directory = Path(tempfile.gettempdir()) / "circle-project-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock")
    # Keep the lock file: unlinking it could give waiters different lock objects.
    with path.open("a+b") as handle:
        if os.name == "nt":
            while True:
                handle.seek(0)
                try:
                    # Windows permits locking a byte beyond the end of a file.
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
