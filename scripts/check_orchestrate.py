"""Isolated LIVE Orchestrate verification using the shared verified client."""
import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services.orchestrate_client import (Configuration, OrchestrateClient as OrchestratePOC, POCError,
    parse_events, text_content, check_status, HOST, IAM_URL, PROMPT)
import httpx

def main():
    # Windows pipe/console defaults can corrupt non-ASCII assistant text.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Make one authorized synthetic LIVE run.")
    args = parser.parse_args()
    if not args.verify:
        print("No API calls made. Use --verify for the isolated LIVE connectivity test.")
        return 0
    for name in ("httpx", "httpcore", "dotenv"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    poc = None
    try:
        config = Configuration.load()
        with httpx.Client(timeout=httpx.Timeout(50, connect=5), trust_env=False,
                          follow_redirects=False) as client:
            poc = OrchestratePOC(config, client)
            metadata, reply = poc.verify()
        print("PASS: Python communicated with the LIVE agent; " + json.dumps(metadata))
        print("Synthetic response: " + reply)
        print(f"API calls: {poc.calls}; inference submissions: 1; retries: 0.")
        return 0
    except POCError as error:
        print("FAIL: " + str(error) + ". No automatic retry or fallback authentication.")
        if poc:
            print(f"API calls attempted: {poc.calls}.")
        if hasattr(error, "diagnostics"):
            print("Stream structure only: " + json.dumps(error.diagnostics))
        return 1
    except Exception:
        print("FAIL: unexpected local/API response error; details suppressed to protect credentials.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
