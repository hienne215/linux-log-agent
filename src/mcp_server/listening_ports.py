"""Read-only socket snapshot core; no process/unit ownership inference or MCP."""

import asyncio
import ipaddress
import re


ARGV = ('/usr/bin/ss', '--no-header', '--numeric', '--listening', '--tcp', '--udp', '--oneline')
ENV = {'LC_ALL': 'C', 'LANG': 'C'}
TIMEOUT = 5.0
STDOUT_LIMIT = 65536
STDERR_LIMIT = 4096
RECORD_LIMIT = 200
_SERVICE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*\.service\Z')
_SCOPE = re.compile(r'[A-Za-z0-9_.-]{1,64}\Z')
_NUMBER = re.compile(r'[0-9]{1,20}\Z')


class ListeningPortsError(Exception):
    """Fixed internal code; no raw OS/output diagnostics in business errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _filters(protocol, port, unit):
    if protocol is not None and (type(protocol) is not str or protocol not in ('tcp', 'udp')):
        raise ListeningPortsError('INVALID_INPUT')
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ListeningPortsError('INVALID_INPUT')
    if unit is not None:
        if type(unit) is not str or len(unit) > 128 or not _SERVICE.fullmatch(unit):
            raise ListeningPortsError('INVALID_INPUT')
        # No verified service attribution source exists in this isolated core.
        raise ListeningPortsError('UNSUPPORTED')


def _endpoint(value, peer=False):
    host, sep, number = value.rpartition(':')
    if not sep or not host or not number:
        raise ValueError
    if peer and number == '*':
        port = 0
    elif not _NUMBER.fullmatch(number):
        raise ValueError
    else:
        port = int(number)
    if not (0 <= port <= 65535 if peer else 1 <= port <= 65535):
        raise ValueError
    if host.startswith('['):
        closing = host.find(']')
        if closing < 0:
            raise ValueError
        enclosed, suffix = host[1:closing], host[closing + 1:]
        if (':' not in enclosed or (suffix and not suffix.startswith('%'))
                or ('%' in enclosed and suffix)):
            raise ValueError
        # iproute2 prints the interface suffix after the IPv6 closing bracket.
        host = enclosed + suffix
    elif '[' in host or ']' in host:
        raise ValueError
    zone = ''
    if '%' in host:
        host, zone = host.split('%', 1)
        if not _SCOPE.fullmatch(zone):
            raise ValueError
    if host == '*':
        address, unspecified = '*' + ('%' + zone if zone else ''), True
    else:
        parsed = ipaddress.ip_address(host)
        address, unspecified = str(parsed), parsed.is_unspecified
        if zone:
            address += '%' + zone
    if peer and (port != 0 or not unspecified):
        raise ValueError
    return address, port


def parse_listening_ports(payload: bytes, protocol=None, port=None, unit=None) -> dict:
    """Validate the full fixed ss profile before applying any filters.

    TCP LISTEN or UDP UNCONN with wildcard peer only. UDP here means a bound
    unconnected socket, not verified service ownership or remote reachability.
    process='' records absence of attribution, unit=None; neither is guessed.
    """
    _filters(protocol, port, unit)
    if type(payload) is not bytes:
        raise ListeningPortsError('EXECUTION_UNKNOWN')
    if len(payload) > STDOUT_LIMIT:
        raise ListeningPortsError('OUTPUT_LIMIT')
    try:
        text = payload.decode('utf-8', errors='strict')
    except UnicodeError:
        raise ListeningPortsError('EXECUTION_UNKNOWN') from None
    if text and not text.endswith('\n'):
        raise ListeningPortsError('EXECUTION_UNKNOWN')
    lines = text[:-1].split('\n') if text else []
    if len(lines) > RECORD_LIMIT:
        raise ListeningPortsError('OUTPUT_LIMIT')
    records = []
    for line in lines:
        if not line.isprintable():
            raise ListeningPortsError('EXECUTION_UNKNOWN')
        fields = line.split()
        if len(fields) != 6:
            raise ListeningPortsError('EXECUTION_UNKNOWN')
        transport, state, recv, send, local, peer = fields
        try:
            if ((transport, state) not in {('tcp', 'LISTEN'), ('udp', 'UNCONN')}
                    or not _NUMBER.fullmatch(recv) or not _NUMBER.fullmatch(send)
                    or int(recv) > (1 << 64) - 1 or int(send) > (1 << 64) - 1):
                raise ValueError
            address, socket_port = _endpoint(local)
            _endpoint(peer, peer=True)
        except ValueError:
            raise ListeningPortsError('EXECUTION_UNKNOWN') from None
        if (protocol is None or protocol == transport) and (port is None or port == socket_port):
            records.append(dict(protocol=transport, port=socket_port, address=address, process='', unit=None))
    # Preserve multiplicity: equal endpoints can be distinct SO_REUSEPORT sockets.
    records.sort(key=lambda row: (row['protocol'], row['address'], row['port']))
    return dict(ports=records, count=len(records))


async def _read(stream, budget):
    kept = bytearray()
    while True:
        chunk = await stream.read(min(4096, budget - len(kept) + 1))
        if not chunk:
            return bytes(kept)
        if len(kept) + len(chunk) > budget:
            raise ListeningPortsError('OUTPUT_LIMIT')
        kept.extend(chunk)


async def _discard(stream):
    while await stream.read(4096):
        pass


async def _run():
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *ARGV, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, shell=False, cwd='/', env=dict(ENV), limit=4096))
    process, readers = None, []
    try:
        async with asyncio.timeout(TIMEOUT):
            process = await asyncio.shield(spawn)
            readers = [asyncio.create_task(_read(process.stdout, STDOUT_LIMIT)),
                       asyncio.create_task(_read(process.stderr, STDERR_LIMIT))]
            stdout, stderr, code = await asyncio.gather(*readers, process.wait())
            return code, stdout, stderr
    finally:
        # Retain an in-flight spawn even if timeout/cancellation occurred first.
        if process is None:
            try:
                process = await asyncio.shield(spawn)
            except Exception:
                pass
        if process is not None:
            try:
                if process.returncode is None:
                    process.kill()
            finally:
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
                await asyncio.gather(_discard(process.stdout), _discard(process.stderr), process.wait())


async def collect_listening_ports(protocol=None, port=None, unit=None) -> dict:
    """Collect one bounded snapshot; only trusted fixed argv reaches ss."""
    _filters(protocol, port, unit)
    try:
        code, stdout, stderr = await _run()
    except TimeoutError:
        raise ListeningPortsError('TIMEOUT') from None
    except PermissionError:
        raise ListeningPortsError('PERMISSION_DENIED') from None
    except (FileNotFoundError, NotImplementedError):
        raise ListeningPortsError('UNSUPPORTED') from None
    except ListeningPortsError:
        raise
    except Exception:
        raise ListeningPortsError('EXECUTION_UNKNOWN') from None
    diagnostic = stderr.lower()
    if any(marker in diagnostic for marker in (b'permission denied', b'operation not permitted', b'access denied')):
        raise ListeningPortsError('PERMISSION_DENIED')
    if any(marker in diagnostic for marker in (b'not supported', b'unrecognized option', b'unknown option')):
        raise ListeningPortsError('UNSUPPORTED')
    if code != 0 or stderr:
        raise ListeningPortsError('EXECUTION_UNKNOWN')
    return parse_listening_ports(stdout, protocol, port, unit)
