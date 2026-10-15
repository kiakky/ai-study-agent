"""Hybrid retrieval of all authorized courses; no examination fact interpretation."""
import json
import mimetypes
import re
from urllib.parse import urlsplit,parse_qs

from pydantic import BaseModel,ConfigDict
from models.sources import SourceMetadata,CourseQuery,SourceQuery,SearchQuery,EvidenceQuery
from services.document_extraction import extract_document
from services.lms_mcp import MCPBrightspaceBackend,MCPUnavailable,response_payload,document_bytes
from services.source_store import SourceStore,LMSAccessDenied


def field(node,*names):
    if not isinstance(node,dict):return None
    for name in names:
        if name in node and node[name] is not None:return node[name]
    return None


def safe_url(value):
    if not isinstance(value,str):return None
    parsed=urlsplit(value)
    if parsed.username or parsed.password or any(key.lower() in {'token','access_token','refresh_token','code','signature','authorization','session'} for key in parse_qs(parsed.query)):
        raise MCPUnavailable('Credential-bearing source URLs are not persisted or followed.')
    return value


def course_items(payload):
    if isinstance(payload,list):return payload
    if isinstance(payload,dict):
        for key in ('courses','Courses','items','Items','enrollments'):
            if isinstance(payload.get(key),list):return payload[key]
        if isinstance(payload.get('data'),(dict,list)):return course_items(payload['data'])
    return []


def next_page(payload):
    if not isinstance(payload,dict):return False,None,False
    paging=field(payload,'PagingInfo','paging')
    if isinstance(paging,dict) and isinstance(field(paging,'MoreItems','hasMore'),bool):
        return field(paging,'MoreItems','hasMore'),field(paging,'Bookmark','nextPageToken','cursor'),True
    for key in ('nextPageToken','nextBookmark','next_cursor','nextPage'):
        if key in payload:return payload[key] is not None,payload[key],True
    return False,None,False


def course_record(node):
    identifier=field(node,'courseId','course_id','Id','id','Identifier','OrgUnitId')
    name=field(node,'name','Name','title','Title')
    if identifier is None or not isinstance(name,str):return None
    active=field(node,'isActive','IsActive','active')
    status=field(node,'status','Status')
    activity='active' if active is True or status=='active' else ('inactive' if active is False or status=='inactive' else 'unknown')
    return {'course_id':str(identifier),'name':name,'activity':activity,'metadata':node}


def content_nodes(payload,parent=None):
    """Field aliases only, not semantic topic/exam inference. Unknown trees survive raw."""
    if isinstance(payload,list):
        for node in payload:yield from content_nodes(node,parent)
    elif isinstance(payload,dict):
        identifier=field(payload,'contentId','topicId','moduleId','Id','id','Identifier')
        title=field(payload,'title','Title','name','Name')
        current=parent
        if identifier is not None and isinstance(title,str):
            current=str(identifier)
            children=any(isinstance(payload.get(key),list) for key in ('modules','Modules','topics','Topics','children','resources'))
            yield payload,parent,'module' if children else 'document'
        for key in ('content','Content','modules','Modules','topics','Topics','children','resources','items','data','pages'):
            child=payload.get(key)
            if isinstance(child,(dict,list)):yield from content_nodes(child,current)


def resource_metadata(course_id,node,parent,kind):
    identifier=str(field(node,'contentId','topicId','moduleId','Id','id','Identifier'))
    url=safe_url(field(node,'url','Url','URL','location','Location'))
    mime=field(node,'mimeType','media_type','MimeType') or (mimetypes.guess_type(urlsplit(url).path)[0] if url else None)
    explicit_parent=field(node,'parentModuleId','ParentModuleId','parentId','parent_id')
    child_fields={'modules','Modules','topics','Topics','children','resources','items'}
    raw={key:value for key,value in node.items() if key not in child_fields}
    raw['_child_collections']={key:[field(child,'contentId','topicId','moduleId','Id','id','Identifier') for child in value] for key,value in node.items() if key in child_fields and isinstance(value,list)}
    return SourceMetadata(course_id=course_id,resource_id=identifier,parent_id=str(explicit_parent) if explicit_parent is not None else parent,title=str(field(node,'title','Title','name','Name')),
                          kind=kind,media_type=mime,source_url=url,created_at=field(node,'createdAt','CreatedDate','created_at'),
                          modified_at=field(node,'lastModified','LastModified','modifiedAt','modified_at'),raw_metadata=raw)


