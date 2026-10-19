"""Focused regression contracts for source-grounded spontaneous practice."""
from contextlib import closing
import json
import unittest
from unittest.mock import patch
import httpx
import test_api
import database
import test_calendar_native as native
import test_practice as prior
from quiz_refinement_fixture import Fixture,NOTES
from services.practice_store import PracticeStore
from services.practice_native import operation
from services import practice
from services.llm_contracts import LLMError,ChatReply

class GroundingTests(unittest.TestCase):
 setUp=prior.PracticeTests.setUp
 request=prior.PracticeTests.request
 def test_original_request_reaches_private_worker_even_when_model_topic_is_broad(self):
  request=self.request();request['requirements']='MODEL OVERRIDE'
  actual='Only queue operations; no stack questions, and use 2 MCQs.'
  result=operation(101,991,actual,'prepare_practice',request)
  with closing(database.get_connection()) as db:
   saved=json.loads(db.execute('SELECT request_json FROM practice_jobs WHERE id=?',(result['practice_job_id'],)).fetchone()[0])
  self.assertEqual(saved['requirements'],actual)
  model=prior.Model();practice.run_one(lambda:model)
  payload=next(json.loads(messages[-1]['content']) for messages,options in model.calls if options['tools'][0]['function']['name']=='deliver_practice')
  self.assertEqual(payload['original_requirements'],actual)
  self.assertNotIn('correct_option',json.dumps(result))

class PreparationTests(Fixture):
 def test_saved_job_needs_no_post_preparation_model_call(self):
  material=PracticeStore(101).add_material('Queue notes',NOTES,'one')
  model=native.ScriptedModel([native.call('prepare_practice',title='Queue practice',topic='Queues',question_count=3,sources=[{'reference':material['reference']}])])
  reply=self.service(model).respond(101,992,'Make 3 queue questions now')
  self.assertIn('preparing',reply['text']);self.assertEqual(len(model.requests),3)
  self.assertEqual(len(PracticeStore(101).listing()['jobs']),1)
  self.assertEqual(self.provider.inserts,[])

class ChoiceTests(unittest.TestCase):
 def test_named_tool_is_represented_as_singleton_required_without_expanding_authority(self):
  from services.orchestrate_native import OrchestrateNativeProvider
  from services.llm_contracts import ToolRegistry
  from services.practice_delivery import DeliveryDecision
  registry=ToolRegistry();registry.register('check_practice_delivery','Check delivery',DeliveryDecision)
  registry.register('unrelated','Never offered',DeliveryDecision);calls=[]
  def transport(request):
   if request.url.host=='iam.cloud.ibm.com':return httpx.Response(200,json={'access_token':'synthetic','expires_in':3600})
   body=json.loads(request.content);calls.append(body)
   self.assertEqual(body['tool_choice'],'required')
   self.assertEqual([t['function']['name'] for t in body['tools']],['check_practice_delivery'])
   raw={'id':'decision','type':'function','function':{'name':'check_practice_delivery','arguments':json.dumps({'outcome':'conversation','clarification_kind':'none'})}}
   return native.NativeProviderTests.response(calls=[raw],reason='tool_calls')
  with httpx.Client(transport=httpx.MockTransport(transport)) as client:
   provider=OrchestrateNativeProvider(native.CONFIG,client)
   reply=provider.complete([{'role':'system','content':'Check delivery'},{'role':'user','content':'Hello'}],tools=registry.definitions(),tool_choice={'type':'function','function':{'name':'check_practice_delivery'}})
   self.assertEqual(reply.tool_calls[0].name,'check_practice_delivery')
   for choice in [{'type':'function','function':{'name':'unknown'}},{'type':'function','function':{'name':'check_practice_delivery','user_id':202}}]:
    with self.assertRaises(LLMError):provider.complete([{'role':'system','content':'Check delivery'},{'role':'user','content':'Hello'}],tools=registry.definitions(),tool_choice=choice)
  self.assertEqual(len(calls),1)

