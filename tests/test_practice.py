"""Milestone 3 contracts: temporary SQLite, synthetic PDFs, mocked model/Telegram."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch,AsyncMock
from uuid import uuid4
import test_api as fixtures
import test_calendar_native as native
import test_lms_retrieval as lms
from fastapi.testclient import TestClient
import database
from models.practice import PracticeRequest,SaveAttempt,SubmitAttempt
from services import practice
from services.practice_store import PracticeStore,PracticeConflict,SCHEMA
from services.llm_contracts import ChatReply,ToolCall,LLMError
from services.practice_pdf import read_pdf,MAX_BYTES
from services.practice_telegram import handle_pdf

NOTES=('A queue processes entries in first-in, first-out order. '
       'A stack uses last-in, first-out order. An array is an indexed sequence of values. '
       'The operations discussed here are enqueue and dequeue for queues. ')


class Model:
    def __init__(self,mode='valid'):self.mode=mode;self.calls=[];self.closed=False;self.n=0
    def close(self):self.closed=True
    def complete(self,messages,**kwargs):
        self.calls.append((messages,kwargs));data=json.loads(messages[-1]['content'])
        if self.mode=='uncertain':raise LLMError('inference_uncertain')
        if kwargs.get('tools',[{}])[0].get('function',{}).get('name')=='classify_practice_scope':
            return native.call('classify_practice_scope',mode='concept_practice')
        if kwargs.get('tools',[{}])[0].get('function',{}).get('name')=='qualify_practice_sources':
            return ChatReply('',(ToolCall(id='grounding',name='qualify_practice_sources',arguments={'learning_passages':[data['evidence'][0]['reference']],'assessment_passages':[],'focus':data['topic']}),))
        if self.mode=='unsupported':return ChatReply('The sources do not support this topic.')
        if kwargs.get('tools',[{}])[0].get('function',{}).get('name')=='check_practice_content':
            return native.call('check_practice_content',issues=[])
        questions=[]
        for _ in range(data['question_count']):
            self.n+=1
            questions.append({'text':'Which queue ordering is stated in the supplied example '+str(self.n)+'?',
                'options':['First-in, first-out','Last-in, first-out','Alphabetical ordering','Random ordering'],
                'correct_option':'A','explanations':{'A':'The supplied notes explicitly say first-in, first-out.',
                    'B':'The notes assign last-in, first-out to a stack, not a queue.',
                    'C':'Alphabetical ordering is not the stated queue property.',
                    'D':'Random ordering is not the stated queue property.'},
                'citations':[{'reference':data['evidence'][0]['reference']}]})
        if self.mode=='bad_quote':questions[0]['citations'][0]['quote']='Unsupported synthetic claim not in the supplied evidence.'
        if self.mode=='bad_source':questions[0]['citations'][0]['reference']='material:someone-else'
        if self.mode=='bad_key':questions[0]['correct_option']='E'
        if self.mode=='duplicate':questions=[questions[0],deepcopy(questions[0])]
        if self.mode=='bad_count':questions=[]
        if self.mode=='injection':questions[0]['user_id']=202
        return ChatReply('',(ToolCall(id='private-generation',name='deliver_practice',arguments={'questions':questions}),))


class PracticeTests(unittest.TestCase):
    def setUp(self):
        fixtures.QuizAPITests.setUp(self)
        database.initialize_database(backfill_approvals=False,include_practice=True,include_lms=True)
        feature=patch('services.practice.enabled',return_value=True);feature.start();self.addCleanup(feature.stop)
        shuffle=patch('services.practice.random.shuffle',side_effect=lambda values:None);shuffle.start();self.addCleanup(shuffle.stop)
        self.store=PracticeStore(101);self.otherstore=PracticeStore(202)
        self.material=self.store.add_material('Synthetic data structures',NOTES,'fixture')

    def request(self,count=2,**patches):
        data={'title':'Queue practice','topic':'Queue ordering','question_count':count,'sources':[{'reference':self.material['reference']}]}
        data.update(patches);return data

    def enqueue(self,**kwargs):return practice.request_practice(101,self.request(**kwargs),str(uuid4()))

    def prepared(self,count=2):
        job=self.enqueue(count=count);model=Model();self.assertTrue(practice.run_one(lambda:model))
        status=self.store.job(job['id']);self.assertEqual(status['status'],'ready');return status['set_id'],model

    def attempt(self):
        identifier,_=self.prepared();return self.store.start(identifier,str(uuid4()))

    def submit(self,a,key=None):
        return SubmitAttempt(revision=a['revision'],answers={q['id']:'A' for q in a['questions']},submission_id=key or uuid4())

    def test_generated_sets_do_not_create_fictitious_exams_or_change_legacy_schema(self):
        with closing(database.get_connection()) as db:
            before=list(db.execute('SELECT * FROM exams'));schema=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        self.prepared()
        with closing(database.get_connection()) as db:
            self.assertEqual(before,list(db.execute('SELECT * FROM exams')))
            self.assertEqual(schema,dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")))

    def test_materials_jobs_and_answers_are_private_to_owner(self):
        identifier,_=self.prepared();a=self.store.start(identifier,'start')
        self.assertNotIn('correct_option',json.dumps(a));self.assertNotIn('explanations',json.dumps(a));self.assertNotIn('citations',json.dumps(a))
        self.assertEqual(self.otherstore.listing()['sets'],[]);self.assertEqual(self.otherstore.materials(),[])
        for action in [lambda:self.otherstore.material(self.material['reference']),lambda:self.otherstore.start(identifier,'forged'),lambda:self.otherstore.attempt(a['id'])]:
            with self.assertRaises(LookupError):action()

    def test_source_snapshot_is_actual_owned_text_not_model_summary(self):
        identifier,model=self.prepared()
        messages=next(messages for messages,options in model.calls if options['tools'][0]['function']['name']=='qualify_practice_sources')
        evidence=json.loads(messages[-1]['content'])['evidence']
        self.assertEqual(practice.normalized(' '.join(e['text'] for e in evidence)),practice.normalized(NOTES));self.assertIn('UNTRUSTED DATA',messages[0]['content'])
        with self.assertRaises(LookupError):practice.request_practice(202,self.request(),'forged')

    def test_job_idempotency_concurrency_and_no_duplicate_model_calls(self):
        key=str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs=list(pool.map(lambda _:practice.request_practice(101,self.request(),key),range(4)))
        self.assertEqual(len({j['id'] for j in jobs}),1)
        model=Model()
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:practice.run_one(lambda:model),range(3)))
        self.assertEqual(sum(results),1);self.assertEqual(len(model.calls),4)
        with self.assertRaises(PracticeConflict):practice.request_practice(101,self.request(count=3),key)

    def test_generation_is_batched_and_answer_keys_never_in_job_listing(self):
        identifier,model=self.prepared(count=10)
        self.assertEqual(len(model.calls),6)
        a=self.store.start(identifier,'ten');self.assertEqual(len(a['questions']),10)
        self.assertNotIn('correct_option',json.dumps(self.store.listing()));self.assertTrue(model.closed)

    def test_bad_generation_is_never_published_and_never_replayed(self):
        for mode in ['bad_quote','bad_source','bad_key','duplicate','bad_count','injection','unsupported','uncertain']:
            with self.subTest(mode=mode):
                job=self.enqueue();model=Model(mode);practice.run_one(lambda:model)
                status=self.store.job(job['id']);self.assertEqual(status['status'],'failed');self.assertIsNone(status['set_id'])
                self.assertEqual(status['error_code'],'generation_uncertain' if mode=='uncertain' else 'generation_failed')
                self.assertFalse(practice.run_one(lambda:(_ for _ in ()).throw(AssertionError('No replay'))))
        self.assertEqual(self.store.listing()['sets'],[])

    def test_interrupted_worker_recovers_as_failed_without_inference(self):
        job=self.enqueue()
        with closing(database.get_connection()) as db,db:db.execute("UPDATE practice_jobs SET status='running' WHERE id=?",(job['id'],))
        practice.recover_interrupted();self.assertEqual(self.store.job(job['id'])['error_code'],'generation_interrupted')
        self.assertFalse(practice.run_one(lambda:Model()))

    def test_interruption_during_inference_cannot_publish_a_finished_set(self):
        job=self.enqueue();model=Model();original=model.complete
        def interrupted(*args,**kwargs):
            result=original(*args,**kwargs);practice.recover_interrupted();return result
        model.complete=interrupted
        practice.run_one(lambda:model)
        self.assertEqual(self.store.job(job['id'])['status'],'failed')
        self.assertEqual(self.store.listing()['sets'],[])

    def test_independent_quizzes_and_attempts_can_resume_without_overwriting(self):
        a=self.attempt();b=self.attempt()
        self.store.update(a['id'],SaveAttempt(revision=0,answers={a['questions'][0]['id']:'B'}))
        self.store.update(b['id'],SaveAttempt(revision=0,answers={b['questions'][1]['id']:'C'}))
        reopened=PracticeStore(101)
        self.assertEqual(reopened.attempt(a['id'])['answers'],{a['questions'][0]['id']:'B'})
        self.assertEqual(reopened.attempt(b['id'])['answers'],{b['questions'][1]['id']:'C'})
        self.assertEqual(len(reopened.listing()['attempts']),2)

    def test_stale_tab_cannot_overwrite_newer_saved_answers(self):
        a=self.attempt();body=SaveAttempt(revision=0,answers={a['questions'][0]['id']:'B'})
        self.store.update(a['id'],body)
        with self.assertRaises(PracticeConflict):self.store.update(a['id'],SaveAttempt(revision=0,answers={}))
        self.assertEqual(self.store.attempt(a['id'])['answers'],body.answers)

    def test_submission_retry_is_immutable_and_grading_matches_saved_llm_key(self):
        a=self.attempt();body=self.submit(a);first=self.store.update(a['id'],body,True)
        second=self.store.update(a['id'],body,True);self.assertEqual(first,second);self.assertEqual(first['score'],100)
        changed=body.model_copy(update={'answers':{q['id']:'B' for q in a['questions']}})
        with self.assertRaises(PracticeConflict):self.store.update(a['id'],changed,True)
        with self.assertRaises(PracticeConflict):self.store.update(a['id'],SaveAttempt(revision=0,answers={}))
        self.assertEqual(self.store.attempt(a['id'])['result'],first)
        self.assertEqual(self.store.listing()['stats']['completed'],1)

    def test_concurrent_submission_retry_creates_one_completed_result(self):
        a=self.attempt();body=self.submit(a)
        with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(lambda _:self.store.update(a['id'],body,True),range(5)))
        self.assertTrue(all(r==results[0] for r in results))
        self.assertEqual(self.store.listing()['stats']['completed'],1)

    def test_incomplete_foreign_question_and_forged_actor_submissions_are_rejected(self):
        a=self.attempt()
        with self.assertRaises(ValueError):self.store.update(a['id'],SubmitAttempt(revision=0,answers={},submission_id=uuid4()),True)
        with self.assertRaises(ValueError):self.store.update(a['id'],SaveAttempt(revision=0,answers={'unknown':'A'}))
        with self.assertRaises(LookupError):self.otherstore.update(a['id'],self.submit(a),True)
        self.assertEqual(self.store.attempt(a['id'])['revision'],0)

    def test_wrong_answer_has_source_based_review_and_simple_progress(self):
        a=self.attempt();body=self.submit(a).model_copy(update={'answers':{q['id']:'B' for q in a['questions']}})
        result=self.store.update(a['id'],body,True)
        self.assertEqual(result['score'],0);self.assertIn('stack',result['feedback'][0]['explanation'])
        self.assertIn('first-in',result['feedback'][0]['correct_explanation'])
        self.assertEqual(result['feedback'][0]['citations'][0]['title'],'Synthetic data structures')
        self.assertEqual(self.store.listing()['stats']['average'],0)

    def test_limits_validation_and_unknown_source_do_not_enqueue(self):
        for args in [{'question_count':11},{'user_id':202},{'sources':[]},{'question_count':True}]:
            with self.assertRaises(ValueError):practice.request_practice(101,{**self.request(),**args},str(uuid4()))
        for _ in range(3):self.enqueue()
        with self.assertRaises(PracticeConflict):self.enqueue()

    def test_source_ranges_do_not_silently_invent_coverage(self):
        request=PracticeRequest.model_validate(self.request(sources=[{'reference':self.material['reference'],'offset':20,'length':100}]))
        data=practice.evidence_for(101,request.sources)[0]
        self.assertTrue(data['partial']);self.assertEqual(data['text'],NOTES[20:120])
        request=PracticeRequest.model_validate(self.request(sources=[{'reference':self.material['reference'],'offset':99999}]))
        with self.assertRaises(ValueError):practice.evidence_for(101,request.sources)

    def test_api_unsigned_and_cross_user_access_denied(self):
        identifier,_=self.prepared();a=self.store.start(identifier,'api')
        for path in ['/api/practice','/api/practice/materials','/api/practice/attempts/'+a['id']]:
            self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get('/api/practice/attempts/'+a['id'],headers=self.other).status_code,404)
        self.assertEqual(self.client.get('/api/practice/material?reference='+self.material['reference'],headers=self.other).status_code,404)
        self.assertEqual(self.client.post('/api/practice/jobs',headers=self.other,json={**self.request(),'request_id':str(uuid4())}).status_code,404)

    def test_api_end_to_end_paste_generate_save_resume_submit_review(self):
        m=self.client.post('/api/practice/materials',headers=self.headers,json={'title':'Private notes','text':NOTES,'request_id':str(uuid4())})
        self.assertEqual(m.status_code,200)
        request={**self.request(sources=[{'reference':m.json()['reference']}]),'request_id':str(uuid4())}
        job=self.client.post('/api/practice/jobs',headers=self.headers,json=request).json();practice.run_one(lambda:Model())
        status=self.client.get('/api/practice/jobs/'+job['id'],headers=self.headers).json()
        a=self.client.post('/api/practice/sets/'+status['set_id']+'/attempts',headers=self.headers,json={'request_id':str(uuid4())}).json()
        self.assertNotIn('correct_option',json.dumps(a));answers={q['id']:'A' for q in a['questions']}
        save=self.client.put('/api/practice/attempts/'+a['id'],headers=self.headers,json={'revision':0,'answers':answers});self.assertEqual(save.status_code,200)
        resumed=self.client.get('/api/practice/attempts/'+a['id'],headers=self.headers).json();self.assertEqual(resumed['answers'],answers)
        body={'revision':1,'answers':answers,'submission_id':str(uuid4())}
        result=self.client.post('/api/practice/attempts/'+a['id']+'/submit',headers=self.headers,json=body)
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['score'],100)
        self.assertEqual(self.client.post('/api/practice/attempts/'+a['id']+'/submit',headers=self.headers,json=body).json(),result.json())

    def test_api_stale_save_and_feature_off(self):
        a=self.attempt();path='/api/practice/attempts/'+a['id'];body={'revision':0,'answers':{}}
        self.assertEqual(self.client.put(path,headers=self.headers,json=body).status_code,200)
        self.assertEqual(self.client.put(path,headers=self.headers,json=body).status_code,409)
        with patch('services.practice.enabled',return_value=False):self.assertEqual(self.client.get('/api/practice',headers=self.headers).status_code,503)

    def test_pdf_real_subprocess_extraction_and_unsupported_inputs(self):
        data=read_pdf(lms.pdf_fixture());self.assertIn('Weeks 14-17',data['text'])
        for value in [b'not a pdf',b'%PDF-'+b'x'*MAX_BYTES,lms.pdf_fixture(True)]:
            with self.assertRaises(ValueError):read_pdf(value)

    def test_pdf_route_and_notes_are_per_user_and_rendered_as_data(self):
        path='/api/practice/materials/pdf?title=../../synthetic.pdf&request_id='+str(uuid4())
        response=self.client.post(path,headers={**self.headers,'Content-Type':'application/pdf'},content=lms.pdf_fixture())
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['kind'],'pdf')
        self.assertEqual(self.client.post(path,headers=self.headers,content=b'opaque').status_code,415)
        response=self.client.post('/api/practice/materials',headers=self.headers,json={'request_id':str(uuid4()),'title':'<script>bad()</script>','text':NOTES})
        self.assertEqual(response.status_code,200);self.assertEqual(self.otherstore.materials(),[])

    def test_pdf_subprocess_does_not_inherit_application_secrets(self):
        import services.practice_pdf as pdf
        with patch.object(pdf.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'text':NOTES,'warnings':[]}).encode())) as run,patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'synthetic-secret','WXO_API_KEY':'synthetic-secret'}):
            read_pdf(b'%PDF-synthetic')
        env=run.call_args.kwargs['env'];self.assertNotIn('TELEGRAM_BOT_TOKEN',env);self.assertNotIn('WXO_API_KEY',env)
        self.assertEqual(run.call_args.kwargs['timeout'],15)

    def test_missing_storage_fails_safely_without_creating_it(self):
        with closing(database.get_connection()) as db,db:
            for name in ['practice_attempts','practice_sets','practice_jobs','practice_materials']:db.execute('DROP TABLE '+name)
        response=self.client.get('/api/practice',headers=self.headers)
        self.assertEqual(response.status_code,503);self.assertNotIn('sqlite',response.text)

    def test_opt_in_migration_is_additive_repeatable_and_default_stays_off(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript(database.SCHEMA.replace(database.LMS_SCHEMA,''));before=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
            db.executescript(SCHEMA);db.executescript(SCHEMA);after=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
            self.assertEqual(set(after)-set(before),{'practice_materials','practice_jobs','practice_sets','practice_attempts'})
            self.assertTrue(all(after[k]==v for k,v in before.items()))

    def test_real_native_gateway_protocol_keeps_private_delivery_out_of_student_routes(self):
        import httpx
        requests=[];synthetic=Model()
        def transport(request):
            if request.url.host=='iam.cloud.ibm.com':return httpx.Response(200,json={'access_token':'synthetic','expires_in':3600})
            body=json.loads(request.content);requests.append(body)
            self.assertIn(body['tools'][0]['function']['name'],['deliver_practice','qualify_practice_sources','check_practice_content','classify_practice_scope'])
            response=synthetic.complete(body['messages'],tools=body['tools'])
            raw={'id':'private','type':'function','function':{'name':response.tool_calls[0].name,'arguments':json.dumps(response.tool_calls[0].arguments)}}
            return native.NativeProviderTests.response(calls=[raw],reason='tool_calls')
        job=self.enqueue()
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            provider=native.OrchestrateNativeProvider(native.CONFIG,client)
            practice.run_one(lambda:provider)
        status=self.store.job(job['id']);self.assertEqual(status['status'],'ready');self.assertEqual(len(requests),4)
        a=self.store.start(status['set_id'],'native');self.assertNotIn('correct_option',json.dumps(a))

    def test_brightspace_generation_requires_owner_revision_and_current_authorization(self):
        from models.sources import SourceMetadata
        from services.source_store import SourceStore,LMSAccessDenied
        from services.document_extraction import extract_document
        with patch.dict('os.environ',{'TELEGRAM_OWNER_ID':'101'}):
            sources=SourceStore(101,owner_id=101,cache_root=Path(self.temp.name)/'private')
            sources.bind_authorized_developer_session('synthetic')
            source=sources.index('synthetic',SourceMetadata(title='Synthetic teaching plan',resource_id='1',kind='document'),{'text':NOTES})
            doc=sources.extracted(source['source_id'],NOTES.encode(),extract_document(NOTES.encode(),'text/plain'),retain_original=False)
            ref='brightspace:'+source['source_id']+':'+doc['id']
            request=self.request(sources=[{'reference':ref}])
            with self.assertRaises(LMSAccessDenied):practice.request_practice(202,request,'forged-owner')
            job=practice.request_practice(101,request,'owner')
            with closing(database.get_connection()) as db,db:db.execute("UPDATE lms_connections SET status='disconnected'")
            model=Model();practice.run_one(lambda:model)
            self.assertEqual(model.calls,[]);self.assertEqual(self.store.job(job['id'])['status'],'failed')

    def test_material_deduplication_and_start_retry_cannot_change_identity_or_set(self):
        duplicate=self.store.add_material('Synthetic data structures',NOTES,'fixture')
        self.assertEqual(duplicate['reference'],self.material['reference'])
        with self.assertRaises(PracticeConflict):self.store.add_material('Altered',NOTES,'fixture')
        identifier,_=self.prepared();a=self.store.start(identifier,'same-start')
        self.assertEqual(self.store.start(identifier,'same-start')['id'],a['id'])
        second,_=self.prepared()
        with self.assertRaises(PracticeConflict):self.store.start(second,'same-start')

    def test_pdf_upload_actual_body_limit_and_no_unauthenticated_extraction(self):
        path='/api/practice/materials/pdf?title=Notes.pdf&request_id='+str(uuid4())
        with patch('routes.practice.read_pdf',side_effect=AssertionError('No parse before authentication')):
            self.assertEqual(self.client.post(path,headers={'Content-Type':'application/pdf'},content=b'%PDF-').status_code,401)
        response=self.client.post(path,headers={**self.headers,'Content-Type':'application/pdf'},content=b'%PDF-'+b'x'*MAX_BYTES)
        self.assertEqual(response.status_code,413)

    def test_default_database_startup_does_not_activate_practice_tables(self):
        with patch.object(database,'DB_PATH',Path(self.temp.name)/'default.db'):
            database.initialize_database(backfill_approvals=False)
            with closing(database.get_connection()) as db:
                names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('practice_materials',names)

    def test_unicode_material_remains_text_and_large_context_fails_before_inference(self):
        text='\u5b66\u4e60\u6750\u6599 '*3000
        material=self.store.add_material('Unicode notes',text,'unicode')
        selection={'reference':material['reference'],'length':6000}
        request=PracticeRequest.model_validate(self.request(sources=[selection]*4)).model_dump(mode='json')
        evidence=practice.evidence_for(101,PracticeRequest.model_validate(request).sources)
        self.assertIn('\u5b66',evidence[0]['text'])
        model=Model()
        with self.assertRaises(ValueError):practice.generate(model,request,evidence)
        self.assertEqual(model.calls,[])


class NativePracticeTests(unittest.TestCase):
    connect=native.NativeCalendarTests.connect
    def setUp(self):
        native.NativeCalendarTests.setUp(self)
        database.initialize_database(backfill_approvals=False,include_practice=True)
        feature=patch('services.practice.enabled',return_value=True);feature.start();self.addCleanup(feature.stop)
        env=patch.dict('os.environ',{'TELEGRAM_OWNER_ID':'101'});env.start();self.addCleanup(env.stop)
    def agent(self,user=101,message=10,**kwargs):return native.NativeCalendarTests.agent(self,user,message,**kwargs)
    def service(self,model):return native.NativeCalendarTests.service(self,model)

    def test_model_selects_material_and_quiz_without_keyword_router_or_calendar_write(self):
        def read(messages):
            ref=json.loads(messages[-1]['content'])['reference'];return native.call('study_material',operation='read',reference=ref)
        def prepare(messages):
            ref=json.loads(messages[-1]['content'])['reference'];return native.call('prepare_practice',title='Queues',topic='Queue ordering',question_count=2,sources=[{'reference':ref}])
        model=native.ScriptedModel([native.call('study_material',operation='save_message',title='My notes'),read,prepare,ChatReply('Your practice is preparing. Open Generated Practice.')])
        text='Use these notes for two practice questions: '+NOTES
        result=self.service(model).respond(202,100,text)
        self.assertEqual(result['status'],'completed');self.assertIn('preparing',result['text'])
        self.assertEqual(PracticeStore(202).material(PracticeStore(202).materials()[0]['reference'])['text'],text)
        self.assertEqual(PracticeStore(101).materials(),[]);self.assertEqual(self.provider.inserts,[])
        self.assertNotIn('correct_option',json.dumps(model.requests))

    def test_ten_owner_tools_seven_other_tools_and_no_forged_identity(self):
        self.assertEqual(len(self.agent().model_definitions()),10);self.assertEqual(len(self.agent(202).model_definitions()),7)
        with self.assertRaises(LLMError):self.agent().invoke(ToolCall(id='bad',name='study_material',arguments={'operation':'list','user_id':202}),1)

    def test_explanations_use_read_material_in_normal_chat_without_creating_quiz(self):
        material=PracticeStore(202).add_material('Notes',NOTES,'explain')
        model=native.ScriptedModel([native.call('study_material',operation='read',reference=material['reference']),ChatReply('Your notes describe a queue as first-in, first-out. Source: Notes.'),native.call('check_practice_delivery',outcome='conversation')])
        result=self.service(model).respond(202,101,'Explain the queue idea from my notes simply.')
        self.assertIn('first-in',result['text']);self.assertEqual(PracticeStore(202).listing()['jobs'],[])

    def test_telegram_pdf_ingestion_is_private_and_does_not_generate_automatically(self):
        remote=SimpleNamespace(download_to_memory=AsyncMock(side_effect=lambda out:out.write(lms.pdf_fixture())))
        doc=SimpleNamespace(mime_type='application/pdf',file_name='Notes.pdf',file_size=1000,get_file=AsyncMock(return_value=remote))
        message=SimpleNamespace(document=doc,message_id=500,reply_text=AsyncMock())
        update=SimpleNamespace(message=message,effective_user=SimpleNamespace(id=202),effective_chat=SimpleNamespace(id=202,type='private'))
        asyncio.run(handle_pdf(update,None))
        self.assertEqual(len(PracticeStore(202).materials()),1);self.assertEqual(PracticeStore(101).materials(),[])
        self.assertEqual(PracticeStore(202).listing()['jobs'],[]);message.reply_text.assert_awaited()
        update.effective_chat.type='group';message.reply_text.reset_mock();asyncio.run(handle_pdf(update,None));message.reply_text.assert_not_awaited()


class SpontaneousBrightspacePracticeTests(unittest.TestCase):
    def setUp(self):
        from test_lms_native import NativeBrightspaceTests
        NativeBrightspaceTests.setUp(self)
        database.initialize_database(backfill_approvals=False,include_practice=True)
        self.backend.document=NOTES
        feature=patch('services.practice.enabled',return_value=True);feature.start();self.addCleanup(feature.stop)
        self.calendar.disconnect(101)

    connect=native.NativeCalendarTests.connect
    def agent(self,user=101,message=10,**kwargs):
        from test_lms_native import NativeBrightspaceTests
        return NativeBrightspaceTests.agent(self,user,message,**kwargs)
    service=native.NativeCalendarTests.service

    @staticmethod
    def materials(messages):
        data=json.loads(messages[-1]['content'])
        return native.call('brightspace_materials',course_id=data['courses'][0]['course_id'])

    @staticmethod
    def read(messages):
        data=json.loads(messages[-1]['content'])
        source=next(row for row in data['materials'] if row['kind']=='document')
        return native.call('brightspace_read',source_id=source['source_id'])

    @staticmethod
    def prepare(messages):
        data=json.loads(messages[-1]['content'])
        return native.call('prepare_practice',title='Spontaneous revision',topic='Queues',question_count=2,sources=[{'reference':data['citation']}])

    def model(self,repeat=False):
        steps=[native.call('brightspace_courses')]
        if repeat:steps.append(native.call('brightspace_courses'))
        steps += [self.materials,self.read,self.prepare,ChatReply('Your practice is preparing. Open Generated Practice.')]
        return native.ScriptedModel(steps)

    def test_owner_course_read_to_spontaneous_generated_quiz_with_no_calendar_or_plan(self):
        model=self.model()
        result=self.service(model).respond(101,700,"Can you generate a practice quiz to prepare me for the quiz I'll have in w3?")
        self.assertEqual(result['status'],'completed')
        self.assertIn('On-demand practice is available immediately',model.requests[0][0]['content'])
        self.assertIn('not a requested Calendar date',model.requests[0][0]['content'])
        self.assertEqual(self.planner.proposals(101),[])
        self.assertEqual(self.provider.inserts,[])
        self.assertEqual(self.provider.reads,[])
        self.assertTrue(practice.run_one(lambda:Model()))
        listing=PracticeStore(101).listing()
        self.assertEqual(len(listing['sets']),1)
        self.assertEqual(PracticeStore(202).listing()['sets'],[])
        attempt=PracticeStore(101).start(listing['sets'][0]['id'],str(uuid4()))
        self.assertEqual(len(attempt['questions']),2)
        self.assertNotIn('correct_option',json.dumps(attempt))

    def test_single_repeated_course_read_recovers_without_repeating_school_request(self):
        model=self.model(repeat=True)
        result=self.service(model).respond(101,701,'Make me a practice quiz now for w3.')
        self.assertEqual(result['status'],'completed')
        feedback=json.loads(model.requests[2][-1]['content'])
        self.assertTrue(feedback['reused_read']);self.assertIn('Practice does not require a schedule',feedback['guidance'])
        self.assertEqual([name for name,args in self.backend.calls].count('get_my_courses'),1)
        self.assertEqual(len(PracticeStore(101).listing()['jobs']),1)
        self.assertEqual(self.provider.inserts,[])

    def test_repeated_reads_are_still_bounded(self):
        model=native.ScriptedModel([native.call('brightspace_courses') for _ in range(3)])
        with self.assertRaises(LLMError) as error:self.service(model).respond(101,702,'Make a quiz now')
        self.assertEqual(error.exception.code,'tool_loop')
        self.assertEqual([name for name,args in self.backend.calls].count('get_my_courses'),1)
        self.assertEqual(PracticeStore(101).listing()['jobs'],[])

    def test_revocation_blocks_cached_read_recovery(self):
        def revoke(messages):
            with closing(database.get_connection()) as db,db:
                db.execute("UPDATE lms_connections SET status='revoked' WHERE telegram_user_id=101")
            return native.call('brightspace_courses')
        model=native.ScriptedModel([native.call('brightspace_courses'),revoke])
        with self.assertRaises(LLMError):self.service(model).respond(101,703,'Make a quiz now')
        self.assertEqual([name for name,args in self.backend.calls].count('get_my_courses'),1)
        self.assertEqual(PracticeStore(101).listing()['jobs'],[])


    def test_duplicate_prepare_returns_existing_receipt_without_duplicate_job(self):
        saved={}
        def prepare(messages):
            reply=self.prepare(messages);saved['arguments']=reply.tool_calls[0].arguments
            return reply
        model=native.ScriptedModel([native.call('brightspace_courses'),self.materials,self.read,prepare,
            lambda messages:native.call('prepare_practice',**saved['arguments'])])
        result=self.service(model).respond(101,704,'Give me a practice quiz now')
        self.assertEqual(result['status'],'completed');self.assertIn('preparing',result['text'])
        self.assertNotIn('ready.',result['text'])
        self.assertEqual(len(PracticeStore(101).listing()['jobs']),1)
        self.assertEqual(self.planner.proposals(101),[])
        self.assertEqual(self.provider.inserts,[])

    def test_queued_job_receipt_does_not_require_an_extra_scheduling_confirmation(self):
        model=self.model();model.steps[-1]=ChatReply('Please give me the dates and number of study sessions first.')
        result=self.service(model).respond(101,705,'Make a quiz now')
        self.assertIn('preparing',result['text']);self.assertIn('No study schedule is required',result['text'])
        self.assertNotIn('number of study sessions',result['text'])
        self.assertEqual(len(PracticeStore(101).listing()['jobs']),1)


class MiniAppDeliveryTests(unittest.TestCase):
    setUp=NativePracticeTests.setUp
    connect=native.NativeCalendarTests.connect
    agent=NativePracticeTests.agent
    service=NativePracticeTests.service
    INLINE='Practice Quiz. Which ordering? A FIFO B LIFO C random D alphabetical. Answers: 1 A.'

    def material(self,user=202):return PracticeStore(user).add_material('Own queue notes',NOTES,'delivery')

    def test_inline_quiz_is_withheld_and_repaired_to_saved_job_and_mini_app_button(self):
        material=self.material()
        def prepare(messages):
            data=json.loads(messages[-1]['content'])
            return native.call('prepare_practice',title='Queue practice',topic='Queues',question_count=2,sources=[{'reference':data['reference']}])
        class ModelWithChoices(native.ScriptedModel):
            def __init__(self,steps):super().__init__(steps);self.choices=[]
            def complete(self,messages,**kwargs):self.choices.append(kwargs.get('tool_choice'));return super().complete(messages,**kwargs)
        model=ModelWithChoices([ChatReply(self.INLINE),native.call('check_practice_delivery',outcome='practice'),
            native.call('study_material',operation='read',reference=material['reference']),prepare,ChatReply('Here are the answers: A A')])
        service=self.service(model)
        result=service.respond(202,800,'Can you test me on those notes?')
        self.assertEqual(result['status'],'completed');self.assertIn('preparing',result['text'])
        self.assertNotIn('Answers:',result['text']);self.assertNotIn(self.INLINE,json.dumps(service.history(202)))
        self.assertIsNone(model.choices[2])  # Recovery stays in automatic tool mode; saved-job checks enforce completion.
        self.assertEqual(model.choices[1],{'type':'function','function':{'name':'check_practice_delivery'}})
        self.assertEqual(len(PracticeStore(202).listing()['jobs']),1)
        from services.conversation_telegram import present_tools
        message=SimpleNamespace(reply_text=AsyncMock())
        with patch('deployment.load_deployment',return_value=SimpleNamespace(mini_app_url='https://study.example')):
            asyncio.run(present_tools(message,202,800))
        button=message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.web_app.url,'https://study.example/practice')
        self.assertEqual(PracticeStore(101).listing()['jobs'],[])
        self.assertEqual(self.provider.inserts,[])
        before=len(model.requests)
        self.assertTrue(service.respond(202,800,'Can you test me on those notes?')['cached'])
        self.assertEqual(len(model.requests),before)

    def test_normal_explanation_remains_conversational(self):
        model=native.ScriptedModel([ChatReply('A queue processes the first entry first.'),native.call('check_practice_delivery',outcome='conversation')])
        result=self.service(model).respond(202,801,'What does FIFO mean?')
        self.assertIn('first entry',result['text']);self.assertEqual(PracticeStore(202).listing()['jobs'],[])

    def test_real_missing_material_gets_only_source_clarification(self):
        question='Which notes or PDF would you like to practise from?'
        model=native.ScriptedModel([ChatReply(self.INLINE),native.call('check_practice_delivery',outcome='clarification',clarification=question)])
        result=self.service(model).respond(202,802,'Give me some practice.')
        self.assertEqual(result['text'],question)
        self.assertNotIn('Answers:',result['text']);self.assertEqual(PracticeStore(202).listing()['jobs'],[])

    def test_ignored_delivery_requirement_never_sends_prose_quiz_or_key(self):
        model=native.ScriptedModel([ChatReply(self.INLINE),native.call('check_practice_delivery',outcome='practice'),ChatReply(self.INLINE)])
        service=self.service(model)
        with self.assertRaises(LLMError) as error:service.respond(202,803,'Give me a quiz from my notes')
        self.assertEqual(error.exception.code,'practice_delivery');self.assertEqual(service.history(202),[])
        self.assertEqual(PracticeStore(202).listing()['jobs'],[])
        self.assertEqual(len(model.requests),3)

    def test_malformed_delivery_check_fails_closed(self):
        for index,decision in enumerate([ChatReply('This is a quiz'),native.call('check_practice_delivery',outcome='clarification'),
                         native.call('check_practice_delivery',outcome='conversation',user_id=101)]):
            model=native.ScriptedModel([ChatReply(self.INLINE),decision])
            with self.subTest(decision=decision):
                service=self.service(model)
                with self.assertRaises(LLMError):service.respond(202,810+index,str(decision))
                self.assertEqual(service.history(202),[])

    def test_about_a_school_quiz_is_not_a_new_generation_request(self):
        model=native.ScriptedModel([ChatReply('The date is not stated in the supplied notes.'),native.call('check_practice_delivery',outcome='conversation')])
        result=self.service(model).respond(202,820,'When is the w3 course quiz?')
        self.assertIn('not stated',result['text']);self.assertEqual(PracticeStore(202).listing()['jobs'],[])

    def test_checker_uses_actual_native_gateway_contract_without_network(self):
        import httpx
        from services.practice_delivery import assess
        sent=[]
        def wire(request):
            if request.url.host=='iam.cloud.ibm.com':return httpx.Response(200,json={'access_token':'synthetic','expires_in':3600})
            body=json.loads(request.content);sent.append(body)
            self.assertEqual(body['tool_choice'],'required')
            self.assertEqual(len(body['tools']),1)
            self.assertFalse(body['tools'][0]['function']['parameters']['additionalProperties'])
            raw={'id':'delivery','type':'function','function':{'name':'check_practice_delivery','arguments':json.dumps({'outcome':'practice','clarification':'','clarification_kind':'none'})}}
            return native.NativeProviderTests.response(calls=[raw],reason='tool_calls')
        with httpx.Client(transport=httpx.MockTransport(wire)) as client:
            provider=native.OrchestrateNativeProvider(native.CONFIG,client)
            outcome=assess(provider,[{'role':'system','content':'private system context'},
                {'role':'user','content':'Test me on those notes.'}],self.INLINE)
        self.assertEqual(outcome.outcome,'practice')
        payload=json.dumps(sent)
        self.assertNotIn('private system context',payload);self.assertNotIn('user_id',payload)
