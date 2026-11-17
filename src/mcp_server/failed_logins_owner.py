"""P03-03 owner/key-loading candidate; deliberately not wired into scaffold #14.

The future binder supplies trusted principal and session identity, owns lifetime,
and invokes close on disconnect. No principal/key/path is obtained from MCP data.
"""
import os
import stat
import sys
import threading

from .failed_login_events import Privacy
from .failed_logins_adapter import FailedLoginsAdapter

KEY_COMPONENTS = ('etc', 'linux-log-agent', 'failed-logins')
KEY_NAME = 'pseudonym.key'


def privacy_from_fd(fd, reader_gid, scope):
    """Read exactly one root-owned, reader-group-readable 32-byte key."""
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != reader_gid
            or stat.S_IMODE(info.st_mode) != 0o640 or info.st_size != 32):
        raise ValueError('Invalid pseudonym key policy')
    value = os.read(fd, 33)
    after = os.fstat(fd)
    if (len(value) != 32 or (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size,
                            info.st_uid, info.st_gid, info.st_mode) !=
            (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size,
             after.st_uid, after.st_gid, after.st_mode)):
        raise ValueError('Unstable pseudonym key')
    return Privacy(value, scope)


def load_privacy(reader_gid, scope):
    """Linux startup only: no-follow directory-fd walk of the fixed key path.

    Admin must provision directories root-owned 0750 (or stricter), not writable
    by group/others, key root:<reader group> 0640. No creation, sudo or fallback.
    """
    if not sys.platform.startswith('linux'):
        raise ValueError('Linux deployment required')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in KEY_COMPONENTS:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                info = os.fstat(child)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError('Untrusted pseudonym key directory')
            except Exception:
                os.close(child)
                raise
            os.close(directory)
            directory = child
        key_fd = os.open(KEY_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            return privacy_from_fd(key_fd, reader_gid, scope)
        finally:
            os.close(key_fd)
    finally:
        os.close(directory)


class FailedLoginsOwner:
    """One instance for one trusted dedicated connection, not for one worker call.

    The principal argument MUST be supplied by trusted binder/peer context, not
    copied from JSON. Deployment authentication/dispatch remains unimplemented.
    """
    def __init__(self, principal, session_id, privacy, provider_factory, clock=None,
                 access_check=None):
        if principal is None or not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            raise ValueError('Trusted owner context required')
        if not isinstance(privacy, Privacy) or access_check is None:
            raise ValueError('Key and trusted access policy required')
        self._principal, self._session = principal, session_id
        self._factory, self._clock, self._access = provider_factory, clock, access_check
        self._lock, self._closed = threading.RLock(), False
        self._adapter = self._new_adapter(privacy)

    def _new_adapter(self, privacy):
        if not isinstance(privacy, Privacy):
            raise ValueError('Valid pseudonym key required')
        options = {} if self._clock is None else dict(clock=self._clock)
        return FailedLoginsAdapter(self._factory(privacy), **options)

    def call(self, request, principal):
        with self._lock:
            if (self._closed or principal != self._principal or not isinstance(request, dict)
                    or request.get('session_id') != self._session):
                return self._adapter._invalid()
            try:
                allowed = self._access() is True
            except Exception:
                allowed = False
            if not allowed:
                # Revoke frozen continuation as well as future source reads.
                self._adapter.close()
                return self._adapter._response({}, 'error', dict(message='Reader policy denied.'), code='POLICY_DENIED')
            return self._adapter.call(request)

    def rotate(self, privacy):
        """Trusted admin lifecycle hook: destroy old cursors before new key epoch.

        A failed replacement closes this owner; old cursors are never revived.
        Python references are released, not advertised as secure memory erasure.
        """
        with self._lock:
            if self._closed:
                raise ValueError('Owner closed')
            self._adapter.close()
            try:
                self._adapter = self._new_adapter(privacy)
            except Exception:
                self._closed = True
                raise ValueError('Key rotation failed') from None

    def close(self):
        with self._lock:
            self._adapter.close()
            self._closed = True
