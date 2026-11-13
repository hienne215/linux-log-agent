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
