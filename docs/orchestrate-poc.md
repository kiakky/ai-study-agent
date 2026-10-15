# Isolated transport proof of concept

The original POC verified Cloud Toronto IAM authentication, LIVE deployed agent invocation
and complete assistant text extraction from streaming events (not merely HTTP 200/run.completed).
It is reused by the provider. Use your own credentials; never copy the original private env.
`python scripts/check_orchestrate.py --verify` makes a real synthetic inference, and is
opt-in. Offline regressions mock HTTP/streams. Avoid repeated live calls on authentication,
quota or rate failures. No agent creation/redeploy, Calendar or Brightspace operation is
needed. See orchestrate-provider.md and README for supported metadata and limitations.
