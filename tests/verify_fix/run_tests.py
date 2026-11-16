"""Run isolated verification tests, optionally with pinned canonical SDK mock."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

REF = 'd442479493d94c54a2d35698d3f920e7758b7744'
ROOT = Path(__file__).resolve().parents[2]


def run(server=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src'), PYTHONDONTWRITEBYTECODE='1')
    env.pop('SCRUM26_CANONICAL_MOCK', None)
    if server is not None:
        env['SCRUM26_CANONICAL_MOCK'] = str(server)
    return subprocess.call([sys.executable,'-B','-m','unittest','discover','-s',
                            str(ROOT/'tests/verify_fix'),'-v'],cwd=ROOT,env=env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-sdk',action='store_true')
    args = parser.parse_args()
    if not args.with_sdk:
        raise SystemExit(run())
    archive = subprocess.check_output(['git','archive',REF,'mock_mcp_server.py','schemas'],cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='scrum26-canonical-mock-') as directory:
        target = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(target,filter='data')
        for name in ('verify_fix.schema.json','verify_fix.response.schema.json','verify_fix.result.schema.json'):
            assert (target/'schemas/tools'/name).read_bytes() == (ROOT/'tests/verify_fix/contracts'/name).read_bytes()
        raise SystemExit(run(target/'mock_mcp_server.py'))
