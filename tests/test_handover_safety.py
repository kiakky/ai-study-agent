"""Publication-only guards use synthetic sessions and temporary databases."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'scripts')]
import database
from services.brightspace import ReadOnlySession,call_brightspace_tool,server_parameters
from init_database import initialize_new


class HandoverSafetyTests(unittest.TestCase):
    def test_write_tools_blocked_before_any_connection(self):
        with patch('services.brightspace.brightspace_connection',side_effect=AssertionError('must not connect')):
            for name in ('submit_assignment','create_announcement','delete_file','acknowledge_announcement','mark_content_read'):
                with self.assertRaises(ValueError):asyncio.run(call_brightspace_tool(name,{}))

    def test_discovery_filters_and_missing_readonly_metadata_fails_closed(self):
        tools=[SimpleNamespace(name='get_my_courses',annotations=SimpleNamespace(readOnlyHint=True)),
               SimpleNamespace(name='download_file',annotations=None),
               SimpleNamespace(name='submit_assignment',annotations=SimpleNamespace(readOnlyHint=True))]
        raw=SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=tools)),call_tool=AsyncMock(return_value='synthetic courses'))
        session=ReadOnlySession(raw)
        self.assertEqual([t.name for t in asyncio.run(session.list_tools()).tools],['get_my_courses'])
        self.assertEqual(asyncio.run(session.call_tool('get_my_courses',{})),'synthetic courses')
        for name in ('download_file','submit_assignment'):
            with self.assertRaises(ValueError):asyncio.run(session.call_tool(name,{}))
        self.assertEqual(raw.call_tool.await_count,1)

    def test_own_origin_and_isolated_cwd_without_other_integration_secrets(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='quiz-test-') as folder:
            with patch.dict('os.environ',{'D2L_BASE_URL':'https://school.example.edu','D2L_SESSION_DIR':folder,'WXO_API_KEY':'synthetic-secret','WXO_AGENT_ID':'synthetic-id','IBM_CLOUD_API_KEY':'synthetic-secret','TELEGRAM_BOT_TOKEN':'synthetic-secret'},clear=True):
                params=server_parameters()
                self.assertEqual(params.cwd,str(Path(folder).resolve()))
                self.assertNotIn('WXO_API_KEY',params.env)
                self.assertNotIn('WXO_AGENT_ID',params.env)
                self.assertNotIn('IBM_CLOUD_API_KEY',params.env)
                self.assertNotIn('TELEGRAM_BOT_TOKEN',params.env)
                self.assertIn('brightspace-mcp-server@3.13.18',params.args)
                self.assertEqual(params.env['D2L_NO_UPDATE_CHECK'],'1')
        with patch.dict('os.environ',{},clear=True):
            self.assertRaises(ValueError,server_parameters)

    def test_new_database_only_and_repeated_initialization_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='quiz-test-') as folder:
            path=Path(folder)/'new.db'
            with patch.object(database,'DB_PATH',path):
                initialize_new()
                before=path.read_bytes()
                self.assertRaises(RuntimeError,initialize_new)
                self.assertEqual(path.read_bytes(),before)