class SourceQualificationTests(unittest.TestCase):
 def evidence(self):return [{'reference':'material:one','title':'Learning notes','text':NOTES,'offset':0,'partial':False,'warnings':[]}]
 def model(self,**changes):
  data={'learning_passages':['passage_1'],'assessment_passages':[],'focus':'Queue ordering','mode':'concept_practice'};data.update(changes)
  class Response:
   def complete(self,*args,**kwargs):
    if kwargs['tools'][0]['function']['name']=='classify_practice_scope':return native.call('classify_practice_scope',mode=data['mode'])
    return native.call('qualify_practice_sources',**{k:v for k,v in data.items() if k!='mode'})
  return Response()
 def test_only_verified_learning_quotes_are_frozen_for_generation(self):
  from services.practice_grounding import qualify
  result,missing=qualify(self.model(),{'topic':'Queues'},self.evidence())
  self.assertEqual(missing,'');self.assertEqual(result[0]['text'].strip(),'A queue processes entries first-in, first-out.')
  self.assertTrue(result[0]['quiz_qualified']);self.assertNotIn('stack',result[0]['text'])
 def test_foreign_or_invented_quotes_cannot_qualify(self):
  from services.practice_grounding import qualify
  for passage in ['material:other','passage_999']:
   with self.assertRaises(ValueError):qualify(self.model(learning_passages=[passage]),{'topic':'Queues'},self.evidence())
 def test_missing_learning_or_assessment_scope_returns_feedback_instead_of_content(self):
  from services.practice_grounding import qualify
  selected,missing=qualify(self.model(mode='assessment_practice'),{'topic':'Queues'},self.evidence())
  self.assertIsNone(selected);self.assertIn('teaching plan',missing)
  selected,missing=qualify(self.model(learning_passages=[]),{'topic':'Queues'},self.evidence())
  self.assertIsNone(selected);self.assertTrue(missing)
 def test_uncertain_source_check_is_never_replayed(self):
  from services.practice_grounding import qualify
  from unittest.mock import Mock
  model=Mock();model.complete.side_effect=LLMError('inference_uncertain')
  with self.assertRaises(LLMError):qualify(model,{'topic':'Queues'},self.evidence())
  self.assertEqual(model.complete.call_count,1)

class SourcePreparationTests(unittest.TestCase):
 setUp=prior.PracticeTests.setUp
 request=prior.PracticeTests.request
 def test_missing_evidence_creates_no_job_and_original_sources_stay_owned(self):
  from services.practice_grounding import qualify
  with patch('services.practice_grounding.qualify',return_value=(None,'Read learning notes.')):
   result=operation(101,993,'Make a quiz','prepare_practice',self.request(),provider=object())
  self.assertEqual(result['error_code'],'practice_sources_incomplete');self.assertEqual(self.store.listing()['jobs'],[])
  self.assertEqual(self.otherstore.materials(),[])
 def test_uncertain_preflight_does_not_turn_into_retryable_material_error(self):
  with patch('services.practice_grounding.qualify',side_effect=LLMError('inference_uncertain')):
   with self.assertRaises(LLMError):operation(101,994,'Make a quiz','prepare_practice',self.request(),provider=object())
  self.assertEqual(self.store.listing()['jobs'],[])

class ReviewAndStorageTests(unittest.TestCase):
 setUp=prior.PracticeTests.setUp
 request=prior.PracticeTests.request
 enqueue=prior.PracticeTests.enqueue
 def test_old_request_json_is_idempotent_after_optional_fields_are_added(self):
  request=self.request();job=practice.request_practice(101,request,'old-version')
  with closing(database.get_connection()) as db,db:
   raw=json.loads(db.execute('SELECT request_json FROM practice_jobs WHERE id=?',(job['id'],)).fetchone()[0])
   raw.pop('requirements');raw.pop('assessment_sources')
   db.execute('UPDATE practice_jobs SET request_json=? WHERE id=?',(json.dumps(raw),job['id']))
  retry=practice.request_practice(101,request,'old-version')
  self.assertEqual(retry['id'],job['id']);self.assertEqual(len(self.store.listing()['jobs']),1)
 def test_reviewer_correction_is_bounded_and_publishes_only_the_corrected_batch(self):
  class Reviewed(prior.Model):
   def __init__(self,always=False):super().__init__();self.reviews=0;self.always=always
   def complete(self,messages,**options):
    if options['tools'][0]['function']['name']=='check_practice_content':
     self.calls.append((messages,options));self.reviews+=1
     return native.call('check_practice_content',issues=['Question 1 tests administration; replace with a learning concept.'] if self.reviews==1 or self.always else [])
    if options['tools'][0]['function']['name']=='deliver_practice':
     payload=json.loads(messages[-1]['content'])
     if 'previous_draft' in payload:
      from services.practice_grounding import DraftBatch
      DraftBatch.model_validate({'questions':payload['previous_draft']})
    return super().complete(messages,**options)
  job=self.enqueue();model=Reviewed();practice.run_one(lambda:model)
  self.assertEqual(self.store.job(job['id'])['status'],'ready');self.assertEqual(model.reviews,2)
  drafts=[json.loads(messages[-1]['content']) for messages,options in model.calls if options['tools'][0]['function']['name']=='deliver_practice']
  self.assertEqual(len(drafts),2);self.assertTrue(drafts[1]['corrections'])
  job=self.enqueue();model=Reviewed(always=True);practice.run_one(lambda:model)
  self.assertEqual(self.store.job(job['id'])['status'],'failed');self.assertEqual(model.reviews,2)
  self.assertEqual(len(self.store.listing()['sets']),1)
 def test_uncertain_review_never_publishes_or_regenerates(self):
  class Uncertain(prior.Model):
   def complete(self,messages,**options):
    if options['tools'][0]['function']['name']=='check_practice_content':
     self.calls.append((messages,options));raise LLMError('inference_uncertain')
    return super().complete(messages,**options)
  job=self.enqueue();model=Uncertain();practice.run_one(lambda:model)
  self.assertEqual(self.store.job(job['id'])['error_code'],'generation_uncertain')
  self.assertEqual(len(model.calls),4);self.assertEqual(self.store.listing()['sets'],[])

