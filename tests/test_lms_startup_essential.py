"""Real stdio transport through the working launcher, using a synthetic Node peer."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_lms_upstream as upstream
import test_lms_retrieval as fixtures
from services import brightspace
from services.lms_mcp import MCPBrightspaceBackend,document_delivery,response_payload
from services.lms_policy import ReadOnlyViolation,ReadOnlySession
from services.lms_upstream_startup import upstream_parameters


class StartupCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which('node.exe') or shutil.which('node'),'Node runtime unavailable')
    async def test_pinned_startup_transport_guard_and_inline_adapter_together(self):
        node=shutil.which('node.exe') or shutil.which('node')
        with upstream.fake_package() as root:
            source=Path(__file__).parent/'fixtures/brightspace_stdio.cjs'
            (root/'build/index.js').write_text(source.read_text(encoding='utf-8'),encoding='utf-8')
            config={**os.environ,'LMS_MCP_PACKAGE_ROOT':str(root), 'LMS_SESSION_DIR':str(root/'synthetic-session'),
                'LMS_NODE_EXECUTABLE':node,'LMS_TENANT_ORIGIN':'https://school.example','LMS_EXPECTED_ACCOUNT':'synthetic-owner',
                'TELEGRAM_BOT_TOKEN':'synthetic','WXO_API_KEY':'synthetic','NODE_OPTIONS':'synthetic','D2L_ACCESS_TOKEN':'synthetic'}
            params=upstream_parameters(config)
            self.assertEqual(params.args[-1],str(root/'build/index.js'));self.assertEqual(params.cwd,str(root))
            self.assertEqual(params.args[0],'--import')
            # This deliberately tiny peer has no upstream API implementation.
            # Test its stdio/policy contract without importing nonexistent APIs.
            # The real preload + pinned upstream tool is verified separately by
            # scripts/check_lms_private_transport.py over actual MCP stdio.
            params=params.model_copy(update={'args':[str(root/'build/index.js')]})
            with patch.object(brightspace,'server_parameters',return_value=params):
                async with brightspace.brightspace_connection() as session:
                    self.assertIsInstance(session,ReadOnlySession)
                    names={t.name for t in (await session.list_tools()).tools}
                    self.assertEqual(len(names),9);self.assertNotIn('sign_out',names)
                    with self.assertRaises(ReadOnlyViolation):await session.call_tool('sign_out',{})
                    with self.assertRaises(ReadOnlyViolation):await session.call_tool('download_file',{'courseId':123,'topicId':5,'downloadPath':'synthetic'})
                    self.assertIn('description',response_payload(await session.call_tool('get_syllabus',{'courseId':123})))
                backend=MCPBrightspaceBackend(allow_document_downloads=True)
                async with backend.batch():
                    courses=response_payload(await backend.request('get_my_courses'))
                    self.assertEqual(courses[0]['id'],123)
                    content=response_payload(await backend.request('get_course_content',course_id='123'))
                    self.assertEqual(content['contentTree'][0]['topicId'],5)
                    result=await backend.request('download_file',course_id='123',resource={'raw_metadata':{'topicId':5}})
                    value,mime,info=document_delivery(result)
                    self.assertEqual(mime,'text/plain');self.assertIn(b'exam date TBC',value);self.assertTrue(info['upstream_text'])
            self.assertFalse((root/'synthetic-session').exists())
