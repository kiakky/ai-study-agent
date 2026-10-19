"""Schema-negotiated, read-only adapter. Unverified formats fail visibly."""
from contextlib import asynccontextmanager

from jsonschema import Draft202012Validator
from services.brightspace import brightspace_connection

from services.lms_policy import READ_TOOLS, validate_arguments
from services.lms_payloads import MCPUnavailable, attribute, response_payload, document_bytes, document_delivery
from services.lms_payloads import MCPInlineLimit, check_document_error


class MCPBrightspaceBackend:
    def __init__(self,allowed_document_hosts=(),allow_document_downloads=False):
        self.session=None;self.schemas={};self.allowed_document_hosts=set(allowed_document_hosts)
        self.allow_document_downloads=allow_document_downloads

    @asynccontextmanager
    async def batch(self):
        if self.session is not None:raise MCPUnavailable('Concurrent use of one LMS adapter is not supported.')
        async with brightspace_connection() as session:
            self.session=session
            try:
                tools=await session.list_tools()
                self.schemas={tool.name:attribute(tool,'inputSchema','input_schema',default={}) for tool in tools.tools}
                yield self
            finally:self.session=None

    async def request(self,name,course_id=None,query=None,resource=None,cursor=None):
        if name not in READ_TOOLS or name not in self.schemas:raise MCPUnavailable('Required read-only MCP tool is unavailable.')
        if name=='download_file' and not self.allow_document_downloads:
            raise MCPUnavailable('Inline course-material reading is disabled for this adapter.')
        schema=self.schemas[name];properties=schema.get('properties',{});arguments={}
        def bind(candidates,value):
            for candidate in candidates:
                if candidate in properties:
                    expected=properties[candidate].get('type')
                    if expected=='integer':
                        if type(value) is not int and not (isinstance(value,str) and value.isascii() and value.isdecimal()):
                            raise MCPUnavailable('MCP identifier type is incompatible.')
                        try:value=int(value)
                        except (ValueError,TypeError):raise MCPUnavailable('MCP identifier type is incompatible.') from None
                    arguments[candidate]=value;return True
            return False
        if course_id is not None and not bind(('courseId','course_id','orgUnitId','org_unit_id'),course_id):
            raise MCPUnavailable('Course argument schema is unverified; no cross-course request made.')
        if query is not None and not bind(('query','searchTerm','search_term'),query):raise MCPUnavailable('Search schema is unverified.')
        if cursor is not None and not bind(('bookmark','pageToken','nextPageToken','cursor','page'),cursor):
            raise MCPUnavailable('Pagination schema is unverified; no guessed page request made.')
        if name=='get_my_courses':
            for key,value in [('includeInactive',True),('include_inactive',True),('activeOnly',False),('active_only',False)]:
                if key in properties:arguments[key]=value
        if name=='get_course_content' and 'maxDepth' in properties:
            maximum=properties['maxDepth'].get('maximum')
            if maximum is not None:arguments['maxDepth']=maximum
        if resource is not None:
            # Only the indexed course/topic pair; never submission IDs, URLs or paths.
            raw=resource.get('raw_metadata',{})
            identifier=raw.get('topicId',raw.get('topic_id'))
            if identifier is None or not bind(('topicId',),identifier):
                raise MCPUnavailable('Known course topic identifier is required for inline reading.')
        validate_arguments(name,arguments)
        try:Draft202012Validator(schema).validate(arguments)
        except Exception:raise MCPUnavailable('MCP input schema needs a verified adapter; no guessed arguments sent.') from None
        result=await self.session.call_tool(name,arguments=arguments)
        if name=='download_file':check_document_error(result)
        elif attribute(result,'isError','is_error',default=False):raise MCPUnavailable('MCP retrieval failed; existing evidence retained.')
        return result

    async def read_document(self, course_id, resource):
        try:
            return document_delivery(await self.request('download_file',course_id=course_id,resource=resource))
        except MCPInlineLimit:
            from pathlib import Path
            from services.lms_temporary import private_scratch
            raw=resource.get('raw_metadata',{})
            topic=raw.get('topicId',raw.get('topic_id'))
            # Same strict identifiers as request(); no URLs, identities or user paths.
            if isinstance(course_id,str) and course_id.isascii() and course_id.isdecimal():course_id=int(course_id)
            if isinstance(topic,str) and topic.isascii() and topic.isdecimal():topic=int(topic)
            with private_scratch() as scratch:
                result=await self.session.download_course_temporary(course_id,topic,scratch)
                check_document_error(result)
                metadata=response_payload(result)
                if (metadata.get('mode')!='disk' or metadata.get('success') is not True
                        or not isinstance(metadata.get('filePath'),str)
                        or Path(metadata['filePath'])!=scratch.target):
                    raise MCPUnavailable('Private document response is unverified.')
                mime=metadata.get('mimeType')
                value=scratch.read_bytes(mime)
                if type(metadata.get('fileSize')) is not int or metadata['fileSize']!=len(value):
                    raise MCPUnavailable('Private document size is unverified.')
                return value,mime,{'temporary_download':True,'warnings':[
                    'Original binary was read in a private temporary folder and removed; only extracted evidence is retained.']}
