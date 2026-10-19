// Local controlled bootstrap boundary. No configuration/session side effects on import.
import {createHash, createDecipheriv} from 'node:crypto';
import path from 'node:path';

export const VERSION = '3.13.18';
export const RECONNECT = 'Brightspace owner reauthentication required. No automatic recovery was attempted.';
export const FIELDS = Object.freeze({
  get_my_courses: ['activeOnly'],
  get_course_content: ['courseId','typeFilter','moduleTitle','maxDepth','modifiedSince'],
  get_syllabus: ['courseId'], get_announcements: ['courseId','count','offset','modifiedSince'],
  get_assignments: ['courseId'], get_upcoming_due_dates: ['courseId','daysAhead'],
  get_calendar_events: ['courseId','from','to','includeGenerated'],
  search_course: ['courseId','query','limit'], download_file: ['courseId','topicId'],
});
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export class SessionValidationError extends Error {
  constructor(code) {super(RECONNECT);this.code=code;}
}
const fail = (code='SESSION_INVALID') => {throw new SessionValidationError(code);};

export function validateConfig(env) {
  const origin = new URL(env.LMS_TENANT_ORIGIN);
  if (origin.protocol !== 'https:' || origin.origin !== env.LMS_TENANT_ORIGIN || origin.username || origin.password) fail();
  for (const key of ['LMS_SESSION_DIR','LMS_MCP_PACKAGE_ROOT','LMS_NODE_EXECUTABLE'])
    if (!path.isAbsolute(env[key] ?? '')) fail();
  const account = env.LMS_EXPECTED_ACCOUNT;
  if (typeof account !== 'string' || !account.trim() || account.length > 320 || /[\x00-\x1f]/.test(account)) fail();
  return Object.freeze({baseUrl:origin.origin, sessionDir:env.LMS_SESSION_DIR,
    packageRoot:env.LMS_MCP_PACKAGE_ROOT, account, courseFilter:Object.freeze({activeOnly:true})});
}

export function decryptSession(record, key) {
  if (record?.version !== 2 || record.kind !== 'session' || !Buffer.isBuffer(key) || key.length !== 32) fail();
  const e = record.encrypted;
  if (!e || !/^[a-f0-9]{24}$/i.test(e.iv) || !/^[a-f0-9]{32}$/i.test(e.authTag)
      || !/^(?:[a-f0-9]{2})+$/i.test(e.data) || e.data.length > 1024*1024) fail();
  const cipher = createDecipheriv('aes-256-gcm',key,Buffer.from(e.iv,'hex'));
  cipher.setAAD(Buffer.from('brightspace-mcp-server:2:session'));
  cipher.setAuthTag(Buffer.from(e.authTag,'hex'));
  return JSON.parse(Buffer.concat([cipher.update(Buffer.from(e.data,'hex')),cipher.final()]).toString('utf8'));
}

export function validateOwnerToken(token,config,{allowExpired=false,now=Date.now}={}) {
      if (!token || typeof token !== 'object') fail('TOKEN_INVALID');
      if (token.tenantOrigin !== config.baseUrl) fail('TENANT_MISMATCH');
      if (typeof token.uniqueName !== 'string' || !token.uniqueName) fail('OWNER_BINDING_MISSING');
      if (token.uniqueName.toLowerCase() !== config.account.toLowerCase()) fail('OWNER_MISMATCH');
      if (typeof token.accessToken !== 'string' || !token.accessToken.trim()
          || /[\r\n]/.test(token.accessToken) || token.accessToken.length > 65536
          || !Number.isFinite(token.capturedAt) || token.capturedAt > now()+60000
          || !Number.isFinite(token.expiresAt) || token.expiresAt <= token.capturedAt
          || !['browser','cache'].includes(token.source)) fail('TOKEN_INVALID');
      if (!allowExpired && token.expiresAt <= now()+300000) fail('SESSION_EXPIRED');
      if (token.cookieHeader !== undefined && (typeof token.cookieHeader !== 'string'
          || /[\r\n]/.test(token.cookieHeader) || token.cookieHeader.length > 65536)) fail('TOKEN_INVALID');

  if (token.csrfToken !== undefined && (typeof token.csrfToken!=='string' || /[\r\n\0]/.test(token.csrfToken))) fail('TOKEN_INVALID');
  return token;
}

