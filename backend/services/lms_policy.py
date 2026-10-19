"""Shared MCP operation boundary. Authorization remains in owner-scoped retrieval.

Mixed-mode tools have a deliberately narrow, version-reviewed inline exception.
This module does not launch processes, authenticate or maintain sessions.
"""
from jsonschema import Draft202012Validator
from datetime import datetime
from copy import copy

REVIEWED_VERSION = '3.13.18'
READ_TOOLS = frozenset({'get_my_courses', 'get_course_content', 'get_syllabus',
    'get_announcements', 'get_assignments', 'get_upcoming_due_dates',
    'get_calendar_events', 'search_course', 'download_file'})
FIELDS = {
    'get_my_courses': {'activeOnly'},
    'get_course_content': {'courseId','typeFilter','moduleTitle','maxDepth','modifiedSince'},
    'get_syllabus': {'courseId'},
    'get_announcements': {'courseId','count','offset','modifiedSince'},
    'get_assignments': {'courseId'},
    'get_upcoming_due_dates': {'courseId','daysAhead'},
    'get_calendar_events': {'courseId','from','to','includeGenerated'},
    'search_course': {'courseId','query','limit'},
    # Only course materials: no submissions, arbitrary URLs, filenames or paths.
    'download_file': {'courseId','topicId'},
}


class ReadOnlyViolation(ValueError):
    pass


def validate_arguments(name, arguments):
    if name not in READ_TOOLS:
        raise ReadOnlyViolation('Brightspace operation is not on the read-only allowlist.')
    if not isinstance(arguments, dict) or set(arguments)-FIELDS[name]:
        raise ReadOnlyViolation('Unsupported Brightspace arguments; identity and filesystem controls are not accepted.')
    if any(isinstance(v, (dict,list)) for v in arguments.values()):
        raise ReadOnlyViolation('Nested Brightspace arguments are not accepted.')
    limits={'courseId':(1,2**63-1),'topicId':(1,2**63-1),'maxDepth':(1,10),
            'daysAhead':(1,90),'count':(1,50),'offset':(0,5000),'limit':(1,50)}
    for key,(lower,upper) in limits.items():
        if key in arguments and (type(arguments[key]) is not int or not lower<=arguments[key]<=upper):
            raise ReadOnlyViolation('Brightspace identifier or request bound is invalid.')
    for key in ('activeOnly','includeGenerated'):
        if key in arguments and type(arguments[key]) is not bool:
            raise ReadOnlyViolation('Brightspace flags must be booleans.')
    for key,lower,upper in [('query',2,200),('moduleTitle',1,200)]:
        if key in arguments and (not isinstance(arguments[key],str) or not lower<=len(arguments[key])<=upper):
            raise ReadOnlyViolation('Brightspace text argument limit exceeded.')
    if 'typeFilter' in arguments and arguments['typeFilter'] not in {'file','link','html','video','all'}:
        raise ReadOnlyViolation('Unsupported content filter.')
    dates={}
    for key in ('from','to','modifiedSince'):
        if key in arguments:
            try:
                parsed=datetime.fromisoformat(arguments[key].replace('Z','+00:00'))
                if parsed.utcoffset() is None:raise ValueError()
                dates[key]=parsed
            except (ValueError,TypeError,AttributeError):
                raise ReadOnlyViolation('Use an explicit ISO datetime with timezone.') from None
    if name=='get_calendar_events':
        if ('from' in dates)!=('to' in dates) or ('from' in dates and not 0<(dates['to']-dates['from']).total_seconds()<=366*86400):
            raise ReadOnlyViolation('Use an increasing Calendar window of at most 366 days.')
    if name in {'download_file','get_syllabus'}:
        required = {'courseId','topicId'} if name=='download_file' else {'courseId'}
        if set(arguments)!=required or any(type(arguments[k]) is not int or arguments[k]<=0 for k in required):
            raise ReadOnlyViolation('Select a known course material using positive integer identifiers.')


