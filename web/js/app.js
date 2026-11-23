// DevTrack 웹 앱 — 정적 호스팅(읽기 전용)과 로컬 서버(쓰기 가능) 모두에서 동작
import {
  html, raw, icon, STATUS, TYPES, APPROVAL_STATUS, APPROVAL_KIND, LOG_KIND, SEVERITY, ART_KIND,
  FINDING_SEV, FINDING_STATUS, VERDICT, CONFIDENCE, DOC_KIND, CHECK_RATING, pick, badge, ago, fmtAbs, fmtRel, fmtBytes, authorName, shellQuote, copyText, LS, tsNum,
  isUnassigned, aiName, agentLabel, MODELS, setModelCatalog, REVIEW_STATE, effortsFor, effortLabel, normalizeModel, specLabel, specMatches,
} from './util.js';
import { artifactUrl, renderArtifactInto, renderMarkdown, enhance } from './viewers.js';
import { L, translated, translatedStrings, getLang, setLang, applyStatic, locale } from './i18n.js';
import { checkAccess, showLock, forgetKey, isLocked } from './lock.js';

/** 번역 문구 안의 {이름} 자리에 값을 이스케이프해 넣은 HTML (번역 문구 자체의 태그는 그대로) */
const LH = (ko, vars = {}) => raw(L(ko).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(html`${vars[k]}`) : m)));

const POLL_MS = 10000;
const ACTIVE = ['in_progress', 'awaiting_approval', 'changes_requested', 'blocked', 'queued'];
const DECISION = translated({
  approve: { label: '승인', tone: 'green' },
  changes: { label: '수정 요청', tone: 'orange' },
  reject: { label: '거절', tone: 'red' },
});
// 작업 유형 → 분야 섹션 (사이드바 메뉴)
const SECTION_OF_TYPE = { code: 'code', paper: 'papers', analysis: 'analysis', visualization: 'analysis', review: 'reviews', docreview: 'docreview', brainstorm: 'brainstorm' };
const SECTION_LABEL = translatedStrings({ code: '코드 개발', papers: '논문 작성', analysis: '데이터 분석', reviews: '코드 리뷰', docreview: '문서 리뷰', brainstorm: '브레인스토밍', tasks: '작업' });
// 작업을 고른 채로 여는 분야 화면 (#/brainstorm/T-0009)
const PER_TASK_SECTIONS = ['brainstorm', 'docreview'];
const ANALYSIS_TYPES = ['analysis', 'visualization'];
const FIGURE_KINDS = ['image', 'html'];

const state = {
  mode: 'static',
  access: null, // 웹 비밀번호 (lock.js 의 checkAccess 결과 — auth 가 있으면 비밀번호가 걸린 사이트)
  index: null,
  loadError: null,
  tasks: new Map(),
  knownPending: null,
  lastEventTs: 0,
  details: new Map(),
  taskSearch: '',
  listeners: null, // 웹 입력을 기다리는 AI (로컬 서버 모드만, null = 알 수 없음)
  guides: {}, // 사용 방법 문서 (파일 이름 → 본문)
  pendingRender: false, // 입력 중이라 미뤄 둔 화면 갱신 — 입력이 끝나면 저절로 반영
  writes: 0, // 보내는 중인 쓰기 요청 수 (그동안은 화면을 다시 그리지 않음)
  pointerDown: false, // 마우스 · 터치를 누르고 있는 중 (그동안 다시 그리면 누르던 버튼의 클릭이 사라짐)
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

// ====================================================================== 데이터
async function getJSON(path) {
  const r = await fetch(`${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`, { cache: 'no-store' });
  if (!r.ok) throw Object.assign(new Error(`${r.status} ${r.statusText || ''} — ${path}`), { status: r.status });
  return r.json();
}

async function detectMode() {
  try {
    const r = await fetch('api/health', { cache: 'no-store' });
    if (r.ok) {
      const j = await r.json();
      if (j && j.mode === 'live') return 'live';
    }
  } catch { /* 정적 호스팅 */ }
  return 'static';
}

async function loadTask(id) {
  if (state.tasks.has(id)) return state.tasks.get(id);
  const t = await getJSON(`data/tasks/${encodeURIComponent(id)}/task.json`);
  state.tasks.set(id, t);
  return t;
}

async function api(path, body) {
  state.writes++;
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let j = {};
    try { j = await r.json(); } catch { /* 빈 응답 */ }
    if (!r.ok) throw new Error(j.error || `${r.status} ${r.statusText}`);
    return j;
  } finally {
    state.writes--;
  }
}

// ====================================================================== 라우팅 / 렌더링
function parseRoute() {
  const h = location.hash.replace(/^#\/?/, '');
  const [p, qs = ''] = h.split('?');
  const parts = p.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || 'dashboard', rest: parts.slice(1), q: Object.fromEntries(new URLSearchParams(qs)) };
}

const VIEWS = {
  dashboard: viewDashboard,
  tasks: viewTasks,
  task: viewTask,
  approvals: viewApprovals,
  code: viewCode,
  reviews: viewReviews,
  papers: viewPapers,
  analysis: viewAnalysis,
  brainstorm: viewBrainstorm,
  docreview: viewDocReview,
  artifacts: viewArtifacts,
  artifact: viewArtifact,
  activity: viewActivity,
  new: viewNewTask,
  guide: viewGuide,
};

let renderSeq = 0;
/** auto: 새 데이터로 저절로 다시 그리는 경우 — 그사이 입력을 시작했으면 바꾸지 않고 미룬다 */
async function render({ keepScroll = false, auto = false } = {}) {
  const seq = ++renderSeq;
  const route = parseRoute();
  const key = [route.name, ...route.rest].join('/');
  const sameView = state.routeKey === key;
  state.routeKey = key;
  const main = $('#main');
  saveDetails(main);
  const y = window.scrollY;
  updateChrome(route);
  let view;
  try {
    if (!state.index && route.name !== 'guide') view = state.loadError ? viewLoadError(state.loadError) : html`<div class="empty">${L('불러오는 중…')}</div>`;
    else view = VIEWS[route.name] ? await VIEWS[route.name](route) : viewNotFound();
  } catch (e) {
    console.error(e);
    view = viewError(e);
  }
  if (seq !== renderSeq) return;
  if (auto && isBusy()) { deferRender(); return; }
  const drafts = sameView ? saveDrafts(main) : null;
  main.innerHTML = String(view);
  restoreDetails(main);
  if (drafts) restoreDrafts(main, drafts);
  hydrate(main);
  state.pendingRender = false;
  $('#update-pill').classList.remove('show');
  window.scrollTo(0, keepScroll ? y : 0);
}

/** 같은 화면을 다시 그릴 때 입력 칸에 써 둔 글(보내기 전 메시지 · 코멘트)을 그대로 둔다 */
function saveDrafts(root) {
  const drafts = new Map();
  $$('textarea[id], input[id]', root).forEach((el) => {
    if (el.id !== 'task-search' && !['checkbox', 'radio', 'hidden'].includes(el.type) && el.value) drafts.set(el.id, el.value);
  });
  return drafts;
}
function restoreDrafts(root, drafts) {
  drafts.forEach((v, id) => {
    const el = document.getElementById(id);
    if (el && root.contains(el) && !el.value) el.value = v;
  });
}

/** 입력 중이라 지금은 다시 그리지 않음 — '새 업데이트 보기' 를 띄우고, 입력이 끝나면 저절로 반영 */
function deferRender() {
  state.pendingRender = true;
  $('#update-pill').classList.add('show');
  updateChrome();
}

/** 입력이 끝났으면 미뤄 둔 갱신을 반영 */
function applyPending() {
  if (state.pendingRender && !isBusy()) render({ keepScroll: true, auto: true });
}

function hydrate(root) {
  enhance(root);
  $$('[data-av]', root).forEach((el) => {
    const d = el.closest('details');
    if (d && !d.open) {
      d.addEventListener('toggle', () => d.open && mountArtifact(el));
      return;
    }
    mountArtifact(el);
  });
  const search = $('#task-search', root);
  if (search) {
    search.value = state.taskSearch;
    search.addEventListener('input', () => { state.taskSearch = search.value; filterTasks(); });
    filterTasks();
  }
  const form = $('#new-task-form', root);
  if (form) {
    form.addEventListener('submit', submitNewTask);
    // 고르기만 한 항목(유형 · 담당 AI …)도 다시 그리면 사라지므로, 손대기 시작하면 저절로 다시 그리지 않는다
    const touch = () => { form.dataset.dirty = '1'; };
    form.addEventListener('input', touch);
    form.addEventListener('change', touch);
  }
  const guide = $('.guide-body', root);
  if (guide) setupGuide(guide, $('.guide-toc', root));
}

function mountArtifact(el) {
  if (el.dataset.mounted) return;
  el.dataset.mounted = '1';
  const [tid, fid] = el.dataset.av.split('/');
  const task = state.tasks.get(tid);
  const art = task?.artifacts?.find((a) => a.id === fid);
  if (!art) { el.innerHTML = `<div class="av-error">${L('결과물을 찾을 수 없습니다')}</div>`; return; }
  const ok = buildInfo(task)?.ok;
  renderArtifactInto(el, tid, art, {
    resolveImage: (p) => resolveFigure(tid, p),
    resolveFile: (p, o) => resolveFile(tid, p, o),
    pdfHref: ok ? `#/artifact/${tid}/${ok.pdf}` : null,
  });
}

/** 결과물을 저장할 때의 파일 이름 정리 (store.py 의 _safe_name 과 같게) — index.json 에 원래 이름(filename)이 없던 기록용 */
function safeName(name) {
  const base = baseName(name) || 'file';
  const i = base.lastIndexOf('.');
  const [stem, ext] = i > 0 ? [base.slice(0, i), base.slice(i)] : [base, ''];
  return (stem.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[._]+|[._]+$/g, '') || 'file').slice(0, 100)
    + ext.replace(/[^A-Za-z0-9.]+/g, '').slice(0, 12);
}

/** 마크다운 리포트의 ![](figs/fig8.png) · [스크립트](analyze.py) → 같은 파일 이름으로 올라온 결과물.
 *  올릴 때의 원래 이름(filename)으로 찾고, 없으면 저장된 이름(F-<n>_ 뒤)과 비교한다. 그림(image)은 이름이 같은 그림이 없으면
 *  확장자만 다른 그림도 받는다. 같은 작업 > 연결된 작업 > 그 외, 같으면 나중에 올린 것 */
function resolveFile(tid, ref, { image = false } = {}) {
  let p = String(ref).split(/[?#]/)[0];
  try { p = decodeURIComponent(p); } catch { /* 그대로 */ }
  const want = baseName(p).toLowerCase();
  if (!want) return null;
  const stemOf = (n) => n.replace(/\.[^.]*$/, '');
  const stored = (a) => baseName(a.path).replace(/^F-\d+_/, '').toLowerCase();
  // 한글 등이 섞인 이름은 저장할 때 글자가 빠져(그림2.png → 2.png) 다른 그림과 맞을 수 있으므로 저장된 이름으로 비교하지 않는다
  const wantSafe = safeName(want).toLowerCase();
  const safeOk = /^[\x20-\x7e]+$/.test(want);
  const arts = (state.index?.artifacts || []).filter((a) => a.path && (!image || a.kind === 'image'));
  const pick = (same) => arts.filter((a) => (a.filename ? same(a.filename.toLowerCase(), want) : safeOk && same(stored(a), wantSafe)));
  let cands = pick((x, y) => x === y);
  if (!cands.length && image) cands = pick((x, y) => stemOf(x) === stemOf(y));
  if (!cands.length) return null;
  const related = new Set(linksOf(tid).map((x) => x.id));
  const rank = (a) => (a.task_id === tid ? 0 : related.has(a.task_id) ? 1 : 2);
  const a = cands.sort((x, y) => rank(x) - rank(y) || String(y.created_at || '').localeCompare(String(x.created_at || '')))[0];
  return { url: artifactUrl(a.task_id, a), title: `${a.title} (${a.task_id} · ${a.id})`, href: `#/artifact/${a.task_id}/${a.id}` };
}

/** \includegraphics{figs/fig4_1.pdf} → 같은 이름(확장자 무관)으로 올라온 그림 결과물. 같은 작업 > 연결된 작업 > 그 외 순 */
function resolveFigure(tid, ref) {
  const stemOf = (p) => baseName(p).replace(/^F-\d+_/, '').replace(/\.[A-Za-z0-9]+$/, '').toLowerCase();
  const want = stemOf(ref);
  if (!want) return null;
  const cands = (state.index?.artifacts || []).filter((a) => a.path && ['image', 'pdf'].includes(a.kind) && stemOf(a.path) === want);
  if (!cands.length) return null;
  const related = new Set(linksOf(tid).map((x) => x.id));
  const rank = (a) => (a.task_id === tid ? 0 : related.has(a.task_id) ? 1 : 2) * 2 + (a.kind === 'image' ? 0 : 1);
  const a = cands.sort((x, y) => rank(x) - rank(y))[0];
  return { url: artifactUrl(a.task_id, a), kind: a.kind, title: `${a.title} (${a.task_id})`, href: `#/artifact/${a.task_id}/${a.id}` };
}

function saveDetails(root) { $$('details[data-key]', root).forEach((d) => state.details.set(d.dataset.key, d.open)); }
function restoreDetails(root) {
  $$('details[data-key]', root).forEach((d) => { if (state.details.has(d.dataset.key)) d.open = state.details.get(d.dataset.key); });
}

/** 지금 다시 그리면 사용자가 하던 일이 끊기는가 — 입력 칸에 커서가 있음(한글 조합 중일 수 있음) · 누르는 중 ·
 *  보내는 중 · 승인 선택지를 고름 · 새 작업 지시를 쓰기 시작함. 써 두기만 한 글은 다시 그려도 남으므로(saveDrafts) 막지 않는다 */
function isBusy() {
  if (state.pointerDown || state.writes > 0) return true;
  const main = $('#main');
  const a = document.activeElement;
  if (a && main.contains(a) && /^(TEXTAREA|INPUT|SELECT)$/.test(a.tagName) && a.id !== 'task-search') return true;
  if ($('.choice-box[data-dirty]', main)) return true;
  return newTaskTouched();
}

/** 새 작업 지시를 쓰기 시작했는가 (고르기만 한 항목 포함 — 폼 항목은 다시 그리면 사라진다) */
function newTaskTouched() {
  const form = $('#new-task-form', $('#main'));
  return !!form && (!!form.dataset.dirty || $$('textarea, input[type="text"]', form).some((t) => t.value.trim()));
}

function isRelevant(changed, prev, idx) {
  if (!prev) return true;
  const r = parseRoute();
  if (r.name === 'guide') return false;
  if (r.name === 'new') return !newTaskTouched(); // 쓰던 지시를 '새 업데이트 보기' 로 날리지 않게 — 이 화면은 기록과 거의 무관
  if (r.name === 'task' || r.name === 'artifact') return changed.has(r.rest[0]);
  if (r.name === 'reviews') {
    const ids = (x) => codeReviews(x).map((v) => v.task_id + v.id).join();
    return ids(prev) !== ids(idx) || changed.has(r.rest[0] || codeReviews(idx)[0]?.task_id);
  }
  return true;
}

// ====================================================================== 웹 지시를 기다리는 AI
// devtrack inbox --wait (대기 모드) · devtrack watch 가 실행 중일 때만 "기다리는 중"으로 본다.
// 로컬 서버 모드에서만 알 수 있다 (정적 모드 · 이 기능이 없는 서버면 null 이고 아무것도 표시하지 않는다).
const LISTENER_VIEWS = ['task', 'dashboard', 'new'];

async function refreshListeners() {
  let next = null;
  if (state.mode === 'live') {
    try {
      const r = await fetch('api/agents', { cache: 'no-store' });
      if (r.ok) next = (await r.json()).listeners || [];
    } catch { /* 서버 연결 끊김 */ }
  }
  const key = (ls) => (ls ? JSON.stringify(ls.map((l) => [l.kind, l.agent, l.task, l.state, l.running, l.model, l.effort, l.seen_tasks])) : 'null');
  const changed = key(next) !== key(state.listeners);
  state.listeners = next;
  return changed;
}

const sameAgent = (a, b) => String(a ?? '').trim().toLowerCase() === String(b ?? '').trim().toLowerCase();

/** 이 작업의 담당 AI 로 지금 기다리는 AI (모델은 따지지 않음) */
const agentListeners = (t) => state.listeners.filter((l) => (!l.task || l.task === t.id)
  && (isUnassigned(t.agent) || !l.agent || sameAgent(l.agent, t.agent)));

/** 이 작업을 가져갈 수 있는, 지금 기다리는 AI 목록 (null = 알 수 없음). 작업에 모델 · 강도가 지정되어 있으면
 *  다른 모델로 기다리는 대화는 빠진다 (모델을 모르는 대화 · 작업마다 지정 모델로 띄우는 실행기는 포함) */
function listenersFor(t) {
  if (!state.listeners) return null;
  return agentListeners(t).filter((l) => specMatches(t, l) !== false);
}
/** 담당 AI 는 맞지만 모델 · 강도가 달라 이 작업을 받지 않는, 기다리는 대화 */
const listenersOtherModel = (t) => (state.listeners ? agentListeners(t).filter((l) => specMatches(t, l) === false) : []);

function listenerText(l) {
  const how = l.kind === 'watch' ? L('실행기') : L('대기 모드');
  const model = l.model === '*' ? L('지정 모델로 실행') : specLabel(l.model, l.effort);
  return `${l.agent || L('모든 AI')} (${how}${model ? ` · ${model}` : ''}${l.state === 'running' && l.running ? L(', {id} 처리 중', { id: l.running }) : ''})`;
}
const listenersText = (ls) => ls.map(listenerText).join(', ');

const copyBtn = (text) => html`<button class="btn btn-sm" data-action="copy" data-copy="${text}">${icon('copy')} ${L('복사')}</button>`;

/** 이 작업의 AI 를 깨우는 방법 */
function wakeHow(t) {
  const a = String(t.agent ?? '').trim();
  const any = isUnassigned(a);
  const claude = html`<li>${any ? L('Claude: 채팅에서') : L('Claude 채팅에서')} <b>"웹 지시 기다려줘"</b> ${copyBtn('웹 지시 기다려줘')}
    — ${LH('대기 모드를 켜면 앞으로 웹 지시를 바로 받습니다. 한 번만 하려면 <b>"devtrack inbox 확인하고 진행해줘"</b>')}</li>`;
  const watch = (name, cmd) => html`<li>${name ? `${name}: ` : ''}${L('터미널에서 실행기를 켜 두세요')} — <code>${cmd}</code> ${copyBtn(cmd)}
    (<a href="#/guide/s11">${L('사용 방법 11단계')}</a>)</li>`;
  // 모델이 지정된 작업은 실행기가 그 모델로 띄우므로 Claude 에도 실행기를 먼저 안내한다
  const pinned = !!(t.model || t.effort);
  if (any) return html`<ul class="how">${claude}${watch('Claude', 'devtrack watch --claude')}${watch('Codex', 'devtrack watch --codex')}</ul>`;
  if (sameAgent(a, 'Claude')) return html`<ul class="how">${pinned ? html`${watch('', 'devtrack watch --claude')}${claude}` : html`${claude}${watch('', 'devtrack watch --claude')}`}</ul>`;
  if (sameAgent(a, 'Codex')) return html`<ul class="how">${watch('', 'devtrack watch --codex')}</ul>`;
  return html`<ul class="how">${watch('', `devtrack watch --agent ${a} -- ${L('<AI 실행 명령>')}`)}</ul>`;
}

/** 대기(queued) 작업 화면: 이 작업을 가져갈 AI 가 지금 기다리고 있는지 */
function waitingBox(t) {
  const ls = listenersFor(t);
  if (!ls) return '';
  const who = aiName(t);
  if (ls.length) {
    const mine = ls.find((l) => l.state === 'running' && l.running === t.id);
    if (mine) {
      return html`<div class="alert alert-ok">${icon('check-circle')}<div class="alert-body">
        <b>${L('{agent} 실행기가 지금 이 작업을 처리하고 있습니다', { agent: mine.agent })}</b><p>${L('곧 진행 기록이 나타납니다.')}</p></div></div>`;
    }
    // 이미 본 지시로는 다시 깨어나지 않는다 (예: 실행기가 실행했지만 시작하지 못한 작업, 확인만 하고 두고 간 작업)
    const fresh = ls.filter((l) => !(l.seen_tasks || []).includes(t.id));
    if (fresh.length) {
      const busy = fresh.every((l) => l.state === 'running');
      return html`<div class="alert alert-ok">${icon('check-circle')}<div class="alert-body">
        <b>${L('{who}가 웹 지시를 기다리고 있습니다', { who })}</b>
        <p>${listenersText(fresh)} — ${busy ? L('지금 다른 작업을 처리하고 있어, 끝나면 이 작업을 시작합니다.') : L('몇 초 안에 이 작업을 시작합니다.')}</p></div></div>`;
    }
    const ask = `${t.id} 진행해줘`;
    return html`<div class="alert alert-amber">${icon('alert')}<div class="alert-body">
      <b>${L('{who}가 기다리고 있지만, 이 작업은 이미 확인하고 시작하지 않았습니다', { who })}</b>
      <p>${listenersText(ls)} — ${L('같은 지시로는 다시 깨어나지 않습니다.')}</p>
      <ul class="how">
        ${ls.some((l) => l.kind === 'wait') ? html`<li>${L('채팅에서 직접 요청하세요')} — <b>"${ask}"</b> ${copyBtn(ask)}</li>` : ''}
        ${ls.some((l) => l.kind === 'watch') ? html`<li>${L('실행기가 실행했지만 시작되지 않았다면 진행 기록의 경고를 확인한 뒤 실행기를 다시 켜세요 — 다시 켜면 남은 지시를 다시 처리합니다')}</li>` : ''}
      </ul></div></div>`;
  }
  const other = listenersOtherModel(t);
  if (other.length) {
    const spec = specLabel(t.model, t.effort);
    const ask = `${t.id} 진행해줘`;
    const cmds = [t.model ? `/model ${t.model}` : '', t.effort ? `/effort ${t.effort}` : ''].filter(Boolean);
    const watchCmd = sameAgent(t.agent, 'Codex') ? 'devtrack watch --codex' : 'devtrack watch --claude';
    return html`<div class="alert alert-amber">${icon('alert')}<div class="alert-body">
      <b>${L('{who}가 기다리고 있지만, 지정한 모델과 달라 이 작업을 받지 않습니다', { who })}</b>
      <p>${L('이 작업: {spec}', { spec })} · ${L('기다리는 AI')}: ${listenersText(other)}</p>
      <ul class="how">
        <li>${L('터미널에서 실행기를 켜 두면 지정한 모델로 실행합니다')} — <code>${watchCmd}</code> ${copyBtn(watchCmd)}</li>
        ${other.some((l) => l.kind === 'wait') ? html`<li>${LH('또는 그 대화에서 {cmds} 로 모델을 바꾼 뒤 채팅에서 요청하세요', { cmds: raw(cmds.map((c) => String(html`<code>${c}</code>`)).join(' · ')) })} — <b>"${ask}"</b> ${copyBtn(ask)}</li>` : ''}
        <li>${L('또는 위의 모델 표시를 눌러 지정을 바꾸거나 해제하세요')}</li>
      </ul></div></div>`;
  }
  return html`<div class="alert alert-amber">${icon('alert')}<div class="alert-body">
    <b>${L('지금 웹 지시를 기다리는 {who}가 없습니다', { who })}</b>
    <p>${L('이 작업은 {who}가 받은 편지함을 확인해야 시작됩니다. 대화 중인 AI 는 작업 단계 사이에만 확인하므로, 지금 다른 일을 하고 있지 않다면 직접 깨워 주세요.', { who })}</p>
    ${wakeHow(t)}</div></div>`;
}

/** 새 작업 지시 화면: 지금 기다리는 AI 요약 */
function listenersSummary() {
  if (!state.listeners) return '';
  if (!state.listeners.length) {
    return html`<p class="form-note">${icon('alert')} ${L('지금 웹 지시를 기다리는 AI 가 없습니다. 등록한 뒤 AI 를 깨워야 시작됩니다 (작업 화면에 방법이 나옵니다).')}</p>`;
  }
  return html`<p class="form-note ok">${icon('check-circle')} ${L('지금 웹 지시를 기다리는 AI:')} ${listenersText(state.listeners)}</p>`;
}

// ====================================================================== 폴링 / 알림
async function poll({ force = false } = {}) {
  if (isLocked()) return;
  const btn = $('#refresh-btn');
  try {
    const [idx, listenersChanged] = await Promise.all([getJSON('data/index.json'), refreshListeners()]);
    const prev = state.index;
    const changed = new Set();
    if (prev) {
      const before = new Map(prev.tasks.map((t) => [t.id, t.updated_at]));
      for (const t of idx.tasks) if (before.get(t.id) !== t.updated_at) changed.add(t.id);
      for (const id of before.keys()) if (!idx.tasks.some((t) => t.id === id)) changed.add(id);
    }
    changed.forEach((id) => state.tasks.delete(id));
    state.index = idx;
    setModelCatalog(idx.models);
    const hadError = !!state.loadError;
    state.loadError = null;
    processNotifications(idx);
    const dataChanged = !prev || idx.generated_at !== prev.generated_at;
    // 미뤄 둔 갱신(state.pendingRender)도 여기서 다시 시도 — state.index 는 이미 새것이라 dataChanged 로는 다시 잡히지 않는다
    if (force || hadError || state.pendingRender || (dataChanged && isRelevant(changed, prev, idx))) {
      if (!force && prev && isBusy()) deferRender();
      else await render({ keepScroll: !!prev, auto: !force && !!prev });
    } else if (listenersChanged && LISTENER_VIEWS.includes(parseRoute().name) && !isBusy()) {
      await render({ keepScroll: true, auto: true }); // 기다리는 AI 표시만 바뀜 — 입력 중이면 다음 폴링에
    } else {
      updateChrome();
    }
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      // 비밀번호를 새로 걸었거나 바꿨거나, 이 브라우저의 쿠키가 지워짐 — 비밀번호를 묻는다
      const a = await checkAccess();
      if (a.state === 'locked') return showLock(a.auth);
    }
    state.loadError = e;
    if (!state.index) await render();
    else updateChrome();
  }
  if (btn && force) { btn.classList.remove('spin'); void btn.offsetWidth; btn.classList.add('spin'); }
}

const EVENT_TONE = { issue: 'orange', error: 'red', commit: 'green', artifact: '', status: 'green' };

function processNotifications(idx) {
  const pending = new Map(idx.notifications.map((n) => [`${n.task_id}/${n.approval_id}`, n]));
  const latest = Math.max(0, ...idx.events.map((e) => tsNum(e.ts)));
  if (state.knownPending) {
    let shown = 0;
    for (const [k, n] of pending) {
      if (state.knownPending.has(k) || shown >= 3) continue;
      shown++;
      notify({
        title: `${L('승인 요청§하나')}: ${n.title}`, meta: `${n.task_id} · ${n.task_title}`, tone: 'amber',
        href: `#/task/${n.task_id}/approvals`, sticky: true,
      });
    }
    idx.events
      .filter((e) => tsNum(e.ts) > state.lastEventTs && e.kind !== 'approval')
      .slice(0, 3)
      .forEach((e) => {
        const tone = e.kind === 'status' && e.meta?.to === 'blocked' ? 'red' : EVENT_TONE[e.kind] ?? '';
        notify({ title: e.message, meta: `${e.task_id} · ${e.task_title}`, tone, href: `#/task/${e.task_id}` });
      });
  }
  state.knownPending = new Set(pending.keys());
  state.lastEventTs = Math.max(state.lastEventTs, latest);
}

function notify(opts) {
  toast(opts);
  if (LS.get('desktopNotify', false) && 'Notification' in window && Notification.permission === 'granted') {
    try {
      const n = new Notification(opts.title, { body: opts.meta || '', tag: opts.href || opts.title });
      n.onclick = () => { window.focus(); if (opts.href) location.hash = opts.href; n.close(); };
    } catch { /* 일부 브라우저는 페이지에서 직접 생성 불가 */ }
  }
}

function toast({ title, meta, tone = '', href, sticky = false }) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = `toast ${tone}`;
  el.setAttribute('role', 'status');
  el.innerHTML = html`<div class="body"><div class="title">${title}</div>${meta ? html`<div class="meta">${meta}</div>` : ''}${href ? html`<a class="go" href="${href}">${L('확인하기')} →</a>` : ''}</div><button class="x" aria-label="${L('닫기')}">${icon('x')}</button>`.s;
  el.querySelector('.x').onclick = () => el.remove();
  el.querySelector('.go')?.addEventListener('click', () => el.remove());
  box.appendChild(el);
  setTimeout(() => el.remove(), sticky ? 20000 : 6000);
  while (box.children.length > 4) box.firstChild.remove();
}

