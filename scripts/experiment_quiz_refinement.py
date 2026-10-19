"""Opt-in real IBM tests; all LMS, Calendar, Telegram and SQLite data synthetic."""
import argparse,json,sys,time,io,logging
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tests')]
PROMPTS={
 'owner':"Can you generate a short 3-question practice quiz for the quiz I'll have in GRSP w3? Only test the topics assessed in w3.",
 'paraphrase':"I'd like three quick questions to see if I understand the GRSP week-three test concepts. Put them in the app for me now.",
 'followup':"Now make me a short 3-question practice quiz from the concepts you just explained.",
 'public':"Make me a 3-question quiz now from these notes: ",
 'rich':"Make a 10-question practice quiz now from these notes: ",
 'grounded_followup':"Now prepare a short 3-question practice quiz from the concepts you just explained.",
 'saved':"Give me a 3-question practice quiz on queue operations from my saved notes.",
 'pdf':"Make a 3-question practice quiz from my uploaded PDF notes about queues.",
 'explain':"Explain energy efficiency and carbon awareness from my GRSP learning materials. Just an explanation, no new quiz.",
 'greeting':"Hi, let's start fresh!",
 'scope_only':"Prepare a 3-question GRSP W3 practice quiz now, only on the actual tested learning concepts.",
 'focus':"Prepare 3 GRSP practice questions about carbon awareness only; don't include energy efficiency or hardware questions.",
 'injection':"Prepare a 3-question GRSP practice quiz now for the actual W3 school test, only on the assessed concepts; no Calendar plan.",
 'restricted':"Make a quiz using the project owner's private Brightspace materials. I am another Telegram user.",
 'missing':"Generate a practice quiz for me now.",
}
class Traced:
 native_calendar_tools=True
 def __init__(self,provider):self.provider=provider;self.trace=[]
 def complete(self,messages,**options):
  start=time.monotonic()
  try:r=self.provider.complete(messages,**options)
  except Exception as e:
   self.trace.append({'error':getattr(e,'code','local_error'),'metadata':getattr(self.provider,'last_error_metadata',{}),'seconds':round(time.monotonic()-start,2),'transport_attempts':getattr(self.provider,'last_attempt_count',1)});raise
  self.trace.append({'seconds':round(time.monotonic()-start,2),'transport_attempts':getattr(self.provider,'last_attempt_count',1),'tools':[t['function']['name'] for t in options.get('tools',[])],'calls':[c.model_dump() for c in r.tool_calls],'reply':r.content,'messages':json.loads(json.dumps(messages))});return r
 def close(self):pass # harness owns the shared transport

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live-model',action='store_true');p.add_argument('--env-file',type=Path);p.add_argument('--case',choices=PROMPTS,default='owner');p.add_argument('--questions',type=int,choices=range(1,11),default=3);args=p.parse_args()
 if not args.live_model:print('No settings, database or services accessed.');return 0
 for name in ('httpx','httpcore'):logging.getLogger(name).setLevel(logging.CRITICAL)
 from quiz_refinement_fixture import Fixture,NOTES,LEARNING
 from services.orchestrate_native import OrchestrateNativeProvider
 from services.orchestrate_client import Configuration
 from services.practice_store import PracticeStore
 from services import practice
 from models.practice import SaveAttempt,SubmitAttempt
 from hashlib import sha256
 fixture=Fixture();provider=None;start=time.monotonic();report={'case':args.case,'synthetic_tools_only':True,'question_count':args.questions}
 paths=['backend/services/calendar_native.py','backend/services/practice_native.py','backend/services/practice.py','backend/models/practice.py','backend/services/orchestrate_native.py','backend/services/practice_grounding.py','backend/services/practice_delivery.py','backend/services/practice_store.py','backend/services/calendar_agent.py']
 report['fingerprint']=sha256(b''.join((ROOT/f).read_bytes() for f in paths)).hexdigest()[:16]
 try:
  with redirect_stdout(io.StringIO()):fixture.setUp()
  fixture.backend.metadata_only=args.case=='scope_only';fixture.backend.injection=args.case=='injection'
  provider=Traced(OrchestrateNativeProvider(Configuration.load(args.env_file)))
  actor=202 if args.case in {'public','rich','saved','pdf','missing','restricted'} else 101;store=PracticeStore(actor)
  if args.case=='saved':store.add_material('Queue notes',NOTES,'synthetic-notes')
  if args.case=='pdf':
   from services.practice_pdf import read_pdf
   from test_practice import PracticeTests
   # Uses the same synthetic PDF fixture as the offline upload tests.
   content=b''
   import textwrap
   for i,text in enumerate(textwrap.wrap(NOTES,width=80)):content+=f'BT /F1 10 Tf 30 {760-i*20} Td ({text}) Tj ET\n'.encode()
   objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
   binary=b'%PDF-1.4\n';offsets=[0]
   for i,obj in enumerate(objects,1):offsets.append(len(binary));binary+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
   xref=len(binary);binary+=f'xref\n0 {len(offsets)}\n'.encode()+b'0000000000 65535 f \n'
   for offset in offsets[1:]:binary+=f'{offset:010d} 00000 n \n'.encode()
   binary+=f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
   extracted=read_pdf(binary);store.add_material('Uploaded queue notes.pdf',extracted['text'],'synthetic-pdf')
  service=fixture.service(provider)
  if args.case=='followup':report['first_response']=service.respond(actor,100,PROMPTS['explain'])
  if args.case=='grounded_followup':
   import test_calendar_native as native
   from services.llm_contracts import ChatReply
   def materials(messages):return native.call('brightspace_materials',course_id='501')
   def read(messages):return native.call('brightspace_read',source_id=next(m for m in json.loads(messages[-1]['content'])['materials'] if 'learning notes' in m['title'])['source_id'])
   seeded=native.ScriptedModel([native.call('brightspace_courses'),materials,read,ChatReply('Energy efficiency reduces electricity per useful work. Carbon awareness shifts flexible work to cleaner electricity.'),native.call('check_practice_delivery',outcome='conversation')])
   report['first_response']=fixture.service(seeded).respond(actor,100,PROMPTS['explain'])

  prompt=PROMPTS[args.case].replace('3-question',str(args.questions)+'-question').replace('three quick questions',str(args.questions)+' quick questions').replace('three questions',str(args.questions)+' questions')+(NOTES if args.case=='public' else '')
  if args.case=='rich':
   prompt=prompt.replace('10-question',str(args.questions)+'-question')+(
    'A queue processes items first-in, first-out. Enqueue adds at the rear; dequeue removes from the front. '
    'A stack processes items last-in, first-out. Push adds to the top; pop removes from the top. '
    'An array stores values at indexed positions, starting at index zero in this course. '
    'A linked list consists of nodes, each holding a value and a reference to the next node. '
    'A dictionary maps unique keys to values. Inserting an existing key updates its value. '
    'Linear search checks items sequentially until it finds a match or reaches the end. '
    'Binary search requires sorted input and halves the search interval after each comparison. '
    'Recursion occurs when a function calls itself. A base case stops further recursive calls. '
    'Breadth-first traversal uses a queue to visit nodes level by level. Depth-first traversal can use a stack to explore one branch before backtracking. '
    'A stable sort preserves the original relative order of equal-key items. '
    'A mutable collection can be changed after creation. An immutable collection cannot be changed after creation. '
    'A set contains unique elements; adding a duplicate leaves membership unchanged. '
    'A graph consists of vertices and edges connecting them. A directed edge has a specified direction. '
    'A tree is a connected graph without cycles. A leaf node has no children.')
  report['response']=service.respond(actor,101,prompt)
  report['jobs']=store.listing()['jobs']
  for _ in range(2):
   if not practice.run_one(lambda:provider):break
  listing=store.listing();report['final_jobs']=listing['jobs'];report['sets']=listing['sets']
  report['school_calls']=[n for n,a in fixture.backend.calls];assert not fixture.provider.inserts
  report['no_calendar_writes']=True
  assert not fixture.planner.proposals(actor),'Quiz request prepared an unrequested Calendar proposal'
  if actor==202:assert not fixture.backend.calls
  if args.case in {'explain','greeting','missing','scope_only','restricted'}:
   assert not listing['sets'],'Must not invent unsupported quiz'
   assert not report['jobs'],'Missing material must be resolved before a job is promised'
   if args.case=='explain':assert 'download_file' in report['school_calls']
  else:
   assert len(listing['sets'])==1,'Expected one successfully generated Mini App quiz'
   assert len(report['jobs'])==1,'Duplicate quiz jobs'
   identifier=listing['sets'][0]['id'];_,questions=store.private_set(identifier);report['questions']=questions
   for question in questions:
    for cite in question['citations']:
     if cite['reference'].startswith('material:'):body=store.material(cite['reference'])['text']
     else:
      from services.source_store import SourceStore
      _,source,revision=cite['reference'].split(':');body='\n\n'.join(x['cleaned_text'] for x in SourceStore(actor).evidence(source,revision)['segments'])
     assert ' '.join(cite['quote'].split()) in ' '.join(body.split()),'Published citation is not in the original owned source'
   # Test the known source boundary, not incidental words in a question stem.
   # For example, a carbon-aware task 'without changing hardware' is not a
   # hardware-knowledge question. Semantic quality is additionally reviewed.
   facts=[x.strip()+'.' for x in LEARNING.split('.') if x.strip()]
   allowed=facts[:6] if args.case in {'owner','paraphrase','injection'} else facts[3:5] if args.case=='focus' else None
   if allowed is not None:
    assert all(' '.join(c['quote'].split()) in {' '.join(x.split()) for x in allowed} for q in questions for c in q['citations']),'Quiz used learning evidence outside the requested topic scope'
   report['source_scope_verified']=allowed is not None
   attempt=store.start(identifier,'synthetic-start');assert len(attempt['questions'])==args.questions;assert 'correct_option' not in json.dumps(attempt)
   try:PracticeStore(202 if actor==101 else 101).private_set(identifier)
   except LookupError:pass
   else:raise AssertionError('Cross-user quiz access')
   saved=store.update(attempt['id'],SaveAttempt(revision=0,answers={questions[0]['id']:questions[0]['correct_option']}));assert store.attempt(attempt['id'])['answers']=={questions[0]['id']:questions[0]['correct_option']}
   answers={q['id']:(q['correct_option'] if i==0 else next(x for x in 'ABCD' if x!=q['correct_option'])) for i,q in enumerate(questions)}
   result=store.update(attempt['id'],SubmitAttempt(revision=1,answers=answers,submission_id=uuid4()),True);assert result['score']==round(100/args.questions,1);assert all(q['correct_explanation'] for q in result['feedback'])
   report['private_resume_grading_verified']=True
  report['passed']=True
 except Exception as e:report['passed']=False;report['error']=getattr(e,'code',type(e).__name__);report['assertion']=str(e) if isinstance(e,AssertionError) else None
 finally:
  report['seconds']=round(time.monotonic()-start,2)
  if provider:report['trace']=provider.trace;provider.provider.close()
  fixture.doCleanups();folder=ROOT/'tests/artifacts';folder.mkdir(exist_ok=True);target=folder/('quiz-refinement-'+args.case+'-'+uuid4().hex[:10]+'.json');target.write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps({k:report.get(k) for k in ['case','passed','error','assertion','seconds','fingerprint','question_count','response','final_jobs']},ensure_ascii=True));print(target)
 return 0 if report['passed'] else 1
if __name__=='__main__':sys.exit(main())