def value(obj, name, default=None):
    return obj.get(name,default) if isinstance(obj,dict) else getattr(obj,name,default)


class ReadOnlySession:
    def __init__(self, session, reviewed_version=None, *, private_downloads=False):
        self._session=session
        self._version=reviewed_version
        self._tools=None
        self._private_downloads=private_downloads

    async def download_course_temporary(self, course_id, topic_id, scratch):
        """Internal capability; never advertised as model arguments or a tool.

        Only the application-created scratch job can supply its directory/name.
        The upstream process must have the bounded-response preload installed.
        """
        from services.lms_temporary import ScratchRead, plain_directory, FILENAME
        if not self._private_downloads or self._version!=REVIEWED_VERSION or not isinstance(scratch,ScratchRead):
            raise ReadOnlyViolation('Private course reading is unavailable.')
        validate_arguments('download_file',{'courseId':course_id,'topicId':topic_id})
        plain_directory(scratch.directory)
        if not scratch.directory.name.startswith('read-') or scratch.target.exists() or scratch.target.is_symlink():
            raise ReadOnlyViolation('Private course reading requires a new scratch job.')
        if self._tools is None:await self.list_tools()
        if 'download_file' not in self._tools:raise ReadOnlyViolation('Required download tool is unavailable.')
        tool=self._tools['download_file']
        schema=value(tool,'inputSchema',value(tool,'input_schema',{}))
        arguments={'courseId':course_id,'topicId':topic_id,'downloadPath':str(scratch.directory),
                   'customFilename':FILENAME}
        try:Draft202012Validator(schema).validate(arguments)
        except Exception:raise ReadOnlyViolation('Private download schema is unverified.') from None
        return await self._session.call_tool('download_file',arguments=arguments)

    async def list_tools(self):
        from types import SimpleNamespace
        response=await self._session.list_tools()
        allowed={}
        for tool in response.tools:
            name=tool.name
            annotations=value(tool,'annotations')
            hint=value(annotations,'read_only_hint',value(annotations,'readOnlyHint'))
            mixed=name in {'download_file','get_syllabus'}
            if name in READ_TOOLS and ((mixed and self._version==REVIEWED_VERSION) or (not mixed and hint is True)):
                if name in allowed:raise ReadOnlyViolation('Ambiguous duplicate MCP tool definition.')
                allowed[name]=tool
        self._tools=allowed
        exposed=[]
        for name,tool in allowed.items():
            schema=value(tool,'inputSchema',value(tool,'input_schema',{}))
            parameters={**schema,'properties':{k:v for k,v in schema.get('properties',{}).items() if k in FIELDS[name]},'additionalProperties':False}
            description=('Read a known course content topic inline; no disk output or submissions.' if name=='download_file' else
                         'Read course overview and syllabus text inline; no disk output.' if name=='get_syllabus' else value(tool,'description',''))
            if hasattr(tool,'model_copy'):
                field='input_schema' if 'input_schema' in type(tool).model_fields else 'inputSchema'
                item=tool.model_copy(update={field:parameters,'description':description})
            else:
                item=copy(tool);item.inputSchema=parameters;item.description=description
            exposed.append(item)
        return SimpleNamespace(tools=exposed)

    async def call_tool(self, name, arguments=None):
        arguments={} if arguments is None else arguments
        validate_arguments(name,arguments)  # Before discovery, including legacy helpers.
        arguments=dict(arguments)  # Caller mutation during discovery cannot add a path/identity.
        if self._tools is None:
            await self.list_tools()
        if name not in self._tools:
            raise ReadOnlyViolation('Tool lacks reviewed read-only metadata or an approved inline contract.')
        tool=self._tools[name]
        schema=value(tool,'inputSchema',value(tool,'input_schema',{}))
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(arguments)
        except Exception:
            raise ReadOnlyViolation('Brightspace arguments do not match the advertised tool schema.') from None
        return await self._session.call_tool(name,arguments=arguments)
