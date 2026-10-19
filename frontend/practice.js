import {initData} from '/static/navigation.js';
const root=document.querySelector('#content'),notice=document.querySelector('#notice');
let user=null,attempt=null,busy=false,poll=null;
const uuid=()=>crypto.randomUUID();
function el(tag,text='',attrs={}){const n=document.createElement(tag);n.textContent=text;for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;}
function button(text,fn){const n=el('button',text,{type:'button',class:'primary'});n.disabled=busy;n.onclick=()=>run(fn);return n;}
function showError(text){notice.textContent=text;notice.hidden=false;}
async function api(path,method='GET',body=null,extra={}){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  try{const r=await fetch('/api/practice'+path,{method,cache:'no-store',signal:controller.signal,
    headers:{'X-Telegram-Init-Data':initData,'Content-Type':'application/json',...extra},body:body===null?null:extra['Content-Type']==='application/pdf'?body:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'Request could not be completed.');return data;
  }finally{clearTimeout(timeout);}
}
async function run(fn){if(busy)return;busy=true;notice.hidden=true;document.querySelectorAll('button,input,textarea,select').forEach(n=>n.disabled=true);
  try{await fn();}catch(e){showError(e.message||'Connection interrupted. Reopen this page to recover saved practice.');}
  finally{busy=false;document.querySelectorAll('button,input,textarea,select').forEach(n=>n.disabled=false);}}
function key(id){return `study-agent:practice:v1:${user.id}:${id}`;}
function stash(id,data){localStorage.setItem(key(id),JSON.stringify(data));}
function saved(id){try{return JSON.parse(localStorage.getItem(key(id))||'null');}catch{return null;}}
function clear(id){localStorage.removeItem(key(id));}
function stopPoll(){if(poll)clearTimeout(poll);poll=null;}
async function home(){
  stopPoll();attempt=null;const data=await api('');root.replaceChildren(el('h1','Your generated practice'),
    el('p','Untimed practice from your own materials. Each attempt saves separately.'),
    el('p',`${data.stats.completed} completed / average ${data.stats.average===null?'--':data.stats.average+'%'} (latest ${data.history_limit} attempts)`),
    button('Create practice from materials',materials),button('Refresh',home));
  for(const job of data.jobs){const card=el('article','',{class:'review-card'});
    card.append(el('p',job.status==='failed'?'Preparation failed or was interrupted. No partial quiz was published; you can make a new request.':'Preparing your requested practice...'));
    root.append(card);}
  for(const a of data.attempts.filter(a=>a.status==='in_progress')){
    const set=data.sets.find(s=>s.id===a.set_id);
    root.append(button(`Continue ${set?.title||'practice'} (${a.answered} answered)`,()=>open(a.id)));}
  for(const set of data.sets){const card=el('article','',{class:'review-card'});card.append(el('h2',set.title),el('p',set.topic));
    const requestKey='start:'+set.id;const existing=saved(requestKey)||{request_id:uuid()};
    card.append(button('Start a new attempt',async()=>{stash(requestKey,existing);const a=await api('/sets/'+set.id+'/attempts','POST',existing);await open(a.id);clear(requestKey);}));root.append(card);}
  for(const a of data.attempts.filter(a=>a.status==='completed'))root.append(button(`Review ${data.sets.find(s=>s.id===a.set_id)?.title||'practice'} / ${a.score}%`,()=>open(a.id)));
  if(data.jobs.some(j=>['queued','running'].includes(j.status)))poll=setTimeout(()=>{if(!document.hidden&&!busy)run(home);},5000);
}
async function materials(){stopPoll();const rows=await api('/materials');root.replaceChildren(el('h1','Your learning materials'),button('Back to practice',home));
  const title=el('input','',{placeholder:'Material title',maxlength:'160','aria-label':'Material title'});
  const notes=el('textarea','',{placeholder:'Paste your study notes here (30 to 30,000 characters)',maxlength:'30000',rows:'8','aria-label':'Study notes'});
  const upload=el('input','',{type:'file',accept:'application/pdf,.pdf','aria-label':'Upload a text-based PDF'});
  root.append(title,notes,button('Save pasted notes',async()=>{await api('/materials','POST',{request_id:uuid(),title:title.value,text:notes.value});await materials();}),upload,
    button('Read PDF (up to 5 MB)',async()=>{const file=upload.files[0];if(!file||file.size>5*1024*1024||!file.name.toLowerCase().endsWith('.pdf'))throw new Error('Choose a PDF up to 5 MB.');
      await api('/materials/pdf?request_id='+uuid()+'&title='+encodeURIComponent(file.name.slice(0,160)),'POST',file,{'Content-Type':'application/pdf'});await materials();}));
  for(const m of rows){const card=el('article','',{class:'review-card'}),topic=el('input','',{placeholder:'Topic or section to practice','aria-label':'Practice topic'});
    const offset=el('select','',{'aria-label':'Material section'});
    const parts=Math.ceil(m.characters/6000);for(let part=0;part<parts;part++)offset.append(el('option',`Part ${part+1} of ${parts}`,{value:String(part*6000)}));
    const count=el('select','',{'aria-label':'Question count'});for(let i=1;i<=10;i++){const o=el('option',String(i),{value:String(i)});o.selected=i===5;count.append(o);}
    card.append(el('h2',m.title),el('p',m.kind==='pdf'?'Your uploaded PDF':'Your pasted notes'),...m.warnings.map(w=>el('p',w)),
      button('Read material excerpt',async()=>{const d=await api('/material?reference='+encodeURIComponent(m.reference)+'&offset='+Number(offset.value));
        const text=el('pre',d.text);text.style.whiteSpace='pre-wrap';card.append(text,el('p',d.next_offset===null?'End of material.':'More material is available in the next part.'));}),
      el('p','Preview a part, then choose the topic you want to practice from that material.'),offset,topic,count,
      button('Prepare practice',async()=>{if(!topic.value.trim())throw new Error('Enter a topic from this material.');
        const draftKey='generation:'+m.reference;let body=saved(draftKey);
        if(!body){body={request_id:uuid(),title:m.title.slice(0,140)+' practice',topic:topic.value,question_count:Number(count.value),sources:[{reference:m.reference,offset:Number(offset.value),length:6000}]};stash(draftKey,body);}
        await api('/jobs','POST',body);clear(draftKey);await home();}));root.append(card);}
}
async function open(id){stopPoll();attempt=await api('/attempts/'+id);if(attempt.status==='completed'){clear(id);return result(attempt.result);}
  const local=saved(id);if(local){root.replaceChildren(el('h1','Recover this attempt'),el('p','This device has an unsent save or submission. Retry that exact request, or reload the server version if another tab changed it.'),
    button('Retry saved request',async()=>{if(local.kind==='submit'){const r=await api('/attempts/'+id+'/submit','POST',local.body);clear(id);result(r);}
      else{await api('/attempts/'+id,'PUT',local.body);clear(id);await open(id);}}),
    button('Use server version instead',async()=>{clear(id);await open(id);}));return;}
  renderAttempt();}
function renderAttempt(){root.replaceChildren(el('h1',attempt.title),el('p','Answers save after each choice. You can leave and resume later.'),button('Back to all practice',home));
  for(const q of attempt.questions){const box=el('fieldset','',{class:'question'});box.append(el('legend',q.text));for(const o of q.options){const label=el('label','',{class:'option'}),radio=el('input','',{type:'radio',name:q.id,value:o.id});radio.checked=attempt.answers[q.id]===o.id;
      radio.onchange=()=>run(async()=>{if(saved(attempt.id))return open(attempt.id);const body={revision:attempt.revision,answers:{...attempt.answers,[q.id]:o.id}};stash(attempt.id,{kind:'save',body});
        const data=await api('/attempts/'+attempt.id,'PUT',body);clear(attempt.id);attempt.answers=body.answers;attempt.revision=data.revision;renderAttempt();});
      label.append(radio,el('span',o.id+'. '+o.text));box.append(label);}root.append(box);}
  root.append(button('Submit completed practice',async()=>{if(saved(attempt.id))return open(attempt.id);if(Object.keys(attempt.answers).length!==attempt.questions.length)throw new Error('Answer every question before submitting.');
    const body={revision:attempt.revision,answers:attempt.answers,submission_id:uuid()};stash(attempt.id,{kind:'submit',body});
    const data=await api('/attempts/'+attempt.id+'/submit','POST',body);clear(attempt.id);result(data);}));}
function result(data){stopPoll();root.replaceChildren(el('h1',data.title),el('h2',`${data.score}%`),button('Practice history',home));
  for(const q of data.feedback){const box=el('article','',{class:'review-card'});box.append(el('h3',q.text),el('p',q.correct?'Correct':'Incorrect'));
    for(const o of q.options)box.append(el('p',o.id+'. '+o.text+(o.id===q.selected_option?' / your answer':'')+(o.id===q.correct_option?' / correct answer':'')));
    box.append(el('p',q.explanation));if(!q.correct)box.append(el('p',q.correct_explanation));
    for(const c of q.citations)box.append(el('p',`Source: ${c.title} / ${c.quote}`));root.append(box);}}
window.Telegram?.WebApp?.ready();
await run(async()=>{if(!initData)throw new Error('Open this page inside Telegram to access your own practice.');
  const response=await fetch('/api/me',{headers:{'X-Telegram-Init-Data':initData},cache:'no-store'});if(!response.ok)throw new Error('Close and reopen the Mini App in Telegram.');user=await response.json();const id=new URLSearchParams(location.search).get('attempt');if(id)await open(id);else await home();});
