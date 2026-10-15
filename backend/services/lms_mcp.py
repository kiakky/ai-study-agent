"""Schema-negotiated, read-only adapter. Unverified formats fail visibly."""
import base64
from contextlib import asynccontextmanager
import json
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from services.brightspace import brightspace_connection, READ_TOOLS
from services.document_extraction import MAX_FILE_BYTES



class MCPUnavailable(ValueError):
    pass


def attribute(value,*names,default=None):
    for name in names:
        if isinstance(value,dict) and name in value:return value[name]
        if hasattr(value,name):return getattr(value,name)
    return default


def response_payload(result):
    if attribute(result,'isError','is_error',default=False):raise MCPUnavailable('LMS retrieval failed; previous evidence retained.')
    structured=attribute(result,'structuredContent','structured_content')
    if structured is not None:return structured
    blocks=attribute(result,'content',default=[])
    text='\n'.join(attribute(block,'text',default='') for block in blocks if attribute(block,'type')=='text')
    if text:
        try:return json.loads(text)
        except ValueError:return {'unparsed_text':text}
    return {'unparsed_content':True}


def document_bytes(result):
    """Accept explicit inline text/base64 only, never arbitrary server local paths."""
    if attribute(result,'isError','is_error',default=False):raise MCPUnavailable('Document retrieval failed.')
    payload=response_payload(result)
    if isinstance(payload,dict):
        mime=payload.get('mimeType',payload.get('mime_type'))
        if isinstance(payload.get('base64'),str):
            encoded=payload['base64']
            if len(encoded)>MAX_FILE_BYTES*4//3+8:raise MCPUnavailable('Document exceeds download limit.')
            try:return base64.b64decode(encoded,validate=True),mime or 'application/octet-stream'
            except ValueError:raise MCPUnavailable('Invalid document encoding.') from None
        if isinstance(payload.get('html'),str):return payload['html'].encode(),'text/html'
        if isinstance(payload.get('text'),str):return payload['text'].encode(),mime or 'text/plain'
    for block in attribute(result,'content',default=[]):
        resource=attribute(block,'resource')
        blob=attribute(resource,'blob') if resource else None
        if isinstance(blob,str):
            if len(blob)>MAX_FILE_BYTES*4//3+8:raise MCPUnavailable('Document exceeds download limit.')
            return base64.b64decode(blob,validate=True),attribute(resource,'mimeType','mime_type',default='application/octet-stream')
    raise MCPUnavailable('MCP download format is unverified. Local paths/remote URLs are not automatically opened; source reference retained.')


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
            raise MCPUnavailable('Live download transport/output location requires separate verification and approval.')
        schema=self.schemas[name];properties=schema.get('properties',{});arguments={}
        def bind(candidates,value):
            for candidate in candidates:
                if candidate in properties:
                    expected=properties[candidate].get('type')
                    if expected=='integer':
                        try:value=int(value)
                        except (ValueError,TypeError):raise MCPUnavailable('MCP identifier type is incompatible.') from None
                    arguments[candidate]=value;return True
            return False
        if course_id is not None and not bind(('courseId','course_id','orgUnitId','org_unit_id'),course_id) and name!='download_file':
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
            raw=resource.get('raw_metadata',{})
            bound=False
            for aliases in [('fileId','file_id','FileId'),('documentId','document_id'),('contentId','content_id'),('topicId','topic_id')]:
                identifier=next((raw[key] for key in aliases if key in raw),None)
                if identifier is not None and bind(aliases,identifier):bound=True;break
            if not bound:
                url=resource.get('source_url')
                if not isinstance(url,str):raise MCPUnavailable('Document retrieval identifier unavailable.')
                parsed=urlsplit(url)
                if parsed.scheme!='https' or parsed.hostname not in self.allowed_document_hosts or parsed.username or parsed.password:
                    raise MCPUnavailable('Document URL host is not explicitly authorized for this connector.')
                if not bind(('url','fileUrl','file_url'),url):raise MCPUnavailable('Download argument schema is unverified.')
        try:Draft202012Validator(schema).validate(arguments)
        except Exception:raise MCPUnavailable('MCP input schema needs a verified adapter; no guessed arguments sent.') from None
        result=await self.session.call_tool(name,arguments=arguments)
        if attribute(result,'isError','is_error',default=False):raise MCPUnavailable('MCP retrieval failed; existing evidence retained.')
        return result
