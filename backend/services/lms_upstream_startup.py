"""Pinned upstream-managed authentication, retaining the existing owner API gate."""
import json
from pathlib import Path
from mcp import StdioServerParameters
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS
from services.lms_startup import configured_environment,controlled_parameters,ControlledStartupRequired


def upstream_parameters(environ=None):
    source=configured_environment(environ)
    # Reuse configuration validation, but pass the root to upstream: it owns
    # account-directory resolution and the normal session refresh/login lifecycle.
    controlled_parameters(source)
    root=Path(source['LMS_MCP_PACKAGE_ROOT'])
    metadata=root/'package.json'
    if metadata.stat().st_size>65536:raise ControlledStartupRequired('Invalid MCP package.')
    pkg=json.loads(metadata.read_text(encoding='utf-8'))
    if pkg.get('name')!='brightspace-mcp-server' or pkg.get('version')!='3.13.18':
        raise ControlledStartupRequired('Use the installed Brightspace MCP 3.13.18 package.')
    entry=root/'build/index.js'
    if not entry.is_file():raise ControlledStartupRequired('Installed MCP entrypoint missing.')
    # Windows/browser runtime paths only. No bot/Google/IBM secrets, NODE_OPTIONS
    # or arbitrary D2L tokens/passwords inherited from the parent application.
    safe=set(DEFAULT_INHERITED_ENV_VARS)|{'WINDIR','USERPROFILE','COMSPEC','TMP'}
    env={key:source.get(key,'') for key in safe}
    env.update(D2L_BASE_URL=source['LMS_TENANT_ORIGIN'],D2L_USERNAME=source['LMS_EXPECTED_ACCOUNT'],
        D2L_SESSION_DIR=source['LMS_SESSION_DIR'],D2L_HEADLESS='false',
        D2L_NO_UPDATE_CHECK='1',D2L_DEV_MODE='false')
    env['AI_STUDY_MCP_PACKAGE_ROOT']=str(root)
    preload=Path(__file__).resolve().parent/'lms_runtime'/'private_preload.mjs'
    return StdioServerParameters(command=source['LMS_NODE_EXECUTABLE'],args=['--import',preload.as_uri(),str(entry)],
        cwd=str(root),env=env)