class ChoiceMappingTests(unittest.TestCase):
 def test_shuffle_preserves_correct_answer_and_all_explanations(self):
  q={'options':['alpha','beta','gamma','delta'],'correct_option':'B','explanations':dict(zip('ABCD',['why alpha','why beta','why gamma','why delta']))}
  with patch('services.practice.random.shuffle',side_effect=lambda values:values.reverse()):practice.label_choices(q)
  self.assertEqual(q['correct_option'],'C');self.assertEqual(q['options'][2]['text'],'beta')
  self.assertEqual(q['explanations']['C'],'why beta');self.assertEqual(q['explanations']['A'],'why delta')

class GatewayFormatTests(unittest.TestCase):
 def provider(self,transport):
  from services.orchestrate_native import OrchestrateNativeProvider
  client=httpx.Client(transport=httpx.MockTransport(transport));self.addCleanup(client.close)
  return OrchestrateNativeProvider(native.CONFIG,client)
 def test_only_explicit_tool_format_rejection_gets_one_correction(self):
  from services.llm_contracts import ToolRegistry
  from services.practice_delivery import DeliveryDecision
  registry=ToolRegistry();registry.register('decision','Private decision',DeliveryDecision);requests=[]
  def transport(request):
   if request.url.host=='iam.cloud.ibm.com':return httpx.Response(200,json={'access_token':'synthetic','expires_in':3600})
   requests.append(json.loads(request.content))
   if len(requests)==1:return httpx.Response(400,json={'error':{'code':'tool_use_failed','message':'invalid JSON'}})
   return native.NativeProviderTests.response(calls=[{'id':'ok','type':'function','function':{'name':'decision','arguments':'{"outcome":"conversation","clarification_kind":"none"}'}}],reason='tool_calls')
  model=self.provider(transport);reply=model.complete([{'role':'system','content':'Decide'},{'role':'user','content':'hello'}],tools=registry.definitions())
  self.assertEqual(len(requests),2);self.assertIn('FORMAT CORRECTION',requests[1]['messages'][0]['content']);self.assertEqual(reply.tool_calls[0].name,'decision')
 def test_transport_uncertainty_is_not_retried(self):
  calls=[]
  def transport(request):
   if request.url.host=='iam.cloud.ibm.com':return httpx.Response(200,json={'access_token':'synthetic','expires_in':3600})
   calls.append(1);raise httpx.ReadTimeout('synthetic timeout')
  model=self.provider(transport)
  with self.assertRaises(LLMError) as error:model.complete([{'role':'system','content':'Reply'},{'role':'user','content':'hello'}])
  self.assertEqual(error.exception.code,'inference_uncertain');self.assertEqual(len(calls),1)

class AdditionalBoundaryTests(unittest.TestCase):
 setUp=prior.PracticeTests.setUp
 request=prior.PracticeTests.request
 def test_assessment_sources_enforce_same_user_boundary_as_learning_sources(self):
  other=self.otherstore.add_material('Other private plan',NOTES,'other-private-plan')
  request=self.request();request['assessment_sources']=[{'reference':other['reference']}]
  with self.assertRaises(LookupError):practice.request_practice(101,request,'forged-assessment')
  self.assertEqual(self.store.listing()['jobs'],[])
 def test_assessment_scope_and_learning_evidence_are_frozen_together(self):
  plan=self.store.add_material('Test scope','The test covers queue operations only. Stack operations are excluded.','test-scope')
  request=self.request();request['assessment_sources']=[{'reference':plan['reference']}]
  job=practice.request_practice(101,request,'with-scope')
  with closing(database.get_connection()) as db:
   evidence=json.loads(db.execute('SELECT evidence_json FROM practice_jobs WHERE id=?',(job['id'],)).fetchone()[0])
  self.assertEqual({e['reference'] for e in evidence},{self.material['reference'],plan['reference']})

