"""Essential upstream contracts only. Synthetic metadata/content, no real auth."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
import test_lms_retrieval as fixtures
from services.lms_policy import ReadOnlySession,ReadOnlyViolation,validate_arguments
from services.lms_payloads import response_payload,document_delivery,MCPUnavailable,MAX_RESPONSE_BYTES
from services.lms_mcp import MCPBrightspaceBackend

def blocks(data,*extra):
    return {'content':[{'type':'text','text':json.dumps(data)}]+[
        {'type':'text','text':json.dumps(v) if not isinstance(v,str) else v} for v in extra]}


def tool(name,hint=True,schema=None):
    return SimpleNamespace(name=name,annotations={'readOnlyHint':hint},inputSchema=schema or {'type':'object'})


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    def session(self,tools,version=None):
        fake=SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=tools)),
            call_tool=AsyncMock(return_value=blocks({'synthetic':True})))
        return ReadOnlySession(fake,version),fake

    async def test_permanent_allowlist_and_metadata_denial(self):
        for name in ['sign_out','submit_assignment','mark_read','delete_file','get_roster']:
            session,fake=self.session([tool(name)])
            with self.assertRaises(ReadOnlyViolation):await session.call_tool(name,{})
            fake.list_tools.assert_not_awaited();fake.call_tool.assert_not_awaited()
        for hint in [False,None,'true',1]:
            session,fake=self.session([tool('get_my_courses',hint)])
            with self.assertRaises(ReadOnlyViolation):await session.call_tool('get_my_courses',{})
            fake.call_tool.assert_not_awaited()

    async def test_inline_exceptions_require_exact_reviewed_version_and_arguments(self):
        for version in [None,'3.13.19','3.13.18-unreviewed']:
            session,fake=self.session([tool('download_file',None)],version)
            with self.assertRaises(ReadOnlyViolation):await session.call_tool('download_file',{'courseId':1,'topicId':2})
            fake.call_tool.assert_not_awaited()
        session,fake=self.session([tool('download_file',None),tool('get_syllabus',None)],'3.13.18')
        await session.call_tool('download_file',{'courseId':1,'topicId':2})
        await session.call_tool('get_syllabus',{'courseId':1})
        self.assertEqual(fake.call_tool.await_count,2)
        for key in ['downloadPath','outputPath','download_path','customFilename','url','fileId','folderId','source','userId']:
            with self.subTest(key=key),self.assertRaises(ReadOnlyViolation):
                await session.call_tool('download_file',{'courseId':1,'topicId':2,key:'synthetic'})
        self.assertEqual(fake.call_tool.await_count,2)

    async def test_legacy_helper_denies_before_any_process_connection(self):
        from services.brightspace import call_brightspace_tool
        with patch('services.brightspace.brightspace_connection',side_effect=AssertionError('No subprocess permitted')):
            for name,args in [('sign_out',{}),('get_syllabus',{'courseId':1,'downloadPath':'synthetic'}),('download_file',{'courseId':1,'topicId':2,'userId':202})]:
                with self.assertRaises(ReadOnlyViolation):await call_brightspace_tool(name,args)

    async def test_schema_changes_and_duplicate_definitions_fail_closed(self):
        session,fake=self.session([tool('get_my_courses',schema={'type':'object','required':['unknown']})])
        with self.assertRaises(ReadOnlyViolation):await session.call_tool('get_my_courses',{})
        fake.call_tool.assert_not_awaited()
        session,_=self.session([tool('get_my_courses'),tool('get_my_courses')])
        with self.assertRaises(ReadOnlyViolation):await session.list_tools()

    async def test_discovery_hides_disk_arguments_without_mutating_original_schema(self):
        from mcp.types import Tool,ToolAnnotations
        original=Tool(name='download_file',input_schema={'type':'object','properties':{'courseId':{'type':'integer'},'topicId':{'type':'integer'},'downloadPath':{'type':'string'}}})
        session,_=self.session([original],'3.13.18')
        exposed=(await session.list_tools()).tools[0]
        self.assertNotIn('downloadPath',exposed.input_schema['properties'])
        self.assertIn('downloadPath',original.input_schema['properties'])
        self.assertFalse(exposed.input_schema['additionalProperties'])
        self.assertNotIn('downloadPath',exposed.model_dump(by_alias=True)['inputSchema']['properties'])
        self.assertNotIn('save',exposed.description)
        normal=Tool(name='get_my_courses',input_schema={'type':'object'},annotations=ToolAnnotations(read_only_hint=True))
        guarded,fake=self.session([normal])
        await guarded.call_tool('get_my_courses',{})
        fake.call_tool.assert_awaited_once()

    async def test_connection_yields_guard_after_mocked_initialization_only(self):
        from contextlib import asynccontextmanager
        from services import brightspace
        from mcp.types import InitializeResult,Implementation,ServerCapabilities
        initialization=InitializeResult(protocol_version='2025-03-26',capabilities=ServerCapabilities(),server_info=Implementation(name='brightspace',version='3.13.18'))
        fake=SimpleNamespace(initialize=AsyncMock(return_value=initialization),
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[tool('get_syllabus',None)])),call_tool=AsyncMock(return_value=blocks({'description':'Synthetic overview'})))
        @asynccontextmanager
        async def stdio(*args,**kwargs):yield object(),object()
        class Client:
            def __init__(self,*args):pass
            async def __aenter__(self):return fake
            async def __aexit__(self,*args):pass
        with patch.object(brightspace,'server_parameters',return_value=object()),patch.object(brightspace,'stdio_client',stdio),patch.object(brightspace,'ClientSession',Client):
            async with brightspace.brightspace_connection() as guarded:
                self.assertIsInstance(guarded,ReadOnlySession)
                await guarded.call_tool('get_syllabus',{'courseId':123})
                with self.assertRaises(ReadOnlyViolation):await guarded.call_tool('sign_out',{})
        fake.initialize.assert_awaited_once();fake.call_tool.assert_awaited_once()

    def test_bounds_types_identity_and_dates(self):
        cases=[('get_course_content',{'courseId':True}),('get_course_content',{'courseId':1,'maxDepth':11}),
            ('get_upcoming_due_dates',{'daysAhead':91}),('get_announcements',{'offset':-1}),
            ('search_course',{'courseId':1,'query':'x'}),('get_my_courses',{'activeOnly':'false'}),
            ('get_calendar_events',{'from':'2026-10-18T00:00:00'}),
            ('get_calendar_events',{'from':'2026-10-18T00:00:00Z'}),
            ('get_calendar_events',{'from':'2026-10-18T00:00:00Z','to':'2026-10-17T00:00:00Z'}),
            ('get_course_content',{'courseId':1,'moduleTitle':{'telegram_user_id':202}})]
        for name,args in cases:
            with self.subTest(name=name,args=args),self.assertRaises(ReadOnlyViolation):validate_arguments(name,args)

    async def test_identifier_coercion_never_rounds_or_accepts_booleans(self):
        backend=MCPBrightspaceBackend();backend.session=SimpleNamespace(call_tool=AsyncMock())
        backend.schemas={'get_course_content':{'type':'object','properties':{'courseId':{'type':'integer'}}}}
        for identifier in [True,1.1,'1.1','-1',' 123']:
            with self.subTest(identifier=identifier),self.assertRaises(MCPUnavailable):
                await backend.request('get_course_content',course_id=identifier)
        backend.session.call_tool.assert_not_awaited()

    async def test_caller_cannot_inject_arguments_while_discovery_waits(self):
        args={'courseId':1,'topicId':2}
        fake=SimpleNamespace(call_tool=AsyncMock(return_value=blocks({})))
        async def discover():
            args['downloadPath']='synthetic-only'
            return SimpleNamespace(tools=[tool('download_file',None)])
        fake.list_tools=discover
        session=ReadOnlySession(fake,'3.13.18')
        await session.call_tool('download_file',args)
        self.assertEqual(fake.call_tool.call_args.kwargs['arguments'],{'courseId':1,'topicId':2})


class PayloadTests(unittest.TestCase):
    def test_response_limits_and_errors(self):
        for fixture in [{'isError':True}, {'content':[{'type':'text','text':'x'*(MAX_RESPONSE_BYTES+1)}]},
            {'content':[{'type':'text','text':'{}'}]*101}]:
            with self.assertRaises(MCPUnavailable):response_payload(fixture)

    def test_pdf_office_and_plain_text_inline_without_binary_claims(self):
        for mime,representation in [('application/pdf','extracted_text'),
            ('application/vnd.openxmlformats-officedocument.wordprocessingml.document','extracted_text'),
            ('application/vnd.openxmlformats-officedocument.presentationml.presentation','extracted_text'),('text/plain','text')]:
            with self.subTest(mime=mime):
                value,kind,info=document_delivery(blocks({'mode':'inline','mimeType':mime,'representation':representation,'truncated':False},'--- Extracted text ---\nSynthetic teaching plan'))
                self.assertIn(b'Synthetic teaching plan',value);self.assertEqual(kind,'text/plain')
                self.assertTrue(info['upstream_text']);self.assertEqual(info['original_media_type'],mime)

    def test_truncation_scans_images_binary_and_returned_paths(self):
        _,_,info=document_delivery(blocks({'mode':'inline','representation':'extracted_text','truncated':True},'partial synthetic text'))
        self.assertTrue(info['truncated'])
        for representation in ['image','binary_description_only','pdf_text_extraction_failed','office_text_extraction_failed']:
            with self.subTest(representation=representation),self.assertRaises(MCPUnavailable):
                document_delivery(blocks({'mode':'inline','representation':representation},'not proof of extracted content'))
        with self.assertRaises(MCPUnavailable):document_delivery(blocks({'mode':'disk','filePath':'synthetic-only.pdf'}))
        with self.assertRaises(MCPUnavailable):document_delivery({'structuredContent':{'base64':'not base64'}})
