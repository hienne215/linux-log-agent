"""Locate bundled canonical resources in a checkout or installed distribution."""
from importlib.util import find_spec
from pathlib import Path


# Application scope, not user-supplied executable paths. A missing nginx binary
# must not block a service-status read that can legitimately return NOT_FOUND.
TOOL_COMMANDS = {
    'service_status': ('/usr/bin/systemctl',),
    'disk_usage': (),
    'list_failed_units': ('/usr/bin/systemctl',),
    'listening_ports': ('/usr/bin/ss',),
    'query_journal': ('/usr/bin/journalctl',),
    'search_log': ('/usr/bin/journalctl',),
    'detect_anomaly': ('/usr/bin/journalctl',),
    'verify_fix': ('/usr/bin/systemctl','/usr/bin/journalctl'),
    'memory_oom_events': ('/usr/bin/journalctl',),
    'failed_logins': ('/usr/bin/journalctl',),
    'check_config': ('/usr/bin/bwrap','/usr/sbin/nginx'),
}


def _executable(path):
    import os
    return Path(path).is_file() and os.access(path, os.X_OK)


def runtime_root():
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / 'schemas/manifest.json').is_file() and (checkout / 'run_mcp_server.py').is_file():
        return checkout
    spec = find_spec('mock_mcp_server')
    if spec is not None and spec.origin is not None:
        installed = Path(spec.origin).resolve().parent
        if (installed / 'schemas/manifest.json').is_file() and (installed / 'run_mcp_server.py').is_file():
            return installed
    raise RuntimeError('INCOMPLETE_INSTALLATION')


def doctor(backend, *, tools=None):
    """Metadata-only readiness check; never starts MCP, a worker or inference."""
    import os
    import sys
    from importlib.metadata import version, PackageNotFoundError
    selected=tuple(TOOL_COMMANDS) if tools is None else tuple(tools)
    if backend not in ('mock','ollama') or not selected or any(t not in TOOL_COMMANDS for t in selected):
        raise ValueError('INVALID_PREFLIGHT_SCOPE')
    selected=tuple(dict.fromkeys(selected))
    rows = []
    blocked = False
    def add(name, ok, detail):
        nonlocal blocked
        rows.append((name, 'OK' if ok else 'BLOCKED', detail))
        blocked |= not ok
    add('Python', sys.version_info[:2] == (3, 12), sys.version.split()[0])
    for package, expected in (('mcp','2.3.0'),('jsonschema','4.26.0'),
                              ('referencing','0.37.0'),('rfc3339-validator','0.1.4')):
        try: installed = version(package)
        except PackageNotFoundError: installed = 'MISSING'
        add(package, installed == expected, installed)
    add('Linux reader', sys.platform == 'linux' and os.geteuid() != 0,
        'user thường; chưa xác nhận least privilege/VM' if sys.platform == 'linux' and os.geteuid() != 0 else 'cần Linux user thường')
    try:
        root=runtime_root()
        resources=all((root/'schemas/tools'/(tool+suffix)).is_file() for tool in selected
            for suffix in ('.schema.json','.response.schema.json','.result.schema.json'))
    except RuntimeError:
        resources=False
    add('Canonical resources', resources, 'schemas/server của bản cài; chưa chạy MCP')
    available={command:_executable(command) for tool in selected for command in TOOL_COMMANDS[tool]}
    for command,found in available.items():
        add(Path(command).name, found,
            'chỉ kiểm tra executable; chưa chạy collector')
    if backend == 'ollama':
        from agent.integration_demo.preflight import check, PreflightError
        try:
            result = check()
            add('Ollama', True, result['model']+'; RAM khả dụng '+format(result['available_ram_gib'],'.2f')+' GiB')
        except PreflightError as error:
            add('Ollama', False, error.code)
    else:
        rows.append(('Model', 'MOCK', 'kịch bản cố định, không inference'))
    text = ['KIỂM TRA MÔI TRƯỜNG — không inference, không khởi động MCP',
            'Phạm vi: '+', '.join(selected)]
    text.extend(name+': '+status+' — '+detail for name,status,detail in rows)
    for tool in selected:
        missing=[Path(p).name for p in TOOL_COMMANDS[tool] if not available[p]]
        text.append(tool+(': BLOCKED — thiếu '+', '.join(missing) if missing else
            ': NOT_VERIFIED — có phần mềm; chưa kiểm tra quyền, nguồn dữ liệu hoặc collector.'))
    if 'check_config' in selected:
        text.append('check_config: quyền/sandbox cần kiểm chứng riêng; executable tồn tại chưa đủ; không chạy ngoài sandbox.')
    text.extend(('restart_service: mặc định không được cấp quyền; propose/status cần reader VM và operator session, execution cần xác nhận riêng.',
                 'Exit 0 chỉ xác nhận điều kiện phần mềm; không có nghĩa Linux smoke hoặc mọi tool đã PASS.',
                 'Preflight không thay thế kiểm thử collector, quyền reader hoặc độ đúng của AI.'))
    return ('\n'.join(text)+'\n', 1 if blocked else 0)
