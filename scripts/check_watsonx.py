"""Tiny authorized IBM verification: synthetic prompt only, never prints secrets."""
import argparse,logging,os,sys,json
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel,ConfigDict,Field
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from services.watsonx import WatsonxProvider,WatsonxCredentials,CANDIDATES
from services.llm_contracts import ToolRegistry,LLMError


class EchoInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text: str = Field(max_length=8)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--continuation',action='store_true',help='Also verify a synthetic native tool-result continuation.')
    args=parser.parse_args()
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    logging.getLogger('httpcore').setLevel(logging.CRITICAL)
    load_dotenv(args.env_file,override=False)
    provider=None
    try:
        provider=WatsonxProvider(WatsonxCredentials.from_environment(),CANDIDATES[0])
        models=provider.models()
        for model in CANDIDATES:print(model+': '+('listed by Tokyo provider' if model in models else 'not listed'))
        selected=os.getenv('WATSONX_CONVERSATION_MODEL') or next((m for m in CANDIDATES if m in models),None)
        if not selected:raise LLMError('unsupported_model')
        provider.model_id=selected
        capability=provider.verify_model()
        registry=ToolRegistry();registry.register('synthetic_echo','Return the supplied synthetic text; this verification never executes a tool.',EchoInput)
        if capability['native_tools'] is not False:
            result=provider.complete([{'role':'system','content':'This is a synthetic connectivity test. Call synthetic_echo with text ok.'},{'role':'user','content':'Call synthetic_echo with text ok.'}],tools=registry.definitions(),tool_choice={'type':'function','function':{'name':'synthetic_echo'}},max_tokens=64)
            assert len(result.tool_calls)==1
            action=registry.validate(result.tool_calls[0])
            assert action.name=='synthetic_echo' and action.arguments=={'text':'ok'}
            print('PASS: actual native tool-call response parsed and validated; no tool executed.')
            if args.continuation:
                messages=[{'role':'system','content':'Synthetic test. A tool response is supplied. Reply with just OK, without calling tools.'},
                    {'role':'user','content':'Return the synthetic echo result.'},
                    {'role':'assistant','content':'','tool_calls':[{'id':action.id,'type':'function','function':{'name':action.name,'arguments':json.dumps(action.arguments)}}]},
                    {'role':'tool','tool_call_id':action.id,'content':'{"text":"ok"}'}]
                continued=provider.complete(messages,tools=registry.definitions(),tool_choice='none',max_tokens=8)
                assert not continued.tool_calls and bool(continued.content.strip())
                print('PASS: actual native assistant/tool-result continuation accepted; synthetic data only.')
        else:
            result=provider.complete([{'role':'user','content':'Synthetic connectivity check: reply with just OK.'}],max_tokens=8)
            assert result.content.strip().upper()=='OK'
            print('PASS: actual minimal chat inference; native tools not advertised. Structured validation fallback is available.')
        print('Verified default model: '+selected)
        print('IAM authentication and project-scoped inference succeeded. No student data, identifiers, keys or tokens printed.')
        return 0
    except LLMError as error:
        print('STOP: IBM verification failed ('+error.code+'). No secrets or provider payloads displayed.');return 1
    except Exception:
        print('STOP: IBM returned an unexpected synthetic response; no payload displayed.');return 1
    finally:
        if provider:provider.close()


if __name__=='__main__':raise SystemExit(main())
