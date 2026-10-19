"""Synthetic package/launcher checks. Never execute upstream auth or read sessions."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.lms_upstream_startup import upstream_parameters
from services.lms_startup import ControlledStartupRequired


@contextmanager
def fake_package():
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-upstream-') as directory:
        root=Path(directory).resolve();(root/'build').mkdir()
        (root/'package.json').write_text(json.dumps({'name':'brightspace-mcp-server','version':'3.13.18'}),encoding='utf-8')
        (root/'build/index.js').write_text('throw Error("Synthetic package must never run");',encoding='utf-8')
        yield root


class UpstreamStartupTests(unittest.TestCase):
    def config(self,root):
        return dict(LMS_MCP_PACKAGE_ROOT=str(root),LMS_SESSION_DIR=str(root/'synthetic-session-root'),
            LMS_TENANT_ORIGIN='https://school.example',LMS_EXPECTED_ACCOUNT='synthetic-owner',
            LMS_NODE_EXECUTABLE=str(root/'node.exe'),USERPROFILE='synthetic-profile',
            TELEGRAM_BOT_TOKEN='synthetic-secret',WXO_API_KEY='synthetic-secret',NODE_OPTIONS='forbidden',
            D2L_ACCESS_TOKEN='forbidden')

    def test_pinned_upstream_owns_account_resolution_and_authentication(self):
        with fake_package() as root:
            config=self.config(root);params=upstream_parameters(config)
            self.assertEqual(params.args[0],'--import')
            self.assertTrue(params.args[1].endswith('/private_preload.mjs'))
            self.assertEqual(params.args[2],str(root/'build/index.js'))
            self.assertEqual(params.env['AI_STUDY_MCP_PACKAGE_ROOT'],str(root))
            self.assertEqual(params.env['D2L_SESSION_DIR'],config['LMS_SESSION_DIR'])
            self.assertEqual(params.env['D2L_BASE_URL'],'https://school.example')
            self.assertEqual(params.env['D2L_USERNAME'],'synthetic-owner')
            self.assertEqual(params.cwd,str(root))
            self.assertEqual(params.env['D2L_HEADLESS'],'false')
            self.assertEqual(params.env['D2L_NO_UPDATE_CHECK'],'1')
            for key in ['TELEGRAM_BOT_TOKEN','WXO_API_KEY','NODE_OPTIONS','D2L_ACCESS_TOKEN']:
                self.assertNotIn(key,params.env)
            self.assertEqual(params.env['USERPROFILE'],'synthetic-profile')

    def test_wrong_package_or_missing_entrypoint_fails_before_launch(self):
        with fake_package() as root:
            config=self.config(root)
            (root/'package.json').write_text(json.dumps({'name':'brightspace-mcp-server','version':'3.13.10'}),encoding='utf-8')
            with self.assertRaises(ControlledStartupRequired):upstream_parameters(config)
            (root/'package.json').write_text(json.dumps({'name':'brightspace-mcp-server','version':'3.13.18'}),encoding='utf-8')
            (root/'build/index.js').unlink()
            with self.assertRaises(ControlledStartupRequired):upstream_parameters(config)
