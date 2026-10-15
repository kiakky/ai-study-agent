import {initData} from '/static/navigation.js';
const telegram=window.Telegram?.WebApp;
const message=document.querySelector('#study-message');
let profile,proposals=[],calendarActions=[],sessions=[],busy=false;
function text(value){message.textContent=value;}
async function api(path,options={}){
  const response=await fetch(path,{...options,cache:'no-store',signal:AbortSignal.timeout(20000),headers:{'X-Telegram-Init-Data':initData,'Content-Type':'application/json'}});
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(response.status===401?'Reopen Study Plan inside Telegram.':typeof error.detail==='string'?error.detail:'Check your inputs and try again.');}
  return response.json();
}
async function action(operation){if(busy)return;busy=true;const buttons=[...document.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{await operation();}catch(error){text(error.message);}finally{busy=false;buttons.forEach(b=>b.disabled=false);}}
function element(tag,value){const node=document.createElement(tag);node.textContent=value;return node;}
async function refresh(){
  [profile,proposals]=await Promise.all([api('/api/profile'),api('/api/study/proposals')]);
  calendarActions=profile.google.calendar_actions_available?await api('/api/study/calendar-actions'):[];
  sessions=await api('/api/study/sessions');
  document.querySelector('#study-content').hidden=false;
  const list=document.querySelector('#proposals');list.replaceChildren();
  if(!proposals.length)list.append(element('p','No study plan has been proposed yet. Your AI Study Agent will prepare one when it has the necessary information.'));
  for(const proposal of proposals){
    const card=document.createElement('article');card.className='review-card';card.append(element('h3',proposal.subject+' · '+proposal.status));
    card.append(element('p',`${proposal.practice_format} · ${proposal.question_count} questions · ${proposal.difficulty} · ${proposal.timezone}`));
    const dates=new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:proposal.timezone});
    const actionForPlan=calendarActions.find(a=>a.proposal_id===proposal.id);
    for(const [index,original] of proposal.sessions.entries()){const current=sessions.find(s=>s.action_id===actionForPlan?.id&&s.session_index===index);const session=current||original;card.append(element('p',`${current?current.status+': ':''}${session.title||session.topic} · ${dates.format(new Date(session.start))} – ${dates.format(new Date(session.end))} · ${(new Date(session.end)-new Date(session.start))/60000} minutes`));}
    for(const warning of proposal.warnings)card.append(element('p',warning));
    card.append(element('p','Intended Calendar: primary Calendar of your connected Google account. Timezone: '+proposal.timezone));
    const calendarAction=calendarActions.find(a=>a.proposal_id===proposal.id);
    card.append(element('p',calendarAction?`Calendar action: ${calendarAction.status}. ${calendarAction.sessions.filter(s=>s.status==='created').length} event(s) confirmed.`:'No Calendar events have been created by this proposal.'));
    card.append(element('p','Review, approve or request changes in Telegram using /studyplan. Individual Calendar actions require your exact approval in chat.'));
    list.append(card);
  }
}
document.querySelector('#refresh').addEventListener('click',()=>action(refresh));
try{telegram?.ready();telegram?.expand();}catch{}
await action(async()=>{if(!initData){text('Open Study Plan from your Telegram bot.');return;}await refresh();text('View your saved plans here. Use /studyplan in Telegram for review and approval.');});
