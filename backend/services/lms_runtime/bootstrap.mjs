// Deliberately bypass upstream index.js/config loader/update checks/auth startup.
// Runtime installation and interactive authentication are separate operator tasks.
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {VERSION,sha256,validateConfig,ReadOnlyTokens,guardedRegistrar,readOnlyFetch} from './core.mjs';

// Never surface upstream diagnostic data, account identifiers or exception text.
for (const name of ['log','info','warn','error','debug','trace']) console[name]=()=>{};

export async function boundedRead(file, max=1024*1024) {
  if ((await fs.lstat(file)).isSymbolicLink()) throw Error('Unsupported file');
  const handle=await fs.open(file,'r');
  try {
    const stat=await handle.stat();
    if (!stat.isFile() || stat.size>max) throw Error('Unsupported file');
    // Fixed-size read bounds even if another process grows the file after stat.
    const buffer=Buffer.alloc(max+1);
    const {bytesRead}=await handle.read(buffer,0,buffer.length,0);
    if (bytesRead>max) throw Error('Unsupported file');
    return buffer.subarray(0,bytesRead);
  } finally {await handle.close();}
}

export async function verifyPackage(root, manifest) {
  const pkg=JSON.parse(await boundedRead(path.join(root,'package.json'),65536));
  if (pkg.name !== 'brightspace-mcp-server' || pkg.version !== VERSION || pkg.type !== 'module') throw Error('Unreviewed package');
  // All inspected upstream JS must match before any upstream code is imported.
  const canonical=await fs.realpath(root);
  for (const [relative,digest] of Object.entries(manifest)) {
    if (!relative.startsWith('build/') || relative.includes('..') || !/^[a-f0-9]{64}$/.test(digest)) throw Error('Invalid manifest');
    const file=path.join(canonical,relative);
    if (await fs.realpath(file) !== file || sha256(await boundedRead(file,2*1024*1024)) !== digest) throw Error('Unreviewed source');
  }
  return canonical;
}

export async function openSessionAccess(config,fromPackage) {
  const sessionDir=await fs.realpath(config.sessionDir); // read-only; never mkdir
  const sessionFile=path.join(sessionDir,'session.json');
  const {AsyncEntry}=await fromPackage('@napi-rs/keyring');
  const account=`session-key:${sha256(sessionDir)}`;
  return {
    readRecord:async()=>{
      if (await fs.realpath(config.sessionDir) !== sessionDir) throw Error('Session replaced');
      return boundedRead(sessionFile);
    },
    readKey:async()=>{
      const key=await new AsyncEntry('brightspace-mcp-server',account).getPassword();
      if (typeof key !== 'string' || !/^[a-f0-9]{64}$/i.test(key)) throw Error('Existing key required');
      return Buffer.from(key,'hex');
    },
  };
}

export async function start(env=process.env, dependencies={}) {
  const config=validateConfig(env);
  const manifest=dependencies.manifest ?? JSON.parse(await fs.readFile(new URL('./reviewed-build.json',import.meta.url),'utf8'));
  const root=await verifyPackage(config.packageRoot,manifest);
  const resolveModule=createRequire(path.join(root,'package.json'));
  const fromPackage=dependencies.fromPackage ?? (specifier=>import(pathToFileURL(resolveModule.resolve(specifier)).href));
  const upstream=dependencies.upstream ?? (relative=>import(pathToFileURL(path.join(root,'build',relative)).href));
  // Currently Windows-only, matching the deployment host. No fallback to shell
  // key tools, plaintext, legacy salt, browser profiles or alternate identities.
  if ((dependencies.platform ?? process.platform) !== 'win32') throw Error('Unsupported credential backend');
  const access=await openSessionAccess(config,fromPackage);
  const tokens=new ReadOnlyTokens(config,access);
  await tokens.getToken(); // reject before transport, tool registration or HTTP
  const fetchImpl=readOnlyFetch((dependencies.fetch ?? globalThis.fetch).bind(globalThis),config.baseUrl,tokens);
  if (dependencies.setFetch) dependencies.setFetch(fetchImpl);
  else globalThis.fetch=fetchImpl;
  const {McpServer}=await fromPackage('@modelcontextprotocol/sdk/server/mcp.js');
  const {StdioServerTransport}=await fromPackage('@modelcontextprotocol/sdk/server/stdio.js');
  const {D2LApiClient}=await upstream('api/client.js');
  const server=new McpServer({name:'brightspace',version:VERSION});
  const api=new D2LApiClient({baseUrl:config.baseUrl,tokenManager:tokens,
    authExpiredMessage:'Owner reauthentication required; automatic recovery is disabled.'});
  // No AuthRunner, TokenManager, SessionStore, loadConfig, activity recorder or
  // updater is constructed/called. No upstream barrel registration of other tools.
  api.tryAutoReauth=async()=>{tokens.invalidate();throw Error('No automatic recovery');};
  const guarded=guardedRegistrar(server,tokens);
  const registrations=[
    ['get-my-courses','registerGetMyCourses'],['get-course-content','registerGetCourseContent'],
    ['get-syllabus','registerGetSyllabus'],['get-announcements','registerGetAnnouncements'],
    ['get-assignments','registerGetAssignments'],['get-upcoming-due-dates','registerGetUpcomingDueDates'],
    ['get-calendar-events','registerGetCalendarEvents'],['search-course','registerSearchCourse'],
    ['download-file','registerDownloadFile'],
  ];
  for (const [file,name] of registrations) (await upstream(`tools/${file}.js`))[name](guarded,api,config);
  await server.connect(new StdioServerTransport());
}

if (process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const stop=()=>{process.stderr.write('Brightspace controlled runtime stopped; operator action required.\n');process.exit(1);};
  process.on('uncaughtException',stop);
  process.on('unhandledRejection',stop);
  try {await start();}
  catch {process.stderr.write('Brightspace controlled startup unavailable; operator setup or reauthentication required.\n');process.exitCode=1;}
}
