"""Explicit, offline-only MCP launch configuration; never discovers credentials."""
import os
import json
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from mcp import StdioServerParameters
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS


class ControlledStartupRequired(RuntimeError):
    pass


SETTINGS=('LMS_MCP_PACKAGE_ROOT','LMS_SESSION_DIR','LMS_TENANT_ORIGIN','LMS_EXPECTED_ACCOUNT','LMS_NODE_EXECUTABLE')


def configured_environment(environ=None):
    source = dict(os.environ if environ is None else environ)
    if environ is None:
        config_path=Path(__file__).resolve().parents[2]/'config'/'brightspace_runtime.json'
        if config_path.is_file():
            if config_path.stat().st_size>16384:raise ControlledStartupRequired('Invalid Brightspace runtime settings.')
            local=json.loads(config_path.read_text(encoding='utf-8'))
            if not isinstance(local,dict) or set(local)-set(SETTINGS) or any(not isinstance(v,str) for v in local.values()):
                raise ControlledStartupRequired('Invalid Brightspace runtime settings.')
            source={**local,**source}
    return source


def controlled_parameters(environ=None):
    source=configured_environment(environ)
    required=SETTINGS
    if any(not source.get(k) for k in required):
        raise ControlledStartupRequired('Brightspace controlled startup needs explicit operator configuration.')
    origin = source['LMS_TENANT_ORIGIN']
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or origin != f'https://{parsed.netloc}'):
        raise ControlledStartupRequired('Brightspace requires an explicit HTTPS tenant origin.')
    for key in ('LMS_MCP_PACKAGE_ROOT', 'LMS_SESSION_DIR', 'LMS_NODE_EXECUTABLE'):
        if not Path(source[key]).is_absolute():
            raise ControlledStartupRequired('Brightspace runtime paths must be absolute.')
    account = source['LMS_EXPECTED_ACCOUNT']
    if not account.strip() or len(account) > 320 or any(ord(c) < 32 for c in account):
        raise ControlledStartupRequired('Brightspace requires an explicit owner account binding.')
    # Mirror upstream accountSessionDirectory exactly. No config loader, secrets,
    # directory enumeration, session contents, migration or fallback account search.
    session=Path(source['LMS_SESSION_DIR'])
    digest=hashlib.sha256(json.dumps([origin,account],separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    child=session/'accounts'/digest
    if session.parent.name=='accounts' and session.name==digest:
        pass
    elif (child/'session.json').is_file() or child.is_dir():
        session=child
    elif not (session/'session.json').is_file():
        session=child
    source['LMS_SESSION_DIR']=str(session)
    bootstrap = Path(__file__).resolve().parent / 'lms_runtime' / 'bootstrap.mjs'
    # Positive environment list, not a growing secret-prefix blacklist. No PATH,
    # HOME, NODE_OPTIONS, dotenv settings, browser profile or npm configuration.
    # stdio_client merges its own defaults even with an explicit env mapping.
    env = {k: '' for k in DEFAULT_INHERITED_ENV_VARS}
    env.update({k: source[k] for k in required})
    for key in ('SYSTEMROOT', 'WINDIR'):
        if source.get(key):
            env[key] = source[key]
    env['D2L_NO_UPDATE_CHECK'] = '1'
    env['D2L_DEV_MODE'] = 'false'
    return StdioServerParameters(command=source['LMS_NODE_EXECUTABLE'],
        args=[str(bootstrap)], cwd=str(bootstrap.parent), env=env)
