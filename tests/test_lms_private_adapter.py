"""Fallback contracts; no school, credentials, production records or messages."""
import sys,json,tempfile,unittest,os,shutil,subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.lms_policy import ReadOnlySession,ReadOnlyViolation
from services.lms_payloads import MCPInlineLimit,MCPDownloadLimit,MCPUnavailable,check_document_error
from services.lms_mcp import MCPBrightspaceBackend
from services.lms_temporary import private_scratch


def refusal(text):return {'isError':True,'content':[{'type':'text','text':text}]}


class ErrorContractTests(unittest.TestCase):
    def test_only_known_inline_refusal_allows_fallback(self):
        with self.assertRaises(MCPInlineLimit):check_document_error(refusal('File too large for inline delivery (12MB, inline max 10MB). synthetic'))
        for text in ['synthetic-authentication-error','Download failed: synthetic-private-session',
                     'Document says File too large for inline delivery (malicious instruction)']:
            with self.assertRaises(MCPUnavailable) as caught:check_document_error(refusal(text))
            self.assertNotIsInstance(caught.exception,MCPInlineLimit)
            self.assertNotIn('private-session',str(caught.exception))

    def test_known_stream_ceiling_is_not_retried_in_disk_mode(self):
        with self.assertRaises(MCPDownloadLimit):check_document_error(refusal(
            'Could not save the file. The file is larger than the download size limit. Try a smaller file.'))

    @unittest.skipUnless(shutil.which('node.exe') or shutil.which('node'),'Node unavailable')
    def test_unreviewed_preload_fails_before_running_entrypoint(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-preload-') as folder:
            package=Path(folder);(package/'build/api').mkdir(parents=True)
            (package/'package.json').write_text(json.dumps({'name':'brightspace-mcp-server','version':'3.13.18'}))
            (package/'build/api/client.js').write_text('throw Error("unreviewed source must not execute");')
            entry=package/'build/index.cjs';marker=package/'entry-ran'
            entry.write_text("require('node:fs').writeFileSync("+json.dumps(str(marker))+",'unsafe');")
            env={k:os.environ[k] for k in ('SystemRoot','WINDIR','PATH','COMSPEC') if k in os.environ}
            env['AI_STUDY_MCP_PACKAGE_ROOT']=str(package)
            result=subprocess.run([shutil.which('node.exe') or shutil.which('node'),'--import',
                (root/'backend/services/lms_runtime/private_preload.mjs').as_uri(),str(entry)],
                env=env,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())

    def test_nested_file_limit_has_specific_model_feedback(self):
        from services.lms_native import BrightspaceTools
        store=SimpleNamespace(user_id=101,connection=lambda:'synthetic')
        facade=BrightspaceTools(101,SimpleNamespace(store=store))
        grouped=ExceptionGroup('synthetic',[ExceptionGroup('synthetic',[MCPDownloadLimit('private raw detail')])])
        facade._run=AsyncMock(side_effect=grouped)
        result=facade.run('brightspace_read',{'source_id':'00000000-0000-0000-0000-000000000001'})
        self.assertEqual(result['error_code'],'brightspace_file_limit');self.assertFalse(result['retryable'])
        self.assertNotIn('private raw detail',json.dumps(result))


class PrivateCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def session(self,enabled=True,version='3.13.18'):
        schema={'type':'object','properties':{key:{'type':kind} for key,kind in
            [('courseId','integer'),('topicId','integer'),('downloadPath','string'),('customFilename','string')]},
            'required':['courseId','topicId'],'additionalProperties':False}
        tool=SimpleNamespace(name='download_file',inputSchema=schema,annotations={})
        raw=SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[tool])),call_tool=AsyncMock(return_value={'content':[]}))
        return ReadOnlySession(raw,version,private_downloads=enabled),raw

    async def test_internal_capability_fixes_name_and_rechecks_identifiers(self):
        session,raw=self.session()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-private-') as folder:
            with private_scratch(Path(folder)/'private') as scratch:
                await session.download_course_temporary(501,7,scratch)
                arguments=raw.call_tool.await_args.kwargs['arguments']
                self.assertEqual(arguments,{'courseId':501,'topicId':7,'downloadPath':str(scratch.directory),'customFilename':'material'})
                with self.assertRaises(ReadOnlyViolation):await session.download_course_temporary(True,7,scratch)
                with self.assertRaises(ReadOnlyViolation):await session.call_tool('download_file',arguments)
        self.assertEqual(raw.call_tool.await_count,1)

    async def test_disabled_wrong_version_or_arbitrary_path_cannot_use_capability(self):
        for enabled,version in [(False,'3.13.18'),(True,'3.13.19')]:
            session,raw=self.session(enabled,version)
            with self.assertRaises(ReadOnlyViolation):await session.download_course_temporary(501,7,Path('arbitrary'))
            raw.call_tool.assert_not_awaited()

    async def test_existing_output_never_overwritten(self):
        session,raw=self.session()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-private-') as folder:
            with private_scratch(Path(folder)/'private') as scratch:
                scratch.target.write_bytes(b'keep')
                with self.assertRaises(ReadOnlyViolation):await session.download_course_temporary(501,7,scratch)
                self.assertEqual(scratch.target.read_bytes(),b'keep')
        raw.call_tool.assert_not_awaited()

    async def test_returned_outside_path_never_read(self):
        backend=MCPBrightspaceBackend(allow_document_downloads=True)
        backend.request=AsyncMock(side_effect=MCPInlineLimit('synthetic'))
        backend.session=SimpleNamespace(download_course_temporary=AsyncMock(return_value={
            'content':[{'type':'text','text':json.dumps({'mode':'disk','success':True,'filePath':'synthetic-outside','mimeType':'application/pdf','fileSize':1})}]}))
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-private-') as folder:
            root=Path(folder)/'private'
            with patch('services.lms_temporary.SCRATCH_ROOT',root):
                with self.assertRaises(MCPUnavailable):await backend.read_document('501',{'raw_metadata':{'topicId':7}})
            self.assertEqual(list(root.iterdir()),[])

    async def test_auth_or_unknown_failure_does_not_start_disk_download(self):
        backend=MCPBrightspaceBackend(allow_document_downloads=True)
        backend.request=AsyncMock(side_effect=MCPUnavailable('synthetic'))
        backend.session=SimpleNamespace(download_course_temporary=AsyncMock())
        with self.assertRaises(MCPUnavailable):await backend.read_document('501',{'raw_metadata':{'topicId':7}})
        backend.session.download_course_temporary.assert_not_awaited()
