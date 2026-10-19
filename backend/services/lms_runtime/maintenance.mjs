// Imported only by the explicit operator command, never by MCP startup.
import {validateOwnerToken} from './core.mjs';

export function browserExecutable(playwright) {
  const chromium=playwright.chromium ?? playwright.default?.chromium;
  if(!chromium)throw Error('BROWSER_NOT_INSTALLED');
  return chromium.executablePath();
}

export async function refreshOwnedSession(config,{load,mint,saveIfCurrent,now=Date.now}) {
  const stale=validateOwnerToken(await load(),config,{allowExpired:true,now});
  if (!stale.cookieHeader || !stale.csrfToken) throw Error('LOGIN_REQUIRED');
  // Only the existing owner's existing tenant-bound material may be exchanged.
  const result=await mint({baseUrl:config.baseUrl,cookieHeader:stale.cookieHeader,csrfToken:stale.csrfToken});
  if (!result?.ok) throw Error('LOGIN_REQUIRED'); // preserve the old state on all failures
  const current=now();
  const renewed={...stale,accessToken:result.accessToken,capturedAt:current,expiresAt:current+3600000,source:'browser'};
  validateOwnerToken(renewed,config,{now});
  if (!await saveIfCurrent(renewed,stale)) throw Error('SESSION_CHANGED');
  return 'REFRESHED';
}

export async function loginOwnedSession(config,{BrowserAuth,save}) {
  const auth=new BrowserAuth({baseUrl:config.baseUrl,username:config.account,sessionDir:config.sessionDir,
    headless:false,tokenTtl:3600,rememberMfa:false,passwordless:false,courseFilter:{activeOnly:true}});
  let staged;
  const persistBrowserState=auth.saveBrowserState.bind(auth);
  // BrowserAuth normally saves before identity verification. Defer that write.
  auth.saveBrowserState=async state=>{staged=state;};
  auth.keepNewerMicrosoftState=async()=>{};
  auth.navigateAndLogin=async page=>{
    await page.goto(config.baseUrl+'/d2l/home',{waitUntil:'domcontentloaded',timeout:60000});
    await auth.waitForVisibleLogin(page); // credentials/MFA entered only in school browser
  };
  let saved=false;
  await auth.authenticate({automatic:false,onAuthenticated:async token=>{
    validateOwnerToken(token,config);
    await save(token);
    saved=true;
    if (staged) await persistBrowserState(staged);
  }});
  if (!saved) throw Error('LOGIN_NOT_COMPLETED');
  return 'SIGNED_IN';
}
