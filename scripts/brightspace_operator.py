"""Explicit owner maintenance. Never imported by Telegram or the API.

status reads the existing owner session/key, without HTTP or state changes.
refresh performs one token exchange and conditional encrypted save; no browser.
login opens a visible school browser for manual login/MFA and saves owner state.
No passwords/tokens are accepted as command arguments or printed in diagnostics.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from services.lms_startup import controlled_parameters


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','refresh','login'])
    args=parser.parse_args()
    try:
        params=controlled_parameters()
        if args.action=='login':
            # Browser binaries use the ordinary Windows cache. These are runtime
            # paths, not credentials or dotenv; normal MCP does not inherit them.
            import os
            for key in ['USERPROFILE','LOCALAPPDATA','TEMP','TMP']:
                if os.environ.get(key):params.env[key]=os.environ[key]
        runtime=ROOT/'backend/services/lms_runtime/operator.mjs'
        result=subprocess.run([params.command,str(runtime),args.action],cwd=runtime.parent,
            env=params.env,stderr=subprocess.DEVNULL,timeout=420 if args.action=='login' else 45)
        return result.returncode
    except (Exception,KeyboardInterrupt):
        print('NOT READY: operator action did not complete; private details suppressed. No automatic fallback was attempted.')
        return 1


if __name__=='__main__':raise SystemExit(main())
