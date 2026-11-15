"""Run four offline gate probes; emit safe actual observations only after PASS."""
import importlib.metadata
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(Path(__file__).parent))
# This process is a test runner; workers must use only this checkout's sources.
os.environ['PYTHONPATH']=str(ROOT/'src')
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import test_gate

if __name__=='__main__':
    test_gate.RECORDS.clear()
    suite=unittest.defaultTestLoader.loadTestsFromModule(test_gate)
    result=unittest.TextTestRunner(stream=sys.stderr,verbosity=2).run(suite)
    if not result.wasSuccessful():raise SystemExit(1)
    probes_hash=hashlib.sha256(Path(test_gate.__file__).read_bytes()).hexdigest()
    runner_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
    print(json.dumps({'mode':'OFFLINE_GATE_SUPPORT_ONLY','source_revision':revision,
        'probe_sha256':probes_hash,'runner_sha256':runner_hash,
        'recorded_at':datetime.now(timezone.utc).isoformat(),
        'python':platform.python_version(),'mcp':importlib.metadata.version('mcp'),
        'tests_passed':result.testsRun,'live_inference':'NOT_RUN','real_linux':'NOT_RUN',
        'semantic_diagnostic_accuracy':'NOT_EVALUABLE',
        'records':sorted(test_gate.RECORDS,key=lambda r:(r['scenario'],r['tool_cap'] or 8))},
        ensure_ascii=False,sort_keys=True,indent=2))
