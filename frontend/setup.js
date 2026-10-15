import {initData} from '/static/navigation.js';
const telegram = window.Telegram?.WebApp;
const message = document.querySelector('#setup-message');
const content = document.querySelector('#setup-content');
const form = document.querySelector('#preferences-form');
let profile;
let busy = false;

function status(text) { message.textContent = text; message.hidden = false; }
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options, cache: 'no-store', signal: AbortSignal.timeout(15000),
    headers: {'X-Telegram-Init-Data': initData, 'Content-Type': 'application/json'},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) throw new Error('Close and reopen Setup inside Telegram to verify your identity.');
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Check your settings and try again.');
  }
  return response.json();
}
async function action(operation) {
  if (busy) return;
  busy = true;
  const enabled = [...document.querySelectorAll('button:not(:disabled)')];
  enabled.forEach(button => { button.disabled = true; });
  try { await operation(); }
  catch (error) { status(error.name === 'TimeoutError' || error instanceof TypeError ? 'Connection interrupted. Refresh to check the saved state before retrying.' : error.message); }
  finally {
    busy = false;
    enabled.forEach(button => { if (button.isConnected) button.disabled = false; });
    if (profile) configureButtons();
  }
}
function addWindow(start = '', end = '') {
  if (document.querySelectorAll('.time-window').length >= 7) return;
  const row = document.createElement('div'); row.className = 'time-window';
  for (const [name, value] of [['Start', start], ['End', end]]) {
    const label = document.createElement('label'); label.textContent = name;
    const input = document.createElement('input'); input.type = 'time'; input.value = value.slice(0, 5); input.required = true;
    input.setAttribute('aria-label', `Preferred window ${name.toLowerCase()}`);
    label.append(input); row.append(label);
  }
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = 'Remove time window';
  remove.addEventListener('click', () => row.remove()); row.append(remove);
  document.querySelector('#study-windows').append(row);
}
function configureButtons() {
  document.querySelector('#connect-google').disabled = busy || !profile.google.available;
  document.querySelector('#disconnect-google').disabled = busy || profile.google.status === 'disconnected';
  document.querySelector('#skip-google').hidden = profile.onboarding.stage !== 'google';
  document.querySelector('#skip-brightspace').disabled = busy || profile.onboarding.stage !== 'brightspace';
}
async function refresh() {
  const [current, plan] = await Promise.all([api('/api/profile'), api('/api/studyplan')]);
  profile = current;
  content.hidden = false;
  const step = current.onboarding.stage;
  document.querySelector('#setup-progress').textContent = step === 'completed' ? 'Setup complete. You can update your profile anytime.' : `Resume setup: ${step}.`;
  document.querySelector('#google-status').textContent = `Connection: ${current.google.status.replaceAll('_', ' ')}. ${current.google.available ? (current.google.event_permission_granted ? 'Free/busy and owned-calendar event permission. Events still require exact approval.' : 'Free/busy access only.') : 'OAuth setup is currently unavailable; you can skip it.'}`;
  document.querySelector('#preference-state').textContent = current.preferences_saved ? 'Your saved preferences' : 'Suggested defaults; save to accept or change them.';
  document.querySelector('#timezone-label').textContent = `Times use ${current.timezone}.`;
  const days = document.querySelector('#study-days'); days.replaceChildren();
  for (const day of ['mon','tue','wed','thu','fri','sat','sun']) {
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'checkbox'; input.name = 'study-day'; input.value = day; input.checked = current.preferences.study_days.includes(day);
    label.append(input, document.createTextNode(day.toUpperCase())); days.append(label);
  }
  document.querySelector('#study-windows').replaceChildren();
  current.preferences.study_windows.forEach(window => addWindow(window.start, window.end));
  document.querySelector('#session-minutes').value = current.preferences.session_minutes;
  document.querySelector('#practice-format').value = current.preferences.practice_format;
  document.querySelector('#question-count').value = current.preferences.question_count;
  document.querySelector('#preference-notes').value = current.preferences.notes;
  document.querySelector('#studyplan-state').textContent = plan.state === 'not_generated' ? 'No study plan has been proposed yet. Your AI Study Agent will prepare one when it has the necessary information.' : `${plan.plans.length} saved plan(s). Use /studyplan in Telegram to review their state.`;
  document.querySelector('#study-planning-link').hidden = !plan.planning_available;
  configureButtons();
}
document.querySelector('#refresh-profile').addEventListener('click', () => action(refresh));
document.querySelector('#add-window').addEventListener('click', () => addWindow());
document.querySelector('#skip-google').addEventListener('click', () => action(async () => {
  await api('/api/onboarding/skip', {method:'POST', body:JSON.stringify({integration:'google'})}); await refresh(); status('Google Calendar skipped. Continue with the next setup step.');
}));
document.querySelector('#skip-brightspace').addEventListener('click', () => action(async () => {
  await api('/api/onboarding/skip', {method:'POST', body:JSON.stringify({integration:'brightspace'})}); await refresh(); status('Brightspace skipped. Save your study preferences to complete setup.');
}));
document.querySelector('#disconnect-google').addEventListener('click', () => action(async () => {
  await api('/api/integrations/google', {method:'DELETE'}); await refresh(); status('Local Google connection removed. To revoke the grant at Google too, use your Google Account permissions page.');
}));
document.querySelector('#connect-google').addEventListener('click', () => action(async () => {
  const result = await api('/api/integrations/google/connect', {method:'POST'});
  const link = new URL(result.authorization_start_url);
  if (link.origin !== location.origin || link.pathname !== '/oauth/google/start') throw new Error('Connection URL does not match this application.');
  status('Google authorization opens in your external browser. Return to Telegram and tap Refresh connection afterward.');
  if (telegram?.openLink) telegram.openLink(link.href);
  else window.open(link.href, '_blank', 'noopener,noreferrer');
}));
form.addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const preferences = {
      study_days: [...document.querySelectorAll('input[name="study-day"]:checked')].map(input => input.value),
      study_windows: [...document.querySelectorAll('.time-window')].map(row => { const [start,end] = row.querySelectorAll('input'); return {start:start.value, end:end.value}; }),
      session_minutes: Number(document.querySelector('#session-minutes').value),
      practice_format: document.querySelector('#practice-format').value,
      question_count: Number(document.querySelector('#question-count').value),
      notes: document.querySelector('#preference-notes').value,
    };
    await api('/api/preferences', {method:'PUT',body:JSON.stringify(preferences)}); await refresh(); status('Preferences saved. Your existing quiz results are unchanged.');
  });
});
try { telegram?.ready(); telegram?.expand(); } catch { }
await action(async () => {
  if (!initData) { status('Open Setup from your Study Agent bot inside Telegram. Google or school accounts are not required for demo quizzes.'); return; }
  await refresh(); status('Your profile is ready. Connections are optional.');
});
