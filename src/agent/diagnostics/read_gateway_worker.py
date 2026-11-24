"""Trusted root read worker, launched only by the opted-in lab gateway."""
import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from agent.diagnostics import read_gateway


def config_command(unit):
    # Unit is generated here, not supplied through CLI, MCP or model output.
    import re
    if re.fullmatch(r'lla-config-check-[0-9a-f]{32}\.service',unit) is None:raise ValueError()
    properties=('Type=exec','DynamicUser=yes','User=lla-config-check','NoNewPrivileges=yes',
        'PrivateNetwork=yes','ProtectSystem=strict','ProtectHome=yes','PrivateTmp=yes','PrivateDevices=yes',
        'TemporaryFileSystem=/run:rw,mode=1777','TemporaryFileSystem=/var/log/nginx:rw,mode=1777',
        'CapabilityBoundingSet=CAP_NET_BIND_SERVICE','AmbientCapabilities=CAP_NET_BIND_SERVICE',
        'ProtectKernelTunables=yes','ProtectKernelModules=yes','ProtectControlGroups=yes',
        'RestrictSUIDSGID=yes','LockPersonality=yes','RuntimeMaxSec=3','TimeoutStopSec=1','KillMode=control-group',
        'MemoryMax=128M','TasksMax=16','UMask=0077')
    return ('/usr/bin/systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,
        *(item for prop in properties for item in ('--property',prop)),
        '/usr/sbin/nginx','-t','-q','-e','stderr')


async def sandbox_config(arguments):
    from agent.diagnostics.system_diagnostics import config_result,SourceError
    from agent.diagnostics.journal_reader import execute
    if arguments.get('profile')!='nginx' or arguments.get('unit','nginx.service')!='nginx.service':
        raise SourceError('POLICY_DENIED')
    unit='lla-config-check-'+uuid4().hex+'.service'
    cancelled=False
    try:
        code,stdout,stderr=await execute(config_command(unit))
        return config_result(code,stdout,stderr)
    finally:
        # One bounded stop of this invocation's own validator unit. Never nginx,
        # the helper or any user-supplied name. Hold the cleanup owner on cancel.
        cleanup=asyncio.create_task(execute(('/usr/bin/systemctl','--no-pager','--no-ask-password','stop',unit)),
                                    name='config-validator-cleanup')
        while True:
            try:
                await asyncio.shield(cleanup);break
            except asyncio.CancelledError:
                if cleanup.cancelled():raise
                cancelled=True
        if cancelled:raise asyncio.CancelledError()


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in read_gateway.ALLOWED or os.geteuid()!=0:return 2
    try:
        if not read_gateway.configured():return 2
        from agent import diagnostic_worker
        diagnostic_worker.check_config=sandbox_config
        return diagnostic_worker.main()
    except Exception:return 2


if __name__=='__main__':raise SystemExit(main())
