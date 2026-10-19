// All credentials below are synthetic, encrypted in memory or a disposable temp dir.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createCipheriv,randomBytes} from 'node:crypto';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {ReadOnlyTokens,RECONNECT,sha256,validateConfig,readOnlyFetch,guardedRegistrar,FIELDS}
  from '../backend/services/lms_runtime/core.mjs';
import {boundedRead,verifyPackage,start} from '../backend/services/lms_runtime/bootstrap.mjs';
import {refreshOwnedSession,loginOwnedSession,browserExecutable} from '../backend/services/lms_runtime/maintenance.mjs';

const now=1000000000,key=Buffer.alloc(32,7);
const config={baseUrl:'https://school.example',account:'synthetic-owner'};
const good=()=>({accessToken:'synthetic-token',capturedAt:now-1000,expiresAt:now+3600000,
  tenantOrigin:config.baseUrl,uniqueName:config.account,source:'browser',cookieHeader:'synthetic-cookie'});
function seal(token=good(),kind='session') {
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);
  cipher.setAAD(Buffer.from(`brightspace-mcp-server:2:${kind}`));
  const data=Buffer.concat([cipher.update(JSON.stringify(token)),cipher.final()]);
  return JSON.stringify({version:2,kind,encrypted:{iv:iv.toString('hex'),authTag:cipher.getAuthTag().toString('hex'),data:data.toString('hex')}});
}
function fixture(raw=seal()) {
  const state={raw,reads:0,keys:0};
  const tokens=new ReadOnlyTokens(config,{readRecord:async()=>{state.reads++;return state.raw;},
    readKey:async()=>{state.keys++;return key;},now:()=>now});
  return {tokens,state};
}
test('valid encrypted v2 owner session reads without persistence capabilities',async()=>{
  const {tokens,state}=fixture();
  const result=await tokens.getToken();
  assert.equal(result.accessToken,'synthetic-token');assert.equal(result.cookieHeader,'synthetic-cookie');
  assert.equal(await tokens.sessionChanged(),false);assert.equal(state.reads,2);
  assert.equal(typeof tokens.save,'undefined');assert.equal(typeof tokens.mint,'undefined');
});
test('same owner account casing is accepted without changing tenant binding',async()=>{
  const {tokens}=fixture(seal({...good(),uniqueName:'SYNTHETIC-OWNER'}));
  assert.equal((await tokens.getToken()).accessToken,'synthetic-token');
});
test('browser dependency supports both ESM and CommonJS export shapes',()=>{
  const chromium={executablePath:()=>'/synthetic-browser'};
  assert.equal(browserExecutable({chromium}),'/synthetic-browser');
  assert.equal(browserExecutable({default:{chromium}}),'/synthetic-browser');
  assert.throws(()=>browserExecutable({}));
});
test('only explicit maintenance can refresh an expired owner token',async()=>{
  const stale={...good(),capturedAt:now-3600000,expiresAt:now-1000,csrfToken:'synthetic-csrf'};
  await assert.rejects(fixture(seal(stale)).tokens.getToken());
  let mints=0,saves=0;
  await refreshOwnedSession(config,{load:async()=>stale,now:()=>now,
    mint:async request=>{mints++;assert.equal(request.baseUrl,config.baseUrl);return {ok:true,accessToken:'synthetic-renewed'};},
    saveIfCurrent:async(token,expected)=>{saves++;assert.equal(expected,stale);assert.equal(token.uniqueName,stale.uniqueName);assert.equal(token.csrfToken,stale.csrfToken);return true;}});
  assert.equal(mints,1);assert.equal(saves,1);
});
test('maintenance denies wrong owner/tenant/missing material before any token exchange',async()=>{
  for(const edit of [{uniqueName:'other'},{tenantOrigin:'https://other.example'},{cookieHeader:undefined},{csrfToken:undefined}]) {
    let mints=0,saves=0;
    await assert.rejects(refreshOwnedSession(config,{load:async()=>({...good(),...edit}),now:()=>now,
      mint:async()=>{mints++;},saveIfCurrent:async()=>{saves++;}}));
    assert.equal(mints,0);assert.equal(saves,0);
  }
});
test('failed/expired/malformed token exchanges preserve saved state and never retry',async()=>{
  for(const result of [{ok:false,reason:'sessionExpired'},{ok:false,reason:'transport'},{ok:true,accessToken:'x\r\ny'}]) {
    let mints=0,saves=0;
    await assert.rejects(refreshOwnedSession(config,{load:async()=>({...good(),csrfToken:'synthetic'}),now:()=>now,
      mint:async()=>{mints++;return result;},saveIfCurrent:async()=>{saves++;}}));
    assert.equal(mints,1);assert.equal(saves,0);
  }
});
test('operator refresh refuses to overwrite a concurrent replacement',async()=>{
  let saves=0;
  await assert.rejects(refreshOwnedSession(config,{load:async()=>({...good(),csrfToken:'synthetic'}),now:()=>now,
    mint:async()=>({ok:true,accessToken:'renewed'}),saveIfCurrent:async()=>{saves++;return false;}}));
  assert.equal(saves,1);
});
test('manual login defers browser/token saves until the expected owner is verified',async()=>{
  for(const wrong of [false,true]) {
    const trace=[];
    class BrowserAuth {
      constructor(settings){assert.equal(settings.headless,false);assert.equal(settings.password,undefined);assert.equal(settings.totpUri,undefined);}
      async saveBrowserState(state){trace.push('browser-save');}
      async keepNewerMicrosoftState(){throw Error('Must be disabled');}
      async waitForVisibleLogin(){trace.push('manual-browser-wait');}
      async authenticate(options){
        assert.equal(options.automatic,false);
        await this.navigateAndLogin({goto:async()=>trace.push('navigate')});
        await this.saveBrowserState({synthetic:true});assert.ok(!trace.includes('browser-save'));
        await options.onAuthenticated({...good(),uniqueName:wrong?'other':config.account,
          capturedAt:Date.now()-1000,expiresAt:Date.now()+3600000});
      }
    }
    const operation=loginOwnedSession(config,{BrowserAuth,save:async()=>trace.push('token-save')});
    if(wrong){await assert.rejects(operation);assert.ok(!trace.includes('token-save'));assert.ok(!trace.includes('browser-save'));}
    else {assert.equal(await operation,'SIGNED_IN');assert.deepEqual(trace,['navigate','manual-browser-wait','token-save','browser-save']);}
  }
});
test('legacy/plaintext/missing/malformed/kind errors deny before key access',async()=>{
  for(const raw of ['', '{}','null','not json',JSON.stringify({version:1}),seal(good(),'browser-state')]) {
    const {tokens,state}=fixture(raw);
    await assert.rejects(tokens.getToken(),{message:RECONNECT});assert.equal(state.keys,0);
  }
});
test('cryptographic authentication rejects corrupt data, key, AAD and tag',async()=>{
  for(const edit of [r=>r.encrypted.authTag='0'.repeat(32),r=>r.encrypted.iv='bad',
      r=>r.encrypted.data='00',r=>r.encrypted.data='z'.repeat(10)]) {
    const raw=JSON.parse(seal());edit(raw);
    await assert.rejects(fixture(JSON.stringify(raw)).tokens.getToken(),{message:RECONNECT});
  }
  const tokens=new ReadOnlyTokens(config,{readRecord:async()=>seal(),readKey:async()=>Buffer.alloc(32),now:()=>now});
  await assert.rejects(tokens.getToken(),{message:RECONNECT});
});
test('wrong account/tenant, missing identity, expiry and malformed token fail closed',async()=>{
  const edits=[{uniqueName:'other'},{uniqueName:undefined},{tenantOrigin:'https://other.example'},
    {tenantOrigin:undefined},{expiresAt:now},{expiresAt:now+300000},{expiresAt:'future'},
    {capturedAt:now+60001},{capturedAt:undefined},{accessToken:''},{accessToken:'x\r\ny'},
    {source:'unknown'},{expiresAt:0},{accessToken:12},{cookieHeader:'x\r\ny'},{cookieHeader:42}];
  for(const edit of edits) await assert.rejects(fixture(seal({...good(),...edit})).tokens.getToken(),{message:RECONNECT});
});
test('session deletion/replacement cannot switch identity or resurrect a running connector',async()=>{
  for(const replacement of [seal(),seal({...good(),uniqueName:'other'}),'']) {
    const {tokens,state}=fixture();await tokens.getToken();state.raw=replacement;
    await assert.rejects(tokens.getToken());state.raw=seal();await assert.rejects(tokens.getToken());
  }
});
test('expiry advances on every read; rejected tokens are never replayed',async()=>{
  let clock=now;const tokens=new ReadOnlyTokens(config,{readRecord:async()=>seal(),readKey:async()=>key,now:()=>clock});
  await tokens.getToken();clock=now+3600000;await assert.rejects(tokens.getToken());
  const {tokens:other,state}=fixture();await other.getToken();const reads=state.reads;
  await assert.rejects(other.getToken('synthetic-token'));await assert.rejects(other.getToken());assert.equal(state.reads,reads);
});
test('concurrent HTTP rejection wins over an in-flight credential read',async()=>{
  let resume;const wait=new Promise(r=>resume=r);
  const tokens=new ReadOnlyTokens(config,{readRecord:async()=>{await wait;return seal();},readKey:async()=>key,now:()=>now});
  const pending=tokens.getToken();tokens.invalidate();resume();await assert.rejects(pending);
});
test('transport permits only tenant GETs and refuses automatic redirects',async()=>{
  const {tokens}=fixture();let calls=0;
  const fetch=readOnlyFetch(async(input,options)=>{calls++;assert.equal(options.redirect,'error');return {status:200};},config.baseUrl,tokens);
  await fetch('https://school.example/d2l/api/versions/');
  for(const [url,options] of [['https://evil.example/',{}],['https://school.example/',{method:'POST'}],
      ['https://school.example/',{method:'DELETE'}],['https://school.example/',{method:'GET',body:'write'}],
      ['https://owner:secret@school.example/',{}]]) await assert.rejects(fetch(url,options));
  assert.equal(calls,1);
});
test('HTTP 401 stops before retry/mint/browser or subsequent request',async()=>{
  for(const status of [401]) {
    const {tokens}=fixture();let calls=0;
    const fetch=readOnlyFetch(async()=>{calls++;return {status};},config.baseUrl,tokens);
    await assert.rejects(fetch(config.baseUrl));await assert.rejects(fetch(config.baseUrl));assert.equal(calls,1);
  }
});
test('HTTP 403 remains a course permission failure without triggering login',async()=>{
  const {tokens}=fixture();let calls=0;
  const fetch=readOnlyFetch(async()=>{calls++;return {status:403};},config.baseUrl,tokens);
  assert.equal((await fetch(config.baseUrl)).status,403);
  await tokens.getToken();assert.equal(calls,1);
});
test('redirect failures propagate without following other origins',async()=>{
  const {tokens}=fixture();let calls=0;
  const fetch=readOnlyFetch(async(_,options)=>{calls++;assert.equal(options.redirect,'error');throw Error('redirect');},config.baseUrl,tokens);
  await assert.rejects(fetch(config.baseUrl));assert.equal(calls,1);
});
test('registrar refuses unknown tools, paths, identity and submission arguments',async()=>{
  const {tokens}=fixture();const handlers={};let calls=0;
  const registrar=guardedRegistrar({registerTool:(n,_,h)=>handlers[n]=h},tokens);
  assert.throws(()=>registrar.registerTool('sign_out',{},()=>{}));
  registrar.registerTool('download_file',{},async args=>{calls++;return {content:[]};});
  for(const args of [{courseId:1,topicId:2,downloadPath:'x'},{courseId:1,topicId:2,fileId:3},
      {courseId:1,topicId:2,userId:42},{courseId:1},{courseId:true,topicId:2},{courseId:1,topicId:2,nested:{}},
      {courseId:1,topicId:9007199254740992}]) assert.equal((await handlers.download_file(args)).isError,true);
  assert.equal(calls,0);await handlers.download_file({courseId:1,topicId:2});assert.equal(calls,1);
});
test('every tool checks credentials before cache/handler; errors are sanitized',async()=>{
  for(const name of Object.keys(FIELDS)) {
    let handler,calls=0;const {tokens}=fixture();
    guardedRegistrar({registerTool:(_,__,h)=>handler=h},tokens).registerTool(name,{},async()=>{calls++;throw Error('synthetic-secret');});
    const args=name==='download_file'?{courseId:1,topicId:2}:name==='get_syllabus'?{courseId:1}:{};
    const result=await handler(args);assert.equal(result.isError,true);assert.ok(!JSON.stringify(result).includes('synthetic-secret'));
    tokens.invalidate();await handler(args);assert.equal(calls,1);
  }
});
test('configuration rejects implicit paths, HTTP, credential URLs and blank account',()=>{
  const env={LMS_TENANT_ORIGIN:config.baseUrl,LMS_EXPECTED_ACCOUNT:config.account,
    LMS_SESSION_DIR:path.resolve('synthetic'),LMS_MCP_PACKAGE_ROOT:path.resolve('package'),LMS_NODE_EXECUTABLE:process.execPath};
  assert.equal(validateConfig(env).baseUrl,config.baseUrl);
  for(const edit of [{LMS_TENANT_ORIGIN:'http://school.example'},{LMS_TENANT_ORIGIN:'https://school.example/'},
      {LMS_TENANT_ORIGIN:'https://x:y@school.example'},{LMS_EXPECTED_ACCOUNT:''},{LMS_SESSION_DIR:'relative'}])
    assert.throws(()=>validateConfig({...env,...edit}));
});
async function temporary(run) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'lms-synthetic-'));
  try {await run(root);} finally {
    assert.equal(path.dirname(path.resolve(root)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('lms-synthetic-'));
    await fs.rm(root,{recursive:true,force:true});
  }
}
test('bounded reader refuses oversized/directory inputs and never modifies files',async()=>temporary(async root=>{
  const file=path.join(root,'synthetic.txt');await fs.writeFile(file,'12345');
  assert.equal((await boundedRead(file,5)).toString(),'12345');await assert.rejects(boundedRead(file,4));
  await assert.rejects(boundedRead(root));assert.equal(await fs.readFile(file,'utf8'),'12345');
}));
test('package manifest/version checks reject tampering before imports',async()=>temporary(async root=>{
  await fs.mkdir(path.join(root,'build'));
  await fs.writeFile(path.join(root,'package.json'),JSON.stringify({name:'brightspace-mcp-server',version:'3.13.18',type:'module'}));
  const file=path.join(root,'build','fixture.js');await fs.writeFile(file,'export {};');
  const manifest={'build/fixture.js':sha256('export {};')};await verifyPackage(root,manifest);
  await fs.writeFile(file,'unexpected');await assert.rejects(verifyPackage(root,manifest));
  await fs.writeFile(path.join(root,'package.json'),JSON.stringify({name:'brightspace-mcp-server',version:'3.13.19',type:'module'}));
  await assert.rejects(verifyPackage(root,manifest));
}));
test('full bootstrap registers nine guarded tools with stub SDK/API/keyring and zero startup HTTP',async()=>temporary(async root=>{
  await fs.mkdir(path.join(root,'build'));await fs.mkdir(path.join(root,'session'));
  await fs.writeFile(path.join(root,'package.json'),JSON.stringify({name:'brightspace-mcp-server',version:'3.13.18',type:'module'}));
  const raw=seal({...good(),capturedAt:Date.now()-1000,expiresAt:Date.now()+3600000});
  const session=path.join(root,'session','session.json');await fs.writeFile(session,raw);
  let connected=0,http=0,keys=0;const handlers={},imports=[];
  const env={LMS_MCP_PACKAGE_ROOT:root,LMS_SESSION_DIR:path.dirname(session),LMS_TENANT_ORIGIN:config.baseUrl,
    LMS_EXPECTED_ACCOUNT:config.account,LMS_NODE_EXECUTABLE:process.execPath};
  const dependencies={manifest:{},platform:'win32',fetch:async()=>{http++;return {status:200};},setFetch:()=>{},
    fromPackage:async name=>{
      imports.push(name);
      if(name==='@napi-rs/keyring') return {AsyncEntry:class {constructor(service,account){assert.equal(service,'brightspace-mcp-server');assert.equal(account,`session-key:${sha256(awaitedDir)}`);}async getPassword(){keys++;return key.toString('hex');}}};
      if(name.includes('/mcp.js')) return {McpServer:class {registerTool(n,_,h){handlers[n]=h;}async connect(){connected++;}}};
      if(name.includes('/stdio.js')) return {StdioServerTransport:class {}};
      throw Error('Unexpected import');
    },
    upstream:async name=>{
      imports.push(name);
      if(name==='api/client.js') return {D2LApiClient:class {constructor(options){assert.equal(options.onAuthExpired,undefined);}}};
      const tool=name.split('/')[1].replace('.js','').replaceAll('-','_');
      const exported='register'+tool.split('_').map(s=>s[0].toUpperCase()+s.slice(1)).join('');
      return {[exported]:(server)=>server.registerTool(tool,{},async()=>({content:[]}))};
    }};
  const awaitedDir=await fs.realpath(path.dirname(session));
  await start(env,dependencies);assert.equal(connected,1);assert.equal(http,0);assert.equal(keys,1);
  assert.deepEqual(Object.keys(handlers).sort(),Object.keys(FIELDS).sort());
  assert.ok(imports.every(n=>!n.includes('auth/')&&!n.includes('index.js')&&!n.includes('config')));
  await handlers.get_my_courses({});assert.equal(keys,2);
  assert.equal((await handlers.download_file({courseId:1,topicId:2,downloadPath:'forbidden'})).isError,true);
  assert.equal(await fs.readFile(session,'utf8'),raw);
  assert.deepEqual((await fs.readdir(path.dirname(session))).sort(),['session.json']);
  await fs.writeFile(session,'{"version":1}');
  assert.equal((await handlers.get_my_courses({})).isError,true);assert.equal(connected,1);assert.equal(http,0);
  // Failed cold starts cannot attach transport or execute HTTP either.
  for(const bad of ['{"version":1}',seal({...good(),uniqueName:'other'}),seal({...good(),expiresAt:0})]) {
    await fs.writeFile(session,bad);await assert.rejects(start(env,dependencies));
    assert.equal(connected,1);assert.equal(http,0);
  }
  await fs.writeFile(session,raw);
  const badKey={...dependencies,fromPackage:async name=>name==='@napi-rs/keyring'
    ? {AsyncEntry:class {async getPassword(){return null;}}}:dependencies.fromPackage(name)};
  await assert.rejects(start(env,badKey));assert.equal(connected,1);assert.equal(http,0);
  await fs.unlink(session);await assert.rejects(start(env,dependencies));assert.equal(connected,1);assert.equal(http,0);
}));
