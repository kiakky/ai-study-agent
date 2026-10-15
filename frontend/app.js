import {initData} from '/static/navigation.js';
const content = document.querySelector('#content');
const notice = document.querySelector('#notice');
const tg = window.Telegram?.WebApp;
const state = {user: null, profile: null, proposals: [], sessions: [], quizzes: [], grades: [], more: false, page: 'dashboard', draft: null, quiz: null, index: 0, busy: false};

function replaceContent(...children) {
  content.replaceChildren(...children.filter(child => child !== null && child !== undefined));
}

// Only textContent is used for API-provided strings, including question content.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function button(label, action, cls = 'primary', disabled = false) {
  const node = el('button', {class: cls, type: 'button', onclick: () => run(action)}, label);
  node.disabled = disabled || state.busy;
  return node;
}
function showError(message) {
  notice.replaceChildren(el('p', {}, message));
  notice.hidden = false;
}
async function run(action) {
  if (state.busy) return;
  notice.hidden = true;
  state.busy = true;
  content.setAttribute('aria-busy', 'true');
  const enabled = [...document.querySelectorAll('button:not(:disabled)')];
  enabled.forEach(node => { node.disabled = true; });
  try { await action(); }
  catch (error) { showError(error.message || 'Something went wrong. Please try again.'); }
  finally {
    state.busy = false;
    content.removeAttribute('aria-busy');
    enabled.forEach(node => { if (node.isConnected) node.disabled = false; });
    // New nodes rendered during the request have their own eligibility rules.
    document.querySelectorAll('[data-nav]').forEach(node => { node.disabled = !state.user; });
    if (state.page === 'quiz' && state.quiz) renderQuiz();
    else document.querySelectorAll('button[data-ready]').forEach(node => { node.disabled = false; });
  }
}
function actionButton(label, action, cls = 'primary', disabled = false) {
  const node = button(label, action, cls, disabled);
  if (!disabled) node.dataset.ready = 'true';
  return node;
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      ...options, signal: controller.signal, cache: 'no-store',
      headers: {'X-Telegram-Init-Data': initData, 'Content-Type': 'application/json'},
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      if (response.status === 401) throw new Error('Your Telegram session has expired or could not be verified. Close and reopen the Mini App in Telegram. Your draft is kept on this device.');
      throw new Error(typeof body.detail === 'string' ? body.detail : 'The request could not be completed. Please try again.');
    }
    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('Connection interrupted. Your answers are kept on this device. Try again; retrying a submission will not create a duplicate result.');
    throw error;
  } finally { clearTimeout(timer); }
}
const draftKey = () => `study-agent:draft:v1:${state.user.id}`;
function saveDraft() {
  try { localStorage.setItem(draftKey(), JSON.stringify(state.draft)); }
  catch { throw new Error('Device storage is unavailable. Enable storage for this page so your answers and submission retry ID can be kept safely.'); }
}
function loadDraft() {
  try {
    const value = JSON.parse(localStorage.getItem(draftKey()) || 'null');
    if (value && typeof value.submission_id === 'string' && Number.isInteger(value.quiz_id) && value.answers && typeof value.answers === 'object') state.draft = value;
  } catch { showError('A saved draft could not be read. You can start a new practice quiz.'); }
}
function dateLabel(value) {
  const date = new Date(value.includes('T') ? value : `${value.replace(' ', 'T')}Z`);
  return new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}).format(date);
}
function setPage(page) {
  state.page = page;
  document.querySelectorAll('[data-nav]').forEach(node => {
    if (node.dataset.nav === (page === 'quiz' ? 'quizzes' : page === 'result' ? 'grades' : page)) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  window.scrollTo({top: 0, behavior: 'instant'});
}
function intro(kicker, title, description) {
  return el('div', {class: 'intro'}, el('span', {class: 'eyebrow'}, kicker), el('h1', {}, title), el('p', {}, description));
}
function demoNote() { return el('p', {class: 'footnote'}, 'Demo content only. These questions are not from Brightspace and are not generated by AI.'); }
function quizCard(quiz) {
  const resume = state.draft?.quiz_id === quiz.id;
  return el('article', {class: 'hero'}, el('span', {class: 'tag'}, 'DEMO PRACTICE'),
    el('h2', {}, quiz.title), el('p', {}, 'A little practice. A clearer picture of what you know.'),
    el('div', {class: 'meta'}, el('span', {}, `${quiz.question_count} questions`), el('span', {}, 'Multiple choice'), el('span', {}, 'Untimed')),
    actionButton(el('span', {}, resume ? (state.draft.pending ? 'Recover submission' : 'Continue practice') : 'Start practice'), () => startQuiz(quiz.id)),
    demoNote());
}
function gradeRow(grade) {
  return actionButton([
    el('span', {class: 'score-tile'}, `${grade.score}%`),
    el('span', {class: 'row-copy'}, el('strong', {}, grade.exam_title), el('small', {}, `${grade.demo ? 'Demo · ' : ''}${dateLabel(grade.completed_at)}`)),
    el('span', {class: 'arrow', 'aria-hidden': 'true'}, '↗'),
  ], () => openAttempt(grade.id), 'grade-row');
}
function emptyResults() {
  return el('div', {class: 'empty'}, el('h3', {}, 'Your progress starts here'), el('p', {}, 'Complete a practice quiz to see your score and review each answer.'), actionButton('Explore quizzes', () => navigate('quizzes'), 'secondary'));
}
function renderDashboard() {
  setPage('dashboard');
  const stats = state.user.stats;
  replaceContent(
    intro('A LITTLE PROGRESS, EVERY DAY', `Hey, ${state.user.first_name || 'Student'}.`, 'Make room for a little revision. Your next practice is ready when you are.'),
    el('article', {class: 'review-card'}, el('h2', {}, 'Your study plan'),
      el('p', {}, 'Google Calendar: '+(state.profile.google.status === 'expired' ? 'token refresh needed on next use' : state.profile.google.status.replaceAll('_',' '))+' - '+(state.profile.google.event_permission_granted ? 'Availability and owned-event permission' : 'Availability permission only')),
      el('p', {}, state.proposals.length ? `${state.proposals.filter(p=>p.status==='approved').length} finalized plan(s), ${state.proposals.filter(p=>p.status==='draft').length} draft proposal(s). Exact Calendar approval happens in Telegram.` : 'No study plan has been proposed yet. Your AI Study Agent will prepare one when it has the necessary information.'),
      ...state.sessions.filter(s=>s.status==='scheduled'&&new Date(s.start)>new Date()).sort((a,b)=>new Date(a.start)-new Date(b.start)).slice(0,3).map(session=>el('p', {}, `${session.title} - ${new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:session.timezone}).format(new Date(session.start))} - ${session.timezone} (Calendar confirmed)`)),
      el('a', {href:'/study',class:'secondary'}, 'Review Study Plan'), el('a', {href:'/setup',class:'secondary'}, 'Setup / Preferences')),
    ...state.quizzes.map(quizCard),
    el('div', {class: 'stats'}, ...[
      [stats.completed, 'Quizzes completed'], [stats.average_score === null ? '—' : `${stats.average_score}%`, 'Average score'], [stats.best_score === null ? '—' : `${stats.best_score}%`, 'Personal best'],
    ].map(([value, label]) => el('div', {class: 'stat'}, el('strong', {}, value), el('small', {}, label)))),
    el('div', {class: 'section-head'}, el('h2', {}, 'Recent activity'), actionButton('View all ↗', () => navigate('grades'), 'text-button')),
    state.grades.length ? el('div', {class: 'list'}, ...state.grades.slice(0, 3).map(gradeRow)) : emptyResults(),
  );
}
function renderQuizzes() {
  setPage('quizzes');
  replaceContent(intro('YOUR PRACTICE SPACE', 'Small steps. Stronger skills.', 'Choose a quiz and take it at your own pace. Every deliberate retake gets its own saved result.'), ...state.quizzes.map(quizCard));
}
function renderGrades() {
  setPage('grades');
  replaceContent(intro('LOOK HOW FAR YOU’VE COME', 'Your results', 'Revisit your answers, learn from the explanations, and try again whenever you like.'),
    state.grades.length ? el('div', {class: 'list'}, ...state.grades.map(gradeRow)) : emptyResults(),
    state.more ? actionButton('Load earlier results', async () => {
      const rows = await api(`/api/grades?offset=${state.grades.length}`);
      state.grades.push(...rows); state.more = rows.length === 30; renderGrades();
    }, 'secondary wide history-more') : null);
}
async function refresh() {
  const [user, quizzes, grades, profile, proposals, sessions] = await Promise.all([api('/api/me'), api('/api/quizzes'), api('/api/grades'), api('/api/profile'), api('/api/study/proposals'), api('/api/study/sessions')]);
  state.profile = profile; state.proposals = proposals; state.sessions = sessions;
  state.user = user; state.quizzes = quizzes; state.grades = grades; state.more = grades.length === 30;
}
async function navigate(page) {
  if (!state.user) return;
  if (page !== 'quizzes') await refresh();
  ({dashboard: renderDashboard, quizzes: renderQuizzes, grades: renderGrades})[page]();
}
async function startQuiz(id, retake = false) {
  if (retake && state.draft) {
    throw new Error('You already have a practice in progress. Open Quizzes to finish it or recover its submission before starting a new retake.');
  }
  const quiz = await api(`/api/quizzes/${id}`);
  if (!state.draft || state.draft.quiz_id !== id || retake) {
    state.draft = {quiz_id: id, submission_id: crypto.randomUUID(), answers: {}, pending: null};
    saveDraft();
  }
  state.quiz = quiz;
  const next = quiz.questions.findIndex(q => !state.draft.answers[q.id]);
  state.index = next < 0 ? quiz.questions.length - 1 : next;
  setPage('quiz'); renderQuiz();
}
function renderQuiz() {
  const quiz = state.quiz;
  const draft = state.draft;
  const question = quiz.questions[state.index];
  if (!question) { showError('This quiz has no questions yet.'); return; }
  const answered = quiz.questions.filter(q => draft.answers[q.id]).length;
  const locked = !!draft.pending;
  const fieldset = el('fieldset', {class: 'question'}, el('legend', {}, question.text));
  for (const option of question.options) {
    const radio = el('input', {type: 'radio', name: `question-${question.id}`, value: option.id});
    radio.checked = draft.answers[question.id] === option.id;
    radio.disabled = locked || state.busy;
    radio.addEventListener('change', () => {
      draft.answers[question.id] = option.id;
      try { saveDraft(); notice.hidden = true; } catch (error) { showError(error.message); }
      renderQuiz();
      [...content.querySelectorAll('input[type="radio"]')]
        .find(input => input.value === option.id)?.focus({preventScroll: true});
    });
    fieldset.append(el('label', {class: 'option'}, radio, el('span', {class: 'letter', 'aria-hidden': 'true'}, option.id), el('span', {class: 'option-text'}, option.text)));
  }
  replaceContent(
    el('div', {class: 'quiz-top'}, actionButton('← All quizzes', () => navigate('quizzes'), 'text-button'), el('span', {class: 'tag'}, 'DEMO · MCQ')),
    el('div', {class: 'progress-label'}, el('span', {}, `Question ${state.index + 1} of ${quiz.questions.length}`), el('span', {}, `${answered} answered`)),
    el('progress', {value: answered, max: quiz.questions.length, 'aria-label': 'Questions answered'}), fieldset,
    el('div', {class: 'quiz-actions'}, actionButton('← Previous', () => { state.index--; renderQuiz(); }, 'secondary', state.index === 0),
      state.index < quiz.questions.length - 1 ? actionButton('Next →', () => { state.index++; renderQuiz(); }) : actionButton(locked ? 'Retry submission' : 'Submit answers', submit, 'primary', answered !== quiz.questions.length)),
    el('div', {class: 'question-map', 'aria-label': 'Jump to question'}, ...quiz.questions.map((q, i) => {
      const node = actionButton(String(i + 1), () => { state.index = i; renderQuiz(); }, draft.answers[q.id] ? 'answered' : '');
      node.setAttribute('aria-label', `Question ${i + 1}${draft.answers[q.id] ? ', answered' : ', unanswered'}`);
      if (i === state.index) node.setAttribute('aria-current', 'step');
      return node;
    })),
    el('p', {class: 'footnote'}, locked ? 'Submission sent. Answers are locked until the result is confirmed. Use Retry submission on the last question to recover the same result.' : 'Answers are saved on this device as you go. Answer every question before submitting. There’s no timer for this practice.'),
  );
}
async function submit() {
  const draft = state.draft;
  if (!draft.pending) {
    draft.pending = {submission_id: draft.submission_id, answers: state.quiz.questions.map(q => ({question_id: q.id, option: draft.answers[q.id]}))};
  }
  // Persist the immutable request BEFORE sending. An uncertain response must reuse it.
  saveDraft();
  const result = await api(`/api/quizzes/${draft.quiz_id}/submit`, {method: 'POST', body: JSON.stringify(draft.pending)});
  try { localStorage.removeItem(draftKey()); } catch { /* Retrying the receipt remains safe. */ }
  state.draft = null;
  state.quiz = null;
  renderResult(result);
}
async function openAttempt(id) { renderResult(await api(`/api/attempts/${id}`)); }
function renderResult(result) {
  setPage('result');
  replaceContent(
    actionButton('← Grade history', () => navigate('grades'), 'text-button'),
    el('section', {class: 'result-hero'}, el('span', {class: 'eyebrow'}, 'PRACTICE COMPLETE · RESULT SAVED'),
      el('div', {class: 'score-ring'}, result.score, el('small', {}, '%')),
      el('h1', {}, result.score >= 80 ? 'Nicely done.' : 'A step forward.'), el('p', {}, `${result.correct} of ${result.total} correct · ${result.title}`),
      el('p', {class: 'footnote'}, `${result.demo ? 'Demo practice · ' : ''}${dateLabel(result.completed_at)}`),
      actionButton('Practise again ↗', () => startQuiz(result.quiz_id, true)),
      el('p', {class: 'footnote'}, 'Starts a new attempt. This result stays in your history.')),
    el('div', {class: 'section-head'}, el('h2', {}, 'Let’s review'), el('span', {class: 'tag'}, `${result.total} ANSWERS`)),
    ...result.feedback.map((answer, i) => el('article', {class: 'review-card'},
      el('div', {class: `review-status ${answer.correct ? '' : 'incorrect'}`}, `${answer.correct ? '✓ CORRECT' : '↗ KEEP LEARNING'} · QUESTION ${i + 1}`),
      el('h3', {}, answer.text),
      ...answer.options.map(option => el('div', {class: `review-option ${option.id === answer.correct_option ? 'correct' : option.id === answer.selected_option ? 'incorrect' : ''}`},
        `${option.id}. ${option.text}${option.id === answer.selected_option ? ' · Your answer' : ''}${option.id === answer.correct_option ? ' · Correct answer' : ''}`)),
      el('p', {class: 'explanation'}, answer.explanation || 'No explanation was provided.'))),
    result.demo ? demoNote() : null,
  );
}
document.querySelectorAll('[data-nav]').forEach(node => {
  node.disabled = true;
  node.addEventListener('click', () => run(() => navigate(node.dataset.nav)));
});
try { tg?.ready(); tg?.expand(); tg?.setHeaderColor('#101218'); tg?.setBackgroundColor('#101218'); } catch { /* Older Telegram clients use the page styles. */ }
await run(async () => {
  if (!initData) {
    replaceContent(intro('YOUR STUDY SPACE', 'One less thing to worry about.', 'Practice, feedback and progress. All in one place.'),
      el('div', {class: 'empty'}, el('span', {class: 'eyebrow'}, 'SECURE TELEGRAM SIGN-IN'), el('h2', {}, 'Open inside Telegram'),
        el('p', {}, 'Launch this Mini App from your Study Agent bot to access your quizzes and saved grades. A regular browser cannot verify your Telegram identity.'),
        el('p', {class: 'footnote'}, 'Setting up locally? Follow the local preview instructions in README.md for an isolated synthetic student.')));
    return;
  }
  try { await refresh(); }
  catch (error) {
    replaceContent(el('div', {class: 'empty'}, el('h1', {}, 'Couldn’t connect yet'), el('p', {}, 'Check your connection, or close and reopen this Mini App in Telegram.'), actionButton('Try again', () => location.reload(), 'secondary')));
    throw error;
  }
  loadDraft();
  if (new URLSearchParams(location.search).get('view') === 'quizzes') renderQuizzes();
  else if (new URLSearchParams(location.search).get('view') === 'grades') await navigate('grades');
  else renderDashboard();
});
