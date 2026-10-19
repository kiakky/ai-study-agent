"""Bounded upstream response decoding. No URLs or returned file paths are opened."""
import base64
import json
from services.document_extraction import MAX_FILE_BYTES

MAX_RESPONSE_BYTES=2*1024*1024
MAX_TEXT_CHARS=400000


class MCPUnavailable(ValueError):
    pass


class MCPInlineLimit(MCPUnavailable):
    pass


class MCPDownloadLimit(MCPUnavailable):
    pass


def check_document_error(result):
    """Classify only the pinned server's known refusal contracts, never echo text."""
    if not attribute(result,'isError','is_error',default=False):return
    blocks=attribute(result,'content',default=[])
    first=attribute(blocks[0],'text',default='') if blocks else ''
    if isinstance(first,str):
        if first.startswith('File too large for inline delivery ('):
            raise MCPInlineLimit('Document requires bounded private reading.')
        if (first.startswith('File too large (') or
                (first.startswith('Could not save the file.') and 'The file is larger than the download size limit.' in first)):
            raise MCPDownloadLimit('Document exceeds the supported file size.')
    raise MCPUnavailable('MCP retrieval failed; existing evidence retained.')


def attribute(value,*names,default=None):
    for name in names:
        if isinstance(value,dict) and name in value:return value[name]
        if hasattr(value,name):return getattr(value,name)
    return default


def response_parts(result):
    if attribute(result,'isError','is_error',default=False):
        raise MCPUnavailable('LMS retrieval failed; previous evidence retained.')
    blocks=attribute(result,'content',default=[])
    if not isinstance(blocks,list) or len(blocks)>100:
        raise MCPUnavailable('LMS response block limit exceeded.')
    texts=[];size=0
    for block in blocks:
        if attribute(block,'type')=='text':
            text=attribute(block,'text',default='')
            if not isinstance(text,str):raise MCPUnavailable('Invalid LMS text block.')
            size+=len(text.encode('utf-8'))
            if size>MAX_RESPONSE_BYTES:raise MCPUnavailable('LMS response size limit exceeded.')
            texts.append(text)
    structured=attribute(result,'structuredContent','structured_content')
    if structured is not None:
        try:
            if len(json.dumps(structured).encode())>MAX_RESPONSE_BYTES:raise ValueError()
        except (ValueError,TypeError,RecursionError):
            raise MCPUnavailable('Invalid or oversized structured LMS response.') from None
        data=structured
    elif texts:
        try:data=json.loads(texts[0])
        except (ValueError,RecursionError):data={'unparsed_text':texts[0]}
    else:data={'unparsed_content':True}
    pagination=None;notices=[]
    for text in texts[1:]:
        try:extra=json.loads(text)
        except (ValueError,RecursionError):extra=None
        if isinstance(extra,dict) and set(extra)=={'pagination'} and isinstance(extra['pagination'],dict):
            if pagination is not None and pagination!=extra['pagination']:
                raise MCPUnavailable('Conflicting LMS pagination metadata.')
            pagination=extra['pagination']
        else:notices.append(text)
    return data,pagination,notices,texts


def response_payload(result):
    data,pagination,notices,_=response_parts(result)
    if pagination is not None or notices:
        data=dict(data) if isinstance(data,dict) else {'items':data}
        if pagination is not None:
            if 'pagination' in data and data['pagination']!=pagination:
                raise MCPUnavailable('Conflicting LMS pagination metadata.')
            data['pagination']=pagination
        if notices:
            # Notices can contain auth instructions/private diagnostics: do not persist them.
            data['retrieval_notices']=['Supplemental MCP notice; retrieval coverage may be incomplete.']
    return data


def document_delivery(result):
    """Return bytes, extraction MIME and provenance; inline text is not original PDF bytes."""
    data,_,notices,texts=response_parts(result)
    if isinstance(data,dict) and data.get('mode')=='inline':
        representation=data.get('representation')
        if representation not in {'extracted_text','text'}:
            raise MCPUnavailable('This document has no supported inline text; no visual/OCR content was inferred.')
        if len(texts)<2 or not texts[1].strip():raise MCPUnavailable('Inline document text is missing.')
        body=texts[1]
        if len(body)>MAX_TEXT_CHARS+1500:raise MCPUnavailable('Inline document text limit exceeded.')
        info={'representation':representation,'original_media_type':data.get('mimeType'),
              'upstream_text':True,'truncated':data.get('truncated',False),
              'warnings':['Upstream-extracted text; original binary bytes and layout are unavailable.']}
        if type(info['truncated']) is not bool:raise MCPUnavailable('Invalid document truncation metadata.')
        if info['truncated']:info['warnings'].append('Upstream text is truncated; this is not the complete document.')
        if len(texts)>2:info['warnings'].append('Supplemental MCP notice; document coverage may be incomplete.')
        return body.encode('utf-8'),'text/plain',info
    if isinstance(data,dict):
        if data.get('mode')=='disk' or any(k in data for k in ('path','filePath','downloadPath')):
            raise MCPUnavailable('Returned filesystem paths are not opened.')
        mime=data.get('mimeType',data.get('mime_type'))
        if isinstance(data.get('base64'),str):
            encoded=data['base64']
            if len(encoded)>MAX_FILE_BYTES*4//3+8:raise MCPUnavailable('Document exceeds download limit.')
            try:value=base64.b64decode(encoded,validate=True)
            except ValueError:raise MCPUnavailable('Invalid document encoding.') from None
            if len(value)>MAX_FILE_BYTES:raise MCPUnavailable('Document exceeds download limit.')
            return value,mime or 'application/octet-stream',{}
        for key,kind in [('html','text/html'),('text',mime or 'text/plain')]:
            if isinstance(data.get(key),str):return data[key].encode(),kind,{}
    for block in attribute(result,'content',default=[]):
        resource=attribute(block,'resource')
        blob=attribute(resource,'blob') if resource else None
        if isinstance(blob,str):
            if len(blob)>MAX_FILE_BYTES*4//3+8:raise MCPUnavailable('Document exceeds download limit.')
            try:value=base64.b64decode(blob,validate=True)
            except ValueError:raise MCPUnavailable('Invalid document encoding.') from None
            if len(value)>MAX_FILE_BYTES:raise MCPUnavailable('Document exceeds download limit.')
            return value,attribute(resource,'mimeType','mime_type',default='application/octet-stream'),{}
    raise MCPUnavailable('MCP document format is unsupported; no local paths or remote URLs were opened.')


def document_bytes(result):
    value,mime,_=document_delivery(result)
    return value,mime