// ====================================================================== 상단바 / 사이드바
/** 작업이 속한 분야 화면 주소 (브레인스토밍 · 문서 리뷰는 그 작업을 고른 채로) */
function sectionHref(t) {
  const sec = SECTION_OF_TYPE[t.type] || 'tasks';
  return PER_TASK_SECTIONS.includes(sec) ? `#/${sec}/${t.id}` : `#/${sec}`;
}

function sectionOfTask(id) {
  const t = state.index?.tasks.find((x) => x.id === id);
  return t ? SECTION_OF_TYPE[t.type] : null;
}

function updateChrome(route = parseRoute()) {
  let navName = { new: 'new' }[route.name] || route.name;
  if (route.name === 'task') navName = sectionOfTask(route.rest[0]) || 'tasks';
  if (route.name === 'artifact') navName = sectionOfTask(route.rest[0]) || 'artifacts';
  $$('.nav-item').forEach((a) => a.classList.toggle('active', a.dataset.nav === navName));
  const idx = state.index;
  const pending = idx ? idx.notifications.length : 0;
  const setCount = (sel, n) => { const el = $(sel); el.textContent = n; el.hidden = !n; };
  setCount('#nav-approvals-count', pending);
  // 분야별 메뉴에는 그 분야의 승인 대기 수
  const pendIn = (sec) => (idx ? idx.notifications.filter((n) => sectionOfTask(n.task_id) === sec).length : 0);
  setCount('#nav-code-count', pendIn('code'));
  setCount('#nav-papers-count', pendIn('papers'));
  setCount('#nav-analysis-count', pendIn('analysis'));
  setCount('#nav-brainstorm-count', pendIn('brainstorm'));
  setCount('#nav-docreview-count', pendIn('docreview'));
  const seen = LS.get('seenTs', 0);
  const unseen = idx ? idx.events.filter((e) => e.kind !== 'approval' && tsNum(e.ts) > seen).length : 0;
  $('#bell-count').textContent = pending;
  $('#bell-count').hidden = !pending;
  $('#bell-dot').hidden = !!pending || !unseen;
  document.title = `${pending ? `(${pending}) ` : ''}DevTrack · ${L('AI 협업 작업 관리')}`;

  const chip = $('#mode-chip');
  if (state.loadError && idx) {
    chip.className = 'mode-chip error';
    chip.innerHTML = `<span class="dot"></span><span>${L('연결 끊김')}</span>`;
    chip.title = `${L('데이터를 새로 불러오지 못했습니다')}: ${state.loadError.message}`;
  } else if (state.mode === 'live') {
    chip.className = 'mode-chip live';
    chip.innerHTML = `<span class="dot"></span><span>${L('실시간')}<span class="label-long"> · ${L('로컬 서버')}</span></span>`;
    chip.title = L('로컬 서버에 연결됨: 웹에서 승인·피드백·작업 지시가 바로 저장됩니다');
  } else {
    chip.className = 'mode-chip';
    chip.innerHTML = `<span class="dot"></span><span>${L('정적')}<span class="label-long"> · ${L('읽기 전용')}</span></span>`;
    chip.title = L('정적 호스팅 모드: 데이터는 자동 갱신되지만, 승인/피드백은 담당 AI 에게 전달할 메시지로 표시됩니다');
  }
  const guard = state.access?.state === 'unprotected'
    ? html`<div class="lock-warn" title="${L('data/ 폴더를 누구나 읽을 수 있습니다 — .htaccess 를 읽는 Apache 서버에 올리거나 devtrack serve 로 여세요')}">${icon('alert')} ${L('비밀번호 보호가 동작하지 않음')}</div>`
    : state.access?.auth ? html`<button class="lock-btn" data-action="lock" title="${L('이 브라우저에서 비밀번호를 지우고 잠급니다')}">${icon('lock')} ${L('잠그기')}</button>` : '';
  $('#sync-info').innerHTML = html`${idx
    ? html`${L('데이터 갱신')} <b>${ago(idx.generated_at)}</b><br>${L('{n}초마다 자동 확인', { n: POLL_MS / 1000 })}`
    : L('데이터 없음')}${guard}`.s;
  if ($('#notif-dd').classList.contains('open')) renderDropdown();
}

function renderDropdown() {
  const idx = state.index;
  const dd = $('#notif-dd');
  if (!idx) { dd.innerHTML = `<div class="empty">${L('데이터 없음')}</div>`; return; }
  const seen = LS.get('seenTs', 0);
  const pend = idx.notifications;
  const evs = idx.events.filter((e) => e.kind !== 'approval').slice(0, 15);
  const perm = 'Notification' in window ? Notification.permission : 'unsupported';
  const on = LS.get('desktopNotify', false) && perm === 'granted';
  dd.innerHTML = html`
    <div class="dd-head"><b>${L('알림')}</b><a href="#/approvals" data-close-dd>${L('승인 요청 보기')}</a></div>
    <div class="dd-sec">${L('결정 대기')} · ${pend.length}</div>
    ${pend.length ? pend.map((n) => html`
      <a class="dd-item" href="#/task/${n.task_id}/approvals" data-close-dd>
        <span class="ic tone-amber">${icon(pick(APPROVAL_KIND, n.kind, 'general').icon)}</span>
        <div class="body"><div class="title">${n.title}</div><div class="meta">${n.task_id} · ${n.task_title} · ${ago(n.created_at)}</div></div>
      </a>`) : html`<div class="dd-item muted">${L('대기 중인 승인 요청이 없습니다')}</div>`}
    <div class="dd-sec">${L('최근 소식')}</div>
    ${evs.length ? evs.map((e) => {
      const k = pick(LOG_KIND, e.kind, 'info');
      return html`<a class="dd-item ${tsNum(e.ts) > seen ? 'unseen' : ''}" href="#/task/${e.task_id}" data-close-dd>
        <span class="ic tone-${k.tone}">${icon(k.icon)}</span>
        <div class="body"><div class="title">${e.message}</div><div class="meta">${e.task_id} · ${authorName(e.author)} · ${ago(e.ts)}</div></div>
      </a>`;
    }) : html`<div class="dd-item muted">${L('새 소식이 없습니다')}</div>`}
    <div class="dd-foot">
      <span>${perm === 'unsupported' ? L('이 브라우저는 데스크톱 알림을 지원하지 않습니다') : perm === 'denied' ? L('브라우저에서 알림이 차단됨') : on ? L('데스크톱 알림 켜짐') : L('데스크톱 알림 꺼짐')}</span>
      ${perm !== 'unsupported' && perm !== 'denied' ? html`<button class="btn btn-sm" data-action="desktop-notify">${on ? L('끄기') : L('켜기')}</button>` : ''}
    </div>`.s;
}

// ====================================================================== 공통 컴포넌트
const statusBadge = (s) => badge(STATUS, s, { dot: true });
const typeBadge = (t) => badge(TYPES, t, { withIcon: true });

function repoLabel(repo = {}) {
  if (repo.url) {
    const m = /github\.com[:/](.+?)(?:\.git)?$/.exec(repo.url);
    if (m) return { text: m[1], href: `https://github.com/${m[1]}` };
    return { text: repo.url, href: /^https?:/.test(repo.url) ? repo.url : null };
  }
  return repo.path ? { text: repo.path, href: null } : null;
}

const shortPath = (p) => {
  const s = String(p || '').split('/').filter(Boolean);
  return s.length > 2 ? `…/${s.slice(-2).join('/')}` : String(p || '');
};

function commitLink(remoteUrl, sha) {
  const short = (sha || '').slice(0, 8);
  const m = /github\.com[:/](.+?)(?:\.git)?$/.exec(remoteUrl || '');
  return m ? html`<a class="mono" href="https://github.com/${m[1]}/commit/${sha}" target="_blank" rel="noopener">${short}</a>` : html`<code>${short}</code>`;
}

function progressBar(t, lg = false) {
  return html`<div class="progress ${lg ? 'lg' : ''} s-${t.status}"><span style="width:${Math.max(0, Math.min(100, t.progress || 0))}%"></span></div>`;
}

/** 작업의 '지금 상태'를 한 줄로: 진행 중이면 현재 단계, 승인 대기면 무엇을 기다리는지 */
function taskNow(t) {
  if (t.status === 'awaiting_approval') {
    const n = state.index?.notifications.find((x) => x.task_id === t.id);
    if (n) return { label: L('승인 대기'), text: n.title };
  }
  if (t.status === 'in_progress' && t.current_step) return { label: L('지금'), text: t.current_step };
  return { label: L('최근'), text: t.last_log?.message || t.current_step || '' };
}

/** 연결된 작업 (양방향: 내가 연결한 것 + 나를 연결한 것) */
function linksOf(id) {
  const tasks = state.index?.tasks || [];
  const me = tasks.find((t) => t.id === id);
  const ids = new Set(me?.related || []);
  tasks.forEach((t) => { if ((t.related || []).includes(id)) ids.add(t.id); });
  return [...ids].map((rid) => tasks.find((t) => t.id === rid)).filter(Boolean);
}
const artsOf = (id) => (state.index?.artifacts || []).filter((a) => a.task_id === id);
const figuresOf = (id) => artsOf(id).filter((a) => a.kind === 'image' && a.path);

function taskCard(t, { thumbs = false, links = false, repo = false } = {}) {
  const now = taskNow(t);
  const rl = repo ? repoLabel(t.repo) : null;
  // 로컬 경로는 앞부분이 길어 잘리므로 끝의 두 단계만 (전체 경로는 마우스를 올리면)
  const repoText = rl && !t.repo?.url ? shortPath(rl.text) : rl?.text;
  const figs = thumbs ? figuresOf(t.id).slice(0, 4) : [];
  const papers = links ? linksOf(t.id).filter((x) => x.type === 'paper') : [];
  return html`<a class="task-card" href="#/task/${t.id}">
    <div class="tc-top">${typeBadge(t.type)}${statusBadge(t.status)}<span class="tc-id">${t.id}</span></div>
    <div class="tc-title">${t.title}</div>
    ${papers.length ? html`<div class="tc-link">${icon('book')} ${L('논문용')} · ${papers.map((p) => `${p.id} ${p.title}`).join(', ')}</div>` : ''}
    ${rl ? html`<div class="tc-link tc-repo" title="${rl.text}">${icon('git')} ${repoText}${t.repo?.branch ? ` · ${t.repo.branch}` : ''}</div>` : ''}
    <div class="tc-step">${now.text ? html`<span class="lbl">${now.label}</span>${now.text}` : html`<span class="muted">${L('기록 없음')}</span>`}</div>
    ${figs.length ? html`<div class="tc-thumbs">${figs.map((a) => html`<img src="${artifactUrl(a.task_id, a)}" alt="" loading="lazy" title="${a.title}">`)}</div>` : ''}
    <div class="tc-prog">${progressBar(t)}<span class="pct">${t.progress || 0}%</span></div>
    <div class="tc-meta">
      <span>${agentLabel(t)}</span><span>${ago(t.updated_at)}</span>
      ${t.pending_approvals ? html`<span class="flag amber">${icon('bell')}${L('승인§개수')} ${t.pending_approvals}</span>` : ''}
      ${t.open_issues ? html`<span class="flag ${t.max_severity >= 2 ? 'red' : 'orange'}">${icon('alert')}${L('문제')} ${t.open_issues}</span>` : ''}
    </div>
  </a>`;
}

function feedItem(e) {
  const k = pick(LOG_KIND, e.kind, 'info');
  return html`<li>
    <span class="ic tone-${k.tone}">${icon(k.icon)}</span>
    <div class="body">
      <div class="msg">${e.kind === 'user' ? html`<b>${L('사용자')}:</b> ${e.message}` : e.message}</div>
      <div class="meta"><a href="#/task/${e.task_id}">${e.task_id}</a><span>${authorName(e.author)}</span>${ago(e.ts)}</div>
    </div>
  </li>`;
}

/** 사용자 메시지가 AI 에게 전달됐는지 (받은 편지함에서 --ack 로 처리 표시했는지) */
const ackBadge = (e) => (e.ack === true ? html`<span class="badge tone-green">${icon('check')} ${L('AI 확인함')}</span>`
  : e.ack === false ? html`<span class="badge tone-amber">${icon('clock')} ${L('AI 확인 전')}</span>` : '');

/** 결과물 화면에서 보낸 메시지가 가리키는 결과물 */
function msgArtifactLink(e, tid) {
  const fid = e.meta?.artifact;
  if (!fid) return '';
  const v = e.meta.version > 1 ? ` (v${e.meta.version})` : '';
  const label = html`${icon('file')} ${L('결과물§하나')} ${fid}${e.meta.artifact_title ? ` · ${e.meta.artifact_title}` : ''}${v}`;
  return tid ? html`<a class="msg-art" href="#/artifact/${tid}/${fid}">${label}</a>` : html`<span class="msg-art">${label}</span>`;
}

function timelineItem(e, { taskLink = false, tid = e.task_id } = {}) {
  const k = pick(LOG_KIND, e.kind, 'info');
  const isUser = e.author === 'user';
  const commit = e.kind === 'commit' && e.meta?.commit ? html` · ${commitLink(e.meta.remote_url, e.meta.commit)}` : '';
  return html`<li class="kind-${e.kind} ${isUser ? 'user' : ''}">
    <span class="ic tone-${k.tone}">${icon(k.icon)}</span>
    <div class="body">
      ${e.kind === 'user'
        ? html`<div class="head"><span class="msg">${L('사용자 메시지')}</span>${msgArtifactLink(e, tid)}${ackBadge(e)}</div><div class="msg-box markdown-body compact">${raw(renderMarkdown(e.message))}</div>`
        : e.kind === 'agent'
          ? html`<div class="head"><span class="msg">${L('{from} → {to} AI 메시지', { from: authorName(e.author), to: e.meta?.to || 'AI' })}</span></div><div class="msg-box agent-msg">${e.message}</div>`
          : html`<div class="head"><span class="msg">${e.message}</span></div>`}
      <div class="meta">${authorName(e.author)} · ${ago(e.ts)}${e.ref ? ` · ${e.ref}` : ''}${commit}${taskLink ? html` · <a href="#/task/${e.task_id}">${e.task_id} ${e.task_title}</a>` : ''}</div>
      ${e.detail ? html`<div class="detail markdown-body compact">${raw(renderMarkdown(e.detail))}</div>` : ''}
    </div>
  </li>`;
}

function issueItem(i) {
  const sev = pick(SEVERITY, i.severity, 'medium');
  return html`<div class="issue ${i.status}">
    <span class="sev sev-${i.severity}"></span>
    <div class="body">
      <div class="title">${i.title} <span class="badge tone-${sev.tone}">${sev.label}</span>${i.status === 'resolved' ? html`<span class="badge tone-green">${icon('check')}${L('해결됨')}</span>` : ''}</div>
      <div class="meta">${i.id} · ${authorName(i.author)} · ${ago(i.created_at)}${i.resolved_at ? html` · ${L('해결')} ${ago(i.resolved_at)}` : ''}</div>
      ${i.detail ? html`<div class="detail markdown-body compact">${raw(renderMarkdown(i.detail))}</div>` : ''}
      ${i.resolution ? html`<div class="resolution"><b>${L('해결')}:</b> ${i.resolution}</div>` : ''}
    </div>
  </div>`;
}

function artRow(tid, a, { showTask = false, thumb = false } = {}) {
  const k = pick(ART_KIND, a.kind, 'file');
  const img = thumb && a.kind === 'image' && a.path ? artifactUrl(tid, a) : null;
  return html`<a class="art-row" href="#/artifact/${tid}/${a.id}">
    <span class="ic ${img ? 'has-img' : ''}">${img ? html`<img src="${img}" alt="" loading="lazy">` : icon(k.icon)}</span>
    <div class="body"><div class="title">${a.title}</div><div class="meta">${showTask ? `${tid} · ` : `${a.id} · `}${k.label}${a.category === 'review' ? ` · ${L('리뷰')}` : a.category === 'source' ? ` · ${sourceLabel(a)}` : ''}${a.version > 1 ? ` · v${a.version}` : ''} · ${ago(a.updated_at || a.created_at)}${a.size ? ` · ${fmtBytes(a.size)}` : ''}${showTask && a.task_title ? ` · ${a.task_title}` : ''}</div></div>
    ${icon('chev')}
  </a>`;
}

/** 결과물 분류 표시: 리뷰 리포트 · 검토할 문서(문서 리뷰의 원본) · 리뷰할 변경(AI 리뷰의 diff) */
const sourceLabel = (a) => (a.kind === 'diff' ? L('리뷰할 변경') : L('검토할 문서'));
function catBadge(a) {
  if (a.category === 'review') return html`<span class="badge tone-violet">${L('리뷰')}</span>`;
  if (a.category === 'source') return html`<span class="badge tone-orange">${sourceLabel(a)}</span>`;
  return '';
}

function artCard(a) {
  const k = pick(ART_KIND, a.kind, 'file');
  const url = a.path ? artifactUrl(a.task_id, a) : null;
  return html`<a class="art-card" href="#/artifact/${a.task_id}/${a.id}">
    <div class="thumb">${a.kind === 'image' && url ? html`<img src="${url}" alt="" loading="lazy">` : icon(k.icon)}</div>
    <div class="info">
      <div class="title" title="${a.title}">${a.title}</div>
      <div class="meta"><span class="badge">${k.label}</span>${catBadge(a)}${a.version > 1 ? html`<span class="badge tone-accent" title="${L('이전 버전 {n}개 보관', { n: a.version - 1 })}">v${a.version}</span>` : ''}<span>${a.task_id}</span>${ago(a.updated_at || a.created_at)}</div>
    </div>
  </a>`;
}

// 결과물 보기 방식: 카드 격자(grid) 또는 세로 목록(list). 보는 사람의 브라우저에 기억한다
const artView = () => (LS.get('artView', 'grid') === 'list' ? 'list' : 'grid');

function artViewToggle() {
  const v = artView();
  const b = (key, ic, label) => html`<button type="button" class="${v === key ? 'on' : ''}" data-action="art-view" data-view="${key}"
    aria-pressed="${v === key ? 'true' : 'false'}" title="${label}" aria-label="${label}">${icon(ic)}</button>`;
  return html`<div class="seg view-toggle" role="group" aria-label="${L('보기 방식')}">${b('grid', 'grid', L('카드로 보기'))}${b('list', 'list', L('목록으로 보기'))}</div>`;
}

// 결과물 순서: 최신순(마지막으로 올리거나 새 버전이 된 것이 위 — 기본) 또는 작업별(작업 번호가 큰 작업부터 묶고,
// 작업 안에서는 만든 순서 F-1, F-2 …). 한 작업의 결과물 탭에서는 작업별 = 만든 순서. 보는 사람의 브라우저에 기억한다
const artSort = () => (LS.get('artSort', 'time') === 'task' ? 'task' : 'time');
const idNum = (id) => parseInt(String(id ?? '').replace(/^\D*-?/, ''), 10) || 0;
const artTime = (a) => tsNum(a.updated_at || a.created_at);

function sortArtifacts(items) {
  const out = [...items];
  if (artSort() === 'task') out.sort((x, y) => idNum(y.task_id) - idNum(x.task_id) || idNum(x.id) - idNum(y.id));
  else out.sort((x, y) => artTime(y) - artTime(x) || idNum(y.task_id) - idNum(x.task_id) || idNum(y.id) - idNum(x.id));
  return out;
}

function artSortToggle({ single = false } = {}) {
  const v = artSort();
  const b = (key, ic, label, tip) => html`<button type="button" class="${v === key ? 'on' : ''}" data-action="art-sort" data-sort="${key}"
    aria-pressed="${v === key ? 'true' : 'false'}" title="${tip}">${icon(ic)} ${label}</button>`;
  return html`<div class="seg sort-toggle" role="group" aria-label="${L('순서')}">${b('time', 'clock', L('최신순'), L('최근에 올리거나 새 버전이 된 결과물부터'))}${single
    ? b('task', 'list', L('만든 순서'), L('만든 순서대로 (F-1, F-2 …)'))
    : b('task', 'folder', L('작업별'), L('작업별로 묶어서, 작업 안에서는 만든 순서대로'))}</div>`;
}

const artTools = (opts) => html`<div class="art-toggles">${artSortToggle(opts)}${artViewToggle()}</div>`;

/** 결과물 모음 — 고른 순서와 보기 방식대로. 여러 작업의 결과물을 작업별로 보면 작업마다 제목 줄을 단다 */
function artCollection(items, { showTask = true } = {}) {
  const sorted = sortArtifacts(items);
  if (!showTask || artSort() !== 'task') return artItems(sorted, { showTask });
  const groups = [];
  for (const a of sorted) {
    const g = groups[groups.length - 1];
    if (g && g.id === a.task_id) g.items.push(a);
    else groups.push({ id: a.task_id, title: a.task_title, items: [a] });
  }
  return html`${groups.map((g) => html`<section class="art-group">
    <h3 class="art-group-head"><a href="#/task/${g.id}/artifacts"><span class="mono">${g.id}</span> ${g.title || ''}</a><span class="n">${g.items.length}</span></h3>
    ${artItems(g.items, { showTask: false })}</section>`)}`;
}

function artItems(items, { showTask }) {
  if (artView() === 'list') {
    return html`<div class="card art-list">${items.map((a) => artRow(a.task_id, a, { showTask, thumb: true }))}</div>`;
  }
  return html`<div class="art-grid">${items.map(artCard)}</div>`;
}

function planList(t) {
  const plan = t.plan || [];
  if (!plan.length) return html`<div class="muted">${L('아직 계획이 등록되지 않았습니다')}</div>`;
  const cur = t.status === 'done' ? -1 : plan.findIndex((p) => !p.done);
  return html`<ol class="plan">${plan.map((p, i) => html`
    <li class="${p.done ? 'done' : ''} ${i === cur ? 'current' : ''}">
      <span class="num">${i + 1}</span><span class="box">${p.done ? icon('check') : ''}</span><span class="txt">${p.text}</span>
    </li>`)}</ol>`;
}

function feedbackHint(t) {
  const ai = aiName(t);
  if (state.mode !== 'live') return L('정적 모드: 보내기를 누르면 {ai}에게 전달할 내용이 표시됩니다', { ai });
  const ls = listenersFor(t);
  if (!ls) return L('{ai}에게 devtrack inbox 로 전달됩니다', { ai });
  if (ls.length) return L('{ai}가 웹 지시를 기다리는 중 — 바로 전달됩니다', { ai });
  return L('지금 기다리는 {ai}가 없어, {ai}가 다음에 받은 편지함을 확인할 때 전달됩니다', { ai });
}

/** 결과물 화면: 이 결과물에 보낸 메시지와 그 뒤의 새 버전 (오래된 것부터) — 보낸 적이 없으면 비움 */
function artifactThread(t, art) {
  const items = (t.log || []).filter((e) => e.ref === art.id && (e.kind === 'user' || e.kind === 'artifact'));
  if (!items.some((e) => e.kind === 'user')) return '';
  return html`<div class="fb-thread">
    <div class="fb-thread-head">${L('이 결과물에 보낸 요청과 새 버전')}</div>
    <ul class="timeline">${items.slice(-12).map((e) => timelineItem(e, { tid: t.id }))}</ul>
  </div>`;
}

/** 작업의 담당 AI 에게 메시지 보내기. artifact 를 주면 결과물 화면의 '수정 요청 · 추가 지시' 칸 (그 결과물을 가리키는 메시지) */
function feedbackBox(t, title = L('{ai}에게 메시지', { ai: aiName(t) }), { artifact = null } = {}) {
  const ai = aiName(t);
  return html`<section class="card feedback-card">
    <div class="card-head"><h2>${icon('message')} ${title}</h2></div>
    <div class="card-body">
      ${artifact ? html`${artifactThread(t, artifact)}<p class="fb-note">${L('보낸 내용은 결과물 {id} 에 대한 메시지로 {ai}에게 전달됩니다. 고친 결과물은 이 화면에 새 버전으로 올라옵니다.', { id: artifact.id, ai })}</p>` : ''}
      <textarea id="fb-${t.id}" placeholder="${artifact ? L('이 결과물에서 고칠 부분이나 추가로 할 일을 적어 주세요 (markdown 지원)') : L('추가 지시, 질문, 수정 요청 등을 입력하세요 (markdown 지원)')}"></textarea>
      <div class="actions">
        <span class="hint">${feedbackHint(t)}</span>
        <button class="btn btn-primary" data-action="comment" data-task="${t.id}" data-artifact="${artifact?.id || ''}">${icon('send')} ${L('보내기')}</button>
      </div>
    </div>
  </section>`;
}

function commitBox(act) {
  const st = act.stat || {};
  const repo = repoLabel({ url: act.remote_url, path: act.repo });
  return html`<div class="commit-box">
    <div class="cb-head">${icon('git')} ${act.push ? L('승인하면 커밋 후 푸시') : L('승인하면 커밋')}
      ${act.branch ? html`<span class="badge">${icon('branch')}${act.branch}</span>` : ''}
      <span class="stat">${L('{n}개 파일', { n: st.files ?? '?' })} <span class="add">+${st.additions ?? 0}</span> <span class="del">−${st.deletions ?? 0}</span></span>
    </div>
    <div class="cb-msg">${act.message}</div>
    <dl class="kv">
      <dt>${L('저장소')}</dt><dd class="mono">${repo?.href ? html`<a href="${repo.href}" target="_blank" rel="noopener">${repo.text}</a>` : repo?.text || act.repo}</dd>
      ${act.remote_url ? html`<dt>${L('경로')}</dt><dd class="mono">${act.repo}</dd>` : ''}
      ${act.files?.length ? html`<dt>${L('대상 파일')}</dt><dd class="mono">${act.files.join(', ')}</dd>` : ''}
      ${act.excluded?.length ? html`<dt>${L('뺀 빌드 결과물')}</dt><dd>${excludedText(act.excluded)}</dd>` : ''}
    </dl>
  </div>`;
}

