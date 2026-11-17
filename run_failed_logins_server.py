"""P03-03 separate opt-in entrypoint; no key or auth source access by default."""
import argparse
import asyncio

from src.mcp_server.server import ServerScaffold


class ConfigParser(argparse.ArgumentParser):
    def error(self,message):
        super().error('Reader/session/key configuration rejected; no collector enabled')


def build_parser():
    parser=ConfigParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--linux-failed-logins',action='store_true')
    parser.add_argument('--session-id')
    parser.add_argument('--pseudonym-scope')
    parser.add_argument('--auth-year',type=int)
    parser.add_argument('--auth-offset-minutes',type=int)
    return parser


def build_server(args):
    values=(args.session_id,args.pseudonym_scope,args.auth_year,args.auth_offset_minutes)
    if not args.linux_failed_logins:
        if any(v is not None for v in values):
            raise ValueError('Live configuration requires explicit opt-in')
        return ServerScaffold()
    if any(v is None for v in values):
        raise ValueError('Complete trusted live configuration required')
    from src.mcp_server.failed_logins_server import create_live_server
    return create_live_server(*values)


def main():
    parser=build_parser()
    args=parser.parse_args()
    try:
        server=build_server(args)
    except (ValueError,OSError,KeyError):
        parser.error('Reader/session/key configuration rejected; no collector enabled')
    asyncio.run(server.serve())


if __name__=='__main__':
    main()
