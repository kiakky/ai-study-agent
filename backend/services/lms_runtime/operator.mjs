// This entrypoint is launched only by scripts/brightspace_operator.py.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {verifyPackage,openSessionAccess} from './bootstrap.mjs';
import {validateConfig,ReadOnlyTokens,decryptSession,SessionValidationError} from './core.mjs';
import {refreshOwnedSession,loginOwnedSession,browserExecutable} from './maintenance.mjs';

const allowed=new Set(['SESSION_READ','SESSION_FORMAT_UNSUPPORTED','KEYRING_READ','SESSION_DECRYPT',
  'TENANT_MISMATCH','OWNER_BINDING_MISSING','OWNER_MISMATCH','TOKEN_INVALID','SESSION_EXPIRED',
  'SESSION_CHANGED','SESSION_REJECTED','SESSION_INVALID','LOGIN_REQUIRED','LOGIN_NOT_COMPLETED','BROWSER_NOT_INSTALLED']);
let stage='PACKAGE_OR_RUNTIME';
try {
  const mode=process.argv[2];
  if (!['status','refresh','login'].includes(mode)) throw Error('INVALID_MODE');
  const config=validateConfig(process.env);
  const manifest=JSON.parse(await fs.readFile(new URL('./reviewed-build.json',import.meta.url),'utf8'));
  const root=await verifyPackage(config.packageRoot,manifest);
  const resolve=createRequire(path.join(root,'package.json'));
  const fromPackage=name=>import(pathToFileURL(resolve.resolve(name)).href);
  const upstream=file=>import(pathToFileURL(path.join(root,'build',file)).href);
  if(process.platform!=='win32')throw Error('UNSUPPORTED_PLATFORM');
  if(mode==='login') {
    stage='LOGIN_FAILED';
    const playwright=await fromPackage('playwright');
    try {await fs.access(browserExecutable(playwright));} catch {throw Error('BROWSER_NOT_INSTALLED');}
    const {BrowserAuth}=await upstream('auth/browser-auth.js');
    const {SessionStore}=await upstream('auth/session-store.js');
    const store=new SessionStore(config.sessionDir);
    process.stdout.write('Complete sign-in and MFA in the visible school browser. No password is collected by this command.\n');
    const result=await loginOwnedSession(config,{BrowserAuth,save:token=>store.save(token)});
    process.stdout.write('PASS '+result+': expected owner session saved through the upstream encrypted store.\n');
  } else {
    stage='SESSION_DIRECTORY_UNAVAILABLE';
    const access=await openSessionAccess(config,fromPackage);
    if(mode==='status') {
      await new ReadOnlyTokens(config,access).getToken();
      process.stdout.write('PASS SESSION_VALID: existing owner-bound session/key are compatible. No school request made.\n');
    } else {
      stage='REFRESH_FAILED';
      const {mintAccessToken}=await upstream('auth/token-mint.js');
      const {SessionStore}=await upstream('auth/session-store.js');
      const store=new SessionStore(config.sessionDir);
      const load=async()=>{
        const record=JSON.parse(await access.readRecord());
        if(record?.version!==2 || record.kind!=='session')throw Error('SESSION_FORMAT_UNSUPPORTED');
        return decryptSession(record,await access.readKey());
      };
      const result=await refreshOwnedSession(config,{load,mint:mintAccessToken,saveIfCurrent:(a,b)=>store.saveIfCurrent(a,b)});
      process.stdout.write('PASS '+result+': one explicit owner token exchange completed and was saved.\n');
    }
  }
} catch(error) {
  const code=error instanceof SessionValidationError && allowed.has(error.code) ? error.code
    : allowed.has(error.message) ? error.message : stage;
  process.stdout.write('NOT READY ['+code+']. Use operator login for missing/legacy/expired state; no automatic fallback or repair was attempted.\n');
  process.exitCode=1;
}