class NativeUncertaintyTests(Fixture):
 def test_qualification_uncertainty_stops_native_turn_without_calendar_error_or_job(self):
  material=PracticeStore(101).add_material('Notes',NOTES,'uncertain')
  class Model(native.ScriptedModel):
   def complete(self,messages,**kwargs):
    if kwargs['tools'][0]['function']['name']=='classify_practice_scope':raise LLMError('inference_uncertain')
    return super().complete(messages,**kwargs)
  model=Model([native.call('prepare_practice',title='Queues',topic='Queues',sources=[{'reference':material['reference']}])])
  with self.assertRaises(LLMError) as error:self.service(model).respond(101,995,'Make a quiz from my notes')
  self.assertEqual(error.exception.code,'inference_uncertain');self.assertEqual(PracticeStore(101).listing()['jobs'],[])
  self.assertEqual(self.provider.inserts,[])

class FollowupSourceContextTests(Fixture):
 def test_recent_source_handles_are_owned_reverified_and_removed_after_clear(self):
  def materials(messages):return native.call('brightspace_materials',course_id=json.loads(messages[-1]['content'])['courses'][0]['course_id'])
  def read(messages):
   source=next(m for m in json.loads(messages[-1]['content'])['materials'] if 'learning notes' in m['title'])
   return native.call('brightspace_read',source_id=source['source_id'])
  model=native.ScriptedModel([native.call('brightspace_courses'),materials,read,ChatReply('Energy efficiency reduces electricity per useful unit of work.'),native.call('check_practice_delivery',outcome='conversation')])
  service=self.service(model);service.respond(101,1100,'Explain the GRSP learning material')
  handles=self.agent(message=1101).recent_learning_sources()
  self.assertEqual(len(handles),1);self.assertTrue(handles[0]['citation'].startswith('brightspace:'))
  self.assertNotIn('text',handles[0]);self.assertEqual(self.agent(user=202,message=1101).recent_learning_sources(),[])
  with closing(database.get_connection()) as db,db:db.execute("UPDATE lms_connections SET status='disconnected' WHERE telegram_user_id=101")
  self.assertEqual(self.agent(message=1101).recent_learning_sources(),[])
  service.clear(101,1102);self.assertEqual(self.agent(message=1103).recent_learning_sources(),[])

class ReviewPayloadTests(unittest.TestCase):
 def test_review_uses_meanings_and_correctness_instead_of_display_letter_keys(self):
  from services.practice_grounding import review
  from unittest.mock import Mock
  model=Mock();model.complete.return_value=native.call('check_practice_content',issues=[])
  q={'text':'Which ordering is a queue?','options':['FIFO','LIFO','Alphabetic','Random'],'correct_option':'A','explanations':dict(zip('ABCD',['Queue order explained','Stack order explained','Alphabetic is not FIFO','Random is not FIFO'])),'citations':[]}
  review(model,{'topic':'Queues'},[],[q])
  payload=json.loads(model.complete.call_args.args[0][-1]['content'])
  checked=payload['questions'][0]
  self.assertNotIn('correct_option',checked);self.assertEqual(checked['choices'][0]['correct'],True)
  self.assertEqual(checked['choices'][0]['explanation'],'Queue order explained')

