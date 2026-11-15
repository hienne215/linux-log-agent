"""Offline unit tests; optional official-SDK mock from an immutable Git archive."""
import argparse
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

CONTRACT_REF='d442479493d94c54a2d35698d3f920e7758b7744'
ROOT=Path(__file__).resolve().parents[2]


def run(server=None):
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),PYTHONDONTWRITEBYTECODE='1')
    env.pop('SCRUM25_CANONICAL_MOCK',None)
    if server is not None:env['SCRUM25_CANONICAL_MOCK']=str(server)
    return subprocess.call([sys.executable,'-B','-m','unittest','discover',
                            '-s',str(ROOT/'tests/anomaly'),'-v'],cwd=ROOT,env=env)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-sdk',action='store_true')
    args=parser.parse_args()
    if not args.with_sdk:raise SystemExit(run())
    # No network/fetch/install; missing PR6 object is a prerequisite failure.
    archive=subprocess.check_output(['git','archive',CONTRACT_REF,'mock_mcp_server.py','schemas'],cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='scrum25-canonical-mock-') as directory:
        target=Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(target,filter='data')
        for name in ('detect_anomaly.schema.json','detect_anomaly.response.schema.json',
                     'detect_anomaly.result.schema.json'):
            assert (target/'schemas/tools'/name).read_bytes()==(ROOT/'tests/anomaly/contracts'/name).read_bytes()
        raise SystemExit(run(target/'mock_mcp_server.py'))