/** diff · 커밋에서 뺀 빌드 결과물: 'build/ (CMake 빌드 폴더) 1,234개 · *.o (컴파일 결과물) 3개' */
function excludedText(list, limit = 6) {
  const parts = list.slice(0, limit).map((e) => `${e.path} (${L(e.reason)})${e.count > 1 ? ' ' + L('{n}개', { n: e.count.toLocaleString() }) : ''}`);
  if (list.length > limit) parts.push(L('외 {n}곳', { n: list.length - limit }));
  return parts.join(' · ');
}

/** 커밋 승인 요청에 붙은 리뷰 요약 (리뷰받기로 한 작업) */
function approvalReview(rv) {
  if (!rv) return '';
  const st = pick(REVIEW_STATE, rv.state, 'planned');
  const who = `${rv.reviewer}${rv.model || rv.effort ? ` (${specLabel(rv.model, rv.effort)})` : ''}`;
  if (rv.kind === 'doc') {
    const j = JOURNAL_VERDICT[rv.verdict];
    return html`<div class="ap-review tone-${j?.tone || st.tone}">${icon(j?.icon || st.icon)} <span>${j ? L('{who} 의 {n}차 심사: {v}', { who, n: rv.round, v: j.label }) : loopStateLabel(rv, st)}</span>${rv.task ? html` <a href="#/task/${rv.task}">${rv.task}</a>` : ''}</div>`;
  }
  const text = rv.skipped ? L('리뷰 없이 요청 (사용자가 리뷰를 건너뛰라고 함)')
    : rv.state === 'approved' ? L('{who} 의 {n}차 리뷰를 통과한 코드입니다', { who, n: rv.round })
      : rv.state === 'exhausted' ? L('{who} 의 리뷰를 {n}번 받았지만 아직 수정 필요 — 남은 항목 {ids}', { who, n: rv.round, ids: (rv.open || []).join(', ') || '-' })
        : st.label;
  return html`<div class="ap-review tone-${rv.skipped ? 'amber' : st.tone}">${icon(rv.skipped ? 'alert' : st.icon)} <span>${text}</span>${rv.task ? html` <a href="#/task/${rv.task}">${rv.task}</a>` : ''}</div>`;
}

