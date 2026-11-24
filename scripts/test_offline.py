"""Run current-checkout offline suites separately, with private evidence.

No Agent branch overlays, benchmark, live inference or host collectors.
Separate pytest processes keep legacy flat fixture module names from colliding.
Two historical SDK tests use only their exact, byte-checked R1 mock server.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tarfile
import xml.etree.ElementTree as ET
from importlib.metadata import PackageNotFoundError, version


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = frozenset({'benchmarks', 'smoke', 'mcp_smoke'})
LEGACY_FIXTURE = 'd442479493d94c54a2d35698d3f920e7758b7744'
LEGACY_ARCHIVE_SHA256 = 'b434b918388bded42613f0aa1dd2a0adc23a7b70b803840df6e679a2b8b4b63f'


def source_digest(root=ROOT):
    digest = hashlib.sha256()
    paths = [root / 'pyproject.toml', root / 'mock_mcp_server.py', root / 'run_mcp_server.py']
    for name in ('src', 'schemas', 'scripts', 'tests'):
        paths.extend(path for path in (root / name).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix in {'.py', '.json', '.txt', '.md', '.sh'})
    fixture = root / 'tests/fixtures/canonical-r1-sdk.tar.gz'
    if fixture.is_file():
        paths.append(fixture)
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def legacy_fixture(root, output):
    """Only the two explicitly pinned R1 SDK tests use historical server source.

    Never overlay Agent/components or replace current production contracts.
    An exact checksum-pinned archive supports source ZIPs without Git history.
    Git fallback is read-only; extraction uses a new runner-owned directory.
    """
    packaged = root / 'tests/fixtures/canonical-r1-sdk.tar.gz'
    if packaged.exists():
        archive = packaged.read_bytes()
        if hashlib.sha256(archive).hexdigest() != LEGACY_ARCHIVE_SHA256:
            raise ValueError('PINNED_FIXTURE_ARCHIVE_MISMATCH')
    else:
        archive = subprocess.check_output(['git', '-C', str(root), 'archive', LEGACY_FIXTURE,
                                           'mock_mcp_server.py', 'schemas'], stderr=subprocess.DEVNULL)
    target = output / 'r1-sdk-fixture'
    target.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(target, filter='data')
    for suite, tool in (('anomaly', 'detect_anomaly'), ('verify_fix', 'verify_fix')):
        for suffix in ('schema.json', 'response.schema.json', 'result.schema.json'):
            name = tool + '.' + suffix
            if hashlib.sha256((target / 'schemas/tools' / name).read_bytes()).digest() != hashlib.sha256(
                    (root / 'tests' / suite / 'contracts' / name).read_bytes()).digest():
                raise ValueError('PINNED_FIXTURE_SCHEMA_MISMATCH')
    return target / 'mock_mcp_server.py'


def suites(root=ROOT):
    tests = root / 'tests'
    result = [('contracts_collectors', sorted(tests.glob('test_*.py')))]
    result += [(folder.name, [folder]) for folder in sorted(tests.iterdir())
               if folder.is_dir() and folder.name not in EXCLUDED
               and any(folder.glob('test_*.py'))]
    return [(name, paths) for name, paths in result if paths]


def read_result(xml, log, exit_code):
    """Count actual checks; a skip/collection error can never become PASS."""
    counts = dict(checks=0, failures=0, errors=0, skipped=0)
    safety_skips = []
    try:
        tree = ET.parse(xml).getroot()
        nodes = [tree] if tree.tag == 'testsuite' else tree.findall('testsuite')
        for node in nodes:
            for key, attribute in (('checks', 'tests'), ('failures', 'failures'), ('errors', 'errors'), ('skipped', 'skipped')):
                counts[key] += int(node.get(attribute, '0'))
            for case in node.findall('testcase'):
                skipped = case.find('skipped')
                if (sys.platform == 'linux' and skipped is not None
                        and case.get('classname', '').endswith('test_disk_usage_profiles')
                        and case.get('name', '') in {
                            'test_sdk_selected_profile_keeps_other_real_backend_disabled[flags0]',
                            'test_sdk_selected_profile_keeps_other_real_backend_disabled[flags1]',
                            'test_sdk_selected_profile_keeps_other_real_backend_disabled[flags2]',
                            'test_sdk_selected_profile_keeps_other_real_backend_disabled[flags3]'}
                        and 'Real opt-in profiles must not read Linux in offline tests' in skipped.get('message', '')):
                    safety_skips.append(case.get('name'))
    except (OSError, ValueError, ET.ParseError):
        counts['errors'] += 1
    passed = re.findall(r'\b(\d+) passed\b', log)
    subtests = re.findall(r'\b(\d+) subtests passed\b', log)
    counts.update(passed_tests=int(passed[-1]) if passed else 0,
                  passed_subtests=int(subtests[-1]) if subtests else 0, exit_code=exit_code)
    counts['status'] = 'PASS' if (exit_code == 0 and counts['checks'] > 0
        and counts['failures'] == counts['errors'] == counts['skipped'] == 0) else 'FAIL'
    # Existing platform guards forbid real collector reads in these four tests.
    # Preserve SKIP, never count them as PASS or grant general skip permission.
    counts['host_safety_skips'] = safety_skips
    if (exit_code == 0 and counts['checks'] > 4 and counts['failures'] == counts['errors'] == 0
            and counts['skipped'] == 4 and len(set(safety_skips)) == 4):
        counts['status'] = 'PASS_WITH_SKIPS'
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--output', type=Path, help='New private directory; must not exist')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        print('BLOCKED: Python 3.12 required', file=sys.stderr)
        return 2
    for name, required in (('mcp', '2.3.0'), ('dbus-next', '0.2.3'), ('pytest', '9.2.0')):
        try: installed = version(name)
        except PackageNotFoundError: installed = None
        if installed != required:
            print('BLOCKED: install the pinned mcp, control and test extras in your venv', file=sys.stderr)
            return 2
    try:
        if args.output is None:
            output = Path(tempfile.mkdtemp(prefix='linux-agent-offline-'))
        else:
            output = args.output.absolute()
            # A new final directory only, no existing output overwritten.
            output.mkdir(mode=0o700)
        os.chmod(output, 0o700)  # Only our newly created directory.
    except OSError:
        print('BLOCKED: output directory must be new and writable', file=sys.stderr)
        return 2
    rows = []
    before = source_digest()
    try:
        fixture = legacy_fixture(ROOT, output)
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError):
        print('BLOCKED: pinned R1 SDK fixture unavailable or snapshot mismatch; no fetch/contract rewrite attempted', file=sys.stderr)
        return 2
    for name, paths in suites():
        extra_paths = [str(ROOT / 'src'), str(ROOT), str(ROOT / 'tests')]
        extra_paths += [str(path) for path in paths if path.is_dir()]
        if os.environ.get('PYTHONPATH'):
            extra_paths.append(os.environ['PYTHONPATH'])
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': os.pathsep.join(extra_paths),
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'RUN_MCP_STDIO_SMOKE': '1',
               'SCRUM25_CANONICAL_MOCK': str(fixture),
               'SCRUM26_CANONICAL_MOCK': str(fixture)}
        xml = output / (name + '.xml')
        command = [sys.executable, '-B', '-m', 'pytest', *map(str, paths), '-q',
                   '-p', 'no:cacheprovider', '--import-mode=importlib', '--junitxml=' + str(xml)]
        log_path = output / (name + '.log')
        with log_path.open('x') as log:
            code = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        row = dict(suite=name, command=command, **read_result(xml, log_path.read_text(), code))
        rows.append(row)
        print(name + ': ' + row['status'] + ' — ' + str(row['passed_tests']) + ' tests, '
              + str(row['passed_subtests']) + ' subtests', flush=True)
    unchanged = before == source_digest()
    complete = unchanged and bool(rows) and all(row['status'] in ('PASS', 'PASS_WITH_SKIPS') for row in rows)
    skipped = sum(row['skipped'] for row in rows)
    summary = dict(status=('PASS_WITH_SKIPS' if skipped else 'PASS') if complete else 'FAIL', python=sys.version.split()[0], mcp=version('mcp'),
        passed_tests=sum(row['passed_tests'] for row in rows),
        passed_subtests=sum(row['passed_subtests'] for row in rows), suites=rows,
        skipped=skipped,
        scope='OFFLINE ONLY: mock HTTP, synthetic SDK stdio, mock D-Bus; no live LLM or host diagnostics',
        formal_acceptance='NOT ESTABLISHED', accuracy='NOT_EVALUABLE', source='current local checkout, including uncommitted changes',
        legacy_sdk_fixture_revision=LEGACY_FIXTURE,
        source_sha256=before, source_unchanged_during_run=unchanged,
        excluded_directories=dict(benchmarks='historical benchmarks not rerun', smoke='manual live inference drivers',
                                  mcp_smoke='manual scripts, not pytest cases; current SDK covered by other suites'),
        legacy_fixture_scope='Only anomaly/verify optional R1 SDK tests; current canonical_agent tests use current source/schemas')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('Evidence: ' + str(output))
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
