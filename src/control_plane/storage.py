"""Private bounded SQLite files for the coordinator and helper candidates.

Only administrator-provisioned directories; no permission changes or cleanup.
Path ancestors must be trusted. Same-UID/root tampering is outside this boundary.
FULL commits/fsync are requested; power-loss durability is not independently
proven, and filesystem operations do not provide a hard wall deadline.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock

from .approval import ApprovalError


class PrivateDatabase:
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, directory, name, *, exclusive=False):
        self.directory_fd = None
        self._file = None
        self._lock = RLock()
        if name not in ('ledger.sqlite', 'coordinator.sqlite') or type(exclusive) is not bool:
            raise ApprovalError('INVALID_POLICY')
        self.name = name
        try:
            self.directory_fd = os.open(Path(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._check_directory()
            try:
                self._file = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=self.directory_fd)
            except FileExistsError:
                self._file = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=self.directory_fd)
            self._identity = self._check_file(self._file)
            if exclusive:
                # One coordinator generation owns this file. A second process
                # must fail closed rather than invalidating the first's state.
                fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.close()
            raise ApprovalError('STATE_UNAVAILABLE') from None

    def _check_directory(self):
        if self.directory_fd is None:
            raise ApprovalError('STATE_UNAVAILABLE')
        info = os.fstat(self.directory_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ApprovalError('STATE_UNAVAILABLE')

    def _check_file(self, fd):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > self.MAX_BYTES):
            raise ApprovalError('STATE_UNAVAILABLE')
        return info.st_dev, info.st_ino

    def _check_path(self):
        self._check_directory()
        fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory_fd)
        try:
            if self._check_file(fd) != self._identity:
                raise ApprovalError('STATE_UNAVAILABLE')
        finally:
            os.close(fd)

    @contextmanager
    def connection(self):
        # Serialize local users and close; the owning service must still join
        # request tasks before shutdown. No background thread/task is started.
        with self._lock:
            self._check_path()
            db = sqlite3.connect('/proc/self/fd/' + str(self.directory_fd) + '/' + self.name,
                                 timeout=.25, isolation_level=None)
            try:
                db.execute('PRAGMA trusted_schema=OFF')
                db.execute('PRAGMA foreign_keys=ON')
                if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                    raise ApprovalError('STATE_UNAVAILABLE')
                db.execute('PRAGMA synchronous=FULL')
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                if type(page_size) is not int or not 512 <= page_size <= 65536:
                    raise ApprovalError('STATE_UNAVAILABLE')
                db.execute('PRAGMA max_page_count=' + str(self.MAX_BYTES // page_size))
                yield db
            finally:
                db.close()  # rollback an uncommitted transaction

    def sync(self):
        with self._lock:
            self._check_path()
            os.fsync(self.directory_fd)

    def close(self):
        with self._lock:
            if self._file is not None:
                os.close(self._file)
                self._file = None
            if self.directory_fd is not None:
                os.close(self.directory_fd)
                self.directory_fd = None
