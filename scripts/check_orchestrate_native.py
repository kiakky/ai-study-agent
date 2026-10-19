"""Opt-in harmless native tool-call feasibility probe. No database or Calendar access."""
import argparse,json,logging,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--verify',action='store_true')
 parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
 args=parser.parse_args()
 if not args.verify:
  print('No credentials or services accessed. Use --verify for two bounded synthetic model calls.');return 0
 logging.disable(logging.CRITICAL)
 from services.orchestrate_client import Configuration,OrchestrateClient,POCError
 try:
  config=Configuration.load(args.env_file)
 except POCError:
  print('STOP: configuration unavailable; no secret details displayed.');return 1
 with httpx.Client(trust_env=False,timeout=httpx.Timeout(45,connect=5),follow_redirects=False) as client:
  transport=OrchestrateClient(config,client)
  try:
   token=transport.authenticate()
   messages=[{'role':'system','content':'You are a synthetic test assistant. Use the supplied tool for availability. Do not invent tool results.'},
    {'role':'user','content':'Find two available synthetic Machine Learning study slots.'}]
   tool={'type':'function','function':{'name':'synthetic_availability','description':'Return synthetic study slots, without external operations.',
    'parameters':{'type':'object','additionalProperties':False,'properties':{'subject':{'type':'string'},'count':{'type':'integer','minimum':1,'maximum':3}},'required':['subject','count']}}}
   endpoint=config.url+'/v1/orchestrate/gateway/model/chat/completions'
   for step in range(2):
    body={'model':'groq/openai/gpt-oss-120b','messages':messages,'tools':[tool],'tool_choice':'auto','stream':False,'max_tokens':512}
    response=client.post(endpoint,headers={'Authorization':'Bearer '+token},json=body)
    print('Synthetic native step '+str(step+1)+': HTTP '+str(response.status_code),flush=True)
    if response.status_code!=200:
     print('STOP: native gateway unavailable; no response body or credentials displayed.');return 1
    value=response.json()
    if 'choices' not in value:
     print('STOP: unexpected native response shape.');return 1
    message=value['choices'][0]['message']
    calls=message.get('tool_calls') or []
    print('Native tool calls: '+str(len(calls))+'; final text present: '+str(bool(message.get('content'))),flush=True)
    if step==0:
     if len(calls)!=1 or calls[0]['function']['name']!='synthetic_availability':
      print('STOP: expected native tool selection missing.');return 1
     arguments=json.loads(calls[0]['function']['arguments'])
     if arguments.get('count')!=2:
      print('STOP: unexpected synthetic count.');return 1
     messages.append(message)
     messages.append({'role':'tool','tool_call_id':calls[0]['id'],'content':json.dumps({'available_slots':['synthetic-slot-a','synthetic-slot-b']})})
    elif calls or not message.get('content'):
     print('STOP: expected tool-result continuation missing.');return 1
   print('PASS: native selection and tool-result continuation with GPT-OSS 120B via IBM Orchestrate gateway; two submissions, no remote deployment or side effects.')
   return 0
  except POCError as error:
   print('STOP: '+error.stage+'/'+error.code);return 1
  except Exception as error:
   print('STOP: '+type(error).__name__+'; details suppressed; no automatic replay.');return 1
if __name__=='__main__':raise SystemExit(main())