// ---------------------------------------------------------------------- 코드 리뷰 검토 항목 (R1, R2…)
const safeHttp = (u) => (/^https?:\/\//i.test(String(u || '')) ? String(u) : null);

function sevBadge(sev) {
  const m = pick(FINDING_SEV, sev, 'minor');
  return html`<span class="badge tone-${m.tone}" title="${m.en} — ${m.hint}">${m.label}</span>`;
}

/** 검토 항목의 GitHub 위치 링크: PR URL + 리뷰한 커밋(head_sha) + 파일:줄 */
function findingHref(pr = {}, f) {
  if (safeHttp(f.url)) return f.url;
  const m = /^https:\/\/github\.com\/([^/]+\/[^/]+)\/pull\/\d+/.exec(pr.url || '');
  if (!m || !pr.head_sha || !f.file) return null;
  const [a, b] = String(f.line ?? '').split('-');
  const anchor = /^\d+$/.test(a || '') ? `#L${a}${/^\d+$/.test(b || '') ? `-L${b}` : ''}` : '';
  return `https://github.com/${m[1]}/blob/${encodeURIComponent(pr.head_sha)}/${String(f.file).split('/').map(encodeURIComponent).join('/')}${anchor}`;
}

function findingItem(pr, f) {
  const loc = f.file ? `${f.file}${f.line ? `:${f.line}` : ''}` : '';
  const href = loc ? findingHref(pr, f) : null;
  return html`<li class="finding s-${f.status}">
    <span class="fid">${f.id}</span>
    <div class="f-body">
      <div class="f-head">${sevBadge(f.severity)}<span class="f-title">${f.title}</span>${f.status !== 'open' ? badge(FINDING_STATUS, f.status) : ''}</div>
      ${f.plain ? html`<div class="f-plain">${f.plain}</div>` : ''}
      <div class="f-meta">${loc ? (href ? html`<a class="mono" href="${href}" target="_blank" rel="noopener">${loc}</a>` : html`<span class="mono">${loc}</span>`) : ''}${f.category ? html`<span>${f.category}</span>` : ''}${f.confidence ? html`<span>${L('확신도')} ${CONFIDENCE[f.confidence] || f.confidence}</span>` : ''}${safeHttp(f.comment_url) ? html`<a href="${f.comment_url}" target="_blank" rel="noopener">${icon('message')} ${L('게시된 코멘트')}</a>` : ''}</div>
    </div>
  </li>`;
}

/** 남은(열린) 항목 수를 심각도별 칩으로 */
function sevChips(by = {}, { empty = '' } = {}) {
  const chips = Object.keys(FINDING_SEV).filter((k) => by[k]).map((k) => {
    const m = FINDING_SEV[k];
    return html`<span class="badge tone-${m.tone}" title="${m.hint}">${m.label} ${by[k]}</span>`;
  });
  return chips.length ? html`${chips}` : empty;
}

function prMeta(pr = {}) {
  const bits = [];
  if (pr.author) bits.push(html`<span>${icon('user')} ${pr.author}</span>`);
  if (pr.base || pr.head) bits.push(html`<span class="mono">${pr.base || '?'} ← ${pr.head || '?'}</span>`);
  if (pr.head_sha) bits.push(html`<span class="mono" title="${L('리뷰한 커밋')}">${icon('git')} ${String(pr.head_sha).slice(0, 8)}</span>`);
  if (pr.files != null || pr.additions != null) bits.push(html`<span>${L('{n}개 파일', { n: pr.files ?? '?' })} <span class="add">+${pr.additions ?? 0}</span> <span class="del">−${pr.deletions ?? 0}</span></span>`);
  if (pr.ci) bits.push(html`<span>CI ${pr.ci}</span>`);
  return bits.length ? html`<div class="pr-meta">${bits}</div>` : '';
}

function reviewPanel(t) {
  const rv = t.review;
  if (!rv) return '';
  const pr = rv.pr || {};
  const all = rv.findings || [];
  const open = all.filter((f) => f.status === 'open');
  const by = {};
  open.forEach((f) => { by[f.severity] = (by[f.severity] || 0) + 1; });
  const v = rv.verdict ? pick(VERDICT, rv.verdict) : null;
  const prHref = safeHttp(pr.url);
  const prTitle = pr.number ? `PR #${pr.number}${pr.title ? ` · ${pr.title}` : ''}` : pr.title;
  const settled = all.length - open.length;
  return html`<section class="card review-sum">
    <div class="card-head"><h2>${icon('eye')} ${L('리뷰 결과 한눈에 보기')}</h2><span class="muted" style="font-size:12.5px">${ago(rv.updated_at)}</span></div>
    <div class="card-body">
      ${prTitle ? html`<div class="pr-title">${prHref ? html`<a href="${prHref}" target="_blank" rel="noopener">${prTitle} ${icon('external')}</a>` : prTitle}</div>` : ''}
      ${prMeta(pr)}
      ${v || rv.summary ? html`<div class="rv-verdict">
        ${v ? html`<div class="rv-v"><span class="badge tone-${v.tone}">${icon(v.icon)} ${L('리뷰 의견')}: ${v.label}</span></div>` : ''}
        ${rv.summary ? html`<div class="markdown-body compact">${raw(renderMarkdown(rv.summary))}</div>` : ''}
      </div>` : ''}
      <div class="sev-row"><span class="lbl">${L('남은 항목 {n}개', { n: open.length })}</span>${sevChips(by, { empty: html`<span class="badge tone-green">${icon('check')} ${L('모두 처리됨')}</span>` })}${settled ? html`<span class="muted">· ${L('처리됨')} ${settled}</span>` : ''}</div>
      ${all.length ? html`<ul class="finding-list">${all.map((f) => findingItem(pr, f))}</ul>` : ''}
    </div>
  </section>`;
}

function choiceBox(t, ap) {
  const n = ap.choices.filter((c) => c.default).length;
  return html`<fieldset class="choice-box" data-choices="${t.id}/${ap.id}">
    <legend>${ap.choices_label || L('진행할 항목 선택')}</legend>
    <div class="choice-tools"><span class="muted" data-choice-count>${L('{n}/{total}개 선택', { n, total: ap.choices.length })}</span>
      <button type="button" class="btn btn-ghost btn-sm" data-action="choices" data-mode="all">${L('전체 선택')}</button>
      <button type="button" class="btn btn-ghost btn-sm" data-action="choices" data-mode="none">${L('모두 해제')}</button></div>
    ${ap.choices.map((c) => html`<label class="choice">
      <input type="checkbox" value="${c.id}" ${c.default ? raw('checked') : ''}>
      <span class="fid">${c.id}</span>
      <span class="c-body"><span class="c-head">${c.severity ? sevBadge(c.severity) : ''}<span class="c-label">${c.label}</span></span>${c.plain ? html`<span class="c-plain" title="${c.plain}">${c.plain}</span>` : ''}</span>
    </label>`)}
  </fieldset>`;
}

function syncChoiceCount(box) {
  const all = $$('input[type="checkbox"]', box);
  const n = all.filter((i) => i.checked).length;
  const el = $('[data-choice-count]', box);
  if (el) el.textContent = L('{n}/{total}개 선택', { n, total: all.length });
}

function approvalCard(t, ap, { showTask = false, skip = [] } = {}) {
  const kind = pick(APPROVAL_KIND, ap.kind, 'general');
  const st = pick(APPROVAL_STATUS, ap.status, 'pending');
  const linked = (ap.artifacts || []).filter((id) => !skip.includes(id))
    .map((id) => t.artifacts.find((a) => a.id === id)).filter(Boolean);
  return html`<article class="approval ${ap.status}" id="ap-${t.id}-${ap.id}">
    <header>
      <span class="ic tone-${st.tone}">${icon(kind.icon)}</span>
      <div class="hd">
        <div class="th-badges" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:5px"><span class="badge">${kind.label}</span>${badge(APPROVAL_STATUS, ap.status, { dot: true })}</div>
        <h3>${ap.title}</h3>
        <div class="meta"><span class="mono">${ap.id}</span>${showTask ? html`<a href="#/task/${t.id}">${t.id} · ${t.title}</a>` : ''}<span>${L('{who} 요청', { who: authorName(ap.author) })} · ${ago(ap.created_at)}</span></div>
      </div>
    </header>
    <div class="ap-body">
      ${ap.description ? html`<div class="ap-desc markdown-body compact">${raw(renderMarkdown(ap.description))}</div>` : ''}
      ${approvalReview(ap.review)}
      ${ap.action ? commitBox(ap.action) : ''}
      ${linked.length ? html`<div class="ap-linked">${linked.map((a, i) => html`
        <details data-key="ap:${t.id}:${ap.id}:${a.id}" ${ap.status === 'pending' && i === 0 ? raw('open') : ''}>
          <summary><span class="s-title">${icon(pick(ART_KIND, a.kind, 'file').icon)} ${a.title} <span class="muted">${pick(ART_KIND, a.kind, 'file').label}</span></span>
            <a class="btn btn-ghost btn-sm" href="#/artifact/${t.id}/${a.id}">${icon('external')} ${L('크게 보기')}</a>${icon('chev', 'chev')}</summary>
          <div data-av="${t.id}/${a.id}"></div>
        </details>`)}</div>` : ''}
    </div>
    ${ap.status === 'pending' ? decisionForm(t, ap) : decisionResult(ap)}
  </article>`;
}

function decisionForm(t, ap) {
  const btn = (d, cls, ic, label) => html`<button class="btn ${cls}" data-action="decide" data-task="${t.id}" data-ap="${ap.id}" data-decision="${d}">${icon(ic)} ${label}</button>`;
  return html`<div class="decision-form">
    ${ap.choices?.length ? choiceBox(t, ap) : ''}
    <textarea id="cm-${t.id}-${ap.id}" placeholder="${L('코멘트 (선택) — 수정 요청이라면 무엇을 고쳐야 하는지 적어주세요')}"></textarea>
    <div class="actions">
      ${btn('approve', 'btn-approve', 'check', ap.action ? L('승인 (커밋·푸시 진행)') : ap.choices?.length ? L('선택한 항목으로 진행') : L('승인'))}
      ${btn('changes', 'btn-changes', 'edit', L('수정 요청'))}
      ${btn('reject', 'btn-reject', 'x', L('거절'))}
      <span class="hint">${state.mode === 'live' ? L('결정은 즉시 저장되어 {ai}에게 전달됩니다', { ai: aiName(t) }) : L('정적 모드: {ai}에게 전달할 메시지가 표시됩니다', { ai: aiName(t) })}</span>
    </div>
  </div>`;
}

function decisionResult(ap) {
  const st = pick(APPROVAL_STATUS, ap.status, 'pending');
  let applied = '';
  if (ap.applied) {
    const a = ap.applied;
    applied = html`<div class="applied">${icon('git')} ${L('커밋')} ${commitLink(ap.action?.remote_url, a.commit)} · ${a.pushed ? L('푸시 완료') : a.push_error ? html`<span class="del">${L('푸시 실패')}</span>` : L('푸시 안 함')} · ${ago(a.at)}</div>`;
  } else if (ap.status === 'approved' && ap.action) {
    applied = html`<div class="applied">${icon('clock')} ${L('에이전트가 커밋/푸시를 실행하기를 기다리는 중')}</div>`;
  }
  const label = (id) => ap.choices?.find((c) => c.id === id)?.label || id;
  const picked = ap.selected?.length ? html`<div class="picked">${L('선택')}: ${ap.selected.map((id) => html`<span class="fid" title="${label(id)}">${id}</span>`)}</div>` : '';
  return html`<div class="decision-result ${ap.status}">${icon(st.icon)}
    <div><b>${st.label}</b> · ${authorName(ap.decided_by)} · ${ago(ap.decided_at)}
      ${picked}${ap.comment ? html`<div class="comment">${ap.comment}</div>` : ''}${applied}</div>
  </div>`;
}

function staticBanner() {
  if (state.mode === 'live') return '';
  return html`<div class="alert alert-info">${icon('info')}<div class="alert-body"><b>${L('정적 모드 (읽기 전용)')}</b>
    <p>${LH('승인·피드백 버튼을 누르면 담당 AI(Claude · Codex 등) 세션에 붙여넣을 메시지가 표시됩니다. 웹에서 바로 저장하려면 <code>devtrack serve</code> 로 실행한 주소로 여세요.')}</p></div></div>`;
}

function attnApproval(n) {
  const k = pick(APPROVAL_KIND, n.kind, 'general');
  return html`<a class="attn-item" href="#/task/${n.task_id}/approvals">
    <span class="ic tone-amber">${icon(k.icon)}</span>
    <div class="body"><div class="title">${n.title}</div><div class="meta">${L('승인 요청§하나')} · ${k.label} · ${n.task_id} ${n.task_title} · ${ago(n.created_at)}</div></div>
    <span class="btn btn-sm">${L('확인')}</span></a>`;
}

function kpi(label, value, ic, tone, href, sub, attention = false) {
  return html`<a class="kpi ${attention ? 'attention' : ''}" href="${href}">
    <div class="kpi-label"><span class="ic tone-${tone}">${icon(ic)}</span>${label}</div>
    <div class="kpi-value">${value}</div><div class="kpi-sub">${sub}</div>
  </a>`;
}

// ====================================================================== 화면: 대시보드
function viewDashboard() {
  const idx = state.index;
  if (!idx.tasks.length) return viewWelcome();
  const tasks = idx.tasks;
  const c = idx.counts || {};
  const active = tasks.filter((t) => ACTIVE.includes(t.status));
  const openIssues = tasks.reduce((s, t) => s + (t.open_issues || 0), 0);
  const pend = idx.notifications;

  const attention = [
    ...pend.map(attnApproval),
    ...tasks.filter((t) => t.status === 'blocked').map((t) => html`<a class="attn-item" href="#/task/${t.id}/issues">
      <span class="ic tone-red">${icon('slash')}</span>
      <div class="body"><div class="title">${t.title}</div><div class="meta">${L('작업이 막혔습니다')} · ${t.id} · ${t.current_step || t.last_log?.message || ''}</div></div>
      <span class="go">${icon('chev')}</span></a>`),
    ...tasks.filter((t) => t.status !== 'blocked' && t.max_severity >= 2 && ACTIVE.includes(t.status)).map((t) => html`<a class="attn-item" href="#/task/${t.id}/issues">
      <span class="ic tone-orange">${icon('alert')}</span>
      <div class="body"><div class="title">${t.title}</div><div class="meta">${L('심각도 높은 문제 {n}건', { n: t.open_issues })} · ${t.id}</div></div>
      <span class="go">${icon('chev')}</span></a>`),
    ...tasks.filter((t) => t.status === 'queued').map((t) => {
      const ls = listenersFor(t);
      const fresh = ls && ls.filter((l) => !(l.seen_tasks || []).includes(t.id));
      const wait = !ls ? '' : fresh.length ? ` · ${L('{ai} 대기 중', { ai: aiName(t) })}` : ` · ${L('기다리는 {ai} 없음', { ai: aiName(t) })}`;
      return html`<a class="attn-item" href="#/task/${t.id}">
      <span class="ic tone-${ls && !fresh.length ? 'amber' : 'gray'}">${icon('inbox')}</span>
      <div class="body"><div class="title">${t.title}</div><div class="meta">${L('에이전트가 아직 시작하지 않은 지시')} · ${t.id} · ${ago(t.created_at)}${wait}</div></div>
      <span class="go">${icon('chev')}</span></a>`;
    }),
  ];
  const working = active.filter((t) => t.status !== 'queued');
  const recentDone = tasks.filter((t) => t.status === 'done').slice(0, 3);

  return html`
    <div class="page-head">
      <div><h1>${L('대시보드')}</h1><p class="sub">${L('AI 에이전트가 진행 중인 작업과 확인이 필요한 항목')}</p></div>
      <a class="btn btn-primary" href="#/new">${icon('plus')} ${L('새 작업 지시')}</a>
    </div>
    <div class="kpis">
      ${kpi(L('진행 중'), working.length, 'play', 'blue', '#/tasks?status=active', `${L('대기')} ${c.queued || 0} · ${L('막힘')} ${c.blocked || 0}`)}
      ${kpi(L('승인 대기'), pend.length, 'bell', 'amber', '#/approvals', pend.length ? L('확인이 필요합니다') : L('대기 중인 요청 없음'), pend.length > 0)}
      ${kpi(L('열린 문제'), openIssues, 'alert', 'red', '#/tasks?filter=issues', L('{n}개 작업', { n: tasks.filter((t) => t.open_issues).length }))}
      ${kpi(L('완료'), c.done || 0, 'check-circle', 'green', '#/tasks?status=done', L('전체 작업 {n}', { n: tasks.length }))}
    </div>
    <div class="dash-grid">
      <div>
        <section class="card">
          <div class="card-head"><h2>${icon('bell')} ${L('확인이 필요한 항목')}</h2>${pend.length ? html`<a href="#/approvals">${L('승인 요청 전체')}</a>` : ''}</div>
          ${attention.length ? html`<div class="attn-list">${attention}</div>` : html`<div class="empty">${icon('check-circle')}<p>${L('지금 확인할 항목이 없습니다')}</p></div>`}
        </section>
        <section class="section">
          <h2 class="section-title">${L('진행 중인 작업')} <span class="muted">${working.length}</span></h2>
          ${working.length ? html`<div class="task-grid">${working.map(taskCard)}</div>` : html`<div class="card empty">${L('진행 중인 작업이 없습니다')}</div>`}
        </section>
        ${recentDone.length ? html`<section class="section">
          <h2 class="section-title">${L('최근 완료')} <span class="muted"><a href="#/tasks?status=done">${L('전체 보기')}</a></span></h2>
          <div class="task-grid">${recentDone.map(taskCard)}</div>
        </section>` : ''}
      </div>
      <section class="card">
        <div class="card-head"><h2>${icon('activity')} ${L('최근 활동')}</h2><a href="#/activity">${L('전체 보기')}</a></div>
        <ul class="feed">${idx.activity.slice(0, 16).map(feedItem)}</ul>
      </section>
    </div>`;
}

function viewWelcome() {
  return html`
    <div class="page-head"><div><h1>${L('대시보드')}</h1><p class="sub">${L('AI 에이전트의 작업 진행 현황')}</p></div>
      <a class="btn btn-primary" href="#/new">${icon('plus')} ${L('새 작업 지시')}</a></div>
    <section class="card welcome">
      <h2>${L('아직 기록된 작업이 없습니다')}</h2>
      <p class="muted">${L('Claude · Codex 같은 AI 에게 일을 맡기면, 무엇을 하고 있는지 · 문제 · 결과물 · 승인 요청이 이 화면에 실시간으로 표시됩니다.')}</p>
      <ol>
        <li>${LH('<b>처음 한 번:</b> AI 가 DevTrack 에 기록하도록 설정합니다 — <a href="#/guide/s5">사용 방법 5단계</a> (Codex 는 <a href="#/guide/s11">11단계</a>)')}</li>
        <li>${LH('<b>작업 맡기기:</b> AI 채팅으로 지시하거나, <a href="#/new">새 작업 지시</a> 로 웹에서 지시합니다 — <a href="#/guide/s6">6단계</a>')}</li>
        <li>${LH('<b>확인 · 승인:</b> 진행 상황을 보고, 승인 요청에 승인 / 수정 요청 / 거절로 답합니다 — <a href="#/guide/s8">8단계</a>')}</li>
      </ol>
      <div class="welcome-actions">
        <a class="btn btn-primary" href="#/guide">${icon('book')} ${L('사용 방법 보기 (처음부터 따라 하기)')}</a>
        <a class="btn" href="#/new">${icon('plus')} ${L('새 작업 지시')}</a>
      </div>
    </section>`;
}

// ====================================================================== 화면: 사용 방법
async function loadGuide(file) {
  if (state.guides[file] == null) {
    const r = await fetch(file, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    state.guides[file] = await r.text();
  }
  return state.guides[file];
}

async function viewGuide({ rest }) {
  // 영어 화면이면 영어판(guide.en.md). 없거나 못 읽으면 한국어판으로
  let file = getLang() === 'en' ? 'guide.en.md' : 'guide.md';
  let text;
  try {
    text = await loadGuide(file);
  } catch (e) {
    if (file !== 'guide.md') {
      try { file = 'guide.md'; text = await loadGuide(file); } catch { /* 아래에서 오류 표시 */ }
    }
    if (text == null) {
      return html`<div class="page-head"><div><h1>${L('사용 방법')}</h1></div></div>
        <div class="card empty">${icon('alert')}<p>${L('사용 방법 문서(guide.md)를 불러오지 못했습니다')} — ${e.message}</p></div>`;
    }
  }
  const body = text.replace(/^# [^\n]*\n/, ''); // 문서 제목은 페이지 제목으로
  const modeNote = state.mode === 'live'
    ? html`<div class="alert alert-ok">${icon('check-circle')}<div class="alert-body"><b>${L('지금 이 화면은 실시간 · 로컬 서버 모드입니다.')}</b>
        <p>${L('웹에서 누른 승인 · 메시지 · 새 작업 지시가 바로 저장됩니다. 대시보드가 이미 실행 중이니(1 · 2 · 4단계 완료), 3단계(PATH)와 5단계부터 확인하세요.')}</p></div></div>`
    : html`<div class="alert alert-info">${icon('info')}<div class="alert-body"><b>${L('지금 이 화면은 정적 · 읽기 전용 모드입니다.')}</b>
        <p>${L('버튼을 누르면 담당 AI 에게 보낼 메시지가 만들어집니다 (10단계 참고).')}</p></div></div>`;
  return html`
    <div class="page-head"><div><h1>${L('사용 방법')}</h1><p class="sub">${L('처음 쓰는 분을 위한 단계별 안내 — 위에서부터 차례로 따라 하세요')}</p></div>
      <a class="btn" href="${file}" target="_blank" rel="noopener">${icon('file-text')} ${L('원문 (Markdown)')}</a></div>
    <div class="guide-layout">
      <details class="card guide-toc" open><summary>${L('목차')}</summary><div class="toc-list"></div></details>
      <div style="min-width:0">
        ${modeNote}
        <article class="card guide-body markdown-body" data-target="${rest[0] || ''}">${raw(renderMarkdown(body))}</article>
      </div>
    </div>`;
}

function guideHeadingId(text, used) {
  const t = text.trim();
  const m = /^(\d+(?:-\d+)*)\./.exec(t);
  let id = m ? `s${m[1]}` : `s-${t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '')}`;
  while (used.has(id)) id += '_';
  used.add(id);
  return id;
}

/** 렌더된 사용 방법 문서에 목차 · 명령 복사 버튼 · 외부 링크 처리를 붙인다 */
function setupGuide(body, toc) {
  const used = new Set();
  const items = $$('h2, h3', body).map((h) => {
    h.id = guideHeadingId(h.textContent, used);
    return { id: h.id, text: h.textContent.trim(), level: h.tagName === 'H2' ? 2 : 3 };
  });
  const list = $('.toc-list', toc);
  list.innerHTML = String(html`${items.map((it) => html`<a class="toc-l${it.level}" href="#/guide/${it.id}" data-toc="${it.id}">${it.text}</a>`)}`);
  if (window.matchMedia('(max-width: 900px)').matches) toc.open = false;
  $$('pre', body).forEach((pre) => {
    if (pre.querySelector('code.language-text')) return; // 그림 · 흐름도는 복사 버튼 없이
    const wrap = document.createElement('div');
    wrap.className = 'code-wrap';
    pre.replaceWith(wrap);
    wrap.appendChild(pre);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-sm code-copy';
    b.dataset.action = 'copy';
    b.dataset.copy = pre.textContent.replace(/\n+$/, '');
    b.innerHTML = `${icon('copy')} ${L('복사')}`;
    wrap.appendChild(b);
  });
  $$('a[href^="http"]', body).forEach((a) => { a.target = '_blank'; a.rel = 'noopener'; });
  const target = body.dataset.target && document.getElementById(body.dataset.target);
  // render() 가 끝에 스크롤을 맨 위로 돌리므로 그 다음 프레임에 이동
  if (target) requestAnimationFrame(() => { target.scrollIntoView({ block: 'start' }); syncGuideToc(); });
  syncGuideToc();
}

function syncGuideToc() {
  const toc = $('.guide-toc');
  if (!toc) return;
  const heads = $$('.guide-body h2, .guide-body h3');
  let cur = heads[0];
  for (const h of heads) { if (h.getBoundingClientRect().top < 120) cur = h; else break; }
  $$('[data-toc]', toc).forEach((a) => a.classList.toggle('active', a.dataset.toc === cur?.id));
}

// ====================================================================== 화면: 작업 목록
function tasksHref(q) {
  const p = new URLSearchParams(Object.entries(q).filter(([, v]) => v && v !== 'all'));
  const s = p.toString();
  return `#/tasks${s ? '?' + s : ''}`;
}

function viewTasks({ q }) {
  const idx = state.index;
  const status = q.status || 'all';
  const type = q.type || 'all';
  let tasks = idx.tasks;
  if (status === 'active') tasks = tasks.filter((t) => ACTIVE.includes(t.status));
  else if (status !== 'all') tasks = tasks.filter((t) => t.status === status);
  if (type !== 'all') tasks = tasks.filter((t) => t.type === type);
  if (q.filter === 'issues') tasks = tasks.filter((t) => t.open_issues > 0);
  const count = (fn) => idx.tasks.filter(fn).length;
  const chip = (key, label, n) => html`<a class="chip ${status === key ? 'active' : ''}" href="${tasksHref({ ...q, status: key })}">${label}${n != null ? html` <span class="n">${n}</span>` : ''}</a>`;
  return html`
    <div class="page-head">
      <div><h1>${L('작업')}</h1><p class="sub">${L('지시된 모든 작업과 진행 상태')}</p></div>
      <a class="btn btn-primary" href="#/new">${icon('plus')} ${L('새 작업 지시')}</a>
    </div>
    <div class="filter-bar">
      <div class="chips">
        ${chip('all', L('전체'), idx.tasks.length)}
        ${chip('active', L('활성'), count((t) => ACTIVE.includes(t.status)))}
        ${['in_progress', 'awaiting_approval', 'changes_requested', 'blocked', 'queued', 'done'].map((s) => chip(s, STATUS[s].label, count((t) => t.status === s)))}
      </div>
    </div>
    <div class="toolbar">
      <label class="search"><span class="sr-only">${L('검색')}</span>${icon('search')}<input id="task-search" type="search" placeholder="${L('제목, ID, 담당 AI 검색')}"></label>
      <select data-change="type-filter" style="width:auto" aria-label="${L('유형')}">
        <option value="all">${L('모든 유형')}</option>
        ${Object.entries(TYPES).map(([k, v]) => html`<option value="${k}" ${type === k ? raw('selected') : ''}>${v.label}</option>`)}
      </select>
      ${q.filter === 'issues' ? html`<a class="chip active" href="${tasksHref({ ...q, filter: '' })}">${L('열린 문제 있음')} ${icon('x')}</a>` : ''}
    </div>
    ${tasks.length ? html`<div class="table-wrap"><table class="data">
      <thead><tr><th>${L('작업')}</th><th>${L('유형')}</th><th>${L('상태')}</th><th>${L('진행률')}</th><th>${L('담당 AI')}</th><th>${L('확인 필요')}</th><th>${L('업데이트')}</th></tr></thead>
      <tbody>${tasks.map((t) => html`<tr data-href="#/task/${t.id}" data-search="${[t.id, t.title, agentLabel(t), TYPES[t.type]?.label, t.current_step, (t.tags || []).join(' ')].filter(Boolean).join(' ').toLowerCase()}">
        <td><div class="t-title"><span class="muted mono" style="font-weight:500;margin-right:6px">${t.id}</span>${t.title}</div><div class="t-step">${taskNow(t).text}</div></td>
        <td class="t-type">${typeBadge(t.type)}</td>
        <td>${statusBadge(t.status)}</td>
        <td><div class="t-prog">${progressBar(t)}<span>${t.progress || 0}%</span></div></td>
        <td class="nowrap t-agent">${agentLabel(t)}</td>
        <td class="nowrap">${t.pending_approvals ? html`<span class="flag amber" style="font-weight:600">${icon('bell')} ${L('승인§개수')} ${t.pending_approvals}</span> ` : ''}${t.open_issues ? html`<span class="flag ${t.max_severity >= 2 ? 'red' : 'orange'}" style="font-weight:600">${icon('alert')} ${L('문제')} ${t.open_issues}</span>` : ''}${!t.pending_approvals && !t.open_issues ? html`<span class="muted">—</span>` : ''}</td>
        <td class="nowrap muted">${ago(t.updated_at)}</td>
      </tr>`)}</tbody></table></div>
      <div id="no-match" class="empty" hidden>${L('검색 결과가 없습니다')}</div>`
    : html`<div class="card empty">${icon('inbox')}<p>${L('조건에 맞는 작업이 없습니다')}</p></div>`}`;
}

function filterTasks() {
  const q = state.taskSearch.trim().toLowerCase();
  let shown = 0;
  $$('tr[data-search]').forEach((tr) => {
    const ok = !q || tr.dataset.search.includes(q);
    tr.hidden = !ok;
    if (ok) shown++;
  });
  const nm = $('#no-match');
  if (nm) nm.hidden = shown > 0;
}

// ====================================================================== 화면: 작업 상세
function nowCard(t) {
  const lastMsg = [...(t.log || [])].reverse().find((e) => e.kind !== 'user')?.message || '';
  const cfg = {
    in_progress: { label: L('지금 하는 일'), pulse: true },
    awaiting_approval: { label: L('사용자 승인 대기 중'), pulse: false },
    changes_requested: { label: L('수정 요청 반영 대기'), pulse: false },
    blocked: { label: L('막힘 — 확인 필요'), pulse: false },
    queued: { label: L('에이전트가 아직 시작하지 않음'), pulse: false },
    done: { label: L('완료'), pulse: false },
    cancelled: { label: L('취소됨'), pulse: false },
  }[t.status] || { label: L('현재 상태') };
  const pending = (t.approvals || []).filter((a) => a.status === 'pending');
  let text = t.status === 'done' ? (lastMsg || L('작업이 완료되었습니다')) : (t.current_step || lastMsg || '—');
  if (t.status === 'awaiting_approval' && pending.length) text = pending[0].title + (pending.length > 1 ? L(' 외 {n}건', { n: pending.length - 1 }) : '');
  const plan = t.plan || [];
  return html`<section class="card now-card">
    <div><div class="now-label">${cfg.pulse ? raw('<span class="pulse"></span>') : icon(STATUS[t.status]?.icon || 'circle')} ${cfg.label}</div><div class="now-text">${text}</div></div>
    <div class="now-prog"><div class="row"><span>${plan.length ? L('계획 {d}/{n}단계', { d: plan.filter((p) => p.done).length, n: plan.length }) : L('진행률')}</span><b>${t.progress || 0}%</b></div>${progressBar(t, true)}</div>
  </section>`;
}

/** 작업 머리말의 모델 표시 — 진행 중인 작업은 눌러서 바꾼다 */
function modelChip(t) {
  const spec = specLabel(t.model, t.effort);
  if (!ACTIVE.includes(t.status)) return spec ? html`<span title="${L('모델 · 추론 강도')}">${icon('sliders')} ${spec}</span>` : '';
  return html`<button type="button" class="th-model ${spec ? 'set' : ''}" data-action="task-model" data-task="${t.id}" title="${L('이 작업을 처리할 AI 모델 · 추론 강도')}">${icon('sliders')} ${spec || L('모델 지정 안 함')}<span class="th-model-edit">${L('변경§버튼')}</span></button>`;
}

async function viewTask({ rest }) {
  const [id, tabArg] = rest;
  const t = await loadTask(id);
  const pend = t.approvals.filter((a) => a.status === 'pending');
  const openIssues = t.issues.filter((i) => i.status === 'open');
  const tab = tabArg || 'overview';
  const repo = repoLabel(t.repo);
  const tabs = [
    ['overview', L('개요')],
    ['log', L('진행 기록'), t.log.length],
    ['issues', L('문제점'), openIssues.length || null, openIssues.length > 0],
    ['artifacts', L('결과물'), t.artifacts.length],
    ...(t.builds?.length ? [['builds', L('빌드§탭'), t.builds.length, t.builds[t.builds.length - 1].status !== 'ok']] : []),
    ['approvals', L('승인§탭'), pend.length || t.approvals.length || null, pend.length > 0],
  ];
  let body;
  switch (tab) {
    case 'log': body = taskLog(t); break;
    case 'builds': body = html`<div class="card">${[...(t.builds || [])].reverse().map((b) => buildRow(t, b))}</div>`; break;
    case 'issues': body = taskIssues(t); break;
    case 'artifacts': body = taskArtifacts(t); break;
    case 'approvals': body = taskApprovals(t); break;
    default: body = taskOverview(t, openIssues);
  }
  const sec = SECTION_OF_TYPE[t.type] || 'tasks';
  const links = linksOf(t.id);
  return html`
    <div class="crumbs"><a href="${sectionHref(t)}">${SECTION_LABEL[sec]}</a><span>/</span><span class="mono">${t.id}</span></div>
    <div class="task-head"><div style="min-width:0;flex:1">
      <div class="th-badges">${typeBadge(t.type)}${statusBadge(t.status)}${t.priority === 'high' ? html`<span class="badge tone-red">${L('우선순위 높음')}</span>` : ''}${(t.tags || []).map((g) => html`<span class="badge">#${g}</span>`)}</div>
      <h1><span class="th-id">${t.id}</span>${t.title}</h1>
      <div class="th-meta">
        <span title="${L('담당 AI')}">${icon('cpu')} ${agentLabel(t)}</span>
        ${modelChip(t)}
        ${repo ? html`<span>${icon('folder')} ${repo.href ? html`<a href="${repo.href}" target="_blank" rel="noopener">${repo.text}</a>` : repo.text}</span>` : ''}
        ${t.repo?.branch ? html`<span>${icon('branch')} ${t.repo.branch}</span>` : ''}
        <span>${icon('clock')} ${L('시작')} ${fmtAbs(t.created_at)}</span>
        <span>${L('업데이트')} ${ago(t.updated_at)}</span>
      </div>
      ${links.length ? html`<div class="th-links">${icon('link')} ${L('관련 작업')} ${links.map((l) => html`<a class="link-chip" href="#/task/${l.id}"><span class="sdot tone-${pick(STATUS, l.status, 'queued').tone}"></span>${l.id} · ${pick(TYPES, l.type).label} · ${l.title}</a>`)}</div>` : ''}
    </div>
    <div class="th-actions">
      ${ACTIVE.includes(t.status) ? html`<button class="btn btn-sm" data-action="task-complete" data-task="${t.id}" title="${L('끝나지 않았어도 이 작업을 완료로 넘깁니다')}">${icon('check-circle')} ${L('완료로 처리')}</button>
        <button class="btn btn-sm" data-action="task-cancel" data-task="${t.id}">${icon('x-circle')} ${L('작업 취소')}</button>` : ''}
      <button class="btn btn-sm btn-ghost btn-danger-ghost" data-action="task-delete" data-task="${t.id}">${icon('trash')} ${L('삭제')}</button>
    </div></div>
    ${t.status === 'done' && t.completed?.by_user ? html`<div class="alert alert-ok">${icon('check-circle')}<div class="alert-body"><b>${L('사용자가 완료로 처리한 작업입니다')}</b> · ${authorName(t.completed.by)} · ${ago(t.completed.at)}${t.completed.was && STATUS[t.completed.was] ? ` · ${L('처리 전 상태')}: ${STATUS[t.completed.was].label}` : ''}${t.completed.note ? html`<p>${L('메모')}: ${t.completed.note}</p>` : ''}</div></div>` : ''}
    ${t.status === 'cancelled' && t.cancelled ? html`<div class="alert alert-gray">${icon('slash')}<div class="alert-body"><b>${L('취소된 작업입니다')}</b> · ${authorName(t.cancelled.by)} · ${ago(t.cancelled.at)}${t.cancelled.reason ? html`<p>${L('사유')}: ${t.cancelled.reason}</p>` : ''}</div></div>` : ''}
    ${pend.length && tab !== 'approvals' ? html`<div class="alert alert-amber">${icon('bell')}<div class="alert-body"><b>${L('승인이 필요한 항목 {n}건', { n: pend.length })}</b><p>${pend.map((a) => a.title).join(' · ')}</p></div><a class="btn btn-sm" href="#/task/${t.id}/approvals">${L('확인하기')}</a></div>` : ''}
    ${t.status === 'queued' ? waitingBox(t) : ''}
    ${nowCard(t)}
    <nav class="tabs">${tabs.map(([k, label, n, hot]) => html`<a class="${tab === k ? 'active' : ''}" href="#/task/${t.id}${k === 'overview' ? '' : '/' + k}">${label}${n != null ? html`<span class="n ${hot ? 'hot' : ''}">${n}</span>` : ''}</a>`)}</nav>
    ${body}`;
}

// ---------------------------------------------------------------------- AI 끼리 리뷰 반복
/** 논문 · 문서 심사의 판정 (store.JOURNAL_VERDICT_LABEL) */
const JOURNAL_VERDICT = translated({
  ready: { label: '게재 가능', tone: 'green', icon: 'check' },
  minor_revision: { label: '소폭 수정', tone: 'amber', icon: 'edit' },
  major_revision: { label: '대폭 수정', tone: 'orange', icon: 'edit' },
  rework: { label: '게재 불가', tone: 'red', icon: 'x' },
});
const verdictBadge = (v, doc = false) => {
  const j = JOURNAL_VERDICT[v];
  if (j) return html`<span class="badge tone-${j.tone}">${icon(j.icon)} ${j.label}</span>`;
  return v === 'approve' ? html`<span class="badge tone-green">${icon('check')} ${L('통과')}</span>`
    : v === 'request_changes' ? html`<span class="badge tone-amber">${icon('edit')} ${L('수정 필요§리뷰')}</span>`
      : html`<span class="badge tone-blue">${icon('eye')} ${doc ? L('심사 중') : L('리뷰 중')}</span>`;
};
/** 리뷰 반복의 상태 이름 — 논문 심사는 '심사' 로 */
function loopStateLabel(loop, st) {
  if (loop.kind !== 'doc') return st.label;
  return { planned: L('심사 전'), reviewing: L('심사 중'), exhausted: L('심사 횟수 다 씀') }[loop.state] || st.label;
}

/** 심사 차례의 결과물: 원고 · 원문 · 지난 심사 이후 바뀐 부분 · 답변서 */
function docRoundLinks(h) {
  const links = [[h.file, L('원고')], [h.manuscript, L('원문')], [h.since, L('지난 심사 이후')], [h.response, L('답변서')]]
    .filter(([id]) => id);
  return links.map(([id, label], i) => html`${i ? ' · ' : ''}<a href="#/artifact/${h.task}/${id}">${label}</a>`);
}

/** 개발 작업: 누가 리뷰하는지, 차례마다 결과, 지금 누구 차례인지 */
function reviewLoopPanel(t) {
  const loop = t.review_loop;
  if (!loop?.reviewer) return '';
  const st = pick(REVIEW_STATE, loop.state, 'planned');
  const hist = loop.history || [];
  const used = hist.length - (loop.cycle_start || 0);
  const spec = specLabel(loop.model, loop.effort);
  const dev = aiName(t);
  const doc = loop.kind === 'doc';
  const next = doc ? ({
    planned: L('원고를 다 쓰면 {dev} 가 {who} 에게 심사를 요청합니다.', { dev, who: loop.reviewer }),
    reviewing: L('{who} 가 심사하는 중입니다. {who} 가 켜져 있어야 이어집니다 (실행기 devtrack watch 또는 대기 모드).', { who: loop.reviewer }),
    changes: L('{dev} 가 원고를 고치고 답변서(response to reviewers)를 써서 다시 심사를 요청합니다.', { dev }),
    approved: L('게재 가능 판정을 받았습니다. {dev} 가 결과를 알려 드립니다.', { dev }),
    exhausted: L('심사를 정한 횟수만큼 받았지만 아직 수정이 필요합니다. {dev} 가 남은 지적과 함께 결정을 요청합니다.', { dev }),
    rejected: L('게재 불가 판정입니다. {dev} 가 다음에 어떻게 할지 결정을 요청합니다.', { dev }),
  }[loop.state] || '') : {
    planned: L('개발이 끝나면 {dev} 가 {who} 에게 리뷰를 요청합니다.', { dev, who: loop.reviewer }),
    reviewing: L('{who} 가 리뷰하는 중입니다. {who} 가 켜져 있어야 이어집니다 (실행기 devtrack watch 또는 대기 모드).', { who: loop.reviewer }),
    changes: L('{dev} 가 지적을 고친 뒤 다시 리뷰를 요청합니다.', { dev }),
    approved: L('리뷰를 통과했습니다. {dev} 가 커밋 승인을 요청합니다 — 커밋 · 푸시는 승인해야만 진행됩니다.', { dev }),
    exhausted: L('리뷰를 정한 횟수만큼 받았지만 아직 수정이 필요합니다. {dev} 가 남은 지적과 함께 결정을 요청합니다.', { dev }),
  }[loop.state] || '';
  return html`<section class="card review-loop">
    <div class="card-head"><h2>${icon('eye')} ${doc ? L('AI 심사') : L('AI 리뷰')}</h2><span class="badge tone-${st.tone}">${icon(st.icon)} ${loopStateLabel(loop, st)}</span></div>
    <div class="card-body">
      <div class="rl-who">${doc ? L('심사위원') : L('리뷰어')} <b>${loop.reviewer}</b>${spec ? html` <span class="muted">· ${spec}</span>` : ''} · ${L('최대 {n}번', { n: loop.rounds })}${hist.length ? html` · ${L('{n}번 받음', { n: used })}` : ''}${loop.task ? html` · <a href="#/task/${loop.task}">${doc ? L('심사 작업') : L('리뷰 작업')} ${loop.task}</a>` : ''}</div>
      ${doc ? html`<div class="rl-doc muted">${icon('file-text')} ${loop.doc || L('원고 파일은 심사를 요청할 때 정합니다')}${loop.venue ? html` · ${L('투고처')} <b>${loop.venue}</b>` : ''}${loop.record ? html` · <a href="#/artifact/${t.id}/${loop.record}">${L('심사 · 답변 기록')}</a>` : ''}</div>` : ''}
      ${hist.length ? html`<ol class="rl-rounds">${hist.map((h) => html`<li>
        <span class="rl-n">${L('{n}차', { n: h.round })}</span>${verdictBadge(h.verdict, doc)}
        ${doc ? (h.stat ? html`<span class="muted">${L('바뀐 부분')} <span class="add">+${h.stat.additions}</span> <span class="del">−${h.stat.deletions}</span></span>` : '')
    : html`<span class="muted">${L('{n}개 파일', { n: h.stat?.files ?? '?' })} <span class="add">+${h.stat?.additions ?? 0}</span> <span class="del">−${h.stat?.deletions ?? 0}</span></span>`}
        ${h.open?.length ? html`<span class="rl-open">${L('열린 항목')} ${h.open.map((id) => html`<span class="fid">${id}</span>`)}</span>` : ''}
        <span class="rl-links">${doc ? docRoundLinks(h)
    : html`${h.diff ? html`<a href="#/artifact/${h.task}/${h.diff}">${L('변경 내용')}</a>` : ''}${h.since ? html` · <a href="#/artifact/${h.task}/${h.since}">${L('지난 리뷰 이후')}</a>` : ''}`}</span>
        ${h.summary ? html`<div class="rl-sum">${h.summary}</div>` : ''}
        ${h.excluded?.length ? html`<div class="rl-ex muted">${L('빌드 결과물 제외')}: ${excludedText(h.excluded, 3)}</div>` : ''}
      </li>`)}</ol>` : ''}
      <p class="rl-next muted">${next}</p>
    </div>
  </section>`;
}

/** 리뷰 작업: 무엇을 리뷰하는지 (개발 작업 · 차례) */
function reviewOfPanel(t) {
  const of = t.review_of;
  if (!of?.task) return '';
  const rounds = t.review_rounds || [];
  return html`<section class="card review-loop">
    <div class="card-head"><h2>${icon('eye')} ${of.kind === 'doc' ? L('심사 대상') : L('리뷰 대상')}</h2><span class="muted">${L('{n}차', { n: t.review_round || 1 })}</span></div>
    <div class="card-body">
      <div class="rl-who"><a href="#/task/${of.task}">${of.task} · ${of.title || ''}</a> · ${of.kind === 'doc' ? L('저자') : L('개발')} <b>${of.agent || 'AI'}</b>${of.model || of.effort ? html` <span class="muted">(${specLabel(of.model, of.effort)})</span>` : ''}</div>
      ${rounds.length ? html`<ol class="rl-rounds">${rounds.map((r) => html`<li><span class="rl-n">${L('{n}차', { n: r.round })}</span>${verdictBadge(r.verdict, of.kind === 'doc')}${r.open?.length ? html`<span class="rl-open">${L('열린 항목')} ${r.open.map((id) => html`<span class="fid">${id}</span>`)}</span>` : ''}${r.summary ? html`<div class="rl-sum">${r.summary}</div>` : ''}</li>`)}</ol>` : ''}
    </div>
  </section>`;
}

function taskOverview(t, openIssues) {
  const plan = t.plan || [];
  const recent = [...t.log].reverse().slice(0, 8);
  const arts = [...t.artifacts].reverse().slice(0, 6);
  return html`<div class="detail-grid">
    <div>
      ${reviewLoopPanel(t)}${reviewOfPanel(t)}
      ${t.type === 'docreview' || t.review?.doc ? (t.review ? docReviewPanel(t) : '') : reviewPanel(t)}
      <section class="card">
        <div class="card-head"><h2>${icon('message')} ${L('작업 지시')}</h2><span class="muted" style="font-size:12.5px">${authorName(t.created_by)} · ${fmtAbs(t.created_at)}</span></div>
        <div class="card-body">${t.instruction ? html`<div class="markdown-body">${raw(renderMarkdown(t.instruction))}</div>` : html`<div class="muted">${L('지시 내용이 기록되지 않았습니다')}</div>`}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>${icon('activity')} ${L('최근 진행 기록')}</h2><a href="#/task/${t.id}/log">${L('전체 {n}건', { n: t.log.length })}</a></div>
        <div class="card-body"><ul class="timeline">${recent.map((e) => timelineItem(e, { tid: t.id }))}</ul></div>
      </section>
      ${feedbackBox(t)}
    </div>
    <div>
      ${t.builds?.length ? html`<section class="card">
        <div class="card-head"><h2>${icon('file-text')} ${L('컴파일된 PDF')}</h2><a href="#/task/${t.id}/builds">${L('빌드 기록')} ${t.builds.length}</a></div>
        <div class="card-body">${buildBox(t)}</div>
      </section>` : ''}
      <section class="card">
        <div class="card-head"><h2>${icon('list')} ${L('작업 계획')}</h2><span class="muted">${plan.filter((p) => p.done).length}/${plan.length}</span></div>
        <div class="card-body">${planList(t)}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>${icon('alert')} ${L('열린 문제')}</h2><a href="#/task/${t.id}/issues">${L('전체 {n}건', { n: t.issues.length })}</a></div>
        ${openIssues.length ? openIssues.map(issueItem) : html`<div class="empty inline" style="padding:14px 18px">${L('열린 문제가 없습니다')}</div>`}
      </section>
      <section class="card">
        <div class="card-head"><h2>${icon('package')} ${L('결과물')}</h2><a href="#/task/${t.id}/artifacts">${L('전체 {n}개', { n: t.artifacts.length })}</a></div>
        ${arts.length ? arts.map((a) => artRow(t.id, a)) : html`<div class="empty inline" style="padding:14px 18px">${L('아직 결과물이 없습니다')}</div>`}
      </section>
    </div>
  </div>`;
}

function taskLog(t) {
  const items = [...t.log].reverse();
  return html`<div class="detail-grid">
    <div><section class="card"><div class="card-body"><ul class="timeline">${items.map((e) => timelineItem(e, { tid: t.id }))}</ul></div></section></div>
    <div>${feedbackBox(t)}</div>
  </div>`;
}

function taskIssues(t) {
  const open = t.issues.filter((i) => i.status === 'open');
  const done = t.issues.filter((i) => i.status !== 'open');
  if (!t.issues.length) return html`<div class="card empty">${icon('check-circle')}<p>${L('기록된 문제가 없습니다')}</p></div>`;
  return html`
    <h2 class="section-title">${L('열린 문제')} <span class="muted">${open.length}</span></h2>
    <div class="card">${open.length ? open.map(issueItem) : html`<div class="empty inline" style="padding:14px 18px">${L('열린 문제가 없습니다')}</div>`}</div>
    ${done.length ? html`<h2 class="section-title" style="margin-top:24px">${L('해결된 문제')} <span class="muted">${done.length}</span></h2><div class="card">${done.map(issueItem)}</div>` : ''}`;
}

function taskArtifacts(t) {
  if (!t.artifacts.length) return html`<div class="card empty">${icon('package')}<p>${L('아직 결과물이 없습니다')}</p></div>`;
  const items = t.artifacts.map((a) => ({ ...a, task_id: t.id }));
  return html`<div class="art-tools">${artTools({ single: true })}</div>${artCollection(items, { showTask: false })}`;
}

function taskApprovals(t) {
  if (!t.approvals.length) return html`<div class="card empty">${icon('bell')}<p>${L('승인 요청이 없습니다')}</p></div>`;
  const list = [...t.approvals].sort((a, b) => (a.status === 'pending') === (b.status === 'pending') ? tsNum(b.created_at) - tsNum(a.created_at) : a.status === 'pending' ? -1 : 1);
  return html`${staticBanner()}${list.map((ap) => approvalCard(t, ap))}`;
}

// ====================================================================== 화면: 승인 요청
async function viewApprovals() {
  const idx = state.index;
  const pend = idx.notifications;
  await Promise.all([...new Set(pend.map((n) => n.task_id))].map(loadTask));
  const history = (idx.approvals || []).filter((a) => a.status !== 'pending').slice(0, 60);
  return html`
    <div class="page-head"><div><h1>${L('승인 요청')}</h1><p class="sub">${L('에이전트가 다음 단계(커밋·푸시, 계획 진행 등) 전에 확인을 요청한 항목')}</p></div></div>
    ${staticBanner()}
    <h2 class="section-title">${L('결정 대기')} <span class="muted">${pend.length}</span></h2>
    ${pend.length ? pend.map((n) => {
      const t = state.tasks.get(n.task_id);
      const ap = t?.approvals.find((a) => a.id === n.approval_id);
      return ap ? approvalCard(t, ap, { showTask: true }) : '';
    }) : html`<div class="card empty">${icon('check-circle')}<p>${L('결정을 기다리는 요청이 없습니다')}</p></div>`}
    <h2 class="section-title" style="margin-top:28px">${L('처리된 요청')} <span class="muted">${history.length}</span></h2>
    ${history.length ? html`<div class="card">${history.map((a) => {
      const st = pick(APPROVAL_STATUS, a.status, 'pending');
      return html`<a class="art-row" href="#/task/${a.task_id}/approvals">
        <span class="ic tone-${st.tone}">${icon(st.icon)}</span>
        <div class="body"><div class="title">${a.title}</div>
          <div class="meta">${st.label} · ${a.task_id} ${a.task_title} · ${ago(a.decided_at)}${a.commit ? ` · ${L('커밋')} ${a.commit.slice(0, 8)}` : ''}${a.comment ? ` · "${a.comment.slice(0, 60)}${a.comment.length > 60 ? '…' : ''}"` : ''}</div></div>
        ${icon('chev')}</a>`;
    })}</div>` : html`<div class="card empty inline" style="padding:16px 18px">${L('아직 처리된 요청이 없습니다')}</div>`}`;
}

// ====================================================================== 화면: 리뷰
function viewerHead(t, art) {
  const k = pick(ART_KIND, art.kind, 'file');
  const url = artifactUrl(t.id, art);
  return html`<div class="viewer-head">
    <div style="min-width:0"><h2>${art.title}</h2>
      <div class="meta"><span class="badge">${icon(k.icon)}${k.label}</span>${catBadge(art)}${art.version > 1 ? html`<span class="badge tone-accent">v${art.version}</span>` : ''}<a href="#/task/${t.id}">${t.id} · ${t.title}</a><span>${authorName(art.updated_by || art.author)} · ${ago(art.updated_at || art.created_at)}</span>${art.size ? html`<span>${fmtBytes(art.size)}</span>` : ''}</div>
      ${art.versions?.length ? html`<div class="versions">${icon('clock')} ${L('이전 버전')} ${[...art.versions].reverse().map((v) => html`<a href="${artifactUrl(t.id, v)}" target="_blank" rel="noopener" title="${fmtAbs(v.created_at)}">v${v.version} · ${fmtRel(v.created_at)}</a>`)}</div>` : ''}
    </div>
    <div class="actions">${url ? html`<a class="btn btn-sm" href="${url}" target="_blank" rel="noopener">${icon('external')} ${L('새 탭')}</a>${art.path ? html`<a class="btn btn-sm" href="${url}" download>${icon('download')} ${L('다운로드')}</a>` : ''}` : ''}</div>
  </div>`;
}

const codeReviews = (idx) => idx.reviews.filter((r) => r.task_type !== 'docreview'); // 문서 리뷰는 '문서 리뷰' 화면에

async function viewReviews({ rest }) {
  const reviews = codeReviews(state.index);
  const head = html`<div class="page-head"><div><h1>${L('코드 리뷰')}</h1><p class="sub">${L('AI가 작성한 리뷰 리포트 (Markdown · PDF)')}</p></div></div>`;
  if (!reviews.length) {
    return html`${head}<div class="card empty">${icon('eye')}<p>${L('등록된 리뷰 리포트가 없습니다')}</p><p>${LH('<code>devtrack review T-0001 review_report.md</code> 로 등록하면 여기에 표시됩니다')}</p></div>`;
  }
  const [tid, fid] = rest;
  const sel = reviews.find((r) => r.task_id === tid && r.id === fid) || reviews[0];
  const t = await loadTask(sel.task_id);
  const art = t.artifacts.find((a) => a.id === sel.id);
  if (!art) return viewNotFound();
  const related = t.approvals.filter((a) => a.status === 'pending' && (a.kind === 'review' || (a.artifacts || []).includes(art.id)));
  const sumOf = (id) => state.index.tasks.find((x) => x.id === id)?.review;
  return html`${head}
    <div class="review-layout">
      <aside class="card review-list">${reviews.map((r) => {
        const rs = sumOf(r.task_id);
        return html`
        <a class="review-item ${r === sel ? 'active' : ''}" href="#/reviews/${r.task_id}/${r.id}">
          <div class="title">${r.title}</div>
          <div class="meta"><span class="badge">${pick(ART_KIND, r.kind, 'file').label}</span>${r.version > 1 ? html`<span class="badge tone-accent">v${r.version}</span>` : ''}<span>${r.task_id}</span>${ago(r.updated_at || r.created_at)}</div>
          <div class="meta">${r.task_title}</div>
          ${rs?.total ? html`<div class="meta">${rs.open ? sevChips(rs.by_severity) : html`<span class="badge tone-green">${L('모두 처리됨')}</span>`}</div>` : ''}
        </a>`;
      })}</aside>
      <div style="min-width:0;display:flex;flex-direction:column;gap:16px">
        ${reviewPanel(t)}
        ${related.map((a) => approvalCard(t, a, { skip: [art.id] }))}
        <section class="card" style="overflow:hidden">
          ${viewerHead(t, art)}
          ${art.description ? html`<div class="card-body markdown-body compact" style="border-bottom:1px solid var(--border)">${raw(renderMarkdown(art.description))}</div>` : ''}
          <div data-av="${t.id}/${art.id}"></div>
        </section>
        ${feedbackBox(t, L('리뷰에 대한 의견 보내기'))}
      </div>
    </div>`;
}

// ====================================================================== 화면: 분야 섹션 (코드 개발 / 논문 작성 / 데이터 분석)
function sectionHead(title, sub, newHref) {
  return html`<div class="page-head"><div><h1>${title}</h1><p class="sub">${sub}</p></div>
    <a class="btn btn-primary" href="${newHref}">${icon('plus')} ${L('새 작업 지시')}</a></div>`;
}

function sectionStats(tasks, arts) {
  const n = (f) => tasks.filter(f).length;
  const pend = tasks.reduce((s, t) => s + (t.pending_approvals || 0), 0);
  const blocked = n((t) => t.status === 'blocked');
  const st = (v, label, cls = '') => html`<div class="st ${cls}"><b>${v}</b><span>${label}</span></div>`;
  return html`<div class="sec-stats">
    ${st(n((t) => ['in_progress', 'changes_requested'].includes(t.status)), L('진행 중'))}
    ${st(pend, L('승인 대기'), pend ? 'hot' : '')}
    ${st(blocked, L('막힘'), blocked ? 'bad' : '')}
    ${st(n((t) => t.status === 'queued'), L('대기'))}
    ${st(n((t) => t.status === 'done'), L('완료'))}
    ${st(arts.length, L('결과물'))}
  </div>`;
}

function sectionAttention(ids) {
  const pend = state.index.notifications.filter((n) => ids.has(n.task_id));
  if (!pend.length) return '';
  return html`<section class="card" style="margin-bottom:20px">
    <div class="card-head"><h2>${icon('bell')} ${L('확인이 필요한 항목')}</h2></div>
    <div class="attn-list">${pend.map(attnApproval)}</div></section>`;
}

function sectionFeed(ids, n = 14) {
  const items = state.index.activity.filter((e) => ids.has(e.task_id)).slice(0, n);
  return html`<section class="card">
    <div class="card-head"><h2>${icon('activity')} ${L('최근 진행')}</h2><a href="#/activity">${L('전체 기록')}</a></div>
    ${items.length ? html`<ul class="feed">${items.map(feedItem)}</ul>` : html`<div class="empty">${L('아직 기록이 없습니다')}</div>`}
  </section>`;
}

const DRAFT_KINDS = ['latex', 'pdf', 'markdown', 'text'];
const isBuildLog = (a) => !!a.build && a.kind !== 'pdf';
const isDraft = (a) => DRAFT_KINDS.includes(a.kind) && !isBuildLog(a);
const PAPER_GROUPS = translated({
  all: { label: '전체', test: () => true },
  pdf: { label: '컴파일된 PDF', test: (a) => a.kind === 'pdf' && !!a.build },
  draft: { label: '원고', test: isDraft },
  figure: { label: '그림', test: (a) => FIGURE_KINDS.includes(a.kind) },
  data: { label: '표 · 데이터', test: (a) => ['csv', 'notebook'].includes(a.kind) },
  other: { label: '기타', test: (a) => isBuildLog(a) || ![...DRAFT_KINDS, ...FIGURE_KINDS, 'csv', 'notebook'].includes(a.kind) },
});

// ---------------------------------------------------------------- LaTeX 빌드
const bnum = (b) => (b?.id || '').split('-')[1];
const baseName = (p) => String(p || '').split('/').pop();
const warnCount = (w = {}) => (w.undefined_citations?.length || 0) + (w.undefined_references?.length || 0)
  + (w.other?.length || 0) + (w.overfull || 0);

function buildInfo(t) {
  const bs = t?.builds || [];
  if (!bs.length) return null;
  return { last: bs[bs.length - 1], ok: [...bs].reverse().find((b) => b.status === 'ok' && b.pdf) };
}

function buildErrText(b) {
  const e = b.errors?.[0];
  if (!e) return L('컴파일 실패');
  return `${e.message}${e.line ? ` (${e.file || baseName(b.source)}:${e.line})` : ''}`;
}

function warnChips(w = {}) {
  const items = [];
  (w.undefined_citations || []).forEach((c) => items.push(html`<span class="wchip">${L('인용 없음')} <code>${c}</code></span>`));
  (w.undefined_references || []).forEach((r) => items.push(html`<span class="wchip">${L('참조 없음')} <code>${r}</code></span>`));
  if (w.overfull) items.push(html`<span class="wchip">Overfull box ${w.overfull}</span>`);
  (w.other || []).slice(0, 4).forEach((o) => items.push(html`<span class="wchip">${o.replace(/^LaTeX Warning:\s*/, '')}</span>`));
  return html`<div class="wchips">${items.length ? items : html`<span class="wchip ok">${icon('check')} ${L('경고 없음')}</span>`}</div>`;
}

/** 논문 카드 / 작업 개요에 쓰는 '최신 PDF' 상자 */
function buildBox(t) {
  const info = buildInfo(t);
  if (!info) return html`<div class="muted mini-empty">${L('아직 컴파일된 PDF 가 없습니다')} <span class="mono">(devtrack latex)</span></div>`;
  const { last, ok } = info;
  const failed = last.status !== 'ok';
  return html`<div class="build-box">
    ${failed ? html`<a class="build-fail" href="#/task/${t.id}/builds">${icon('x-circle')}<span><b>${L('최근 컴파일 실패')}</b> · ${L('빌드')} #${bnum(last)} · ${buildErrText(last)} · ${ago(last.created_at)}</span></a>` : ''}
    ${ok ? html`<a class="pdf-row" href="#/artifact/${t.id}/${ok.pdf}">
      <span class="pdf-ic">PDF</span>
      <div class="body"><div class="title">${baseName(ok.source).replace(/\.tex$/, '')}.pdf <span class="muted">${failed ? `${L('마지막 성공')} · ` : ''}${L('빌드')} #${bnum(ok)}</span></div>
        <div class="meta">${ok.pages ? `${L('{n}쪽', { n: ok.pages })} · ` : ''}${warnCount(ok.warnings) ? html`<span class="warn-t">${L('경고')} ${warnCount(ok.warnings)}</span>` : L('경고 없음')} · ${ago(ok.created_at)}</div></div>
      <span class="btn btn-sm btn-primary">${icon('eye')} ${L('PDF 보기')}</span></a>` : html`<div class="muted mini-empty">${L('성공한 빌드가 아직 없습니다')}</div>`}
  </div>`;
}

function buildRow(t, b, { actions = true } = {}) {
  const ok = b.status === 'ok';
  const meta = [b.engine, b.pages ? L('{n}쪽', { n: b.pages }) : '', b.duration != null ? L('{n}초', { n: b.duration }) : '', authorName(b.author)].filter(Boolean).join(' · ');
  return html`<div class="build-row">
    <span class="ic tone-${ok ? 'green' : 'red'}">${icon(ok ? 'check' : 'x')}</span>
    <div class="body">
      <div class="title">${L('빌드')} #${bnum(b)} <span class="badge tone-${ok ? 'green' : 'red'}">${ok ? L('성공') : L('실패')}</span><span class="mono muted">${b.source}</span></div>
      <div class="meta">${meta} · ${ago(b.created_at)}</div>
      ${!ok && b.errors?.length ? html`<ul class="build-errors">${b.errors.map((e) => html`<li><code>${e.file || baseName(b.source)}${e.line ? `:${e.line}` : ''}</code> ${e.message}</li>`)}</ul>` : ''}
      ${ok ? warnChips(b.warnings) : ''}
    </div>
    ${actions ? html`<div class="actions">${b.pdf ? html`<a class="btn btn-sm btn-primary" href="#/artifact/${t.id}/${b.pdf}">${icon('eye')} PDF</a>` : ''}${b.log ? html`<a class="btn btn-sm" href="#/artifact/${t.id}/${b.log}">${icon('file')} ${L('로그')}</a>` : ''}</div>` : ''}
  </div>`;
}

/** PDF/로그 뷰어 위에 붙는 빌드 정보 + 다른 빌드로 이동 */
function buildPanel(t, bid) {
  const b = (t.builds || []).find((x) => x.id === bid);
  if (!b) return '';
  const all = (t.builds || []).filter((x) => x.pdf || x.log);
  return html`<div class="build-panel">
    ${buildRow(t, b, { actions: false })}
    ${all.length > 1 ? html`<div class="bp-hist"><span class="muted">${L('다른 빌드')}</span>${[...all].reverse().map((x) => html`<a class="bchip ${x.id === bid ? 'active' : ''} ${x.status}" href="#/artifact/${t.id}/${x.pdf || x.log}">${icon(x.status === 'ok' ? 'check' : 'x')}#${bnum(x)}</a>`)}</div>` : ''}
  </div>`;
}

function paperCard(t, s) {
  if (!t) return '';
  const arts = artsOf(t.id);
  const drafts = arts.filter((a) => isDraft(a) && !a.build).slice(0, 4);
  const figs = arts.filter((a) => a.kind === 'image' && a.path);
  const links = linksOf(t.id);
  const issues = (t.issues || []).filter((i) => i.status === 'open');
  const plan = t.plan || [];
  const now = taskNow(s);
  const sdot = (st) => html`<span class="sdot tone-${pick(STATUS, st, 'queued').tone}"></span>`;
  return html`<article class="card work-card">
    <div class="wc-head">
      <div class="wc-title">
        <div class="th-badges">${statusBadge(t.status)}${s.pending_approvals ? html`<span class="badge tone-amber">${icon('bell')}${L('승인§개수')} ${s.pending_approvals}</span>` : ''}${(t.tags || []).map((g) => html`<span class="badge">#${g}</span>`)}</div>
        <h3><a href="#/task/${t.id}"><span class="th-id">${t.id}</span>${t.title}</a></h3>
        ${now.text ? html`<div class="wc-now"><span class="lbl">${now.label}</span>${now.text}</div>` : ''}
        <div class="wc-meta">${agentLabel(t)} · ${L('업데이트')} ${ago(t.updated_at)}</div>
      </div>
      <div class="wc-prog"><div class="row"><span>${plan.length ? `${L('작성 계획')} ${plan.filter((p) => p.done).length}/${plan.length}` : L('진행률')}</span><b>${t.progress || 0}%</b></div>${progressBar(t, true)}</div>
    </div>
    <div class="wc-body">
      <div>
        <div class="wc-label"><span>${L('원고 구성 · 작성 계획')}</span></div>
        ${planList(t)}
      </div>
      <div>
        <div class="wc-label"><span>${L('컴파일된 PDF')}</span>${t.builds?.length ? html`<a href="#/task/${t.id}/builds">${L('빌드 기록')} ${t.builds.length}</a>` : ''}</div>
        ${buildBox(t)}
        <div class="wc-label"><span>${L('원고 파일')}</span>${drafts.length ? html`<a href="#/task/${t.id}/artifacts">${L('전체 결과물')}</a>` : ''}</div>
        ${drafts.length ? drafts.map((a) => html`<a class="mini-row" href="#/artifact/${t.id}/${a.id}">${icon(pick(ART_KIND, a.kind, 'file').icon)}<span class="t">${a.title}</span>${a.kind === 'latex' ? html`<span class="badge tone-teal">${L('문서 보기')}</span>` : ''}<span class="muted nowrap">${ago(a.created_at)}</span></a>`) : html`<div class="muted mini-empty">${L('아직 원고가 없습니다')}</div>`}
        <div class="wc-label"><span>${L('그림')} ${figs.length}</span></div>
        ${figs.length ? html`<div class="thumbs">${figs.slice(0, 8).map((a) => html`<a class="thumb-sm" href="#/artifact/${t.id}/${a.id}" title="${a.title}"><img src="${artifactUrl(t.id, a)}" alt="${a.title}" loading="lazy"></a>`)}</div>` : html`<div class="muted mini-empty">${L('아직 그림이 없습니다')}</div>`}
        <div class="wc-label"><span>${L('연결된 데이터 분석')}</span><a href="#/new?type=analysis&related=${t.id}">${icon('plus')} ${L('이 논문용 분석 지시')}</a></div>
        ${links.length ? html`<div>${links.map((l) => html`<a class="link-chip" href="#/task/${l.id}" title="${pick(STATUS, l.status, 'queued').label}">${sdot(l.status)}${l.id} ${l.title}</a>`)}</div>` : html`<div class="muted mini-empty">${L('연결된 분석이 없습니다')}</div>`}
        ${issues.length ? html`<div class="wc-label"><span>${L('열린 문제')} ${issues.length}</span></div>${issues.slice(0, 3).map((i) => html`<a class="mini-row" href="#/task/${t.id}/issues"><span class="sev-dot sev-${i.severity}"></span><span class="t">${i.title}</span></a>`)}` : ''}
      </div>
    </div>
  </article>`;
}

async function viewPapers({ q }) {
  const idx = state.index;
  const papers = idx.tasks.filter((t) => t.type === 'paper');
  const ids = new Set(papers.map((t) => t.id));
  const active = papers.filter((t) => ACTIVE.includes(t.status));
  const finished = papers.filter((t) => !ACTIVE.includes(t.status));
  await Promise.all(active.map((t) => loadTask(t.id)));
  const arts = idx.artifacts.filter((a) => ids.has(a.task_id));
  const g = PAPER_GROUPS[q.kind] ? q.kind : 'all';
  const shown = arts.filter(PAPER_GROUPS[g].test);
  return html`
    ${sectionHead(L('논문 작성'), L('논문 원고 작성 과정과 원고 · 그림 · 표 등 결과물'), '#/new?type=paper')}
    ${sectionStats(papers, arts)}
    ${sectionAttention(ids)}
    <div class="dash-grid">
      <div>
        <h2 class="section-title">${L('작성 중인 논문')} <span class="muted">${active.length}</span></h2>
        ${active.length ? active.map((s) => paperCard(state.tasks.get(s.id), s)) : html`<div class="card empty">${icon('book')}<p>${L('작성 중인 논문이 없습니다')}</p><p><a href="#/new?type=paper">${L('논문 작성 지시하기')}</a></p></div>`}
        ${finished.length ? html`<h2 class="section-title" style="margin-top:24px">${L('완료된 논문 작업')} <span class="muted">${finished.length}</span></h2><div class="task-grid">${finished.map((t) => taskCard(t, { thumbs: true }))}</div>` : ''}
        <h2 class="section-title" style="margin-top:24px">${L('논문 결과물')} <span class="muted">${arts.length}</span></h2>
        <div class="chips" style="margin-bottom:12px">${Object.entries(PAPER_GROUPS).map(([k, v]) => html`<a class="chip ${g === k ? 'active' : ''}" href="#/papers${k === 'all' ? '' : '?kind=' + k}">${v.label} <span class="n">${arts.filter(v.test).length}</span></a>`)}</div>
        ${shown.length ? html`<div class="art-grid">${shown.map(artCard)}</div>` : html`<div class="card empty inline" style="padding:16px 18px">${L('해당하는 결과물이 없습니다')}</div>`}
      </div>
      ${sectionFeed(ids)}
    </div>`;
}

const ANALYSIS_FILTERS = translated({
  all: { label: '전체', test: () => true },
  analysis: { label: '데이터 분석', test: (t) => t.type === 'analysis' },
  visualization: { label: '시각화', test: (t) => t.type === 'visualization' },
  paper: { label: '논문용', test: (t) => linksOf(t.id).some((x) => x.type === 'paper') },
  active: { label: '활성', test: (t) => ACTIVE.includes(t.status) },
  done: { label: '완료', test: (t) => t.status === 'done' },
});

function viewAnalysis({ q }) {
  const idx = state.index;
  const all = idx.tasks.filter((t) => ANALYSIS_TYPES.includes(t.type));
  const allIds = new Set(all.map((t) => t.id));
  const f = ANALYSIS_FILTERS[q.f] ? q.f : 'all';
  const tasks = all.filter(ANALYSIS_FILTERS[f].test)
    .sort((a, b) => Number(!ACTIVE.includes(a.status)) - Number(!ACTIVE.includes(b.status)));
  const ids = new Set(tasks.map((t) => t.id));
  const arts = idx.artifacts.filter((a) => ids.has(a.task_id));
  const figs = arts.filter((a) => FIGURE_KINDS.includes(a.kind));
  const others = arts.filter((a) => !FIGURE_KINDS.includes(a.kind));
  return html`
    ${sectionHead(L('데이터 분석'), L('데이터 · 그림 분석, 데이터 가시화, 논문에 쓰일 분석의 진행 과정과 결과물'), '#/new?type=analysis')}
    ${sectionStats(all, idx.artifacts.filter((a) => allIds.has(a.task_id)))}
    ${sectionAttention(allIds)}
    <div class="chips" style="margin-bottom:16px">${Object.entries(ANALYSIS_FILTERS).map(([k, v]) => html`<a class="chip ${f === k ? 'active' : ''}" href="#/analysis${k === 'all' ? '' : '?f=' + k}">${v.label} <span class="n">${all.filter(v.test).length}</span></a>`)}</div>
    <div class="dash-grid">
      <div>
        <h2 class="section-title">${L('분석 작업')} <span class="muted">${tasks.length}</span></h2>
        ${tasks.length ? html`<div class="task-grid">${tasks.map((t) => taskCard(t, { thumbs: true, links: true }))}</div>` : html`<div class="card empty">${icon('bar')}<p>${L('해당하는 분석 작업이 없습니다')}</p><p><a href="#/new?type=analysis">${L('데이터 분석 지시하기')}</a></p></div>`}
        <h2 class="section-title" style="margin-top:24px">${L('그림 · 시각화')} <span class="muted">${figs.length}</span></h2>
        ${figs.length ? html`<div class="art-grid gallery">${figs.map(artCard)}</div>` : html`<div class="card empty inline" style="padding:16px 18px">${L('아직 그림이 없습니다')}</div>`}
        <h2 class="section-title" style="margin-top:24px">${L('데이터 · 노트북 · 리포트')} <span class="muted">${others.length}</span></h2>
        ${others.length ? html`<div class="card">${others.map((a) => artRow(a.task_id, a, { showTask: true }))}</div>` : html`<div class="card empty inline" style="padding:16px 18px">${L('아직 결과물이 없습니다')}</div>`}
      </div>
      ${sectionFeed(ids)}
    </div>`;
}

// ---------------------------------------------------------------- 브레인스토밍
// 아이디어 노트(문서 결과물)를 읽고, AI 가 올린 선택지(--choices "I1=…")에서 이어갈 아이디어를 고르고,
// 의견을 보내고, 고른 아이디어로 새 작업을 지시하는 곳
const NOTE_KINDS = ['markdown', 'text', 'html', 'pdf'];

function bsListItem(t, sel) {
  const now = taskNow(t);
  return html`<a class="review-item ${t.id === sel ? 'active' : ''}" href="#/brainstorm/${t.id}">
    <div class="title">${t.title}</div>
    <div class="meta">${statusBadge(t.status)}<span>${t.id}</span>${ago(t.updated_at)}
      ${t.pending_approvals ? html`<span class="flag amber">${icon('bell')}${L('승인§개수')} ${t.pending_approvals}</span>` : ''}</div>
    ${now.text ? html`<div class="meta bs-now">${now.text}</div>` : ''}
  </a>`;
}

/** 지금까지 고른 아이디어 (결정된 선택 요청, 최근 것부터) */
function bsPicked(t) {
  const rounds = t.approvals.filter((a) => a.status === 'approved' && a.choices?.length && a.selected?.length).reverse();
  if (!rounds.length) return '';
  return html`<section class="card">
    <div class="card-head"><h2>${icon('check-circle')} ${L('고른 아이디어')}</h2></div>
    <div class="card-body bs-picked">${rounds.map((a) => {
      const byId = new Map(a.choices.map((c) => [c.id, c]));
      return html`<div class="bs-round">
        <div class="bs-round-head"><span>${a.title}</span><span class="muted">${ago(a.decided_at)}</span></div>
        <ul>${a.selected.map((id) => html`<li><span class="fid">${id}</span><span>${byId.get(id)?.label || id}</span></li>`)}</ul>
        ${a.comment ? html`<div class="bs-comment">${icon('message')}<span>${a.comment}</span></div>` : ''}
      </div>`;
    })}</div>
  </section>`;
}

async function viewBrainstorm({ rest }) {
  const idx = state.index;
  const head = sectionHead(L('브레인스토밍'), L('AI 와 함께 아이디어 · 연구 방향 · 실험 설계를 정리하고, 고른 아이디어를 실제 작업으로 이어 갑니다'), '#/new?type=brainstorm');
  const all = idx.tasks.filter((t) => t.type === 'brainstorm')
    .sort((a, b) => Number(!ACTIVE.includes(a.status)) - Number(!ACTIVE.includes(b.status)) || tsNum(b.updated_at) - tsNum(a.updated_at));
  if (!all.length) {
    return html`${head}<div class="card empty">${icon('bulb')}<p>${L('아직 브레인스토밍이 없습니다')}</p>
      <p class="muted">${L('주제와 조건을 적어 지시하면, AI 가 아이디어를 정리해 올리고 이어갈 아이디어를 웹에서 고를 수 있게 합니다.')}</p>
      <p><a href="#/new?type=brainstorm">${L('브레인스토밍 시작하기')}</a></p></div>`;
  }
  const [tid, fid] = rest;
  const sel = all.find((t) => t.id === tid) || all[0];
  const t = await loadTask(sel.id);
  const notes = t.artifacts.filter((a) => NOTE_KINDS.includes(a.kind)).reverse();
  const note = notes.find((a) => a.id === fid) || notes[0];
  const pending = t.approvals.filter((a) => a.status === 'pending');
  const links = linksOf(t.id);
  const figs = t.artifacts.filter((a) => a.kind === 'image' && a.path);
  const now = taskNow(sel);
  const sdot = (st) => html`<span class="sdot tone-${pick(STATUS, st, 'queued').tone}"></span>`;
  const allIds = new Set(all.map((x) => x.id));
  return html`${head}
    ${sectionStats(all, idx.artifacts.filter((a) => allIds.has(a.task_id)))}
    <div class="review-layout">
      <aside class="card review-list">${all.map((x) => bsListItem(x, sel.id))}</aside>
      <div style="min-width:0;display:flex;flex-direction:column;gap:16px">
        <article class="card work-card" style="margin-bottom:0">
          <div class="wc-head">
            <div class="wc-title">
              <div class="th-badges">${statusBadge(t.status)}${(t.tags || []).map((g) => html`<span class="badge">#${g}</span>`)}</div>
              <h3><a href="#/task/${t.id}"><span class="th-id">${t.id}</span>${t.title}</a></h3>
              ${now.text ? html`<div class="wc-now"><span class="lbl">${now.label}</span>${now.text}</div>` : ''}
              <div class="wc-meta">${agentLabel(t)} · ${L('업데이트')} ${ago(t.updated_at)}</div>
            </div>
            <div class="wc-prog"><div class="row"><span>${L('진행률')}</span><b>${t.progress || 0}%</b></div>${progressBar(t, true)}</div>
          </div>
          <div class="wc-body">
            <div>
              <div class="wc-label"><span>${L('주제 · 조건')}</span><a href="#/task/${t.id}">${L('작업 화면')}</a></div>
              ${t.instruction ? html`<div class="markdown-body compact">${raw(renderMarkdown(t.instruction))}</div>` : html`<div class="muted mini-empty">${L('지시 내용이 기록되지 않았습니다')}</div>`}
            </div>
            <div>
              <div class="wc-label"><span>${L('진행 계획')}</span></div>
              ${planList(t)}
              <div class="wc-label"><span>${L('연결된 작업')}</span><a href="#/new?related=${t.id}">${icon('plus')} ${L('이 아이디어로 작업 지시')}</a></div>
              ${links.length ? html`<div>${links.map((l) => html`<a class="link-chip" href="#/task/${l.id}" title="${pick(STATUS, l.status, 'queued').label}">${sdot(l.status)}${l.id} ${l.title}</a>`)}</div>` : html`<div class="muted mini-empty">${L('고른 아이디어로 새 작업을 지시하면 여기에 연결됩니다')}</div>`}
            </div>
          </div>
        </article>
        ${pending.map((a) => approvalCard(t, a, { skip: note ? [note.id] : [] }))}
        ${bsPicked(t)}
        ${note ? html`<section class="card" style="overflow:hidden">
          ${notes.length > 1 ? html`<div class="chips bs-notes">${notes.map((a) => html`<a class="chip ${a === note ? 'active' : ''}" href="#/brainstorm/${t.id}/${a.id}">${a.title}</a>`)}</div>` : ''}
          ${viewerHead(t, note)}
          <div data-av="${t.id}/${note.id}"></div>
        </section>` : html`<div class="card empty inline" style="padding:16px 18px">${L('아직 아이디어 노트가 없습니다')}</div>`}
        ${figs.length ? html`<section class="card"><div class="card-head"><h2>${icon('image')} ${L('그림')} ${figs.length}</h2></div>
          <div class="card-body"><div class="thumbs">${figs.slice(0, 12).map((a) => html`<a class="thumb-sm" href="#/artifact/${t.id}/${a.id}" title="${a.title}"><img src="${artifactUrl(t.id, a)}" alt="${a.title}" loading="lazy"></a>`)}</div></div></section>` : ''}
        ${feedbackBox(t, L('아이디어 · 의견 보내기'))}
      </div>
    </div>`;
}

// ---------------------------------------------------------------- 문서 리뷰
// 논문 · 보고서 · 제안서(PDF · Word · LaTeX)를 AI 가 리뷰한 결과: 문서 요약, 점검 영역별 평가, 보완 · 수정 항목(R1…),
// 리뷰 리포트(PDF)와 원본 문서를 한 화면에서 본다. 기록은 devtrack review --findings (doc · checks · strengths)
const DOC_VIEW_KINDS = ['pdf', 'markdown', 'text', 'html', 'latex', 'docx'];

function drListItem(t, sel) {
  const rs = t.review;
  const kind = rs?.doc?.kind ? pick(DOC_KIND, rs.doc.kind, 'other').label : '';
  const v = rs?.verdict ? VERDICT[rs.verdict] : null;
  return html`<a class="review-item ${t.id === sel ? 'active' : ''}" href="#/docreview/${t.id}">
    <div class="title">${t.title}</div>
    <div class="meta">${statusBadge(t.status)}<span>${t.id}</span>${ago(t.updated_at)}
      ${t.pending_approvals ? html`<span class="flag amber">${icon('bell')}${L('승인§개수')} ${t.pending_approvals}</span>` : ''}</div>
    ${kind || v ? html`<div class="meta">${kind ? html`<span class="badge">${kind}</span>` : ''}${v ? html`<span class="badge tone-${v.tone}">${v.label}</span>` : ''}</div>` : ''}
    ${rs?.total ? html`<div class="meta">${rs.open ? sevChips(rs.by_severity) : html`<span class="badge tone-green">${L('모두 처리됨')}</span>`}</div>` : ''}
  </a>`;
}

function docMeta(t, doc) {
  const bits = [];
  if (doc.kind) bits.push(html`<span class="badge tone-orange">${pick(DOC_KIND, doc.kind, 'other').label}</span>`);
  if (doc.authors) bits.push(html`<span>${icon('user')} ${doc.authors}</span>`);
  if (doc.pages) bits.push(html`<span>${L('{n}쪽', { n: doc.pages })}</span>`);
  if (doc.words) bits.push(html`<span>${L('{n}단어', { n: doc.words })}</span>`);
  if (doc.format) bits.push(html`<span>${doc.format}</span>`);
  const src = doc.source && t.artifacts.find((a) => a.id === doc.source);
  if (src) bits.push(html`<a href="#/docreview/${t.id}/${src.id}">${icon('file-text')} ${L('원본 보기')}</a>`);
  return bits.length ? html`<div class="pr-meta">${bits}</div>` : '';
}

function checkTable(checks) {
  return html`<ul class="dr-checks">${checks.map((c) => {
    const r = pick(CHECK_RATING, c.rating, 'na');
    return html`<li><span class="dr-area">${c.area}</span><span class="badge tone-${r.tone}">${icon(r.icon)}${r.label}</span>${c.note ? html`<span class="dr-note">${c.note}</span>` : ''}</li>`;
  })}</ul>`;
}

function docFindingItem(f) {
  const loc = f.location || (f.file ? `${f.file}${f.line ? `:${f.line}` : ''}` : '');
  return html`<li class="finding s-${f.status}">
    <span class="fid">${f.id}</span>
    <div class="f-body">
      <div class="f-head">${sevBadge(f.severity)}<span class="f-title">${f.title}</span>${f.status !== 'open' ? badge(FINDING_STATUS, f.status) : ''}</div>
      ${f.plain ? html`<div class="f-plain">${f.plain}</div>` : ''}
      ${f.quote || f.suggestion ? html`<div class="dr-edit">
        ${f.quote ? html`<div class="dr-quote"><span class="lbl">${L('원문')}</span><span class="txt">${f.quote}</span></div>` : ''}
        ${f.suggestion ? html`<div class="dr-sugg"><span class="lbl">${L('수정 제안')}</span><span class="txt">${f.suggestion}</span></div>` : ''}
      </div>` : ''}
      ${loc || f.category || f.confidence ? html`<div class="f-meta">${loc ? html`<span>${icon('file-text')} ${loc}</span>` : ''}${f.category ? html`<span>${f.category}</span>` : ''}${f.confidence ? html`<span>${L('확신도')} ${CONFIDENCE[f.confidence] || f.confidence}</span>` : ''}</div>` : ''}
    </div>
  </li>`;
}

function docReviewPanel(t) {
  const rv = t.review;
  if (!rv) return html`<div class="card empty inline" style="padding:16px 18px">${L('아직 리뷰 결과가 없습니다 — AI 가 문서를 읽고 요약 · 점검 결과를 올리면 여기에 표시됩니다')}</div>`;
  const doc = rv.doc || {};
  const all = rv.findings || [];
  const open = all.filter((f) => f.status === 'open');
  const by = {};
  open.forEach((f) => { by[f.severity] = (by[f.severity] || 0) + 1; });
  const v = rv.verdict ? pick(VERDICT, rv.verdict) : null;
  const main = all.filter((f) => f.severity !== 'nit');
  const nits = all.filter((f) => f.severity === 'nit');
  const nitsOpen = nits.filter((f) => f.status === 'open').length;
  return html`<section class="card review-sum">
    <div class="card-head"><h2>${icon('file-search')} ${L('리뷰 결과 한눈에 보기')}</h2><span class="muted" style="font-size:12.5px">${ago(rv.updated_at)}</span></div>
    <div class="card-body">
      ${doc.title ? html`<div class="pr-title">${doc.title}</div>` : ''}
      ${docMeta(t, doc)}
      ${doc.purpose || doc.audience ? html`<dl class="kv dr-purpose">
        ${doc.purpose ? html`<dt>${L('문서의 목적')}</dt><dd>${doc.purpose}</dd>` : ''}
        ${doc.audience ? html`<dt>${L('읽는 사람')}</dt><dd>${doc.audience}</dd>` : ''}
      </dl>` : ''}
      ${v || rv.summary ? html`<div class="rv-verdict">
        ${v ? html`<div class="rv-v"><span class="badge tone-${v.tone}">${icon(v.icon)} ${L('리뷰 의견')}: ${v.label}</span></div>` : ''}
        ${rv.summary ? html`<div class="markdown-body compact">${raw(renderMarkdown(rv.summary))}</div>` : ''}
      </div>` : ''}
      ${doc.synopsis ? html`<div class="dr-block"><div class="wc-label"><span>${L('문서 요약')}</span></div><div class="markdown-body compact">${raw(renderMarkdown(doc.synopsis))}</div></div>` : ''}
      ${rv.checks?.length ? html`<div class="dr-block"><div class="wc-label"><span>${L('점검 영역')}</span></div>${checkTable(rv.checks)}</div>` : ''}
      ${rv.strengths?.length ? html`<div class="dr-block"><div class="wc-label"><span>${L('잘된 점')}</span></div>
        <ul class="dr-strengths">${rv.strengths.map((x) => html`<li>${icon('check')}<span>${x}</span></li>`)}</ul></div>` : ''}
      <div class="dr-block">
        <div class="wc-label"><span>${L('보완 · 수정할 부분')}</span></div>
        <div class="sev-row"><span class="lbl">${L('남은 항목 {n}개', { n: open.length })}</span>${sevChips(by, { empty: all.length ? html`<span class="badge tone-green">${icon('check')} ${L('모두 처리됨')}</span>` : '' })}</div>
        ${main.length ? html`<ul class="finding-list">${main.map(docFindingItem)}</ul>` : ''}
        ${nits.length ? html`<details class="dr-nits" data-key="dr-nits:${t.id}" ${main.length ? '' : raw('open')}>
          <summary>${icon('chev', 'chev')} ${L('사소한 항목 (문장 · 맞춤법 등) {n}개', { n: nits.length })}${nitsOpen !== nits.length ? html` <span class="muted">· ${L('남은 항목 {n}개', { n: nitsOpen })}</span>` : ''}</summary>
          <ul class="finding-list">${nits.map(docFindingItem)}</ul>
        </details>` : ''}
        ${all.length ? '' : html`<div class="muted mini-empty">${L('아직 검토 항목이 없습니다')}</div>`}
      </div>
    </div>
  </section>`;
}

/** 리포트(PDF 먼저) → 그 밖의 결과 문서 → 검토할 문서(원본) 순서 */
function drDocs(t) {
  const docs = t.artifacts.filter((a) => DOC_VIEW_KINDS.includes(a.kind) && !isBuildLog(a) && !a.figure);
  const rank = (a) => (a.category === 'review' ? (a.kind === 'pdf' ? 0 : 1) : a.category === 'source' ? 3 : 2);
  return docs.map((a, i) => [a, i]).sort((x, y) => rank(x[0]) - rank(y[0]) || y[1] - x[1]).map(([a]) => a);
}

async function viewDocReview({ rest }) {
  const idx = state.index;
  const head = sectionHead(L('문서 리뷰'), L('논문 · 보고서 · 제안서(PDF · Word · LaTeX)를 AI 가 읽고 요약 · 보완할 점 · 논리 · 문장을 점검한 리뷰'), '#/new?type=docreview');
  const all = idx.tasks.filter((t) => t.type === 'docreview')
    .sort((a, b) => Number(!ACTIVE.includes(a.status)) - Number(!ACTIVE.includes(b.status)) || tsNum(b.updated_at) - tsNum(a.updated_at));
  if (!all.length) {
    return html`${head}<div class="card empty">${icon('file-search')}<p>${L('아직 문서 리뷰가 없습니다')}</p>
      <p class="muted">${L('검토할 문서의 경로와 목적을 적어 지시하면, AI 가 문서를 요약하고 보완 · 수정할 부분을 PDF 리포트로 올립니다.')}</p>
      <p><a href="#/new?type=docreview">${L('문서 리뷰 맡기기')}</a></p></div>`;
  }
  const [tid, fid] = rest;
  const sel = all.find((t) => t.id === tid) || all[0];
  const t = await loadTask(sel.id);
  const docs = drDocs(t);
  const cur = docs.find((a) => a.id === fid) || docs[0];
  const sources = t.artifacts.filter((a) => a.category === 'source' && !a.figure);
  const figures = t.artifacts.filter((a) => a.figure);  // 심사에 붙은 원고 그림 — 한 줄로 묶어 보여 준다
  const pending = t.approvals.filter((a) => a.status === 'pending');
  const now = taskNow(sel);
  const allIds = new Set(all.map((x) => x.id));
  const tabLabel = (a) => (a.category === 'source' ? `${L('원본')} · ${a.title}` : a.title);
  return html`${head}
    ${sectionStats(all, idx.artifacts.filter((a) => allIds.has(a.task_id)))}
    <div class="review-layout">
      <aside class="card review-list">${all.map((x) => drListItem(x, sel.id))}</aside>
      <div style="min-width:0;display:flex;flex-direction:column;gap:16px">
        <article class="card work-card" style="margin-bottom:0">
          <div class="wc-head">
            <div class="wc-title">
              <div class="th-badges">${statusBadge(t.status)}${(t.tags || []).map((g) => html`<span class="badge">#${g}</span>`)}</div>
              <h3><a href="#/task/${t.id}"><span class="th-id">${t.id}</span>${t.title}</a></h3>
              ${now.text ? html`<div class="wc-now"><span class="lbl">${now.label}</span>${now.text}</div>` : ''}
              <div class="wc-meta">${agentLabel(t)} · ${L('업데이트')} ${ago(t.updated_at)}</div>
            </div>
            <div class="wc-prog"><div class="row"><span>${L('진행률')}</span><b>${t.progress || 0}%</b></div>${progressBar(t, true)}</div>
          </div>
          <div class="wc-body">
            <div>
              <div class="wc-label"><span>${L('검토 요청')}</span><a href="#/task/${t.id}">${L('작업 화면')}</a></div>
              ${t.instruction ? html`<div class="markdown-body compact">${raw(renderMarkdown(t.instruction))}</div>` : html`<div class="muted mini-empty">${L('지시 내용이 기록되지 않았습니다')}</div>`}
            </div>
            <div>
              <div class="wc-label"><span>${L('진행 계획')}</span></div>
              ${planList(t)}
              <div class="wc-label"><span>${L('검토할 문서')}</span></div>
              ${sources.length ? sources.map((a) => html`<a class="mini-row" href="#/docreview/${t.id}/${a.id}">${icon(pick(ART_KIND, a.kind, 'file').icon)}<span class="t">${a.title}</span><span class="badge">${pick(ART_KIND, a.kind, 'file').label}</span>${a.version > 1 ? html`<span class="badge tone-accent">v${a.version}</span>` : ''}</a>`)
                : html`<div class="muted mini-empty">${L('AI 가 원본 문서를 올리면 여기에 표시됩니다')}</div>`}
              ${figures.length ? html`<a class="mini-row" href="#/task/${t.id}/artifacts">${icon('image')}<span class="t">${L('원고 그림 {n}개', { n: figures.length })}</span>${figures.some((a) => a.version > 1) ? html`<span class="badge tone-accent">${L('바뀐 적 있는 그림 {n}', { n: figures.filter((a) => a.version > 1).length })}</span>` : ''}</a>` : ''}
            </div>
          </div>
        </article>
        ${pending.map((a) => approvalCard(t, a, { skip: cur ? [cur.id] : [] }))}
        ${docReviewPanel(t)}
        ${cur ? html`<section class="card" style="overflow:hidden">
          ${docs.length > 1 ? html`<div class="chips bs-notes">${docs.map((a) => html`<a class="chip ${a === cur ? 'active' : ''}" href="#/docreview/${t.id}/${a.id}">${tabLabel(a)}</a>`)}</div>` : ''}
          ${viewerHead(t, cur)}
          <div data-av="${t.id}/${cur.id}"></div>
        </section>` : html`<div class="card empty inline" style="padding:16px 18px">${L('아직 리뷰 리포트나 문서가 없습니다')}</div>`}
        ${feedbackBox(t, L('리뷰에 대한 의견 보내기'))}
      </div>
    </div>`;
}

// ---------------------------------------------------------------- 코드 개발
const CODE_ART_KINDS = ['code', 'diff'];
const CODE_FILTERS = translated({
  all: { label: '전체', test: () => true },
  active: { label: '활성', test: (t) => ACTIVE.includes(t.status) },
  approval: { label: '승인 대기', test: (t) => t.pending_approvals > 0 },
  done: { label: '완료', test: (t) => t.status === 'done' },
});

function viewCode({ q }) {
  const idx = state.index;
  const all = idx.tasks.filter((t) => t.type === 'code');
  const allIds = new Set(all.map((t) => t.id));
  const f = CODE_FILTERS[q.f] ? q.f : 'all';
  const tasks = all.filter(CODE_FILTERS[f].test)
    .sort((a, b) => Number(!ACTIVE.includes(a.status)) - Number(!ACTIVE.includes(b.status)));
  const ids = new Set(tasks.map((t) => t.id));
  const arts = idx.artifacts.filter((a) => ids.has(a.task_id));
  const changes = arts.filter((a) => CODE_ART_KINDS.includes(a.kind));
  const others = arts.filter((a) => !CODE_ART_KINDS.includes(a.kind));
  const commits = idx.activity.filter((e) => e.kind === 'commit' && ids.has(e.task_id));
  const none = (msg) => html`<div class="card empty inline" style="padding:16px 18px">${msg}</div>`;
  return html`
    ${sectionHead(L('코드 개발'), L('코드 작성 · 수정 작업의 진행 과정, 커밋 승인과 반영된 커밋, 코드 · 변경사항'), '#/new?type=code')}
    ${sectionStats(all, idx.artifacts.filter((a) => allIds.has(a.task_id)))}
    ${sectionAttention(allIds)}
    <div class="chips" style="margin-bottom:16px">${Object.entries(CODE_FILTERS).map(([k, v]) => html`<a class="chip ${f === k ? 'active' : ''}" href="#/code${k === 'all' ? '' : '?f=' + k}">${v.label} <span class="n">${all.filter(v.test).length}</span></a>`)}</div>
    <div class="dash-grid">
      <div>
        <h2 class="section-title">${L('코드 작업')} <span class="muted">${tasks.length}</span></h2>
        ${tasks.length ? html`<div class="task-grid">${tasks.map((t) => taskCard(t, { repo: true }))}</div>` : html`<div class="card empty">${icon('code')}<p>${L('해당하는 코드 작업이 없습니다')}</p><p><a href="#/new?type=code">${L('코드 개발 지시하기')}</a></p></div>`}
        <h2 class="section-title" style="margin-top:24px">${L('반영된 커밋')} <span class="muted">${commits.length}</span></h2>
        ${commits.length ? html`<section class="card"><div class="card-body"><ul class="timeline">${commits.map((e) => timelineItem(e, { taskLink: true }))}</ul></div></section>` : none(L('아직 반영된 커밋이 없습니다 — 커밋 승인 요청을 승인하면 여기에 표시됩니다'))}
        <h2 class="section-title" style="margin-top:24px">${L('코드 · 변경사항')} <span class="muted">${changes.length}</span></h2>
        ${changes.length ? html`<div class="card">${changes.map((a) => artRow(a.task_id, a, { showTask: true }))}</div>` : none(L('아직 코드 · 변경사항 결과물이 없습니다'))}
        ${others.length ? html`<h2 class="section-title" style="margin-top:24px">${L('리포트 · 기타 결과물')} <span class="muted">${others.length}</span></h2>
        <div class="card">${others.map((a) => artRow(a.task_id, a, { showTask: true }))}</div>` : ''}
      </div>
      ${sectionFeed(ids)}
    </div>`;
}

// ====================================================================== 화면: 결과물
const ART_GROUPS = translated({
  all: { label: '전체', test: () => true },
  doc: { label: '문서', test: (a) => ['markdown', 'pdf', 'text', 'latex', 'docx'].includes(a.kind) },
  code: { label: '코드 · 변경사항', test: (a) => ['code', 'diff'].includes(a.kind) },
  figure: { label: '그림 · 시각화', test: (a) => ['image', 'html'].includes(a.kind) },
  data: { label: '데이터 · 노트북', test: (a) => ['csv', 'notebook'].includes(a.kind) },
  review: { label: '리뷰', test: (a) => a.category === 'review' },
  link: { label: '링크 · 기타', test: (a) => ['link', 'file'].includes(a.kind) },
});

function viewArtifacts({ q }) {
  const all = state.index.artifacts;
  const g = ART_GROUPS[q.kind] ? q.kind : 'all';
  const items = all.filter(ART_GROUPS[g].test);
  return html`
    <div class="page-head"><div><h1>${L('결과물')}</h1><p class="sub">${L('코드 변경, 리포트, 그림, 데이터 등 에이전트가 만든 모든 결과물')}</p></div></div>
    <div class="filter-bar"><div class="chips">${Object.entries(ART_GROUPS).map(([k, v]) => html`<a class="chip ${g === k ? 'active' : ''}" href="#/artifacts${k === 'all' ? '' : '?kind=' + k}">${v.label} <span class="n">${all.filter(v.test).length}</span></a>`)}</div>${artTools()}</div>
    ${items.length ? artCollection(items) : html`<div class="card empty">${icon('package')}<p>${L('결과물이 없습니다')}</p></div>`}`;
}

async function viewArtifact({ rest }) {
  const [tid, fid] = rest;
  const t = await loadTask(tid);
  const art = t.artifacts.find((a) => a.id === fid);
  if (!art) return viewNotFound();
  const linked = t.approvals.filter((a) => (a.artifacts || []).includes(art.id));
  return html`
    <div class="crumbs"><a href="${sectionHref(t)}">${SECTION_LABEL[SECTION_OF_TYPE[t.type] || 'tasks']}</a><span>/</span><a href="#/task/${t.id}">${t.id}</a><span>/</span><a href="#/task/${t.id}/artifacts">${L('결과물')}</a><span>/</span><span class="mono">${art.id}</span></div>
    <section class="card" style="overflow:hidden">
      ${viewerHead(t, art)}
      ${art.build ? buildPanel(t, art.build) : ''}
      ${art.description ? html`<div class="card-body markdown-body compact" style="border-bottom:1px solid var(--border)">${raw(renderMarkdown(art.description))}</div>` : ''}
      <div data-av="${t.id}/${art.id}"></div>
    </section>
    ${linked.length ? html`<h2 class="section-title" style="margin-top:22px">${L('연결된 승인 요청')}</h2>${linked.map((a) => approvalCard(t, a, { skip: [art.id] }))}` : ''}
    <div class="art-feedback">${feedbackBox(t, L('결과물 수정 요청 · 추가 지시'), { artifact: art })}</div>`;
}

// ====================================================================== 화면: 활동 기록
const ACT_FILTERS = translated({
  all: { label: '전체', test: () => true },
  step: { label: '진행', test: (e) => ['step', 'success', 'status', 'info'].includes(e.kind) },
  issue: { label: '문제 · 오류', test: (e) => ['issue', 'error', 'warning'].includes(e.kind) },
  approval: { label: '승인 · 결정', test: (e) => ['approval', 'decision'].includes(e.kind) },
  commit: { label: '커밋', test: (e) => e.kind === 'commit' },
  artifact: { label: '결과물', test: (e) => e.kind === 'artifact' },
  user: { label: '사용자', test: (e) => e.author === 'user' },
});

function viewActivity({ q }) {
  const f = ACT_FILTERS[q.f] ? q.f : 'all';
  const items = state.index.activity.filter(ACT_FILTERS[f].test);
  const groups = [];
  const today = new Date().toDateString();
  const yest = new Date(Date.now() - 86400000).toDateString();
  for (const e of items) {
    const d = new Date(e.ts);
    const key = d.toDateString();
    const label = key === today ? L('오늘') : key === yest ? L('어제') : d.toLocaleDateString(locale(), { month: 'long', day: 'numeric', weekday: 'short' });
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, label, items: [] });
    groups[groups.length - 1].items.push(e);
  }
  return html`
    <div class="page-head"><div><h1>${L('활동 기록')}</h1><p class="sub">${L('모든 작업의 진행 기록 (최근 300건)')}</p></div></div>
    <div class="filter-bar"><div class="chips">${Object.entries(ACT_FILTERS).map(([k, v]) => html`<a class="chip ${f === k ? 'active' : ''}" href="#/activity${k === 'all' ? '' : '?f=' + k}">${v.label}</a>`)}</div></div>
    ${groups.length ? groups.map((g) => html`
      <h2 class="section-title" style="margin-top:18px">${g.label} <span class="muted">${g.items.length}</span></h2>
      <section class="card"><div class="card-body"><ul class="timeline">${g.items.map((e) => timelineItem(e, { taskLink: true }))}</ul></div></section>`)
    : html`<div class="card empty">${L('기록이 없습니다')}</div>`}`;
}

// ====================================================================== 화면: 새 작업 지시
function viewNewTask({ q = {} } = {}) {
  const type = TYPES[q.type] ? q.type : 'code';
  const tasks = [...state.index.tasks].sort((a, b) => Number(b.type === 'paper') - Number(a.type === 'paper'));
  const rel = parseTaskIds(q.related).ids.filter((id) => tasks.some((t) => t.id === id)); // ?related=T-0005,T-0008
  return html`
    <div class="crumbs"><a href="#/tasks">${L('작업')}</a><span>/</span><span>${L('새 작업 지시')}</span></div>
    <div class="page-head"><div><h1>${L('새 작업 지시')}</h1><p class="sub">${L('AI 에이전트에게 맡길 작업을 적어주세요. 에이전트는 진행 상황을 기록하고, 확인이 필요하면 알림으로 요청합니다.')}</p></div></div>
    ${state.mode !== 'live' ? html`<div class="alert alert-info">${icon('info')}<div class="alert-body"><b>${L('정적 모드')}</b><p>${L('등록을 누르면 담당 AI 세션에 붙여넣을 지시문이 만들어집니다.')}</p></div></div>` : ''}
    <form id="new-task-form" class="card card-pad" style="max-width:820px" autocomplete="off">
      <label class="field"><span>${L('작업 제목')} *</span><input type="text" name="title" required maxlength="200" placeholder="${L('예: 논문 4장 실험 결과 그림 정리 및 LaTeX 표 작성')}"></label>
      <div class="field-row">
        <label class="field"><span>${L('작업 유형')}</span><select name="type">${Object.entries(TYPES).map(([k, v]) => html`<option value="${k}" ${k === type ? raw('selected') : ''}>${v.label}</option>`)}</select></label>
        <label class="field"><span>${L('담당 AI')}</span><select name="agent">${agentOptions()}</select><small>${L('이 AI 만 이 작업을 가져갑니다')}</small></label>
        <label class="field"><span>${L('우선순위')}</span><select name="priority"><option value="normal">${L('보통')}</option><option value="high">${L('높음')}</option><option value="low">${L('낮음')}</option></select></label>
      </div>
      ${listenersSummary()}
      ${modelFields(LS.get('newAgent', 'Claude'))}
      <div class="field-row" data-type-only="docreview" ${type === 'docreview' ? '' : raw('hidden')}>
        <label class="field" style="flex:2"><span>${L('검토할 문서 경로')}</span><input type="text" name="doc_path" placeholder="~/Documents/proposal_v3.docx"><small>${L('PDF · Word(.docx) · LaTeX(.tex) — AI 가 일하는 컴퓨터의 경로')}</small></label>
        <label class="field"><span>${L('문서 종류')}</span><select name="doc_kind">${Object.entries(DOC_KIND).map(([k, v]) => html`<option value="${k}">${v.label}</option>`)}</select></label>
      </div>
      <label class="field" data-type-only="docreview" ${type === 'docreview' ? '' : raw('hidden')}><span>${L('문서의 목적 · 제출처')}</span><input type="text" name="doc_purpose" placeholder="${L('예: 학술지 투고 전 점검, 연구 과제 제안서 심사 대비, 연차 보고서 제출')}"><small>${L('목적에 맞게 잘 썼는지를 기준으로 리뷰합니다')}</small></label>
      <div class="field-row" data-type-hide="docreview" ${type === 'docreview' ? raw('hidden') : ''}>
        <label class="field"><span>${L('대상 저장소 경로')}</span><input type="text" name="repo" placeholder="/ccs/home/user/project"><small>${L('코드 작업일 때 결과를 커밋할 저장소')}</small></label>
        <label class="field"><span>${L('브랜치')}</span><input type="text" name="branch" placeholder="${L('(현재 브랜치)')}"></label>
      </div>
      ${reviewFields(type)}
      <div class="field rel-field"><span class="field-label">${L('관련 작업 (선택)')}</span>
        <div class="rel-pick">
          <select data-rel-add aria-label="${L('목록에서 관련 작업 추가')}"><option value="">${L('목록에서 고르기…')}</option>${tasks.map((t) => html`<option value="${t.id}" ${rel.includes(t.id) ? raw('disabled') : ''}>${t.id} · ${pick(TYPES, t.type).label} · ${t.title}</option>`)}</select>
          <input type="text" name="related" value="${rel.join(', ')}" placeholder="T-0001, T-0002" aria-label="${L('관련 작업 번호')}" spellcheck="false">
        </div>
        <div class="rel-chips" data-rel-chips ${rel.length ? '' : raw('hidden')}>${rel.map((id) => relChip(id))}</div>
        <small>${L('목록에서 여러 개를 차례로 고르거나 작업 번호를 직접 적으세요 (쉼표 · ; · 띄어쓰기로 구분, 3 = T-0003). 예: 논문에 쓸 데이터 분석이라면 그 논문 작업을 고르세요 — 논문 작성 화면에 연결되어 표시됩니다.')}</small>
      </div>
      <label class="field"><span>${L('지시 내용')}</span><textarea name="instruction" rows="9" placeholder="${L('무엇을, 어떤 기준으로 해야 하는지 구체적으로 적어주세요. (markdown 지원)\n\n- 목표:\n- 참고 자료:\n- 완료 기준:\n- 승인이 필요한 시점:')}"></textarea></label>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <a class="btn btn-ghost" href="#/tasks">${L('취소')}</a>
        <button class="btn btn-primary" type="submit">${icon('send')} ${state.mode === 'live' ? L('작업 지시 등록') : L('지시문 만들기')}</button>
      </div>
    </form>`;
}

const AGENTS = [['Claude', 'Claude'], ['Codex', 'Codex'], ['Gemini', 'Gemini'], ['누구나', '누구나 (먼저 시작한 AI)']];
const REVIEW_TYPES = ['code', 'paper']; // 리뷰 반복을 정할 수 있는 작업 유형 (논문은 저널 심사)

/** 코드 작업의 '리뷰 받기' 칸: 끝나면 다른 AI 에게 리뷰받고, 수정이 필요하면 고친 뒤 다시 리뷰 (최대 횟수까지) */
function reviewFields(type) {
  const last = LS.get('newReviewer', '');
  const same = !!last && String(LS.get('newAgent', 'Claude')).toLowerCase() === last.toLowerCase();
  const on = REVIEW_TYPES.includes(type);
  return html`<div class="review-fields" data-type-in="${REVIEW_TYPES.join(' ')}" data-review-fields ${on ? '' : raw('hidden')}>
    <div class="field-row">
      <label class="field"><span data-type-only="code" ${type === 'paper' ? raw('hidden') : ''}>${L('리뷰 받기 (선택)')}</span><span data-type-only="paper" ${type === 'paper' ? '' : raw('hidden')}>${L('심사 받기 (선택)')}</span><select name="review_by">
        <option value="">${L('안 받음')}</option>${AGENTS.filter(([v]) => !isUnassigned(v)).map(([v, label]) => html`<option value="${v}" ${v === last ? raw('selected') : ''}>${L(label)}</option>`)}
      </select></label>
      <label class="field"><span data-type-only="code" ${type === 'paper' ? raw('hidden') : ''}>${L('리뷰 최대 횟수')}</span><span data-type-only="paper" ${type === 'paper' ? '' : raw('hidden')}>${L('심사 최대 횟수')}</span><select name="review_rounds">${[1, 2, 3, 4, 5].map((n) => html`<option value="${n}" ${n === 3 ? raw('selected') : ''}>${L('{n}번', { n })}</option>`)}</select></label>
    </div>
    <div data-review-model ${last ? '' : raw('hidden')}>${modelFields(last || 'Codex', '', '', { prefix: 'review_', label: type === 'paper' ? L('심사위원 모델 (선택)') : L('리뷰어 모델 (선택)') })}</div>
    <div class="field-row" data-type-only="paper" ${type === 'paper' ? '' : raw('hidden')}>
      <label class="field" style="flex:2"><span>${L('심사할 원고')}</span><input type="text" name="review_doc" placeholder="paper/main.tex · draft.docx" spellcheck="false"><small>${L('LaTeX 본문(.tex, 불러오는 파일까지) · Word · PDF · Markdown. 저장소 기준 상대 경로도 됩니다. 비워 두면 AI 가 심사를 요청할 때 정합니다.')}</small></label>
      <label class="field"><span>${L('투고처 (저널)')}</span><input type="text" name="review_venue" placeholder="${L('예: Journal of Fluid Mechanics')}"></label>
    </div>
    <small class="mf-note" data-type-only="code" ${type === 'paper' ? raw('hidden') : ''}>${L('개발이 끝나면 이 AI 가 변경된 코드(빌드 결과물 제외)를 리뷰합니다. 수정이 필요하면 담당 AI 가 고친 뒤 다시 리뷰받고, 통과하면 커밋 승인을 요청합니다. 두 AI 가 모두 켜져 있어야 저절로 이어집니다.')}</small>
    <small class="mf-note" data-type-only="paper" ${type === 'paper' ? '' : raw('hidden')}>${L('원고를 다 쓰면 이 AI 가 투고처의 심사위원 관점으로 심사합니다 (판정: 게재 가능 · 소폭 수정 · 대폭 수정 · 게재 불가). 수정이 필요하면 담당 AI 가 원고를 고치고 답변서(response to reviewers)를 써서 다시 심사받습니다. 두 AI 가 모두 켜져 있어야 저절로 이어집니다.')}</small>
    <div class="alert alert-info review-same" data-review-same ${same ? '' : raw('hidden')}>${icon('info')}<div class="alert-body"><p>${LH('같은 AI 가 리뷰합니다. 리뷰는 개발한 대화와 다른 <b>새 대화</b>에서 하므로, 실행기(<code>devtrack watch</code>)를 켜 두세요 — 작업마다 따로 대화를 엽니다.')}</p></div></div>
  </div>`;
}

/** 리뷰어가 담당 AI 와 같으면 '리뷰는 다른 대화에서' 안내를 보여 준다 */
function syncReviewSame(form) {
  const note = form.querySelector('[data-review-same]');
  if (!note) return;
  const a = form.elements.agent.value;
  note.hidden = !(form.elements.review_by.value && !isUnassigned(a) && a.toLowerCase() === form.elements.review_by.value.toLowerCase());
}

function syncReviewFields(form) {
  const box = form.querySelector('[data-review-fields]');
  if (!box) return;
  const reviewer = form.elements.review_by.value;
  const wrap = box.querySelector('[data-review-model]');
  wrap.hidden = !reviewer;
  if (reviewer) syncModelFields(wrap.querySelector('[data-model-fields]'), reviewer);
}

function agentOptions() {
  const last = LS.get('newAgent', 'Claude');
  return AGENTS.map(([v, label]) => html`<option value="${v}" ${v === last ? raw('selected') : ''}>${L(label)}</option>`);
}

/** 담당 AI 에 맞는 모델 목록 묶음: Claude 는 Claude 목록, Codex 는 Codex 목록, Gemini 는 직접 입력만, 누구나 · 그 밖에는 둘 다 */
function modelGroups(agent) {
  const a = String(agent ?? '').trim().toLowerCase();
  if (a === 'gemini') return [];
  if (a === 'claude') return [['Claude', MODELS.claude]];
  if (a === 'codex') return [['Codex', MODELS.codex]];
  return [['Claude', MODELS.claude], ['Codex', MODELS.codex]];
}

/** 추론 강도 선택지. keep: 이 모델이 받지 않는 지금 값도 남겨 둔다 (이미 정해 둔 값을 창을 열기만 해서 바꾸지 않게) */
function effortOptions(agent, model, effort, keep) {
  const list = effortsFor(agent, model);
  if (effort && !list.some(([k]) => k === effort)) {
    if (keep) list.push([effort, effortLabel(effort)]);
    else effort = '';
  }
  return html`<option value="">${L('지정 안 함')}</option>${list.map(([k, label]) => html`<option value="${k}" ${k === effort ? raw('selected') : ''}>${label}</option>`)}`;
}

/** 모델 · 추론 강도 칸 (새 작업 지시 · 모델 변경 창). 담당 AI 의 모델 목록에서 고르고, 목록에 없는 모델은 직접 입력.
 *  추론 강도는 고른 모델이 받는 값만 보여 준다 */
function modelFields(agent, model = '', effort = '', { keep = true, custom: forceCustom = false, prefix = '', label = '' } = {}) {
  const groups = modelGroups(agent);
  const listed = groups.some(([, list]) => list.some((m) => m.id === model));
  const custom = forceCustom || (!!model && !listed);
  const title = label || L('모델 (선택)');
  return html`<div class="model-fields" data-model-fields data-agent="${agent || ''}" ${prefix ? raw(`data-prefix="${prefix}"`) : ''}>
    <div class="field-row">
      <div class="field mf-model"><span class="field-label">${title}</span>
        <select name="${prefix}model_pick" aria-label="${title}">
          <option value="">${L('지정 안 함 — 어떤 모델이든')}</option>
          ${groups.map(([name, list]) => html`<optgroup label="${name}">${list.map((m) => html`<option value="${m.id}" ${!custom && m.id === model ? raw('selected') : ''}>${m.label}</option>`)}</optgroup>`)}
          <option value="__custom" ${custom ? raw('selected') : ''}>${L('직접 입력…')}</option>
        </select>
        <input type="text" name="${prefix}model_custom" value="${custom ? model : ''}" placeholder="${L('모델 이름 (예: gpt-6-sol)')}" aria-label="${L('모델 이름')}" spellcheck="false" ${custom ? '' : raw('hidden')}>
      </div>
      <label class="field"><span>${L('추론 강도')}</span><select name="${prefix}effort">${effortOptions(agent, model, effort, keep)}</select></label>
    </div>
    ${prefix ? '' : html`<small class="mf-note">${L('지정하면 이 작업은 그 모델로만 처리합니다. 실행기(devtrack watch)는 이 모델로 AI 를 띄우고, 다른 모델로 켜 둔 대화는 이 작업을 받지 않습니다.')}</small>`}
  </div>`;
}

/** 모델 칸 안의 입력 (prefix 가 붙은 칸도 같은 이름 규칙) */
const mfEl = (box, name) => box.querySelector(`[name="${box.dataset.prefix || ''}${name}"]`);

/** 모델 칸에서 고른 값 → { model, effort } (지정 안 함은 '') */
function readModelFields(box) {
  const pickEl = mfEl(box, 'model_pick');
  const model = pickEl.value === '__custom' ? mfEl(box, 'model_custom').value.trim() : pickEl.value;
  return { model: normalizeModel(model), effort: mfEl(box, 'effort').value };
}

/** 담당 AI 를 바꾸면 모델 목록 · 강도 목록을 그 AI 에 맞게 다시 그린다 (고른 모델은 유지 — 새 목록에 없으면 직접 입력 칸으로) */
function syncModelFields(box, agent) {
  const { model, effort } = readModelFields(box);
  const custom = mfEl(box, 'model_pick').value === '__custom';
  const prefix = box.dataset.prefix || '';
  const label = prefix ? box.querySelector('.field-label').textContent : '';
  box.outerHTML = String(modelFields(agent, model, effort, { keep: false, custom, prefix, label }));
}

/** 고른 모델이 받는 강도로 강도 목록을 바꾼다 (지금 값이 없으면 '지정 안 함') */
function syncEfforts(box) {
  const sel = mfEl(box, 'effort');
  const { model, effort } = readModelFields(box);
  sel.innerHTML = String(effortOptions(box.dataset.agent, model, effort, false));
}

function onModelPick(select) {
  const box = select.closest('[data-model-fields]');
  const input = mfEl(box, 'model_custom');
  input.hidden = select.value !== '__custom';
  syncEfforts(box);
  if (!input.hidden) input.focus();
}

const modelArgs = ({ model, effort }) => [model ? `--model ${shellQuote(model)}` : '', effort ? `--effort ${effort}` : ''].filter(Boolean).join(' ');
/** 정적 모드의 지시문에 넣는 '리뷰 / 심사' 줄 (AI 에게 가는 글이라 한국어 그대로) */
function reviewMsgLine(d) {
  const spec = d.review_model || d.review_effort ? ` (${specLabel(d.review_model, d.review_effort)})` : '';
  const what = d.type === 'paper'
    ? `${d.review_venue || '저널'} 심사위원 관점의 심사받기${d.review_doc ? ` (원고 ${d.review_doc})` : ''}` : '리뷰받기';
  return `${d.type === 'paper' ? '심사' : '리뷰'}: 끝나면 ${d.review_by}${spec} 에게 ${what}, 최대 ${d.review_rounds}번 (devtrack new 에 ${reviewArgs(d)})`;
}
const reviewArgs = (d) => (d.review_by ? [`--review-by ${shellQuote(d.review_by)}`, `--review-rounds ${d.review_rounds}`,
  d.review_model ? `--review-model ${shellQuote(d.review_model)}` : '', d.review_effort ? `--review-effort ${d.review_effort}` : '',
  d.review_doc ? `--review-doc ${shellQuote(d.review_doc)}` : '', d.review_venue ? `--review-venue ${shellQuote(d.review_venue)}` : ''].filter(Boolean).join(' ') : '');

/** 관련 작업 칸의 글 → 작업 ID 목록 (중복 제거) 과 작업 번호로 읽을 수 없는 조각.
 *  "T-0001, 2; t-3" → ids ['T-0001', 'T-0002', 'T-0003'] (store.normalize_task_id 와 같은 규칙) */
function parseTaskIds(text) {
  const ids = [];
  const bad = [];
  for (const part of String(text || '').split(/[\s,;]+/)) {
    if (!part) continue;
    const id = taskIdOf(part);
    if (!id) bad.push(part);
    else if (!ids.includes(id)) ids.push(id);
  }
  return { ids, bad };
}
function taskIdOf(token) {
  const m = /^(?:T-?)?(\d+)$/i.exec(String(token).trim());
  return m ? `T-${String(Number(m[1])).padStart(4, '0')}` : null;
}

/** 고른 관련 작업 하나 (없는 번호 · 읽을 수 없는 글은 빨갛게). × 로 뺀다 */
function relChip(token) {
  const id = taskIdOf(token);
  const t = id && state.index.tasks.find((x) => x.id === id);
  const x = html`<button type="button" class="rel-x" data-action="rel-remove" data-token="${token}" title="${L('빼기')}" aria-label="${L('빼기')}">${icon('x')}</button>`;
  if (!t) return html`<span class="link-chip rel-chip bad">${icon('alert')}<span class="rel-t">${L('{id}: 없는 작업', { id: id || token })}</span>${x}</span>`;
  return html`<span class="link-chip rel-chip"><span class="sdot tone-${pick(STATUS, t.status, 'queued').tone}"></span><span class="rel-t">${t.id} · ${pick(TYPES, t.type).label} · ${t.title}</span>${x}</span>`;
}

/** 관련 작업 칸이 바뀌면: 아래 표시를 다시 그리고, 목록에서 이미 고른 작업은 흐리게 */
function syncRelated(form) {
  const { ids, bad } = parseTaskIds(form.elements.related.value);
  const box = $('[data-rel-chips]', form);
  box.innerHTML = String(html`${ids.map((id) => relChip(id))}${bad.map((b) => relChip(b))}`);
  box.hidden = !ids.length && !bad.length;
  $$('[data-rel-add] option', form).forEach((o) => { o.disabled = !!o.value && ids.includes(o.value); });
}

/** 목록에서 고른 작업을 번호 칸 끝에 더한다 (여러 개를 차례로 고를 수 있게 목록은 다시 비운다) */
function addRelated(select) {
  const id = select.value;
  select.value = '';
  const input = select.form.elements.related;
  if (id && !parseTaskIds(input.value).ids.includes(id)) {
    const cur = input.value.trim().replace(/[\s,;]+$/, '');
    input.value = cur ? `${cur}, ${id}` : id;
  }
  syncRelated(select.form);
}

function removeRelated(btn) {
  const form = btn.closest('form');
  const input = form.elements.related;
  const target = taskIdOf(btn.dataset.token) || btn.dataset.token;
  input.value = input.value.split(/[\s,;]+/).filter((p) => p && (taskIdOf(p) || p) !== target).join(', ');
  syncRelated(form);
}

/** 작업 유형에 따라 보이는 칸: 문서 리뷰는 문서 경로 · 종류 · 목적, 그 밖에는 저장소 */
function syncTypeFields(form) {
  const type = form.elements.type.value;
  $$('[data-type-only]', form).forEach((el) => { el.hidden = el.dataset.typeOnly !== type; });
  $$('[data-type-hide]', form).forEach((el) => { el.hidden = el.dataset.typeHide === type; });
  $$('[data-type-in]', form).forEach((el) => { el.hidden = !el.dataset.typeIn.split(' ').includes(type); });
  const rmLabel = form.querySelector('[data-model-fields][data-prefix="review_"] .field-label');
  if (rmLabel) rmLabel.textContent = type === 'paper' ? L('심사위원 모델 (선택)') : L('리뷰어 모델 (선택)');
}

/** 문서 리뷰 칸 → 지시 내용 앞에 붙일 요약 (AI 에게 가는 글이라 한국어 그대로) */
function takeDocFields(data) {
  const { doc_path: path = '', doc_kind: kind = '', doc_purpose: purpose = '', ...rest } = data;
  if (rest.type !== 'docreview') return rest;
  const brief = [
    path.trim() ? `- 검토할 문서: ${path.trim()}` : '',
    kind ? `- 문서 종류: ${DOC_KIND[kind]?.labelKo || kind}` : '',
    purpose.trim() ? `- 문서의 목적 · 제출처: ${purpose.trim()}` : '',
  ].filter(Boolean).join('\n');
  return { ...rest, repo: '', branch: '', instruction: [brief, (rest.instruction || '').trim()].filter(Boolean).join('\n\n') };
}

async function submitNewTask(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const data = takeDocFields(Object.fromEntries(new FormData(form).entries()));
  data.title = (data.title || '').trim();
  if (!data.title) return;
  const { ids: relIds, bad: relBad } = parseTaskIds(data.related);
  const rels = relIds.map((id) => state.index.tasks.find((t) => t.id === id));
  const missing = [...relBad, ...relIds.filter((id, i) => !rels[i])];
  if (missing.length) {
    toast({ title: L('관련 작업을 확인해 주세요'), meta: L('찾을 수 없는 작업: {ids}', { ids: missing.join(', ') }), tone: 'red' });
    form.elements.related.focus();
    return;
  }
  data.related = relIds;
  Object.assign(data, readModelFields(form.querySelector('[data-model-fields]:not([data-prefix])')));
  const rv = REVIEW_TYPES.includes(data.type) && data.review_by ? readModelFields(form.querySelector('[data-model-fields][data-prefix="review_"]')) : null;
  for (const k of ['model_pick', 'model_custom', 'review_model_pick', 'review_model_custom', 'review_effort']) delete data[k];
  if (rv) Object.assign(data, { review_model: rv.model, review_effort: rv.effort, review_rounds: Number(data.review_rounds) || 3 });
  else { delete data.review_by; delete data.review_rounds; }
  if (!rv || data.type !== 'paper') { delete data.review_doc; delete data.review_venue; }
  else { data.review_doc = (data.review_doc || '').trim(); data.review_venue = (data.review_venue || '').trim(); }
  for (const [m, el] of [[data.model, 'model_custom'], [data.review_model, 'review_model_custom']]) {
    if (m && /\s/.test(m)) {
      toast({ title: L('모델 이름에 공백을 넣을 수 없습니다'), meta: m, tone: 'red' });
      form.elements[el].focus();
      return;
    }
  }
  LS.set('newAgent', data.agent); // 다음 지시에도 같은 AI 를 기본으로
  LS.set('newReviewer', data.review_by || '');
  const ai = aiName(data);
  if (state.mode === 'live') {
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      const r = await api('api/tasks', data);
      form.reset();
      form.elements.agent.value = data.agent;
      form.elements.type.value = data.type;
      syncTypeFields(form);
      syncRelated(form);
      syncModelFields(form.querySelector('[data-model-fields]:not([data-prefix])'), data.agent);
      form.elements.review_by.value = data.review_by || '';
      syncReviewFields(form);
      syncReviewSame(form);
      toast({ title: L('작업 지시를 등록했습니다'), meta: `${r.task.id} — ${isUnassigned(data.agent) ? L('먼저 시작한 AI 가 맡습니다') : L('{ai}에게 devtrack inbox 로 전달됩니다', { ai })}`, tone: 'green' });
      await poll({ force: true });
      location.hash = `#/task/${r.task.id}`;
    } catch (e) {
      toast({ title: L('등록 실패'), meta: e.message, tone: 'red' });
      btn.disabled = false;
    }
    return;
  }
  const typeLabel = TYPES[data.type]?.labelKo || data.type; // AI 에게 보내는 지시문은 한국어로
  const cmd = [
    'devtrack new', shellQuote(data.title), '-t', data.type, '--agent', shellQuote(data.agent),
    data.priority !== 'normal' ? `--priority ${data.priority}` : '',
    data.repo ? `--repo ${shellQuote(data.repo)}` : '', data.branch ? `--branch ${shellQuote(data.branch)}` : '',
    relIds.length ? `--related ${relIds.join(' ')}` : '',
    modelArgs(data),
    reviewArgs(data),
    data.instruction ? `-i ${shellQuote(data.instruction)}` : '',
  ].filter(Boolean).join(' ');
  const msg = [
    '[DevTrack 새 작업 지시]',
    `제목: ${data.title}`,
    `유형: ${typeLabel} (${data.type})`,
    `담당 AI: ${data.agent}`,
    data.repo ? `저장소: ${data.repo}${data.branch ? ` (브랜치 ${data.branch})` : ''}` : null,
    data.priority !== 'normal' ? `우선순위: ${data.priority === 'high' ? '높음' : '낮음'}` : null,
    rels.length ? `관련 작업: ${rels.map((t) => `${t.id} ${t.title}`).join(', ')} (devtrack new 에 --related ${relIds.join(' ')})` : null,
    data.model || data.effort ? `모델: ${specLabel(data.model, data.effort)} (devtrack new 에 ${modelArgs(data)})` : null,
    data.review_by ? reviewMsgLine(data) : null,
    '',
    data.instruction ? `지시 내용:\n${data.instruction}` : '',
    '',
    'devtrack 으로 작업을 등록하고, 진행 단계·문제점·결과물을 기록하면서 진행해 주세요. 커밋/푸시 전에는 반드시 승인 요청을 올려 주세요.',
  ].filter((x) => x !== null).filter((x, i, arr) => x !== '' || (arr[i - 1] !== '' && i > 0)).join('\n');
  openModal({
    title: L('{ai}에게 보낼 작업 지시', { ai }),
    body: html`<p>${L('정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 지시문을 {ai} 세션에 붙여넣으세요.', { ai })}</p>${ghIssueBlock({ title: data.title, body: msg, labels: [ghAgentLabel(data.agent)], ai })}${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 등록'), cmd)}`,
  });
}

// ====================================================================== 화면: 오류
function viewNotFound() {
  return html`<div class="card empty">${icon('alert')}<p>${L('페이지를 찾을 수 없습니다')}</p><p><a href="#/">${L('대시보드로 이동')}</a></p></div>`;
}
function viewError(e) {
  return html`<div class="alert alert-red">${icon('alert')}<div class="alert-body"><b>${L('화면을 그리는 중 오류가 발생했습니다')}</b><p>${e.message}</p></div></div>`;
}
function viewLoadError(e) {
  const file = location.protocol === 'file:';
  return html`<section class="card welcome">
    <h2>${icon('alert')} ${L('데이터를 불러올 수 없습니다')}</h2>
    <p class="muted"><code>data/index.json</code> — ${e.message}</p>
    <ol>
      ${file ? html`<li>${LH('<b>file:// 로 직접 연 경우 동작하지 않습니다.</b> 웹 서버로 열어주세요: <code>devtrack serve</code>')}</li>` : ''}
      <li>${LH('로컬: <code>devtrack serve</code> 실행 후 <code>http://localhost:8765</code> 접속')}</li>
      <li>${LH('정적 호스팅(웹 공개 폴더): <code>devtrack publish --to ~/www/devtrack</code> 로 웹과 데이터를 함께 배포했는지 확인')}</li>
      <li>${LH('데이터가 아직 없다면 <code>devtrack init</code> 또는 첫 작업을 <code>devtrack new</code> 로 생성')}</li>
    </ol>
  </section>`;
}

// ====================================================================== GitHub 이슈 우편함 (정적 웹 — devtrack mailbox)
// 정적 웹은 기록을 쓸 수 없어, 보내기 창의 메시지를 내용이 채워진 '새 이슈' 페이지로 연다. 사용자가 Submit 하면
// 이 컴퓨터의 devtrack(watch · 대기 모드)이 우편함 저장소를 읽어 기록한다 (devtrack/mailbox.py — 본문은 붙여 넣기 메시지 그대로).
const GH_URL_MAX = 7000; // GitHub 새 이슈 주소 길이 한도(약 8KB) 아래로 — 넘으면 본문은 클립보드로
const ghRepo = () => (state.mode !== 'live' && state.index?.mailbox?.github) || '';
const ghAgentLabel = (agent) => ({ claude: 'claude', codex: 'codex' })[String(agent || '').toLowerCase()];
function ghIssueUrl(repo, title, body, labels) {
  const q = new URLSearchParams({ title, body });
  if (labels) q.set('labels', labels);
  return `https://github.com/${repo}/issues/new?${q}`;
}
/** 정적 모드 보내기 창의 'GitHub 이슈로 보내기' (우편함을 정해 두었을 때만) */
function ghIssueBlock({ title, body, labels = [], ai }) {
  const repo = ghRepo();
  if (!repo) return '';
  const site = location.origin + location.pathname.replace(/index\.html$/, '');
  return html`<div class="gh-send">
    <button class="btn btn-primary" data-action="gh-issue" data-title="${title}" data-body="${`${body}\n\n<!-- devtrack-site: ${site} -->`}" data-labels="${labels.filter(Boolean).join(',')}">${icon('github')} ${L('GitHub 이슈로 보내기')}</button>
    <p class="hint">${LH('<b>{repo}</b> 에 내용이 채워진 새 이슈가 열립니다. 확인하고 <b>Submit</b> 하면 devtrack 이 1분 안에 받아 {ai}에게 넘깁니다 (devtrack watch 나 대기 모드가 켜져 있어야 함).', { repo, ai })}</p>
  </div>`;
}

// ====================================================================== 모달 / 복사
function copyBlock(label, text) {
  return html`<div class="copy-block"><div class="lbl">${label}</div><pre>${text}</pre><button class="btn btn-sm" data-action="copy" data-copy="${text}">${icon('copy')} ${L('복사')}</button></div>`;
}

function openModal({ title, body }) {
  closeModal();
  const root = $('#modal-root');
  root.innerHTML = html`<div class="modal-back" data-action="modal-close-back"><div class="modal" role="dialog" aria-modal="true" aria-label="${title}">
    <div class="m-head"><h2>${title}</h2><button class="icon-btn" data-action="modal-close" aria-label="${L('닫기')}">${icon('x')}</button></div>
    <div class="m-body">${body}</div>
    <div class="m-foot"><button class="btn" data-action="modal-close">${L('닫기')}</button></div>
  </div></div>`.s;
  root.querySelector('.modal .btn')?.focus();
}
function closeModal() { $('#modal-root').innerHTML = ''; }

// ====================================================================== 액션
async function actDecide(btn) {
  const { task, ap, decision } = btn.dataset;
  const ta = document.getElementById(`cm-${task}-${ap}`);
  const comment = ta?.value.trim() || '';
  if (decision === 'changes' && !comment) {
    toast({ title: L('무엇을 수정해야 하는지 코멘트를 적어주세요'), tone: 'orange' });
    ta?.focus();
    return;
  }
  const box = document.querySelector(`.choice-box[data-choices="${task}/${ap}"]`);
  const selected = box ? $$('input[type="checkbox"]', box).filter((i) => i.checked).map((i) => i.value) : null;
  if (box && decision === 'approve' && !selected.length) {
    toast({ title: L('진행할 항목을 하나 이상 선택해주세요'), tone: 'orange' });
    return;
  }
  if (state.mode !== 'live') {
    const t = state.tasks.get(task);
    const a = t?.approvals.find((x) => x.id === ap);
    const label = DECISION[decision].labelKo; // AI 에게 보내는 메시지는 한국어로
    const sel = selected?.length ? selected : null;
    const cmd = `devtrack decide ${task} ${ap} ${decision}${sel ? ' --select ' + sel.join(' ') : ''}${comment ? ' -m ' + shellQuote(comment) : ''}`;
    const msg = `[DevTrack 결정] ${task} / ${ap} "${a?.title || ''}" → ${label}${sel ? `\n선택: ${sel.join(', ')}` : ''}${comment ? `\n코멘트: ${comment}` : ''}\n\n위 결정을 기록하고(${cmd}) 다음 단계를 진행해 주세요.`;
    const ai = aiName(t);
    openModal({
      title: `${DECISION[decision].label} — ${L('{ai}에게 전달', { ai })}`,
      body: html`<p>${LH('이 페이지는 <b>정적 모드(읽기 전용)</b>라 웹에서 직접 저장할 수 없습니다. 아래 메시지를 {ai} 세션에 붙여넣으면 결정이 기록되고 다음 단계가 진행됩니다.', { ai })}</p>${ghIssueBlock({ title: `${task} ${ap} ${label}`, body: msg, ai })}${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 기록'), cmd)}`,
    });
    return;
  }
  const form = btn.closest('.decision-form');
  $$('button', form).forEach((b) => { b.disabled = true; });
  try {
    await api(`api/tasks/${task}/approvals/${ap}/decision`, selected ? { decision, comment, selected } : { decision, comment });
    if (ta) ta.value = '';
    toast({ title: L('{d} 처리했습니다', { d: DECISION[decision].label }), meta: `${task} ${ap} — ${L('{ai}에게 전달됩니다', { ai: aiName(state.tasks.get(task)) })}`, tone: DECISION[decision].tone });
    await poll({ force: true });
  } catch (e) {
    toast({ title: L('저장하지 못했습니다'), meta: e.message, tone: 'red' });
    $$('button', form).forEach((b) => { b.disabled = false; });
  }
}

// ---------------------------------------------------------------------- 작업 취소 · 삭제 (확인 창을 거친다)
function openCancelTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  const pend = t.approvals.filter((a) => a.status === 'pending').length;
  openModal({
    title: `${L('작업 취소')} — ${t.id}`,
    body: html`<p>${LH('<b>{task}</b> 을(를) 취소할까요?', { task: `${t.id} · ${t.title}` })}</p>
      <ul class="m-list">
        <li>${LH('작업 상태가 <b>취소</b>로 바뀌고, 진행 중 목록에서 빠집니다. 기록과 결과물은 그대로 남습니다.')}</li>
        ${pend ? html`<li>${LH('결정을 기다리던 승인 요청 <b>{n}건</b>은 철회되어 알림에서 사라집니다.', { n: pend })}</li>` : ''}
        <li>${LH('{ai}에게 이 작업을 중단하라는 알림이 갑니다. <code>devtrack watch</code> 로 실행 중이면 바로, 대화 중인 AI 는 다음 명령을 실행하기 전에 멈춥니다 (훅 설치 시 — <a href="#/guide/s8">사용 방법 8단계</a>).', { ai: aiName(t) })}</li>
      </ul>
      <label class="field"><span>${L('취소 사유 (선택)')}</span><textarea id="cancel-reason" placeholder="${L('예: 다른 작업(T-0002)으로 대체')}"></textarea></label>
      <div class="m-actions"><button class="btn btn-reject" data-action="task-cancel-go" data-task="${t.id}">${icon('x-circle')} ${L('작업 취소하기')}</button>
        <span class="hint">${state.mode === 'live' ? '' : L('정적 모드: {ai}에게 보낼 메시지가 만들어집니다', { ai: aiName(t) })}</span></div>`,
  });
  setTimeout(() => $('#cancel-reason')?.focus(), 0);
}

async function actCancelTask(btn) {
  const id = btn.dataset.task;
  const t = state.tasks.get(id);
  const reason = $('#cancel-reason')?.value.trim() || '';
  if (state.mode !== 'live') {
    const cmd = `devtrack cancel ${id}${reason ? ' -m ' + shellQuote(reason) : ''}`;
    const msg = `[DevTrack 작업 취소] ${id} "${t?.title || ''}"${reason ? `\n사유: ${reason}` : ''}\n\n이 작업을 취소로 기록하고(${cmd}) 진행을 멈춰 주세요.`;
    const ai = aiName(t);
    openModal({ title: `${L('작업 취소')} — ${L('{ai}에게 전달', { ai })}`, body: html`<p>${L('정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 메시지를 {ai} 세션에 붙여넣으세요.', { ai })}</p>${ghIssueBlock({ title: `${id} 취소`, body: msg, ai })}${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 기록'), cmd)}` });
    return;
  }
  btn.disabled = true;
  try {
    await api(`api/tasks/${id}/cancel`, { reason });
    closeModal();
    state.tasks.delete(id);
    toast({ title: L('{id} 작업을 취소했습니다', { id }), meta: L('{ai}에게 중단 알림이 전달됩니다', { ai: aiName(t) }), tone: 'green' });
    await poll({ force: true });
  } catch (e) {
    toast({ title: L('취소하지 못했습니다'), meta: e.message, tone: 'red' });
    btn.disabled = false;
  }
}

function openModelTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  openModal({
    title: `${L('모델 · 추론 강도')} — ${t.id}`,
    body: html`<p>${LH('<b>{task}</b> 을(를) 처리할 AI 모델과 추론 강도를 정합니다.', { task: `${t.id} · ${t.title}` })}</p>
      <ul class="m-list">
        <li>${LH('<code>devtrack watch</code> 실행기는 이 작업을 실행할 때마다 이 모델 · 강도로 AI 를 띄우고, 같은 작업의 다음 메시지는 같은 대화로 이어 갑니다.')}</li>
        <li>${L('대기 모드로 켜 둔 대화는 모델이나 강도가 다르면 이 작업을 받지 않습니다. 이미 진행 중이면 다음 메시지부터 적용됩니다.')}</li>
      </ul>
      <div id="model-form">${modelFields(t.agent, t.model || '', t.effort || '')}</div>
      <div class="m-actions"><button class="btn btn-primary" data-action="task-model-go" data-task="${t.id}">${icon('check')} ${L('저장')}</button>
        <span class="hint">${state.mode === 'live' ? '' : L('정적 모드: {ai}에게 보낼 메시지가 만들어집니다', { ai: aiName(t) })}</span></div>`,
  });
}

