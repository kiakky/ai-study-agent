"""IBM responses are mocked; inputs and credentials are synthetic."""
import sys,json,time
from pathlib import Path
import unittest
import httpx
from pydantic import BaseModel,ConfigDict
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from services.watsonx import WatsonxProvider,WatsonxCredentials,CANDIDATES,IAM_URL
from services.llm_contracts import LLMError,ToolRegistry,ToolCall


class IBMFixture:
    def __init__(self):self.requests=[];self.now=1000;self.auth=0;self.replies=[];self.status=200;self.functions=['text_chat','function_calling']
    def __call__(self,request):
        self.requests.append(request)
        if request.url.host=='iam.cloud.ibm.com':
            self.auth+=1
            return httpx.Response(200,json={'access_token':'synthetic-bearer-'+str(self.auth),'expiration':self.now+3600})
        if request.url.path.endswith('foundation_model_specs'):
            return httpx.Response(200,json={'resources':[{'model_id':CANDIDATES[0],'functions':[{'id':value} for value in self.functions]}]})
        if self.replies:
            item=self.replies.pop(0)
            if isinstance(item,Exception):raise item
            return item
        if self.status!=200:return httpx.Response(self.status,json={'errors':[{'message':'synthetic-secret-not-for-logs'}]})
        return httpx.Response(200,json={'model_id':CANDIDATES[0],'choices':[{'message':{'role':'assistant','content':'Try a small loop exercise.'}}]})


