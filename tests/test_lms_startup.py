"""No app config, real sessions, keyring, MCP runtime or service access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import tempfile
import hashlib
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.lms_startup import controlled_parameters, ControlledStartupRequired
from services import lms_startup


class StartupTests(unittest.TestCase):
    def config(self):
        return dict(LMS_MCP_PACKAGE_ROOT=str(Path('synthetic-package').resolve()),
            LMS_SESSION_DIR=str(Path('synthetic-session').resolve()),
            LMS_TENANT_ORIGIN='https://school.example',LMS_EXPECTED_ACCOUNT='synthetic-owner',
            LMS_NODE_EXECUTABLE=str(Path('node.exe').resolve()))

    def test_no_implicit_runtime_or_auth_discovery(self):
        with self.assertRaises(ControlledStartupRequired):controlled_parameters({})
        for key in self.config():
            config=self.config();del config[key]
            with self.assertRaises(ControlledStartupRequired):controlled_parameters(config)

    def test_upstream_account_directory_resolved_by_metadata_only(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-startup-') as temp:
            root=Path(temp).resolve();config=self.config();config['LMS_SESSION_DIR']=str(root)
            digest=hashlib.sha256(json.dumps([config['LMS_TENANT_ORIGIN'],config['LMS_EXPECTED_ACCOUNT']],separators=(',',':')).encode()).hexdigest()
            child=root/'accounts'/digest;child.mkdir(parents=True)
            (child/'session.json').write_text('synthetic; must not be opened',encoding='utf-8')
            with patch.object(Path,'read_text',side_effect=AssertionError('No session contents may be read')):
                params=controlled_parameters(config)
                self.assertEqual(params.env['LMS_SESSION_DIR'],str(child))
                config['LMS_SESSION_DIR']=str(child)
                self.assertEqual(controlled_parameters(config).env['LMS_SESSION_DIR'],str(child))

    def test_private_nonsecret_settings_loaded_without_dotenv_and_env_overrides(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-startup-') as temp:
            root=Path(temp).resolve();(root/'config').mkdir()
            local=self.config();(root/'config/brightspace_runtime.json').write_text(json.dumps(local),encoding='utf-8')
            with patch.object(lms_startup,'__file__',str(root/'backend/services/lms_startup.py')),patch.dict(os.environ,{},clear=True):
                params=controlled_parameters()
                self.assertEqual(params.env['LMS_EXPECTED_ACCOUNT'],'synthetic-owner')
                os.environ['LMS_EXPECTED_ACCOUNT']='synthetic-override'
                self.assertEqual(controlled_parameters().env['LMS_EXPECTED_ACCOUNT'],'synthetic-override')
                (root/'config/brightspace_runtime.json').write_text(json.dumps({'password':'forbidden'}),encoding='utf-8')
                with self.assertRaises(ControlledStartupRequired):controlled_parameters()

    def test_shell_npm_dotenv_and_inherited_secrets_excluded(self):
        from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS
        config=self.config();config.update(NODE_OPTIONS='--require synthetic',PATH='private',
            HOME='private',USERPROFILE='private',TELEGRAM_BOT_TOKEN='synthetic',D2L_ACCESS_TOKEN='synthetic')
        params=controlled_parameters(config)
        self.assertEqual(params.command,config['LMS_NODE_EXECUTABLE'])
        self.assertEqual(len(params.args),1);self.assertTrue(params.args[0].endswith('bootstrap.mjs'))
        self.assertTrue(params.cwd.endswith('lms_runtime'))
        for key in ['NODE_OPTIONS','TELEGRAM_BOT_TOKEN','D2L_ACCESS_TOKEN','HOME']:
            self.assertNotIn(key,params.env)
        for key in DEFAULT_INHERITED_ENV_VARS:self.assertEqual(params.env.get(key),'')
        self.assertEqual(params.env['D2L_NO_UPDATE_CHECK'],'1')

    def test_invalid_origins_and_paths_fail_before_launch(self):
        for origin in ['http://school.example','https://school.example/',
            'https://user:pass@school.example','https://school.example/?token=x']:
            config=self.config();config['LMS_TENANT_ORIGIN']=origin
            with self.assertRaises(ControlledStartupRequired):controlled_parameters(config)
        for key in ['LMS_MCP_PACKAGE_ROOT','LMS_SESSION_DIR','LMS_NODE_EXECUTABLE']:
            config=self.config();config[key]='relative'
            with self.assertRaises(ControlledStartupRequired):controlled_parameters(config)

    def test_node_synthetic_startup_security_contracts(self):
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node is required for controlled startup tests.')
        root=Path(__file__).resolve().parents[1]
        env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP'}}
        result=subprocess.run([node,'--test','--test-reporter=tap',str(root/'tests/lms_runtime.test.mjs')],
            cwd=root,env=env,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('# fail 0',result.stdout)

    def test_real_node_can_start_with_isolated_sdk_environment(self):
        config=self.config();config['LMS_NODE_EXECUTABLE']=shutil.which('node')
        config['SYSTEMROOT']=os.environ.get('SYSTEMROOT','C:\\Windows')
        params=controlled_parameters(config)
        result=subprocess.run([params.command,'--version'],env=params.env,cwd=params.cwd,
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0)
        self.assertTrue(result.stdout.startswith('v'))

    def test_operator_checker_default_is_configuration_only(self):
        root=Path(__file__).resolve().parents[1]
        env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR'}}
        result=subprocess.run([sys.executable,str(root/'scripts/check_brightspace_startup.py')],
            cwd=root,env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2)
        self.assertIn('No session, keyring, subprocess or external service accessed.',result.stdout)
        self.assertNotIn('Traceback',result.stderr)