async function actModelTask(btn) {
  const id = btn.dataset.task;
  const t = state.tasks.get(id);
  const spec = readModelFields($('#model-form [data-model-fields]'));
  if (/\s/.test(spec.model)) {
    toast({ title: L('모델 이름에 공백을 넣을 수 없습니다'), meta: spec.model, tone: 'red' });
    return;
  }
  const label = specLabel(spec.model, spec.effort);
  if (state.mode !== 'live') {
    const cmd = `devtrack model ${id} ${spec.model || spec.effort ? [spec.model ? shellQuote(spec.model) : '', spec.effort ? `--effort ${spec.effort}` : ''].filter(Boolean).join(' ') : '--clear'} --by-user`;
    const msg = `[DevTrack 모델 지정] ${id} "${t?.title || ''}" → ${label || '지정 해제 (어떤 모델이든)'}\n\n이 지정을 기록해 주세요(${cmd}). 이 세션의 모델 · 강도가 다르면 이 작업은 더 진행하지 말고 알려 주세요.`;
    const ai = aiName(t);
    openModal({ title: `${L('모델 · 추론 강도')} — ${L('{ai}에게 전달', { ai })}`, body: html`<p>${L('정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 메시지를 {ai} 세션에 붙여넣으세요.', { ai })}</p>${ghIssueBlock({ title: `${id} 모델`, body: msg, ai })}${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 기록'), cmd)}` });
    return;
  }
  btn.disabled = true;
  try {
    await api(`api/tasks/${id}/model`, spec);
    closeModal();
    state.tasks.delete(id);
    toast({ title: label ? L('{id} 모델: {spec}', { id, spec: label }) : L('{id} 모델 지정을 해제했습니다', { id }), tone: 'green' });
    await poll({ force: true });
  } catch (e) {
    toast({ title: L('저장하지 못했습니다'), meta: e.message, tone: 'red' });
    btn.disabled = false;
  }
}

function openCompleteTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  const pend = t.approvals.filter((a) => a.status === 'pending').length;
  openModal({
    title: `${L('완료로 처리')} — ${t.id}`,
    body: html`<p>${LH('<b>{task}</b> 을(를) 지금 완료로 처리할까요?', { task: `${t.id} · ${t.title}` })}</p>
      <ul class="m-list">
        <li>${LH('끝나지 않았더라도 상태가 <b>완료</b>(진행률 100%)로 바뀌고, 진행 중 목록에서 빠집니다. 기록과 결과물은 그대로 남습니다.')}</li>
        ${pend ? html`<li>${LH('결정을 기다리던 승인 요청 <b>{n}건</b>은 철회되어 알림에서 사라집니다. 커밋 요청이었다면 커밋되지 않습니다.', { n: pend })}</li>` : ''}
        <li>${LH('{ai}에게 이 작업을 멈추라는 알림이 갑니다. <code>devtrack watch</code> 로 실행 중이면 바로, 대화 중인 AI 는 다음 명령을 실행하기 전에 멈춥니다 (훅 설치 시 — <a href="#/guide/s8">사용 방법 8단계</a>).', { ai: aiName(t) })}</li>
      </ul>
      <label class="field"><span>${L('메모 (선택)')}</span><textarea id="complete-note" placeholder="${L('예: 남은 부분은 직접 마무리함')}"></textarea></label>
      <div class="m-actions"><button class="btn btn-approve" data-action="task-complete-go" data-task="${t.id}">${icon('check-circle')} ${L('완료로 처리하기')}</button>
        <span class="hint">${state.mode === 'live' ? '' : L('정적 모드: {ai}에게 보낼 메시지가 만들어집니다', { ai: aiName(t) })}</span></div>`,
  });
  setTimeout(() => $('#complete-note')?.focus(), 0);
}