class EmptyInput(BaseModel):
    model_config=ConfigDict(extra='forbid')


class BrightspaceRetrieval:
    def __init__(self,authenticated_user_id,backend=None,store=None):
        self.store=store or SourceStore(authenticated_user_id)
        if self.store.user_id!=authenticated_user_id:raise LMSAccessDenied('Source store actor mismatch.')
        self.backend=backend or MCPBrightspaceBackend()

    async def pages(self,tool,**arguments):
        payload=response_payload(await self.backend.request(tool,**arguments))
        pages=[payload];seen=set();warnings=[]
        more,cursor,known=next_page(payload)
        while more:
            if cursor is None or str(cursor) in seen or len(pages)>=100:
                warnings.append('Pagination incomplete; prior evidence retained.');return pages,False,warnings
            seen.add(str(cursor))
            try:payload=response_payload(await self.backend.request(tool,cursor=cursor,**arguments))
            except Exception:warnings.append('Next-page retrieval unavailable; fetched pages retained.');return pages,False,warnings
            pages.append(payload);more,cursor,known=next_page(payload)
        return pages,known,warnings

    async def list_courses(self):
        self.store.connection()
        async with self.backend.batch():
            pages,complete,warnings=await self.pages('get_my_courses')
        records=[record for payload in pages for node in course_items(payload) if (record:=course_record(node)) is not None]
        self.store.index('catalog',SourceMetadata(resource_id='catalog',title='Authorized course catalog',kind='catalog'),{'pages':pages})
        for record in records:self.store.save_course(record)
        return {'courses':self.store.courses(),'warnings':warnings+([] if records else ['Course response shape is unverified or empty; raw catalog retained.']),
                'enumeration_complete':complete,'completeness_note':'Includes every returned course; absent/failed lookups never delete history. Unknown server filtering still needs live verification.'}

    async def refresh_resources(self,course_id):
        self.store.require_course(course_id)
        pages,complete,warnings=await self.pages('get_course_content',course_id=course_id)
        payload={'pages':pages}
        aggregate=self.store.index('tree:'+course_id,SourceMetadata(course_id=course_id,resource_id='tree',title='Course content tree',kind='content_tree'),payload)
        changes=[]
        for node,parent,kind in content_nodes(payload):
            metadata=resource_metadata(course_id,node,parent,kind)
            metadata.raw_metadata['_tree_source_id']=aggregate['source_id']
            changes.append(self.store.index(course_id+':'+kind+':'+metadata.resource_id,metadata,metadata.raw_metadata))
        return {'resources':self.store.sources(course_id),'changes':changes,'tree_source_id':aggregate['source_id'],
                'warnings':warnings+([] if changes else ['Unrecognized/empty content tree; original response retained.']), 'enumeration_complete':complete}

    async def list_course_resources(self,request):
        self.store.connection()
        async with self.backend.batch():return await self.refresh_resources(request.course_id)

    async def assessment_sources(self,course_id):
        self.store.require_course(course_id);sources=[];warnings=[]
        for tool,kind in [('get_syllabus','syllabus'),('get_announcements','announcements'),('get_assignments','assessments'),('get_upcoming_due_dates','due_dates'),('get_calendar_events','course_calendar')]:
            try:
                pages,complete,page_warnings=await self.pages(tool,course_id=course_id)
                warnings.extend(kind+': '+warning for warning in page_warnings)
                if not complete:warnings.append(kind+': pagination/filter completeness is unverified.')
                payload=pages[0] if len(pages)==1 else {'pages':pages}
                reference=self.store.index(course_id+':'+kind,SourceMetadata(course_id=course_id,resource_id=kind,title=kind.replace('_',' ').title(),kind=kind),payload)
                sources.append(reference)
                items=[]
                for page in pages:
                    if isinstance(page,list):items.extend(page)
                    elif isinstance(page,dict):
                        items.extend(next((page[key] for key in ('announcements','assignments','assessments','items') if isinstance(page.get(key),list)),[]))
                for item in items:
                    identifier=field(item,'Id','id','Identifier','announcementId','assignmentId')
                    title=field(item,'Title','title','Name','name')
                    if identifier is not None and isinstance(title,str):
                        node={'id':identifier,'title':title,**item}
                        metadata=resource_metadata(course_id,node,kind,kind+':item')
                        sources.append(self.store.index(course_id+':'+kind+':'+str(identifier),metadata,item))
            except Exception:
                warnings.append(kind+': retrieval unavailable; earlier evidence retained.')
        # No keyword parser turns missing, project or contradictory evidence into facts.
        return {'source_references':sources,'warnings':warnings,'exam_classification':'not_interpreted','absence_is_not_evidence_of_no_exam':True}

    async def discover_exam_sources(self,request):
        self.store.connection()
        async with self.backend.batch():
            sources=await self.assessment_sources(request.course_id)
        return {**sources,'course_resources':self.store.sources(request.course_id)}

    async def initial_discovery(self):
        catalog=await self.list_courses();reports=[]
        async with self.backend.batch():
            for course in catalog['courses']:
                report={'course_id':course['course_id']}
                try:report['content']=await self.refresh_resources(course['course_id'])
                except Exception:report['content']={'warnings':['Content retrieval failed; previous metadata/evidence retained.']}
                report['assessment_evidence']=await self.assessment_sources(course['course_id'])
                reports.append(report)
        return {'catalog':catalog,'courses':reports,'documents_downloaded':0}

    async def retrieve_course_document(self,request):
        row=self.store.source(request.source_id);metadata=json.loads(row['metadata_json'])
        if request.refresh and metadata['course_id']:
            async with self.backend.batch():
                if metadata['kind'] in {'document','module','content_tree'}:
                    await self.refresh_resources(metadata['course_id'])
                else:await self.assessment_sources(metadata['course_id'])
            row=self.store.source(request.source_id);metadata=json.loads(row['metadata_json'])
        cached=self.store.cached_document(request.source_id)
        if cached and not request.refresh:
            result=self.store.evidence(request.source_id,cached['id'])
            result['cache_used']=True
            if not metadata['modified_at']:result['freshness_warning']='No reliable modification timestamp; request refresh for latest content.'
            return result
        latest=self.store.latest_payload(request.source_id)
        payload=json.loads(latest['original_payload_json']) if latest and latest['original_payload_json'] else {}
        node=metadata['raw_metadata'];text=field(node,'html','body','text','Html','Body','Text')
        if not isinstance(text,str):text=field(payload,'html','body','text','Html','Body','Text')
        if isinstance(text,str):
            value=text.encode();mime='text/html' if 'html' in node or 'html' in payload or metadata['media_type']=='text/html' else (metadata['media_type'] or 'text/plain')
        elif metadata['kind']!='document':
            value=json.dumps(payload,ensure_ascii=False,indent=2).encode();mime='application/json'
        else:
            async with self.backend.batch():
                response=await self.backend.request('download_file',course_id=metadata['course_id'],resource=metadata)
                value,mime=document_bytes(response)
        document=extract_document(value,mime)
        if metadata['media_type'] and metadata['media_type']!=mime:
            document.warnings.append('Retrieved MIME type differs from indexed metadata; verify the original source.')
        revision=self.store.extracted(request.source_id,value,document)
        return {**self.store.evidence(request.source_id,revision['id']),'cache_used':False}

    async def search_learning_materials(self,request):
        self.store.require_course(request.course_id)
        sources=self.store.sources(request.course_id)
        terms=request.query.casefold().split()
        local=[]
        for source in sources:
            text=canonical_search(source)
            if any(term in text for term in terms):local.append(source)
        warnings=[];remote=None
        try:
            async with self.backend.batch():remote=response_payload(await self.backend.request('search_course',course_id=request.course_id,query=request.query))
            ref=self.store.index(request.course_id+':search:'+request.query,SourceMetadata(course_id=request.course_id,resource_id='search:'+request.query,title='Course search results',kind='search'),remote)
        except Exception:warnings.append('MCP search unavailable; title/module/metadata token matching only, not semantic search.');ref=None
        return {'local_candidates':local,'remote_results':remote,'source_reference':ref,'warnings':warnings,
                'note':'Search does not limit document retrieval; any authorized resource ID can be fetched for broader context.'}

    async def check_course_updates(self,request):
        self.store.require_course(request.course_id);warnings=[];content=None
        async with self.backend.batch():
            try:content=await self.refresh_resources(request.course_id)
            except Exception:warnings.append('Content check failed; prior resources/revisions retained.')
            assessments=await self.assessment_sources(request.course_id)
        return {'content':content,'assessment_sources':assessments,'warnings':warnings,
                'unknown_freshness_sources':[source['source_id'] for source in self.store.sources(request.course_id) if not source['modified_at']],
                'documents_downloaded':0,'removals_inferred':False}

    @classmethod
    def specifications(cls):
        schemas={'list_courses':EmptyInput,'initial_discovery':EmptyInput,'list_course_resources':CourseQuery,
                 'discover_exam_sources':CourseQuery,'retrieve_course_document':SourceQuery,
                 'search_learning_materials':SearchQuery,'retrieve_source_evidence':EvidenceQuery,'check_course_updates':CourseQuery}
        descriptions={'list_courses':'List every accessible returned course, without exam or activity filtering.',
                      'initial_discovery':'Index courses, module/resource metadata and assessment evidence without bulk document downloads.',
                      'list_course_resources':'Refresh lightweight metadata and preserve parent relationships for any authorized course.',
                      'discover_exam_sources':'Retrieve assessment-related evidence; do not classify courses or interpret exam facts.',
                      'retrieve_course_document':'Retrieve a complete supported document on demand, with original text, structure and extraction warnings.',
                      'search_learning_materials':'Search authorized course references through metadata and available MCP search; no exact topic-name requirement.',
                      'retrieve_source_evidence':'Retrieve a pinned source revision/page/table and original content for provenance.',
                      'check_course_updates':'Compare metadata/assessment snapshots; preserve history and do not download all files or infer removals.'}
        return [{'name':name,'description':descriptions[name],'input_schema':schema.model_json_schema()} for name,schema in schemas.items()]

    async def invoke(self,name,arguments):
        self.store.connection()  # Enforced before cache access OR MCP subprocess creation.
        operations={'list_courses':(EmptyInput,lambda request:self.list_courses()),'initial_discovery':(EmptyInput,lambda request:self.initial_discovery()),
                    'list_course_resources':(CourseQuery,self.list_course_resources),'discover_exam_sources':(CourseQuery,self.discover_exam_sources),
                    'retrieve_course_document':(SourceQuery,self.retrieve_course_document),'search_learning_materials':(SearchQuery,self.search_learning_materials),
                    'check_course_updates':(CourseQuery,self.check_course_updates)}
        if name=='retrieve_source_evidence':
            request=EvidenceQuery.model_validate(arguments)
            return {**self.store.evidence(request.source_id,request.revision_id,request.locator),'untrusted_source_material':True}
        if name not in operations:raise LookupError('Unknown LMS retrieval operation.')
        schema,operation=operations[name]
        return {**await operation(schema.model_validate(arguments)),'untrusted_source_material':True}


def canonical_search(source):
    return json.dumps(source,ensure_ascii=False).casefold()