class WatsonxTests(unittest.TestCase):
    def setUp(self):
        self.fixture=IBMFixture();self.client=httpx.Client(transport=httpx.MockTransport(self.fixture));self.addCleanup(self.client.close)
        self.provider=WatsonxProvider(WatsonxCredentials('synthetic-api-key','synthetic-project'),CANDIDATES[0],self.client,lambda:self.fixture.now,lambda s:None)
        self.messages=[{'role':'user','content':'Explain Python loops.'}]

    def test_iam_exchange_cache_refresh_and_no_api_key_as_bearer(self):
        self.provider.complete(self.messages);self.provider.complete(self.messages)
        self.assertEqual(self.fixture.auth,1)
        self.fixture.now+=3550;self.provider.complete(self.messages)
        self.assertEqual(self.fixture.auth,2)
        for request in self.fixture.requests:
            if request.url.host=='iam.cloud.ibm.com':
                self.assertIn(b'grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Aapikey',request.content)
                self.assertIn(b'apikey=synthetic-api-key',request.content)
            else:self.assertTrue(request.headers['Authorization'].startswith('Bearer synthetic-bearer-'))
        self.assertNotIn('synthetic-api-key',repr(self.provider.credentials))
        self.assertNotIn('synthetic-project',repr(self.provider.credentials))

    def test_401_refresh_once_and_403_no_pointless_refresh(self):
        self.fixture.replies=[httpx.Response(401,json={})]
        self.provider.complete(self.messages);self.assertEqual(self.fixture.auth,2)
        self.fixture.status=403
        with self.assertRaises(LLMError) as failure:self.provider.complete(self.messages)
        self.assertEqual(failure.exception.code,'authentication');self.assertEqual(self.fixture.auth,2)

    def test_timeout_rate_limit_and_server_errors_bounded(self):
        for status,code in [(429,'rate_limited'),(503,'unavailable')]:
            self.fixture.status=status
            before=len(self.fixture.requests)
            with self.assertRaises(LLMError) as error:self.provider.complete(self.messages)
            self.assertEqual(error.exception.code,code)
            self.assertLessEqual(len(self.fixture.requests)-before,5)
            self.assertNotIn('synthetic-secret',str(error.exception))
        self.fixture.status=200
        self.fixture.replies=[httpx.ReadTimeout('synthetic secret') for _ in range(3)]
        with self.assertRaises(LLMError) as error:self.provider.complete(self.messages)
        self.assertEqual(error.exception.code,'timeout')

    def test_quota_exhaustion_is_not_misreported_as_invalid_credentials(self):
        self.fixture.replies=[httpx.Response(403,json={'errors':[{'code':'token_quota_reached','message':'synthetic-private-provider-message'}]})]
        with self.assertRaises(LLMError) as failure:self.provider.complete(self.messages)
        self.assertEqual(failure.exception.code,'quota_exceeded')
        self.assertNotIn('synthetic-private',str(failure.exception))
        self.assertEqual(self.fixture.auth,1)

    def test_transient_failure_recovers(self):
        self.fixture.replies=[httpx.Response(429,json={},headers={'Retry-After':'1'}),httpx.Response(503,json={})]
        self.assertIn('loop',self.provider.complete(self.messages).content)

    def test_model_missing_or_chat_unsupported_fails_before_inference(self):
        self.provider.model_id='vendor/unavailable'
        self.assertRaises(LLMError,self.provider.complete,self.messages)
        self.provider.model_id=CANDIDATES[0];self.fixture.functions=['text_generation']
        self.assertRaises(LLMError,self.provider.complete,self.messages)
        self.assertFalse(any(r.url.path.endswith('/text/chat') for r in self.fixture.requests))

    def test_invalid_model_ids_urls_and_credentials_rejected(self):
        for url in ['http://jp-tok.ml.cloud.ibm.com','https://attacker.example','https://jp-tok.ml.cloud.ibm.com/evil']:
            self.assertRaises(LLMError,WatsonxCredentials,'secret','project',url)
        self.assertRaises(LLMError,WatsonxProvider,self.provider.credentials,'bad model')

    def test_malformed_or_empty_responses_rejected(self):
        for value in [{},{'choices':[]},{'choices':[{'message':{'role':'user','content':'fake'}}]}, {'choices':[{'message':{'role':'assistant','content':''}}]}, {'model_id':'wrong','choices':[{'message':{'role':'assistant','content':'text'}}]}]:
            self.fixture.replies=[httpx.Response(200,json=value)]
            self.assertRaises(LLMError,self.provider.complete,self.messages)
        self.fixture.replies=[httpx.Response(200,content=b'not JSON')]
        self.assertRaises(LLMError,self.provider.complete,self.messages)

    def test_native_calls_are_parsed_and_never_executed(self):
        class Echo(BaseModel):
            model_config=ConfigDict(extra='forbid')
            text:str
        registry=ToolRegistry();registry.register('echo','Synthetic echo',Echo)
        response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'echo','arguments':'{"text":"ok"}'}}]}}]}
        self.fixture.replies=[httpx.Response(200,json=response)]
        result=self.provider.complete(self.messages,registry.definitions())
        self.assertEqual(registry.validate(result.tool_calls[0]).arguments,{'text':'ok'})
        self.fixture.replies=[httpx.Response(200,json=response)]
        self.assertRaises(LLMError,self.provider.complete,self.messages)
        for arguments in ['{"text":"ok","user_id":202}','{"text":"a","text":"b"}','[]','not JSON']:
            response['choices'][0]['message']['tool_calls'][0]['function']['arguments']=arguments
            self.fixture.replies=[httpx.Response(200,json=response)]
            try:reply=self.provider.complete(self.messages,registry.definitions())
            except LLMError:continue
            self.assertRaises(LLMError,registry.validate,reply.tool_calls[0])

    def test_tool_registry_and_structured_fallback_fail_closed(self):
        class Count(BaseModel):
            model_config=ConfigDict(extra='forbid')
            count:int
        registry=ToolRegistry();registry.register('count','Synthetic count',Count)
        self.assertEqual(registry.parse_action('{"id":"a","name":"count","arguments":{"count":2}}').arguments,{'count':2})
        for text in ['{"id":"a","name":"calendar_write","arguments":{}}','{"id":"a","name":"count","arguments":{"count":2,"user_id":202}}','{"id":"a","name":"count","arguments":{"count":NaN}}']:
            self.assertRaises(LLMError,registry.parse_action,text)
        self.assertEqual(ToolRegistry().definitions(),[])
        self.assertFalse(hasattr(registry,'execute'))

    def test_non_tool_model_declines_tools(self):
        self.fixture.functions=['text_chat']
        self.provider.verify_model();self.provider._model['native_tools']=False
        self.assertRaises(LLMError,self.provider.complete,self.messages,[{'type':'function'}])


if __name__=='__main__':unittest.main()