async function actCompleteTask(btn) {
  const id = btn.dataset.task;
  const t = state.tasks.get(id);
  const note = $('#complete-note')?.value.trim() || '';
  if (state.mode !== 'live') {
    // AI 에게 보내는 메시지는 한국어로 (AI 규칙의 '[DevTrack 작업 완료]')
    const cmd = `devtrack done ${id} --by-user${note ? ' -m ' + shellQuote(note) : ''}`;
    const msg = `[DevTrack 작업 완료] ${id} "${t?.title || ''}"${note ? `\n메모: ${note}` : ''}\n\n사용자가 이 작업을 완료로 처리했습니다. 완료로 기록하고(${cmd}) 진행 중이었다면 멈춰 주세요.`;
    const ai = aiName(t);
    openModal({ title: `${L('완료로 처리')} — ${L('{ai}에게 전달', { ai })}`, body: html`<p>${L('정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 메시지를 {ai} 세션에 붙여넣으세요.', { ai })}</p>${ghIssueBlock({ title: `${id} 완료`, body: msg, ai })}${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 기록'), cmd)}` });
    return;
  }
  btn.disabled = true;
  try {
    await api(`api/tasks/${id}/complete`, { note });
    closeModal();
    state.tasks.delete(id);
    toast({ title: L('{id} 작업을 완료로 처리했습니다', { id }), meta: L('{ai}에게 멈추라는 알림이 전달됩니다', { ai: aiName(t) }), tone: 'green' });
    await poll({ force: true });
  } catch (e) {
    toast({ title: L('완료로 처리하지 못했습니다'), meta: e.message, tone: 'red' });
    btn.disabled = false;
  }
}

