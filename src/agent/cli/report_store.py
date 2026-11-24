"""Operator-side report writer for Ubuntu: bounded, no-follow, no overwrite."""
import os
from pathlib import Path
import re
import secrets
import stat

_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.md\Z')


class StoreError(Exception):
    pass


def valid_name(name):
    return type(name) is str and _NAME.fullmatch(name) is not None


def save_report(name, content):
    """Write only reports/<basename> under the operator's working directory.

    Stage a private file, then publish with linkat's exclusive destination. Keep
    directory FDs open; never resolve a user-provided path or follow a symlink.
    """
    if not valid_name(name) or type(content) is not str:
        raise StoreError('INVALID_REPORT_NAME')
    payload = content.encode('utf-8')
    if len(payload) > 70000:
        raise StoreError('REPORT_TOO_LARGE')
    parent = directory = None
    temporary = None
    created = False
    try:
        parent = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.mkdir('reports', mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        directory = os.open('reports', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise StoreError('UNSAFE_REPORT_DIRECTORY')
        temporary = '.pending-' + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
        return Path('reports') / name
    except StoreError:
        raise
    except OSError:
        raise StoreError('REPORT_WRITE_FAILED') from None
    finally:
        if created and temporary is not None and directory is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        if directory is not None:
            os.close(directory)
        if parent is not None:
            os.close(parent)


def read_report(name):
    """Read one owned private report; no traversal, symlink, FIFO or side effect."""
    if not valid_name(name):
        raise StoreError('INVALID_REPORT_NAME')
    directory = descriptor = None
    try:
        directory = os.open('reports', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise StoreError('UNSAFE_REPORT_DIRECTORY')
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > 70000):
            raise StoreError('UNSAFE_REPORT_FILE')
        data = bytearray()
        while chunk := os.read(descriptor, 4096):
            data.extend(chunk)
            if len(data) > 70000:
                raise StoreError('REPORT_TOO_LARGE')
        text = data.decode('utf-8', errors='strict')
        # Treat saved reports as data, never terminal escape instructions.
        if re.search(r'[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]',text):
            raise StoreError('UNSAFE_REPORT_CONTENT')
        return text
    except StoreError:
        raise
    except (OSError, UnicodeError):
        raise StoreError('REPORT_READ_FAILED') from None
    finally:
        if descriptor is not None:os.close(descriptor)
        if directory is not None:os.close(directory)
