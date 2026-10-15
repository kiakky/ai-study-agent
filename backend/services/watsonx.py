"""IBM Cloud IAM + watsonx chat. Secrets stay in process memory, never repr/logs."""
from dataclasses import dataclass,field
import json,os,re,threading,time
from urllib.parse import urlsplit
import httpx
from services.llm_contracts import ChatReply,ToolCall,LLMError,strict_json

IAM_URL='https://iam.cloud.ibm.com/identity/token'
TOKYO_URL='https://jp-tok.ml.cloud.ibm.com'
API_VERSION='2023-05-29'
CANDIDATES=('meta-llama/llama-3-3-70b-instruct','mistralai/mistral-small-3-1-24b-instruct-2503','meta-llama/llama-4-maverick-17b-128e-instruct-fp8')


@dataclass(frozen=True)
class WatsonxCredentials:
    api_key: str=field(repr=False)
    project_id: str=field(repr=False)
    url: str=TOKYO_URL

    def __post_init__(self):
        if not self.api_key or not self.project_id or self.url.rstrip('/')!=TOKYO_URL:
            raise LLMError('configuration')
        object.__setattr__(self,'url',TOKYO_URL)

    @classmethod
    def from_environment(cls):
        return cls(os.getenv('IBM_CLOUD_API_KEY',''),os.getenv('WATSONX_PROJECT_ID',''),os.getenv('WATSONX_URL',''))


