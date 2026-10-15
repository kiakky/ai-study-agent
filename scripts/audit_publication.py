"""Redacted source/index and reachable-history audit. Never opens private local state."""
import argparse
from pathlib import Path
import re
import subprocess
ROOT=Path(__file__).resolve().parents[1]
RULES={
 'private-key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'telegram-token':r'\b\d{7,12}:[A-Za-z0-9_-]{30,}\b',
 'github-token':r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})\b',
 'google-client-id':r'\b\d{8,}-[a-z0-9]+\.apps\.googleusercontent\.com',
 'google-api-key':r'\bAIza[A-Za-z0-9_-]{30,}\b',
 'bearer-literal':r'(?i)bearer\s+[A-Za-z0-9_.-]{45,}',
 'jwt':r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b',
 'personal-tunnel':r'https://[a-z0-9-]+\.ngrok-free\.(?:dev|app)',
 'personal-machine-path':r'[A-Za-z]:[\\/](?:Users|Python)[\\/]',
}
PRIVATE_DIRS={'credentials','database','.private-lms','.local-backups','.worktrees','.venv','node_modules','.d2l-session','.brightspace-mcp','downloads','__pycache__','browser-profile','sessions','.auth'}
SAFE_CONFIG={'conversation.example.json','deployment.example.json','google_oauth.example.json','calendar_test.example.json'}
EMAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def private(name):
 p=Path(name)
 return bool(set(p.parts)&PRIVATE_DIRS or p.name=='.env' or p.name.startswith('.env.') and p.name!='.env.example'
  or p.suffix.lower() in {'.db','.sqlite','.sqlite3','.log','.pyc','.pem','.key','.p12','.pfx','.tgz','.zip'}
  or p.parts[0]=='config' and p.name not in SAFE_CONFIG
  or re.search(r'(?:storage[-_]state|auth[-_]state|cookie|token\.json)',p.name,re.I))

def inspect_text(label,data,failures):
 try:text=data.decode('utf-8-sig')
 except UnicodeDecodeError:failures.add((label,'unreviewed-binary'));return
 for rule,pattern in RULES.items():
  if re.search(pattern,text):failures.add((label,rule))
 for email in EMAIL.findall(text):
  domain=email.rsplit('@',1)[1].lower()
  if email!='1638ngshikai@gmail.com' and not (domain in {'example.com','example.org','example.net','example.invalid','example.edu','school.example.edu','school.edu'} or domain.endswith('.example') or domain.endswith('.invalid')):
   failures.add((label,'unreviewed-email'))

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--history',action='store_true')
 parser.add_argument('--initial-snapshot',action='store_true',help='Legacy one-commit guard')
 args=parser.parse_args();failures=set();count=0;seen=set();commits=[]
 names=git('ls-files','-z').decode().split('\0')
 for name in filter(None,names):
  if private(name):failures.add((name,'private-file'));continue
  p=ROOT/name
  if p.is_symlink():failures.add((name,'symlink'));continue
  inspect_text(name,p.read_bytes(),failures);count+=1
  # Also inspect the exact staged blob, preventing index/worktree discrepancies.
  staged=git('ls-files','-s','--',name).decode().split()
  if staged and staged[0]!='100644':failures.add((name,'nonregular-file'))
  if len(staged)>1:inspect_text('index:'+name,git('cat-file','blob',staged[1]),failures)
 if args.history or args.initial_snapshot:
  commits=git('rev-list','--all').decode().split()
  for commit in commits:
   inspect_text('commit:'+commit[:12],git('show','-s','--format=%an <%ae>%n%cn <%ce>%n%B',commit),failures)
   for item in git('ls-tree','-rz',commit).split(b'\0'):
    if not item:continue
    metadata,name=item.split(b'\t',1);name=name.decode();mode,kind,oid=metadata.decode().split()
    if private(name):failures.add(('history:'+name,'private-file'));continue
    if mode!='100644' or kind!='blob':failures.add(('history:'+name,'nonregular-file'));continue
    if oid not in seen:
     inspect_text('history:'+name,git('cat-file','blob',oid),failures);seen.add(oid)
 if args.initial_snapshot and len(commits)!=1:failures.add(('history','expected-one-initial-commit'))
 for label,rule in sorted(failures):print('STOP:',label,'['+rule+']')
 print(f'Current files: {count}; historical commits: {len(commits)}; unique history blobs: {len(seen)}; findings: {len(failures)}')
 return int(bool(failures))
if __name__=='__main__':raise SystemExit(main())