// Dependencies are read-only capabilities: no save, mkdir, migration, mint, or login.
export class ReadOnlyTokens {
  constructor(config, {readRecord, readKey, now=Date.now}) {
    this.config=config; this.readRecord=readRecord; this.readKey=readKey; this.now=now;
    this.rejected=false; this.fingerprint=null;
  }
  invalidate() {this.rejected=true;}
  async getToken(rejected) {
    if (rejected) this.invalidate();
    if (this.rejected) fail('SESSION_REJECTED');
    let stage='SESSION_READ';
    try {
      const raw=await this.readRecord();
      const fingerprint=sha256(raw);
      if (this.fingerprint !== null && this.fingerprint !== fingerprint) fail('SESSION_CHANGED');
      stage='SESSION_FORMAT_UNSUPPORTED';
      const record=JSON.parse(raw);
      if (record?.version !== 2 || record.kind !== 'session') fail(stage);
      stage='KEYRING_READ';
      const key=await this.readKey();
      stage='SESSION_DECRYPT';
      const token=decryptSession(record,key);
      validateOwnerToken(token,this.config,{now:this.now});
      if (this.rejected) fail('SESSION_REJECTED');
      if (this.fingerprint !== null && this.fingerprint !== fingerprint) fail('SESSION_CHANGED');
      this.fingerprint=fingerprint;
      // Some read tools use getPage for course HTML, which needs the existing
      // cookie header. Keep it in memory only; there is no mint/recovery provider.
      return Object.freeze({accessToken:token.accessToken, capturedAt:token.capturedAt,
        expiresAt:token.expiresAt,source:token.source,tenantOrigin:token.tenantOrigin,
        cookieHeader:token.cookieHeader});
    } catch(error) {
      this.invalidate();
      if (error instanceof SessionValidationError) throw error;
      fail(stage);
    }
  }
  async sessionChanged() {await this.getToken();return false;}
}

export function readOnlyFetch(original, origin, tokens) {
  return async (input, options={}) => {
    await tokens.getToken();
    const url=new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    const method=options.method ?? input?.method ?? 'GET';
    if (url.origin !== origin || url.username || url.password || method !== 'GET' || options.body) fail();
    const response=await original(input,{...options,redirect:'error'});
    if (response.status === 401) {tokens.invalidate();fail();}
    return response;
  };
}

export function guardedRegistrar(server,tokens) {
  return {registerTool(name, metadata, handler) {
    if (!Object.hasOwn(FIELDS,name)) fail();
    return server.registerTool(name,metadata,async (args, ...rest) => {
      try {
        if (!args || typeof args !== 'object' || Array.isArray(args)
            || Object.keys(args).some(k=>!FIELDS[name].includes(k))) fail();
        const safe={...args};
        if (Object.values(safe).some(v=>v !== null && typeof v === 'object')) fail();
        for (const key of ['courseId','topicId'])
          if (Object.hasOwn(safe,key) && (!Number.isSafeInteger(safe[key]) || safe[key]<=0)) fail();
        for (const key of (name==='download_file' ? ['courseId','topicId'] : name==='get_syllabus' ? ['courseId'] : []))
          if (!Object.hasOwn(safe,key)) fail();
        await tokens.getToken();
        const result=await handler(safe,...rest);
        if (result?.isError) throw Error('Operation failed');
        return result;
      } catch {return {isError:true,content:[{type:'text',text:'Brightspace read unavailable; operator may need to reconnect. No automatic login or changes were attempted.'}]};}
    });
  }};
}