class WatsonxProvider:
    def __init__(self,credentials,model_id,client=None,clock=time.time,sleep=time.sleep):
        if not isinstance(model_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+',model_id):raise LLMError('configuration')
        self.credentials=credentials;self.model_id=model_id;self.clock=clock;self.sleep=sleep
        self.client=client or httpx.Client(timeout=httpx.Timeout(20,connect=5),follow_redirects=False,trust_env=False)
        self._own_client=client is None;self._token=None;self._expiry=0;self._lock=threading.Lock();self._model=None;self._deadline=threading.local()

    def close(self):
        if self._own_client:self.client.close()

    def _request(self,method,url,**kwargs):
        for attempt in range(3):
            try:
                remaining=getattr(self._deadline,'until',time.monotonic()+60)-time.monotonic()
                if remaining<=0:raise LLMError('timeout')
                response=self.client.request(method,url,timeout=httpx.Timeout(min(20,remaining),connect=min(5,remaining)),**kwargs)
                if response.status_code==429 or response.status_code in (500,502,503,504):
                    if attempt==2:raise LLMError('rate_limited' if response.status_code==429 else 'unavailable')
                    delay=2**attempt
                    try:delay=min(4,max(delay,float(response.headers.get('Retry-After',0))))
                    except ValueError:pass
                    self.sleep(delay);continue
                if response.status_code in (401,403):
                    if response.status_code==403:
                        try:
                            codes={e.get('code') for e in response.json().get('errors',[]) if isinstance(e,dict)}
                        except (ValueError,AttributeError,TypeError):codes=set()
                        if codes & {'token_quota_reached','token_quota_exceeded','quota_exceeded'}:
                            raise LLMError('quota_exceeded')
                    error=LLMError('authentication');error.http_status=response.status_code;raise error
                if response.status_code in (400,404,422):raise LLMError('unsupported_request')
                if response.status_code!=200:raise LLMError('unavailable')
                if len(response.content)>2_000_000:raise LLMError('invalid_response')
                try:return response.json()
                except ValueError:raise LLMError('invalid_response') from None
            except (httpx.TimeoutException,httpx.TransportError):
                if attempt==2:raise LLMError('timeout') from None
                self.sleep(2**attempt)
        raise LLMError('unavailable')

    def token(self):
        with self._lock:
            if self._token and self._expiry>self.clock()+60:return self._token
            value=self._request('POST',IAM_URL,data={'grant_type':'urn:ibm:params:oauth:grant-type:apikey','apikey':self.credentials.api_key},headers={'Accept':'application/json'})
            try:
                token=value['access_token'];expiry=float(value.get('expiration',self.clock()+float(value.get('expires_in',0))))
                if not isinstance(token,str) or not token or expiry<=self.clock()+5:raise ValueError()
            except (KeyError,TypeError,ValueError):raise LLMError('authentication') from None
            self._token=token;self._expiry=expiry;return token

    def _api(self,method,path,**kwargs):
        params={'version':API_VERSION,**kwargs.pop('params',{})}
        for attempt in range(2):
            token=self.token()
            try:return self._request(method,self.credentials.url+path,headers={'Authorization':'Bearer '+token,'Accept':'application/json'},params=params,**kwargs)
            except LLMError as error:
                if error.code!='authentication' or getattr(error,'http_status',None)!=401 or attempt:raise
                with self._lock:
                    if self._token==token:self._token=None;self._expiry=0
        raise LLMError('authentication')

    def models(self):
        # Bounded pagination; do not follow arbitrary provider-supplied links.
        models={};offset=None
        for page in range(5):
            value=self._api('GET','/ml/v1/foundation_model_specs',params={'limit':100,**({'start':offset} if offset is not None else {})})
            if not isinstance(value,dict) or not isinstance(value.get('resources'),list):raise LLMError('invalid_response')
            for model in value['resources']:
                if isinstance(model,dict) and isinstance(model.get('model_id'),str):models[model['model_id']]=model
            next_page=value.get('next')
            if not next_page:break
            href=next_page.get('href','') if isinstance(next_page,dict) else ''
            from urllib.parse import parse_qs
            parsed=urlsplit(href)
            if parsed.netloc and parsed.netloc!=urlsplit(self.credentials.url).netloc:raise LLMError('invalid_response')
            if parsed.path!='/ml/v1/foundation_model_specs':raise LLMError('invalid_response')
            try:offset=parse_qs(parsed.query)['start'][0]
            except (KeyError,ValueError,IndexError):raise LLMError('invalid_response') from None
            try:
                if not offset or len(offset)>1000:raise ValueError()
            except (KeyError,ValueError,IndexError):raise LLMError('invalid_response') from None
        return models

    def verify_model(self):
        if self._model is None:
            model=self.models().get(self.model_id)
            if not model:raise LLMError('unsupported_model')
            functions={item.get('id') for item in model.get('functions',[]) if isinstance(item,dict)}
            if 'text_chat' not in functions:raise LLMError('unsupported_model')
            self._model={'model_id':self.model_id,'native_tools':True if 'function_calling' in functions else None}
        return dict(self._model)

    def complete(self,messages,tools=None,tool_choice=None,max_tokens=None):
        self._deadline.until=time.monotonic()+60
        try:return self._complete(messages,tools,tool_choice,max_tokens)
        finally:del self._deadline.until

    def _complete(self,messages,tools=None,tool_choice=None,max_tokens=None):
        capability=self.verify_model()
        if tools and capability['native_tools'] is False:raise LLMError('unsupported_tools')
        limit=256 if max_tokens is None else max_tokens
        if type(limit) is not int or not 1<=limit<=512:raise LLMError('configuration')
        validate_messages(messages, tools)
        body={'model_id':self.model_id,'project_id':self.credentials.project_id,'messages':messages,'max_tokens':limit,'temperature':0.3}
        if tools:body.update(tools=tools,tool_choice=tool_choice or 'auto')
        try:value=self._api('POST','/ml/v1/text/chat',json=body)
        except LLMError as error:
            if tools and error.code=='unsupported_request':
                raise LLMError('unsupported_tools') from None
            raise
        try:
            if value.get('model_id',self.model_id)!=self.model_id:raise ValueError()
            choices=value['choices']
            if not isinstance(choices,list) or len(choices)!=1:raise ValueError()
            message=choices[0]['message']
            if message.get('role')!='assistant':raise ValueError()
            content=message.get('content') or ''
            if not isinstance(content,str) or len(content)>8000:raise ValueError()
            raw=message.get('tool_calls') or []
            if not isinstance(raw,list) or len(raw)>4:raise ValueError()
            calls=[]
            for call in raw:
                if call['type']!='function':raise ValueError()
                function=call['function'];arguments=function['arguments']
                if not isinstance(arguments,str) or len(arguments)>8000:raise ValueError()
                calls.append(ToolCall(id=call['id'],name=function['name'],arguments=strict_json(arguments)))
            if len({call.id for call in calls})!=len(calls):raise ValueError()
            if calls and not tools:raise ValueError()
            if not content.strip() and not calls:raise ValueError()
            if calls:self._model['native_tools']=True
            return ChatReply(content.strip(),tuple(calls))
        except (KeyError,IndexError,TypeError,ValueError):raise LLMError('invalid_response') from None


def validate_messages(messages, tools=None):
    """Validate native tool continuation and pairing before sending to IBM."""
    pending=set();seen=set()
    try:
        if not messages:raise ValueError()
        for m in messages:
            if not isinstance(m,dict) or not isinstance(m.get('content'),str):raise ValueError()
            role=m.get('role')
            if role=='tool':
                if not tools or set(m)!={'role','content','tool_call_id'} or m['tool_call_id'] not in pending:raise ValueError()
                pending.remove(m['tool_call_id'])
            elif role=='assistant' and 'tool_calls' in m:
                if not tools or pending or set(m)!={'role','content','tool_calls'}:raise ValueError()
                if not isinstance(m['tool_calls'],list) or not 1<=len(m['tool_calls'])<=4:raise ValueError()
                for c in m['tool_calls']:
                    if set(c)!={'id','type','function'} or c['type']!='function':raise ValueError()
                    f=c['function']
                    if set(f)!={'name','arguments'}:raise ValueError()
                    call=ToolCall(id=c['id'],name=f['name'],arguments=strict_json(f['arguments']))
                    if call.id in seen:raise ValueError()
                    seen.add(call.id);pending.add(call.id)
            elif role in {'system','user','assistant'}:
                if pending or set(m)!={'role','content'}:raise ValueError()
            else:raise ValueError()
        if pending:raise ValueError()
    except (ValueError,TypeError,KeyError):raise LLMError('invalid_request') from None
