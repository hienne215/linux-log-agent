"""Import-only diagnostic for the #24 VM timeout; never calls statvfs/systemctl.

Run with the exact checkout's Python 3.12 runtime. No privilege or package changes.
The parent owns/reaps one bounded child; only timings and safe codes are printed.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
t=time.monotonic()
print(json.dumps({'stage':'interpreter_ready'}),flush=True)
import mock_mcp_server
print(json.dumps({'stage':'canonical_import_complete','elapsed_ms':round((time.monotonic()-t)*1000,3)}),flush=True)
import src.mcp_server.disk_usage_worker
print(json.dumps({'stage':'worker_import_complete','elapsed_ms':round((time.monotonic()-t)*1000,3)}),flush=True)
'''


def main():
    tick=time.monotonic()
    child=subprocess.Popen([sys.executable,'-I','-B','-c',CHILD,str(ROOT)],
        cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        env={'PATH':'/usr/bin:/bin','PYTHONUNBUFFERED':'1'})
    timed_out=False
    try:
        output,_=child.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        timed_out=True;child.kill();output,_=child.communicate()
    result={'scope':'IMPORT_ONLY_NO_COLLECTOR','python':sys.version.split()[0],
            'wall_ms':round((time.monotonic()-tick)*1000,3),'child_returncode':child.returncode,
            'reaped':child.returncode is not None,'timeout':timed_out,'stages':[]}
    if len(output)>4096:
        result['code']='OUTPUT_LIMIT'
    else:
        try:result['stages']=[json.loads(line) for line in output.splitlines()]
        except Exception:result['code']='MALFORMED_PROBE_OUTPUT'
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if child.returncode==0 and not timed_out and len(result['stages'])==3 else 1


if __name__=='__main__':raise SystemExit(main())