function openDeleteTask(id) {
  const t = state.tasks.get(id);
  if (!t) return;
  const pend = t.approvals.filter((a) => a.status === 'pending').length;
  const links = linksOf(t.id);
  openModal({
    title: `${L('작업 삭제')} — ${t.id}`,
    body: html`<div class="alert alert-red">${icon('alert')}<div class="alert-body"><b>${L('삭제하면 되돌릴 수 없습니다.')}</b>
        <p>${LH('기록을 남기고 멈추기만 하려면 삭제 대신 <b>작업 취소</b>를 쓰세요.')}</p></div></div>
      <p>${LH('<b>{task}</b> 의 다음 내용이 모두 지워집니다.', { task: `${t.id} · ${t.title}` })}</p>
      <ul class="m-list">
        <li>${L('진행 기록 {log}건, 문제 {issues}건', { log: t.log.length, issues: t.issues.length })}</li>
        <li>${L('결과물 {n}개 (파일 포함)', { n: t.artifacts.length })}</li>
        <li>${L('승인 요청 {n}건', { n: t.approvals.length })}${pend ? ` (${L('결정 대기 {n}건', { n: pend })})` : ''}</li>
        ${links.length ? html`<li>${L('관련 작업 연결 {ids} 해제', { ids: links.map((l) => l.id).join(', ') })}</li>` : ''}
      </ul>
      <p class="hint">${L('{ai}에게 이 작업을 중단하라는 알림이 갑니다 (작업 취소와 같은 방식으로 멈춥니다).', { ai: aiName(t) })}</p>
      <label class="field"><span>${LH('확인을 위해 작업 ID <b class="mono">{id}</b> 를 입력하세요', { id: t.id })}</span>
        <input type="text" id="del-confirm" autocomplete="off" spellcheck="false" placeholder="${t.id}"></label>
      <div class="m-actions"><button class="btn btn-danger" id="del-go" data-action="task-delete-go" data-task="${t.id}" disabled>${icon('trash')} ${L('영구 삭제')}</button>
        <span class="hint">${state.mode === 'live' ? '' : L('정적 모드: {ai}에게 보낼 메시지가 만들어집니다', { ai: aiName(t) })}</span></div>`,
  });
  setTimeout(() => $('#del-confirm')?.focus(), 0);
}

async function actDeleteTask(btn) {
  const id = btn.dataset.task;
  const typed = $('#del-confirm')?.value.trim().toUpperCase();
  if (typed !== id) { toast({ title: L('작업 ID 를 정확히 입력해주세요'), tone: 'orange' }); return; }
  const t = state.tasks.get(id);
  if (state.mode !== 'live') {
    const cmd = `devtrack delete ${id} --yes`;
    const msg = `[DevTrack 작업 삭제] ${id} "${t?.title || ''}"\n사용자가 삭제를 확인했습니다 (되돌릴 수 없음).\n\n이 작업을 삭제하고(${cmd}) 진행 중이었다면 멈춰 주세요.`;
    const ai = aiName(t);
    openModal({ title: `${L('작업 삭제')} — ${L('{ai}에게 전달', { ai })}`, body: html`<p>${L('정적 모드에서는 웹에서 직접 삭제할 수 없습니다. 아래 메시지를 {ai} 세션에 붙여넣으세요.', { ai })}</p>${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 실행'), cmd)}` });
    return;
  }
  btn.disabled = true;
  try {
    await api(`api/tasks/${id}/delete`, { confirm: typed });
    closeModal();
    state.tasks.delete(id);
    toast({ title: L('{id} 작업을 삭제했습니다', { id }), tone: 'green' });
    if (parseRoute().rest[0] === id) location.hash = '#/tasks';
    await poll({ force: true });
  } catch (e) {
    toast({ title: L('삭제하지 못했습니다'), meta: e.message, tone: 'red' });
    btn.disabled = false;
  }
}

async function actComment(btn) {
  const task = btn.dataset.task;
  const fid = btn.dataset.artifact || ''; // 결과물 화면에서 보낸 메시지면 그 결과물
  const ta = document.getElementById(`fb-${task}`);
  const message = ta?.value.trim();
  if (!message) { ta?.focus(); return; }
  const art = fid ? state.tasks.get(task)?.artifacts.find((a) => a.id === fid) : null;
  if (state.mode !== 'live') {
    const cmd = `devtrack comment ${task}${fid ? ` --artifact ${fid}` : ''} ${shellQuote(message)}`;
    // AI 에게 가는 글이라 한국어 그대로
    const msg = `[DevTrack 메시지] ${task}${fid ? ` · 결과물 ${fid} "${art?.title || ''}" 에 대해` : ''}\n${message}`;
    const ai = aiName(state.tasks.get(task));
    openModal({
      title: L('{ai}에게 메시지 전달', { ai }),
      body: html`<p>${L('정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 내용을 {ai} 세션에 붙여넣으세요.', { ai })}</p>${ghIssueBlock({ title: `${task} 메시지${fid ? ` (${fid})` : ''}`, body: msg, ai })}${copyBlock(L('{ai}에게 보낼 메시지', { ai }), msg)}${copyBlock(L('또는 터미널에서 직접 기록'), cmd)}`,
    });
    return;
  }
  btn.disabled = true;
  try {
    await api(`api/tasks/${task}/comments`, fid ? { message, artifact: fid } : { message });
    ta.value = ''; // 다시 그릴 때 보낸 글이 초안으로 남지 않게 (saveDrafts)
    toast({ title: L('메시지를 보냈습니다'), meta: `${task}${fid ? ` · ${fid}` : ''} — ${L('{ai}에게 devtrack inbox 로 전달됩니다', { ai: aiName(state.tasks.get(task)) })}`, tone: 'green' });
    await poll({ force: true });
  } catch (e) {
    toast({ title: L('보내지 못했습니다'), meta: e.message, tone: 'red' });
    btn.disabled = false;
  }
}

function effectiveTheme() {
  return document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}
function syncThemeIcon() {
  $('#theme-btn').innerHTML = String(icon(effectiveTheme() === 'dark' ? 'sun' : 'moon'));
}
/** 언어 단추: 누르면 바뀔 언어를 보여 준다 (한국어 화면이면 EN, 영어 화면이면 한) */
function syncLangBtn() {
  const btn = $('#lang-btn');
  const en = getLang() === 'en';
  btn.textContent = en ? '한' : 'EN';
  btn.title = en ? '한국어로 보기' : 'Switch to English';
  btn.setAttribute('aria-label', btn.title);
}

async function onAction(el, ev) {
  const a = el.dataset.action;
  switch (a) {
    case 'decide': return actDecide(el);
    case 'comment': return actComment(el);
    case 'task-complete': return openCompleteTask(el.dataset.task);
    case 'task-complete-go': return actCompleteTask(el);
    case 'task-cancel': return openCancelTask(el.dataset.task);
    case 'task-cancel-go': return actCancelTask(el);
    case 'task-model': return openModelTask(el.dataset.task);
    case 'task-model-go': return actModelTask(el);
    case 'task-delete': return openDeleteTask(el.dataset.task);
    case 'task-delete-go': return actDeleteTask(el);
    case 'choices': {
      const box = el.closest('.choice-box');
      $$('input[type="checkbox"]', box).forEach((i) => { i.checked = el.dataset.mode === 'all'; });
      box.dataset.dirty = '1';
      syncChoiceCount(box);
      return;
    }
    case 'rel-remove': return removeRelated(el);
    case 'gh-issue': {
      const { title, body, labels } = el.dataset;
      const repo = ghRepo();
      if (!repo) return;
      let url = ghIssueUrl(repo, title, body, labels);
      if (url.length > GH_URL_MAX) { // 긴 지시: 본문은 클립보드로, 이슈에는 붙여 넣을 자리만
        const ok = await copyText(body);
        url = ghIssueUrl(repo, title, '<!-- devtrack-paste-here: 본문이 길어 클립보드에 복사했습니다. 이 줄 아래에 붙여 넣으세요 (Ctrl+V / ⌘V). Body copied to the clipboard — paste it below. -->\n', labels);
        toast(ok ? { title: L('본문이 길어 클립보드에 복사했습니다'), meta: L('열린 이슈의 본문에 붙여 넣고 Submit 하세요'), tone: 'blue', sticky: true }
          : { title: L('본문을 복사하지 못했습니다'), meta: L('보내기 창의 메시지를 직접 복사해 이슈 본문에 붙여 넣으세요'), tone: 'orange', sticky: true });
      }
      window.open(url, '_blank', 'noopener');
      return;
    }
    case 'copy': {
      const ok = await copyText(el.dataset.copy || '');
      const prev = el.innerHTML;
      el.innerHTML = ok ? `${icon('check')} ${L('복사됨')}` : L('복사 실패');
      setTimeout(() => { el.innerHTML = prev; }, 1400);
      return;
    }
    case 'theme': {
      const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      LS.set('theme', next);
      syncThemeIcon();
      return;
    }
    case 'lock': forgetKey(); location.reload(); return;
    case 'lang': {
      setLang(getLang() === 'en' ? 'ko' : 'en');
      applyStatic();
      syncLangBtn();
      updateChrome();
      return render({ keepScroll: true });
    }
    case 'art-view': {
      LS.set('artView', el.dataset.view === 'list' ? 'list' : 'grid');
      return render({ keepScroll: true });
    }
    case 'art-sort': {
      LS.set('artSort', el.dataset.sort === 'task' ? 'task' : 'time');
      return render({ keepScroll: true });
    }
    case 'refresh': return poll({ force: true });
    case 'apply-update': return render({ keepScroll: true });
    case 'bell': {
      const dd = $('#notif-dd');
      const open = !dd.classList.contains('open');
      dd.classList.toggle('open', open);
      if (open) { renderDropdown(); LS.set('seenTs', Date.now()); updateChrome(); renderDropdown(); }
      return;
    }
    case 'desktop-notify': {
      if (LS.get('desktopNotify', false) && Notification.permission === 'granted') {
        LS.set('desktopNotify', false);
      } else {
        const p = await Notification.requestPermission();
        LS.set('desktopNotify', p === 'granted');
        if (p === 'granted') toast({ title: L('데스크톱 알림을 켰습니다'), meta: L('새 승인 요청이 오면 알려드립니다'), tone: 'green' });
      }
      renderDropdown();
      return;
    }
    case 'menu': document.body.classList.toggle('nav-open'); return;
    case 'modal-close': closeModal(); return;
    case 'modal-close-back': if (ev.target === el) closeModal(); return;
    default:
  }
}

// ====================================================================== 시작
function bindEvents() {
  document.addEventListener('click', (ev) => {
    const actionEl = ev.target.closest('[data-action]');
    if (actionEl) {
      if (actionEl.tagName === 'BUTTON') ev.preventDefault();
      onAction(actionEl, ev);
      if (actionEl.dataset.action !== 'bell' && !actionEl.closest('#notif-dd')) $('#notif-dd').classList.remove('open');
      return;
    }
    if (ev.target.closest('[data-close-dd]')) $('#notif-dd').classList.remove('open');
    else if (!ev.target.closest('#notif-dd')) $('#notif-dd').classList.remove('open');
    if (ev.target.closest('.sidebar a')) document.body.classList.remove('nav-open');
    const row = ev.target.closest('tr[data-href]');
    if (row && !ev.target.closest('a')) location.hash = row.dataset.href;
  });
  document.addEventListener('input', (ev) => {
    if (ev.target.matches('#new-task-form input[name="related"]')) return syncRelated(ev.target.form);
    if (ev.target.id !== 'del-confirm') return;
    const go = $('#del-go');
    if (go) go.disabled = ev.target.value.trim().toUpperCase() !== go.dataset.task;
  });
  document.addEventListener('change', (ev) => {
    const el = ev.target.closest('[data-change]');
    if (el?.dataset.change === 'type-filter') location.hash = tasksHref({ ...parseRoute().q, type: el.value });
    if (ev.target.matches('#new-task-form select[name="type"]')) syncTypeFields(ev.target.form);
    if (ev.target.matches('#new-task-form [data-rel-add]')) addRelated(ev.target);
    if (ev.target.matches('#new-task-form select[name="agent"]')) syncModelFields(ev.target.form.querySelector('[data-model-fields]:not([data-prefix])'), ev.target.value);
    if (ev.target.matches('#new-task-form select[name="review_by"]')) syncReviewFields(ev.target.form);
    if (ev.target.closest('#new-task-form')) syncReviewSame(ev.target.closest('#new-task-form'));
    if (ev.target.matches('[data-model-fields] select[name$="model_pick"]')) onModelPick(ev.target);
    if (ev.target.matches('[data-model-fields] input[name$="model_custom"]')) syncEfforts(ev.target.closest('[data-model-fields]'));
    const box = ev.target.closest('.choice-box');
    if (box) { box.dataset.dirty = '1'; syncChoiceCount(box); }
  });
  document.addEventListener('toggle', (ev) => {
    const d = ev.target;
    if (d.matches?.('details[data-key]')) state.details.set(d.dataset.key, d.open);
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { closeModal(); $('#notif-dd').classList.remove('open'); document.body.classList.remove('nav-open'); }
  });
  window.addEventListener('hashchange', () => {
    // 같은 화면에서 필터만 바뀌면 스크롤 위치 유지
    const r = parseRoute();
    const key = [r.name, ...r.rest].join('/');
    render({ keepScroll: key === state.routeKey });
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  let tocTick = false;
  window.addEventListener('scroll', () => {
    if (tocTick || !$('.guide-toc')) return;
    tocTick = true;
    requestAnimationFrame(() => { tocTick = false; syncGuideToc(); });
  }, { passive: true });
  matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', syncThemeIcon);
  setInterval(() => {
    $$('time[data-ts]').forEach((t) => { if (t.dataset.ts) t.textContent = fmtRel(t.dataset.ts); });
  }, 30000);
}

async function init() {
  document.documentElement.lang = getLang();
  applyStatic();
  syncLangBtn();
  syncThemeIcon();
  bindEvents();
  state.access = await checkAccess();
  if (state.access.state === 'locked') return showLock(state.access.auth); // 맞는 비밀번호를 넣으면 다시 불러온다
  state.mode = await detectMode();
  await poll({ force: true });
  if (state.access.state === 'unprotected') {
    toast({ title: L('비밀번호를 정했지만 이 웹 서버는 기록을 막지 않습니다'), tone: 'red', sticky: true,
      meta: L('data/ 폴더를 누구나 읽을 수 있습니다 — .htaccess 를 읽는 Apache 서버에 올리거나 devtrack serve 로 여세요') });
  }
  setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
  // 다른 탭에 있다가 돌아오면 다음 폴링을 기다리지 않고 바로 확인
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  // 입력 칸에서 나오거나 누르기를 마치면, 입력 중이라 미뤄 둔 갱신을 반영 (클릭이 끝날 틈을 둔다)
  const later = () => setTimeout(applyPending, 600);
  $('#main').addEventListener('focusout', later);
  window.addEventListener('pointerdown', () => { state.pointerDown = true; }, true);
  for (const type of ['pointerup', 'pointercancel']) {
    window.addEventListener(type, () => { state.pointerDown = false; later(); }, true);
  }
}

init();
