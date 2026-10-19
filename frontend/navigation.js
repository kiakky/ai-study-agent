// Telegram's SDK supplies launch identity across WebView navigation.
// Every request still uses the existing server-side signature and expiry checks.
export const initData=window.Telegram?.WebApp?.initData||'';
const nav=document.createElement('nav');
nav.className='section-nav';nav.setAttribute('aria-label','Study Agent sections');
for(const [label,href] of [['Dashboard','/'],['Study Plan','/study'],['Quizzes','/?view=quizzes'],['Generated Practice','/practice'],['Grades','/?view=grades'],['Settings','/setup']]){
  const link=document.createElement('a');link.textContent=label;link.href=href;
  if(location.pathname+location.search===href)link.setAttribute('aria-current','page');
  nav.append(link);
}
document.querySelector('.app-header')?.after(nav);
