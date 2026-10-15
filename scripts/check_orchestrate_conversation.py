"""One synthetic context-replay inference; no Telegram, database or tool imports."""
import argparse
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from services.orchestrate import OrchestrateProvider
from services.orchestrate_client import Configuration, POCError
from services.llm_contracts import LLMError


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    if not args.verify:
        print('No configuration read or API calls. Use --verify for one synthetic context test.')
        return 0
    for name in ('httpx', 'httpcore', 'dotenv'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    provider = None
    try:
        provider = OrchestrateProvider(Configuration.load(args.env_file))
        reply = provider.complete([
            {'role': 'system', 'content': 'You are a concise study assistant. Synthetic saved default: 45-minute sessions. Explicit current requests override defaults without saving them. No tools are available.'},
            {'role': 'user', 'content': 'Please call me Nova. For this task I want 20-minute sessions.'},
            {'role': 'assistant', 'content': 'Okay, Nova: 20-minute sessions for this task; saved defaults unchanged.'},
            {'role': 'user', 'content': 'What preferred name did I choose, and what session length do I want for this task? Answer in one sentence.'},
        ])
        print('Synthetic response: ' + reply.content)
        if 'Nova' not in reply.content or '20' not in reply.content:
            print('FAIL: synthetic context or explicit task override was not respected. No retry.')
            return 1
        print('PASS: adapter preserved synthetic name/history and current task override.')
        print(f'API calls: {provider.transport.calls}; inference submissions: 1; retries: 0.')
        return 0
    except (LLMError, POCError) as error:
        print('FAIL: ' + error.code + '; details suppressed. No retry.')
        return 1
    except Exception:
        print('FAIL: local/provider error; private details suppressed. No retry.')
        return 1
    finally:
        if provider:
            provider.close()


if __name__ == '__main__':
    raise SystemExit(main())