class PassageBindingTests(unittest.TestCase):
 setUp=prior.PracticeTests.setUp
 request=prior.PracticeTests.request
 enqueue=prior.PracticeTests.enqueue
 def test_citations_are_attached_from_verified_passages_not_model_supplied_quotes(self):
  job=self.enqueue();model=prior.Model();practice.run_one(lambda:model)
  identifier=self.store.job(job['id'])['set_id'];self.assertTrue(identifier)
  _,questions=self.store.private_set(identifier)
  for q in questions:
   for cite in q['citations']:
    self.assertEqual(cite['reference'],self.material['reference'])
    self.assertEqual(cite['quote'],'A queue processes entries in first-in, first-out order.');self.assertEqual(cite['title'],'Synthetic data structures')
  draft=next(options['tools'][0] for messages,options in model.calls if options['tools'][0]['function']['name']=='deliver_practice')
  self.assertEqual(set(draft['function']['parameters']['$defs']['PassageReference']['properties']),{'reference'})
 def test_unknown_passage_handle_cannot_publish_a_quiz(self):
  class Forged(prior.Model):
   def complete(self,messages,**options):
    reply=super().complete(messages,**options)
    if options['tools'][0]['function']['name']=='deliver_practice':reply.tool_calls[0].arguments['questions'][0]['citations']=[{'reference':'passage_999'}]
    return reply
  job=self.enqueue();practice.run_one(lambda:Forged())
  self.assertEqual(self.store.job(job['id'])['status'],'failed');self.assertEqual(self.store.listing()['sets'],[])
 def test_qualification_retains_lms_authorization_requirement_when_only_own_notes_are_selected(self):
  from services.practice_grounding import qualify
  evidence=[{'reference':'material:own','title':'Own notes','text':NOTES,'offset':0,'warnings':[]},
   {'reference':'brightspace:synthetic:revision','title':'Scope','text':'The test covers queue operations only.','offset':0,'warnings':[]}]
  class Model:
   def complete(self,messages,**options):
    if options['tools'][0]['function']['name']=='classify_practice_scope':return native.call('classify_practice_scope',mode='assessment_practice')
    evidence=json.loads(messages[-1]['content'])['evidence']
    return native.call('qualify_practice_sources',learning_passages=[evidence[0]['reference']],assessment_passages=[next(item['reference'] for item in evidence if item['title']=='Scope')],focus='Queue operations')
  selected,missing=qualify(Model(),{'topic':'Queue operations'},evidence)
  self.assertFalse(missing);self.assertEqual(len(selected),1);self.assertEqual(selected[0]['reference'],'material:own')
  self.assertTrue(selected[0]['lms_authorization_required'])

class ClarificationReceiptTests(unittest.TestCase):
 def assess(self,kind,access):
  from services.practice_delivery import assess
  from unittest.mock import Mock
  model=Mock();model.complete.return_value=native.call('check_practice_delivery',outcome='clarification',clarification_kind=kind,clarification='Please provide the missing information.')
  return assess(model,[{'role':'user','content':'Make a practice quiz'}],'Please provide notes.',[],access)
 def test_missing_material_claim_cannot_skip_an_available_untried_library(self):
  result=self.assess('material',{'brightspace_available':True,'brightspace_attempted':False})
  self.assertEqual(result.outcome,'practice')
  result=self.assess('material',{'saved_notes_or_pdfs_available':True})
  self.assertEqual(result.outcome,'practice')
 def test_genuinely_missing_topic_or_unavailable_material_can_still_be_clarified(self):
  self.assertEqual(self.assess('topic',{'brightspace_available':True}).outcome,'clarification')
  self.assertEqual(self.assess('material',{'brightspace_available':False}).outcome,'clarification')
  self.assertEqual(self.assess('material',{'brightspace_available':True,'brightspace_attempted':True}).outcome,'clarification')

class TextSpanTests(unittest.TestCase):
 def test_passages_are_bounded_actual_source_spans_without_semantic_keyword_rules(self):
  from services.practice_grounding import bounded_passages
  text='A short heading.\n'+'A supported synthetic explanation with several words. '*30
  spans=list(bounded_passages(text));self.assertTrue(spans)
  self.assertTrue(all(15<=len(piece)<=600 and piece in text for piece in spans))

class FeedbackFormatTests(unittest.TestCase):
 setUp=prior.PracticeTests.setUp
 request=prior.PracticeTests.request
 enqueue=prior.PracticeTests.enqueue
 def test_display_letter_references_cannot_be_published_after_shuffling(self):
  class LetterFeedback(prior.Model):
   def complete(self,messages,**options):
    reply=super().complete(messages,**options)
    if options['tools'][0]['function']['name']=='deliver_practice':reply.tool_calls[0].arguments['questions'][0]['explanations']['A']='This matches option A and is correct.'
    return reply
  job=self.enqueue();practice.run_one(lambda:LetterFeedback())
  self.assertEqual(self.store.job(job['id'])['status'],'failed');self.assertEqual(self.store.listing()['sets'],[])
 def test_letter_free_correction_can_publish_without_replaying_the_job(self):
  class Corrected(prior.Model):
   def __init__(self):super().__init__();self.drafts=0
   def complete(self,messages,**options):
    reply=super().complete(messages,**options)
    if options['tools'][0]['function']['name']=='deliver_practice':
     self.drafts+=1
     if self.drafts==1:reply.tool_calls[0].arguments['questions'][0]['explanations']['A']='This matches option A and is correct.'
    return reply
  job=self.enqueue();model=Corrected();practice.run_one(lambda:model)
  self.assertEqual(model.drafts,2);self.assertEqual(self.store.job(job['id'])['status'],'ready')
  self.assertEqual(len(self.store.listing()['sets']),1)
