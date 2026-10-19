"""Operator compatibility check. Never loads dotenv, SQLite or the Telegram bot.

Default checks configuration/package presence only. --live starts the pinned
upstream MCP and may perform its normal local authentication maintenance.
--read-courses additionally performs one get_my_courses read. No content is printed.
Run as the Windows user who owns the existing Brightspace native key.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from services.lms_startup import ControlledStartupRequired, configured_environment
from services.lms_upstream_startup import upstream_parameters

SETTINGS={'package_root':'LMS_MCP_PACKAGE_ROOT','session_dir':'LMS_SESSION_DIR',
    'tenant_origin':'LMS_TENANT_ORIGIN','expected_account':'LMS_EXPECTED_ACCOUNT',
    'node':'LMS_NODE_EXECUTABLE'}
TOOLS={'get_my_courses','get_course_content','get_syllabus','get_announcements',
    'get_assignments','get_upcoming_due_dates','get_calendar_events','search_course','download_file'}


async def live_check(params,read_courses):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    with open(os.devnull,'w') as diagnostics:
        async with stdio_client(params,errlog=diagnostics) as (read,write):
            async with ClientSession(read,write) as session:
                initialized=await session.initialize()
                info=getattr(initialized,'server_info',getattr(initialized,'serverInfo',None))
                if getattr(info,'name',None)!='brightspace' or getattr(info,'version',None)!='3.13.18':
                    raise RuntimeError('Unexpected runtime')
                tools=await session.list_tools()
                if not TOOLS.issubset({tool.name for tool in tools.tools}):
                    raise RuntimeError('Unexpected tool registrations')
                print('PASS pinned upstream MCP started; required read tools discovered. Authentication is verified only by an actual read.')
                if read_courses:
                    result=await session.call_tool('get_my_courses',arguments={})
                    if getattr(result,'is_error',getattr(result,'isError',False)):
                        raise RuntimeError('Read failed')
                    blocks=getattr(result,'content',[])
                    if not blocks or getattr(blocks[0],'type',None)!='text':raise RuntimeError('Unsupported response')
                    data=json.loads(blocks[0].text)
                    if not isinstance(data,list):raise RuntimeError('Unsupported response')
                    print('PASS one authenticated course-list read; returned data was not printed or saved.')
                else:
                    print('No Brightspace course request was made. Use --live --read-courses for the separate read check.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for option in SETTINGS:parser.add_argument('--'+option.replace('_','-'))
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--read-courses',action='store_true')
    args=parser.parse_args()
    if args.read_courses and not args.live:parser.error('--read-courses requires --live')
    try:env=configured_environment()
    except (ValueError,OSError,ControlledStartupRequired):
        print('NOT READY: invalid private Brightspace runtime settings. No authentication attempted.')
        return 2
    for option,setting in SETTINGS.items():
        if getattr(args,option) is not None:env[setting]=getattr(args,option)
    missing=[key for key in SETTINGS.values() if not env.get(key)]
    if missing:
        print('NOT READY: explicit operator settings missing: '+', '.join(missing))
        print('No session, keyring, subprocess or external service accessed.')
        return 2
    try:
        params=upstream_parameters(env)
        print('PASS pinned upstream startup configuration/package (values not displayed).')
        if not args.live:
            print('No session or runtime accessed. Use --live only when ready to test the existing owner session.')
            return 0
        asyncio.run(asyncio.wait_for(live_check(params,args.read_courses),timeout=360))
        return 0
    except (Exception,KeyboardInterrupt):
        print('NOT READY: controlled startup/read did not complete. Check the fixed package installation, existing native key, modern session, exact owner/tenant binding and expiry. Private error details suppressed.')
        print('Upstream may have attempted its normal session maintenance or visible login. Do not delete existing authentication files.')
        return 1


if __name__=='__main__':raise SystemExit(main())
