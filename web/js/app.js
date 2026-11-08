// DevTrack 웹 앱 — 정적 호스팅(읽기 전용)과 로컬 서버(쓰기 가능) 모두에서 동작
import {
  html, raw, icon, STATUS, TYPES, APPROVAL_STATUS, APPROVAL_KIND, LOG_KIND, SEVERITY, ART_KIND,
  FINDING_SEV, FINDING_STATUS, VERDICT, CONFIDENCE, pick, badge, ago, fmtAbs, fmtRel, fmtBytes, authorName, shellQuote, copyText, LS, tsNum,
} from './util.js';
import { artifactUrl, renderArtifactInto, renderMarkdown, enhance } from './viewers.js';

const POLL_MS = 10000;
const ACTIVE = ['in_progress', 'awaiting_approval', 'changes_requested', 'blocked', 'queued'];
const DECISION = {
  approve: { label: '승인', tone: 'green' },
  changes: { label: '수정 요청', tone: 'orange' },
  reject: { label: '거절', tone: 'red' },
};
// 작업 유형 → 분야 섹션 (사이드바 메뉴)
const SECTION_OF_TYPE = { paper: 'papers', analysis: 'analysis', visualization: 'analysis', review: 'reviews' };
const SECTION_LABEL = { papers: '논문 작성', analysis: '데이터 분석', reviews: '코드 리뷰', tasks: '작업' };
const ANALYSIS_TYPES = ['analysis', 'visualization'];
const FIGURE_KINDS = ['image', 'html'];

const state = {
  mode: 'static',
  index: null,
  loadError: null,
  tasks: new Map(),
  knownPending: null,
  lastEventTs: 0,
  details: new Map(),
  taskSearch: '',
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

// ====================================================================== 데이터
async function getJSON(path) {
  const r = await fetch(`${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText || ''} — ${path}`);
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
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let j = {};
  try { j = await r.json(); } catch { /* 빈 응답 */ }
  if (!r.ok) throw new Error(j.error || `${r.status} ${r.statusText}`);
  return j;
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
  reviews: viewReviews,
  papers: viewPapers,
  analysis: viewAnalysis,
  artifacts: viewArtifacts,
  artifact: viewArtifact,
  activity: viewActivity,
  new: viewNewTask,
  guide: viewGuide,
};

let renderSeq = 0;
async function render({ keepScroll = false } = {}) {
  const seq = ++renderSeq;
  const route = parseRoute();
  state.routeKey = [route.name, ...route.rest].join('/');
  const main = $('#main');
  saveDetails(main);
  const y = window.scrollY;
  updateChrome(route);
  let view;
  try {
    if (!state.index && route.name !== 'guide') view = state.loadError ? viewLoadError(state.loadError) : html`<div class="empty">불러오는 중…</div>`;
    else view = VIEWS[route.name] ? await VIEWS[route.name](route) : viewNotFound();
  } catch (e) {
    console.error(e);
    view = viewError(e);
  }
  if (seq !== renderSeq) return;
  main.innerHTML = String(view);
  restoreDetails(main);
  hydrate(main);
  $('#update-pill').classList.remove('show');
  window.scrollTo(0, keepScroll ? y : 0);
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
  if (form) form.addEventListener('submit', submitNewTask);
  const guide = $('.guide-body', root);
  if (guide) setupGuide(guide, $('.guide-toc', root));
}

function mountArtifact(el) {
  if (el.dataset.mounted) return;
  el.dataset.mounted = '1';
  const [tid, fid] = el.dataset.av.split('/');
  const task = state.tasks.get(tid);
  const art = task?.artifacts?.find((a) => a.id === fid);
  if (!art) { el.innerHTML = '<div class="av-error">결과물을 찾을 수 없습니다</div>'; return; }
  const ok = buildInfo(task)?.ok;
  renderArtifactInto(el, tid, art, {
    resolveImage: (p) => resolveFigure(tid, p),
    pdfHref: ok ? `#/artifact/${tid}/${ok.pdf}` : null,
  });
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

function isEditing() {
  const main = $('#main');
  const a = document.activeElement;
  if (a && main.contains(a) && /^(TEXTAREA|INPUT|SELECT)$/.test(a.tagName) && a.id !== 'task-search') return true;
  if ($('.choice-box[data-dirty]', main)) return true;
  return $$('textarea, #new-task-form input[type="text"]', main).some((t) => t.value.trim());
}

function isRelevant(changed, prev, idx) {
  if (!prev) return true;
  const r = parseRoute();
  if (r.name === 'guide') return false;
  if (r.name === 'task' || r.name === 'artifact') return changed.has(r.rest[0]);
  if (r.name === 'reviews') {
    const ids = (x) => x.reviews.map((v) => v.task_id + v.id).join();
    return ids(prev) !== ids(idx) || changed.has(r.rest[0] || idx.reviews[0]?.task_id);
  }
  return true;
}

// ====================================================================== 폴링 / 알림
async function poll({ force = false } = {}) {
  const btn = $('#refresh-btn');
  try {
    const idx = await getJSON('data/index.json');
    const prev = state.index;
    const changed = new Set();
    if (prev) {
      const before = new Map(prev.tasks.map((t) => [t.id, t.updated_at]));
      for (const t of idx.tasks) if (before.get(t.id) !== t.updated_at) changed.add(t.id);
      for (const id of before.keys()) if (!idx.tasks.some((t) => t.id === id)) changed.add(id);
    }
    changed.forEach((id) => state.tasks.delete(id));
    state.index = idx;
    const hadError = !!state.loadError;
    state.loadError = null;
    processNotifications(idx);
    const dataChanged = !prev || idx.generated_at !== prev.generated_at;
    if (force || hadError || (dataChanged && isRelevant(changed, prev, idx))) {
      if (!force && prev && isEditing()) {
        $('#update-pill').classList.add('show');
        updateChrome();
      } else {
        await render({ keepScroll: !!prev });
      }
    } else {
      updateChrome();
    }
  } catch (e) {
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
        title: `승인 요청: ${n.title}`, meta: `${n.task_id} · ${n.task_title}`, tone: 'amber',
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
  el.innerHTML = html`<div class="body"><div class="title">${title}</div>${meta ? html`<div class="meta">${meta}</div>` : ''}${href ? html`<a class="go" href="${href}">확인하기 →</a>` : ''}</div><button class="x" aria-label="닫기">${icon('x')}</button>`.s;
  el.querySelector('.x').onclick = () => el.remove();
  el.querySelector('.go')?.addEventListener('click', () => el.remove());
  box.appendChild(el);
  setTimeout(() => el.remove(), sticky ? 20000 : 6000);
  while (box.children.length > 4) box.firstChild.remove();
}

// ====================================================================== 상단바 / 사이드바
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
  setCount('#nav-papers-count', pendIn('papers'));
  setCount('#nav-analysis-count', pendIn('analysis'));
  const seen = LS.get('seenTs', 0);
  const unseen = idx ? idx.events.filter((e) => e.kind !== 'approval' && tsNum(e.ts) > seen).length : 0;
  $('#bell-count').textContent = pending;
  $('#bell-count').hidden = !pending;
  $('#bell-dot').hidden = !!pending || !unseen;
  document.title = `${pending ? `(${pending}) ` : ''}DevTrack · AI 개발 진행 현황`;

  const chip = $('#mode-chip');
  if (state.loadError && idx) {
    chip.className = 'mode-chip error';
    chip.innerHTML = '<span class="dot"></span><span>연결 끊김</span>';
    chip.title = `데이터를 새로 불러오지 못했습니다: ${state.loadError.message}`;
  } else if (state.mode === 'live') {
    chip.className = 'mode-chip live';
    chip.innerHTML = '<span class="dot"></span><span>실시간<span class="label-long"> · 로컬 서버</span></span>';
    chip.title = '로컬 서버에 연결됨: 웹에서 승인·피드백·작업 지시가 바로 저장됩니다';
  } else {
    chip.className = 'mode-chip';
    chip.innerHTML = '<span class="dot"></span><span>정적<span class="label-long"> · 읽기 전용</span></span>';
    chip.title = '정적 호스팅 모드: 데이터는 자동 갱신되지만, 승인/피드백은 Claude에게 전달할 메시지로 표시됩니다';
  }
  $('#sync-info').innerHTML = idx
    ? html`데이터 갱신 <b>${ago(idx.generated_at)}</b><br>${POLL_MS / 1000}초마다 자동 확인`.s
    : '데이터 없음';
  if ($('#notif-dd').classList.contains('open')) renderDropdown();
}

function renderDropdown() {
  const idx = state.index;
  const dd = $('#notif-dd');
  if (!idx) { dd.innerHTML = '<div class="empty">데이터 없음</div>'; return; }
  const seen = LS.get('seenTs', 0);
  const pend = idx.notifications;
  const evs = idx.events.filter((e) => e.kind !== 'approval').slice(0, 15);
  const perm = 'Notification' in window ? Notification.permission : 'unsupported';
  const on = LS.get('desktopNotify', false) && perm === 'granted';
  dd.innerHTML = html`
    <div class="dd-head"><b>알림</b><a href="#/approvals" data-close-dd>승인 요청 보기</a></div>
    <div class="dd-sec">결정 대기 · ${pend.length}</div>
    ${pend.length ? pend.map((n) => html`
      <a class="dd-item" href="#/task/${n.task_id}/approvals" data-close-dd>
        <span class="ic tone-amber">${icon(pick(APPROVAL_KIND, n.kind, 'general').icon)}</span>
        <div class="body"><div class="title">${n.title}</div><div class="meta">${n.task_id} · ${n.task_title} · ${ago(n.created_at)}</div></div>
      </a>`) : html`<div class="dd-item muted">대기 중인 승인 요청이 없습니다</div>`}
    <div class="dd-sec">최근 소식</div>
    ${evs.length ? evs.map((e) => {
      const k = pick(LOG_KIND, e.kind, 'info');
      return html`<a class="dd-item ${tsNum(e.ts) > seen ? 'unseen' : ''}" href="#/task/${e.task_id}" data-close-dd>
        <span class="ic tone-${k.tone}">${icon(k.icon)}</span>
        <div class="body"><div class="title">${e.message}</div><div class="meta">${e.task_id} · ${authorName(e.author)} · ${ago(e.ts)}</div></div>
      </a>`;
    }) : html`<div class="dd-item muted">새 소식이 없습니다</div>`}
    <div class="dd-foot">
      <span>${perm === 'unsupported' ? '이 브라우저는 데스크톱 알림을 지원하지 않습니다' : perm === 'denied' ? '브라우저에서 알림이 차단됨' : on ? '데스크톱 알림 켜짐' : '데스크톱 알림 꺼짐'}</span>
      ${perm !== 'unsupported' && perm !== 'denied' ? html`<button class="btn btn-sm" data-action="desktop-notify">${on ? '끄기' : '켜기'}</button>` : ''}
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
    if (n) return { label: '승인 대기', text: n.title };
  }
  if (t.status === 'in_progress' && t.current_step) return { label: '지금', text: t.current_step };
  return { label: '최근', text: t.last_log?.message || t.current_step || '' };
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

function taskCard(t, { thumbs = false, links = false } = {}) {
  const now = taskNow(t);
  const figs = thumbs ? figuresOf(t.id).slice(0, 4) : [];
  const papers = links ? linksOf(t.id).filter((x) => x.type === 'paper') : [];
  return html`<a class="task-card" href="#/task/${t.id}">
    <div class="tc-top">${typeBadge(t.type)}${statusBadge(t.status)}<span class="tc-id">${t.id}</span></div>
    <div class="tc-title">${t.title}</div>
    ${papers.length ? html`<div class="tc-link">${icon('book')} 논문용 · ${papers.map((p) => `${p.id} ${p.title}`).join(', ')}</div>` : ''}
    <div class="tc-step">${now.text ? html`<span class="lbl">${now.label}</span>${now.text}` : html`<span class="muted">기록 없음</span>`}</div>
    ${figs.length ? html`<div class="tc-thumbs">${figs.map((a) => html`<img src="${artifactUrl(a.task_id, a)}" alt="" loading="lazy" title="${a.title}">`)}</div>` : ''}
    <div class="tc-prog">${progressBar(t)}<span class="pct">${t.progress || 0}%</span></div>
    <div class="tc-meta">
      <span>${t.agent}</span><span>${ago(t.updated_at)}</span>
      ${t.pending_approvals ? html`<span class="flag amber">${icon('bell')}승인 ${t.pending_approvals}</span>` : ''}
      ${t.open_issues ? html`<span class="flag ${t.max_severity >= 2 ? 'red' : 'orange'}">${icon('alert')}문제 ${t.open_issues}</span>` : ''}
    </div>
  </a>`;
}

function feedItem(e) {
  const k = pick(LOG_KIND, e.kind, 'info');
  return html`<li>
    <span class="ic tone-${k.tone}">${icon(k.icon)}</span>
    <div class="body">
      <div class="msg">${e.kind === 'user' ? html`<b>사용자:</b> ${e.message}` : e.message}</div>
      <div class="meta"><a href="#/task/${e.task_id}">${e.task_id}</a><span>${authorName(e.author)}</span>${ago(e.ts)}</div>
    </div>
  </li>`;
}

function timelineItem(e, { taskLink = false } = {}) {
  const k = pick(LOG_KIND, e.kind, 'info');
  const isUser = e.author === 'user';
  const commit = e.kind === 'commit' && e.meta?.commit ? html` · ${commitLink(e.meta.remote_url, e.meta.commit)}` : '';
  return html`<li class="kind-${e.kind} ${isUser ? 'user' : ''}">
    <span class="ic tone-${k.tone}">${icon(k.icon)}</span>
    <div class="body">
      ${e.kind === 'user'
        ? html`<div class="head"><span class="msg">사용자 메시지</span></div><div class="msg-box markdown-body compact">${raw(renderMarkdown(e.message))}</div>`
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
      <div class="title">${i.title} <span class="badge tone-${sev.tone}">${sev.label}</span>${i.status === 'resolved' ? html`<span class="badge tone-green">${icon('check')}해결됨</span>` : ''}</div>
      <div class="meta">${i.id} · ${authorName(i.author)} · ${ago(i.created_at)}${i.resolved_at ? html` · 해결 ${ago(i.resolved_at)}` : ''}</div>
      ${i.detail ? html`<div class="detail markdown-body compact">${raw(renderMarkdown(i.detail))}</div>` : ''}
      ${i.resolution ? html`<div class="resolution"><b>해결:</b> ${i.resolution}</div>` : ''}
    </div>
  </div>`;
}

function artRow(tid, a, { showTask = false } = {}) {
  const k = pick(ART_KIND, a.kind, 'file');
  return html`<a class="art-row" href="#/artifact/${tid}/${a.id}">
    <span class="ic">${icon(k.icon)}</span>
    <div class="body"><div class="title">${a.title}</div><div class="meta">${showTask ? `${tid} · ` : `${a.id} · `}${k.label}${a.category === 'review' ? ' · 리뷰' : ''} · ${ago(a.created_at)}${a.size ? ` · ${fmtBytes(a.size)}` : ''}${showTask && a.task_title ? ` · ${a.task_title}` : ''}</div></div>
    ${icon('chev')}
  </a>`;
}

function artCard(a) {
  const k = pick(ART_KIND, a.kind, 'file');
  const url = a.path ? artifactUrl(a.task_id, a) : null;
  return html`<a class="art-card" href="#/artifact/${a.task_id}/${a.id}">
    <div class="thumb">${a.kind === 'image' && url ? html`<img src="${url}" alt="" loading="lazy">` : icon(k.icon)}</div>
    <div class="info">
      <div class="title" title="${a.title}">${a.title}</div>
      <div class="meta"><span class="badge">${k.label}</span>${a.category === 'review' ? html`<span class="badge tone-violet">리뷰</span>` : ''}<span>${a.task_id}</span>${ago(a.created_at)}</div>
    </div>
  </a>`;
}

function planList(t) {
  const plan = t.plan || [];
  if (!plan.length) return html`<div class="muted">아직 계획이 등록되지 않았습니다</div>`;
  const cur = t.status === 'done' ? -1 : plan.findIndex((p) => !p.done);
  return html`<ol class="plan">${plan.map((p, i) => html`
    <li class="${p.done ? 'done' : ''} ${i === cur ? 'current' : ''}">
      <span class="num">${i + 1}</span><span class="box">${p.done ? icon('check') : ''}</span><span class="txt">${p.text}</span>
    </li>`)}</ol>`;
}

function feedbackBox(t, title = '에이전트에게 메시지') {
  return html`<section class="card feedback-card">
    <div class="card-head"><h2>${icon('message')} ${title}</h2></div>
    <div class="card-body">
      <textarea id="fb-${t.id}" placeholder="추가 지시, 질문, 수정 요청 등을 입력하세요 (markdown 지원)"></textarea>
      <div class="actions">
        <span class="hint">${state.mode === 'live' ? '에이전트가 devtrack inbox 로 확인합니다' : '정적 모드: 보내기를 누르면 Claude에게 전달할 내용이 표시됩니다'}</span>
        <button class="btn btn-primary" data-action="comment" data-task="${t.id}">${icon('send')} 보내기</button>
      </div>
    </div>
  </section>`;
}

function commitBox(act) {
  const st = act.stat || {};
  const repo = repoLabel({ url: act.remote_url, path: act.repo });
  return html`<div class="commit-box">
    <div class="cb-head">${icon('git')} 승인하면 커밋${act.push ? ' 후 푸시' : ''}
      ${act.branch ? html`<span class="badge">${icon('branch')}${act.branch}</span>` : ''}
      <span class="stat">${st.files ?? '?'}개 파일 <span class="add">+${st.additions ?? 0}</span> <span class="del">−${st.deletions ?? 0}</span></span>
    </div>
    <div class="cb-msg">${act.message}</div>
    <dl class="kv">
      <dt>저장소</dt><dd class="mono">${repo?.href ? html`<a href="${repo.href}" target="_blank" rel="noopener">${repo.text}</a>` : repo?.text || act.repo}</dd>
      ${act.remote_url ? html`<dt>경로</dt><dd class="mono">${act.repo}</dd>` : ''}
      ${act.files?.length ? html`<dt>대상 파일</dt><dd class="mono">${act.files.join(', ')}</dd>` : ''}
    </dl>
  </div>`;
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
      <div class="f-meta">${loc ? (href ? html`<a class="mono" href="${href}" target="_blank" rel="noopener">${loc}</a>` : html`<span class="mono">${loc}</span>`) : ''}${f.category ? html`<span>${f.category}</span>` : ''}${f.confidence ? html`<span>확신도 ${CONFIDENCE[f.confidence] || f.confidence}</span>` : ''}${safeHttp(f.comment_url) ? html`<a href="${f.comment_url}" target="_blank" rel="noopener">${icon('message')} 게시된 코멘트</a>` : ''}</div>
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
  if (pr.head_sha) bits.push(html`<span class="mono" title="리뷰한 커밋">${icon('git')} ${String(pr.head_sha).slice(0, 8)}</span>`);
  if (pr.files != null || pr.additions != null) bits.push(html`<span>${pr.files ?? '?'}개 파일 <span class="add">+${pr.additions ?? 0}</span> <span class="del">−${pr.deletions ?? 0}</span></span>`);
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
    <div class="card-head"><h2>${icon('eye')} 리뷰 결과 한눈에 보기</h2><span class="muted" style="font-size:12.5px">${ago(rv.updated_at)}</span></div>
    <div class="card-body">
      ${prTitle ? html`<div class="pr-title">${prHref ? html`<a href="${prHref}" target="_blank" rel="noopener">${prTitle} ${icon('external')}</a>` : prTitle}</div>` : ''}
      ${prMeta(pr)}
      ${v || rv.summary ? html`<div class="rv-verdict">
        ${v ? html`<div class="rv-v"><span class="badge tone-${v.tone}">${icon(v.icon)} 리뷰 의견: ${v.label}</span></div>` : ''}
        ${rv.summary ? html`<div class="markdown-body compact">${raw(renderMarkdown(rv.summary))}</div>` : ''}
      </div>` : ''}
      <div class="sev-row"><span class="lbl">남은 항목 ${open.length}개</span>${sevChips(by, { empty: html`<span class="badge tone-green">${icon('check')} 모두 처리됨</span>` })}${settled ? html`<span class="muted">· 처리됨 ${settled}</span>` : ''}</div>
      ${all.length ? html`<ul class="finding-list">${all.map((f) => findingItem(pr, f))}</ul>` : ''}
    </div>
  </section>`;
}

function choiceBox(t, ap) {
  const n = ap.choices.filter((c) => c.default).length;
  return html`<fieldset class="choice-box" data-choices="${t.id}/${ap.id}">
    <legend>${ap.choices_label || '진행할 항목 선택'}</legend>
    <div class="choice-tools"><span class="muted" data-choice-count>${n}/${ap.choices.length}개 선택</span>
      <button type="button" class="btn btn-ghost btn-sm" data-action="choices" data-mode="all">전체 선택</button>
      <button type="button" class="btn btn-ghost btn-sm" data-action="choices" data-mode="none">모두 해제</button></div>
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
  if (el) el.textContent = `${n}/${all.length}개 선택`;
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
        <div class="meta"><span class="mono">${ap.id}</span>${showTask ? html`<a href="#/task/${t.id}">${t.id} · ${t.title}</a>` : ''}<span>${authorName(ap.author)} 요청 · ${ago(ap.created_at)}</span></div>
      </div>
    </header>
    <div class="ap-body">
      ${ap.description ? html`<div class="ap-desc markdown-body compact">${raw(renderMarkdown(ap.description))}</div>` : ''}
      ${ap.action ? commitBox(ap.action) : ''}
      ${linked.length ? html`<div class="ap-linked">${linked.map((a, i) => html`
        <details data-key="ap:${t.id}:${ap.id}:${a.id}" ${ap.status === 'pending' && i === 0 ? raw('open') : ''}>
          <summary><span class="s-title">${icon(pick(ART_KIND, a.kind, 'file').icon)} ${a.title} <span class="muted">${pick(ART_KIND, a.kind, 'file').label}</span></span>
            <a class="btn btn-ghost btn-sm" href="#/artifact/${t.id}/${a.id}">${icon('external')} 크게 보기</a>${icon('chev', 'chev')}</summary>
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
    <textarea id="cm-${t.id}-${ap.id}" placeholder="코멘트 (선택) — 수정 요청이라면 무엇을 고쳐야 하는지 적어주세요"></textarea>
    <div class="actions">
      ${btn('approve', 'btn-approve', 'check', ap.action ? '승인 (커밋·푸시 진행)' : ap.choices?.length ? '선택한 항목으로 진행' : '승인')}
      ${btn('changes', 'btn-changes', 'edit', '수정 요청')}
      ${btn('reject', 'btn-reject', 'x', '거절')}
      <span class="hint">${state.mode === 'live' ? '결정은 즉시 저장되어 에이전트에게 전달됩니다' : '정적 모드: Claude에게 전달할 메시지가 표시됩니다'}</span>
    </div>
  </div>`;
}

function decisionResult(ap) {
  const st = pick(APPROVAL_STATUS, ap.status, 'pending');
  let applied = '';
  if (ap.applied) {
    const a = ap.applied;
    applied = html`<div class="applied">${icon('git')} 커밋 ${commitLink(ap.action?.remote_url, a.commit)} · ${a.pushed ? '푸시 완료' : a.push_error ? html`<span class="del">푸시 실패</span>` : '푸시 안 함'} · ${ago(a.at)}</div>`;
  } else if (ap.status === 'approved' && ap.action) {
    applied = html`<div class="applied">${icon('clock')} 에이전트가 커밋/푸시를 실행하기를 기다리는 중</div>`;
  }
  const label = (id) => ap.choices?.find((c) => c.id === id)?.label || id;
  const picked = ap.selected?.length ? html`<div class="picked">선택: ${ap.selected.map((id) => html`<span class="fid" title="${label(id)}">${id}</span>`)}</div>` : '';
  return html`<div class="decision-result ${ap.status}">${icon(st.icon)}
    <div><b>${st.label}</b> · ${authorName(ap.decided_by)} · ${ago(ap.decided_at)}
      ${picked}${ap.comment ? html`<div class="comment">${ap.comment}</div>` : ''}${applied}</div>
  </div>`;
}

function staticBanner() {
  if (state.mode === 'live') return '';
  return html`<div class="alert alert-info">${icon('info')}<div class="alert-body"><b>정적 모드 (읽기 전용)</b>
    <p>승인·피드백 버튼을 누르면 Claude 세션에 붙여넣을 메시지가 표시됩니다. 웹에서 바로 저장하려면 <code>devtrack serve</code> 로 실행한 주소로 여세요.</p></div></div>`;
}

function attnApproval(n) {
  const k = pick(APPROVAL_KIND, n.kind, 'general');
  return html`<a class="attn-item" href="#/task/${n.task_id}/approvals">
    <span class="ic tone-amber">${icon(k.icon)}</span>
    <div class="body"><div class="title">${n.title}</div><div class="meta">승인 요청 · ${k.label} · ${n.task_id} ${n.task_title} · ${ago(n.created_at)}</div></div>
    <span class="btn btn-sm">확인</span></a>`;
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
      <div class="body"><div class="title">${t.title}</div><div class="meta">작업이 막혔습니다 · ${t.id} · ${t.current_step || t.last_log?.message || ''}</div></div>
      <span class="go">${icon('chev')}</span></a>`),
    ...tasks.filter((t) => t.status !== 'blocked' && t.max_severity >= 2 && ACTIVE.includes(t.status)).map((t) => html`<a class="attn-item" href="#/task/${t.id}/issues">
      <span class="ic tone-orange">${icon('alert')}</span>
      <div class="body"><div class="title">${t.title}</div><div class="meta">심각도 높은 문제 ${t.open_issues}건 · ${t.id}</div></div>
      <span class="go">${icon('chev')}</span></a>`),
    ...tasks.filter((t) => t.status === 'queued').map((t) => html`<a class="attn-item" href="#/task/${t.id}">
      <span class="ic tone-gray">${icon('inbox')}</span>
      <div class="body"><div class="title">${t.title}</div><div class="meta">에이전트가 아직 시작하지 않은 지시 · ${t.id} · ${ago(t.created_at)}</div></div>
      <span class="go">${icon('chev')}</span></a>`),
  ];
  const working = active.filter((t) => t.status !== 'queued');
  const recentDone = tasks.filter((t) => t.status === 'done').slice(0, 3);

  return html`
    <div class="page-head">
      <div><h1>대시보드</h1><p class="sub">AI 에이전트가 진행 중인 작업과 확인이 필요한 항목</p></div>
      <a class="btn btn-primary" href="#/new">${icon('plus')} 새 작업 지시</a>
    </div>
    <div class="kpis">
      ${kpi('진행 중', working.length, 'play', 'blue', '#/tasks?status=active', `대기 ${c.queued || 0} · 막힘 ${c.blocked || 0}`)}
      ${kpi('승인 대기', pend.length, 'bell', 'amber', '#/approvals', pend.length ? '확인이 필요합니다' : '대기 중인 요청 없음', pend.length > 0)}
      ${kpi('열린 문제', openIssues, 'alert', 'red', '#/tasks?filter=issues', `${tasks.filter((t) => t.open_issues).length}개 작업`)}
      ${kpi('완료', c.done || 0, 'check-circle', 'green', '#/tasks?status=done', `전체 작업 ${tasks.length}`)}
    </div>
    <div class="dash-grid">
      <div>
        <section class="card">
          <div class="card-head"><h2>${icon('bell')} 확인이 필요한 항목</h2>${pend.length ? html`<a href="#/approvals">승인 요청 전체</a>` : ''}</div>
          ${attention.length ? html`<div class="attn-list">${attention}</div>` : html`<div class="empty">${icon('check-circle')}<p>지금 확인할 항목이 없습니다</p></div>`}
        </section>
        <section class="section">
          <h2 class="section-title">진행 중인 작업 <span class="muted">${working.length}</span></h2>
          ${working.length ? html`<div class="task-grid">${working.map(taskCard)}</div>` : html`<div class="card empty">진행 중인 작업이 없습니다</div>`}
        </section>
        ${recentDone.length ? html`<section class="section">
          <h2 class="section-title">최근 완료 <span class="muted"><a href="#/tasks?status=done">전체 보기</a></span></h2>
          <div class="task-grid">${recentDone.map(taskCard)}</div>
        </section>` : ''}
      </div>
      <section class="card">
        <div class="card-head"><h2>${icon('activity')} 최근 활동</h2><a href="#/activity">전체 보기</a></div>
        <ul class="feed">${idx.activity.slice(0, 16).map(feedItem)}</ul>
      </section>
    </div>`;
}

function viewWelcome() {
  return html`
    <div class="page-head"><div><h1>대시보드</h1><p class="sub">AI 에이전트의 작업 진행 현황</p></div>
      <a class="btn btn-primary" href="#/new">${icon('plus')} 새 작업 지시</a></div>
    <section class="card welcome">
      <h2>아직 기록된 작업이 없습니다</h2>
      <p class="muted">Claude 에게 일을 맡기면, 무엇을 하고 있는지 · 문제 · 결과물 · 승인 요청이 이 화면에 실시간으로 표시됩니다.</p>
      <ol>
        <li><b>처음 한 번:</b> Claude 가 DevTrack 에 기록하도록 설정합니다 — <a href="#/guide/s5">사용 방법 5단계</a></li>
        <li><b>작업 맡기기:</b> Claude 채팅으로 지시하거나, <a href="#/new">새 작업 지시</a> 로 웹에서 지시합니다 — <a href="#/guide/s6">6단계</a></li>
        <li><b>확인 · 승인:</b> 진행 상황을 보고, 승인 요청에 승인 / 수정 요청 / 거절로 답합니다 — <a href="#/guide/s8">8단계</a></li>
      </ol>
      <div class="welcome-actions">
        <a class="btn btn-primary" href="#/guide">${icon('book')} 사용 방법 보기 (처음부터 따라 하기)</a>
        <a class="btn" href="#/new">${icon('plus')} 새 작업 지시</a>
      </div>
    </section>`;
}

// ====================================================================== 화면: 사용 방법
async function viewGuide({ rest }) {
  if (state.guide == null) {
    try {
      const r = await fetch('guide.md', { cache: 'no-cache' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      state.guide = await r.text();
    } catch (e) {
      return html`<div class="page-head"><div><h1>사용 방법</h1></div></div>
        <div class="card empty">${icon('alert')}<p>사용 방법 문서(guide.md)를 불러오지 못했습니다 — ${e.message}</p></div>`;
    }
  }
  const body = state.guide.replace(/^# [^\n]*\n/, ''); // 문서 제목은 페이지 제목으로
  const modeNote = state.mode === 'live'
    ? html`<div class="alert alert-ok">${icon('check-circle')}<div class="alert-body"><b>지금 이 화면은 실시간 · 로컬 서버 모드입니다.</b>
        <p>웹에서 누른 승인 · 메시지 · 새 작업 지시가 바로 저장됩니다. 대시보드가 이미 실행 중이니(1 · 2 · 4단계 완료), 3단계(PATH)와 5단계부터 확인하세요.</p></div></div>`
    : html`<div class="alert alert-info">${icon('info')}<div class="alert-body"><b>지금 이 화면은 정적 · 읽기 전용 모드입니다.</b>
        <p>버튼을 누르면 Claude 에게 보낼 메시지가 만들어집니다 (10단계 참고).</p></div></div>`;
  return html`
    <div class="page-head"><div><h1>사용 방법</h1><p class="sub">처음 쓰는 분을 위한 단계별 안내 — 위에서부터 차례로 따라 하세요</p></div>
      <a class="btn" href="guide.md" target="_blank" rel="noopener">${icon('file-text')} 원문 (Markdown)</a></div>
    <div class="guide-layout">
      <details class="card guide-toc" open><summary>목차</summary><div class="toc-list"></div></details>
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
    b.innerHTML = `${icon('copy')} 복사`;
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
      <div><h1>작업</h1><p class="sub">지시된 모든 작업과 진행 상태</p></div>
      <a class="btn btn-primary" href="#/new">${icon('plus')} 새 작업 지시</a>
    </div>
    <div class="filter-bar">
      <div class="chips">
        ${chip('all', '전체', idx.tasks.length)}
        ${chip('active', '활성', count((t) => ACTIVE.includes(t.status)))}
        ${['in_progress', 'awaiting_approval', 'changes_requested', 'blocked', 'queued', 'done'].map((s) => chip(s, STATUS[s].label, count((t) => t.status === s)))}
      </div>
    </div>
    <div class="toolbar">
      <label class="search"><span class="sr-only">검색</span>${icon('search')}<input id="task-search" type="search" placeholder="제목, ID, 에이전트 검색"></label>
      <select data-change="type-filter" style="width:auto" aria-label="유형">
        <option value="all">모든 유형</option>
        ${Object.entries(TYPES).map(([k, v]) => html`<option value="${k}" ${type === k ? raw('selected') : ''}>${v.label}</option>`)}
      </select>
      ${q.filter === 'issues' ? html`<a class="chip active" href="${tasksHref({ ...q, filter: '' })}">열린 문제 있음 ${icon('x')}</a>` : ''}
    </div>
    ${tasks.length ? html`<div class="table-wrap"><table class="data">
      <thead><tr><th>작업</th><th>유형</th><th>상태</th><th>진행률</th><th>에이전트</th><th>확인 필요</th><th>업데이트</th></tr></thead>
      <tbody>${tasks.map((t) => html`<tr data-href="#/task/${t.id}" data-search="${[t.id, t.title, t.agent, TYPES[t.type]?.label, t.current_step, (t.tags || []).join(' ')].filter(Boolean).join(' ').toLowerCase()}">
        <td><div class="t-title"><span class="muted mono" style="font-weight:500;margin-right:6px">${t.id}</span>${t.title}</div><div class="t-step">${taskNow(t).text}</div></td>
        <td class="t-type">${typeBadge(t.type)}</td>
        <td>${statusBadge(t.status)}</td>
        <td><div class="t-prog">${progressBar(t)}<span>${t.progress || 0}%</span></div></td>
        <td class="nowrap t-agent">${t.agent}</td>
        <td class="nowrap">${t.pending_approvals ? html`<span class="flag amber" style="font-weight:600">${icon('bell')} 승인 ${t.pending_approvals}</span> ` : ''}${t.open_issues ? html`<span class="flag ${t.max_severity >= 2 ? 'red' : 'orange'}" style="font-weight:600">${icon('alert')} 문제 ${t.open_issues}</span>` : ''}${!t.pending_approvals && !t.open_issues ? html`<span class="muted">—</span>` : ''}</td>
        <td class="nowrap muted">${ago(t.updated_at)}</td>
      </tr>`)}</tbody></table></div>
      <div id="no-match" class="empty" hidden>검색 결과가 없습니다</div>`
    : html`<div class="card empty">${icon('inbox')}<p>조건에 맞는 작업이 없습니다</p></div>`}`;
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
    in_progress: { label: '지금 하는 일', pulse: true },
    awaiting_approval: { label: '사용자 승인 대기 중', pulse: false },
    changes_requested: { label: '수정 요청 반영 대기', pulse: false },
    blocked: { label: '막힘 — 확인 필요', pulse: false },
    queued: { label: '에이전트가 아직 시작하지 않음', pulse: false },
    done: { label: '완료', pulse: false },
    cancelled: { label: '취소됨', pulse: false },
  }[t.status] || { label: '현재 상태' };
  const pending = (t.approvals || []).filter((a) => a.status === 'pending');
  let text = t.status === 'done' ? (lastMsg || '작업이 완료되었습니다') : (t.current_step || lastMsg || '—');
  if (t.status === 'awaiting_approval' && pending.length) text = pending[0].title + (pending.length > 1 ? ` 외 ${pending.length - 1}건` : '');
  const plan = t.plan || [];
  return html`<section class="card now-card">
    <div><div class="now-label">${cfg.pulse ? raw('<span class="pulse"></span>') : icon(STATUS[t.status]?.icon || 'circle')} ${cfg.label}</div><div class="now-text">${text}</div></div>
    <div class="now-prog"><div class="row"><span>${plan.length ? `계획 ${plan.filter((p) => p.done).length}/${plan.length}단계` : '진행률'}</span><b>${t.progress || 0}%</b></div>${progressBar(t, true)}</div>
  </section>`;
}

async function viewTask({ rest }) {
  const [id, tabArg] = rest;
  const t = await loadTask(id);
  const pend = t.approvals.filter((a) => a.status === 'pending');
  const openIssues = t.issues.filter((i) => i.status === 'open');
  const tab = tabArg || 'overview';
  const repo = repoLabel(t.repo);
  const tabs = [
    ['overview', '개요'],
    ['log', '진행 기록', t.log.length],
    ['issues', '문제점', openIssues.length || null, openIssues.length > 0],
    ['artifacts', '결과물', t.artifacts.length],
    ...(t.builds?.length ? [['builds', '빌드', t.builds.length, t.builds[t.builds.length - 1].status !== 'ok']] : []),
    ['approvals', '승인', pend.length || t.approvals.length || null, pend.length > 0],
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
    <div class="crumbs"><a href="#/${sec}">${SECTION_LABEL[sec]}</a><span>/</span><span class="mono">${t.id}</span></div>
    <div class="task-head"><div style="min-width:0;flex:1">
      <div class="th-badges">${typeBadge(t.type)}${statusBadge(t.status)}${t.priority === 'high' ? html`<span class="badge tone-red">우선순위 높음</span>` : ''}${(t.tags || []).map((g) => html`<span class="badge">#${g}</span>`)}</div>
      <h1><span class="th-id">${t.id}</span>${t.title}</h1>
      <div class="th-meta">
        <span>${icon('cpu')} ${t.agent}</span>
        ${repo ? html`<span>${icon('folder')} ${repo.href ? html`<a href="${repo.href}" target="_blank" rel="noopener">${repo.text}</a>` : repo.text}</span>` : ''}
        ${t.repo?.branch ? html`<span>${icon('branch')} ${t.repo.branch}</span>` : ''}
        <span>${icon('clock')} 시작 ${fmtAbs(t.created_at)}</span>
        <span>업데이트 ${ago(t.updated_at)}</span>
      </div>
      ${links.length ? html`<div class="th-links">${icon('link')} 관련 작업 ${links.map((l) => html`<a class="link-chip" href="#/task/${l.id}"><span class="sdot tone-${pick(STATUS, l.status, 'queued').tone}"></span>${l.id} · ${pick(TYPES, l.type).label} · ${l.title}</a>`)}</div>` : ''}
    </div></div>
    ${pend.length && tab !== 'approvals' ? html`<div class="alert alert-amber">${icon('bell')}<div class="alert-body"><b>승인이 필요한 항목 ${pend.length}건</b><p>${pend.map((a) => a.title).join(' · ')}</p></div><a class="btn btn-sm" href="#/task/${t.id}/approvals">확인하기</a></div>` : ''}
    ${nowCard(t)}
    <nav class="tabs">${tabs.map(([k, label, n, hot]) => html`<a class="${tab === k ? 'active' : ''}" href="#/task/${t.id}${k === 'overview' ? '' : '/' + k}">${label}${n != null ? html`<span class="n ${hot ? 'hot' : ''}">${n}</span>` : ''}</a>`)}</nav>
    ${body}`;
}

function taskOverview(t, openIssues) {
  const plan = t.plan || [];
  const recent = [...t.log].reverse().slice(0, 8);
  const arts = [...t.artifacts].reverse().slice(0, 6);
  return html`<div class="detail-grid">
    <div>
      ${reviewPanel(t)}
      <section class="card">
        <div class="card-head"><h2>${icon('message')} 작업 지시</h2><span class="muted" style="font-size:12.5px">${authorName(t.created_by)} · ${fmtAbs(t.created_at)}</span></div>
        <div class="card-body">${t.instruction ? html`<div class="markdown-body">${raw(renderMarkdown(t.instruction))}</div>` : html`<div class="muted">지시 내용이 기록되지 않았습니다</div>`}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>${icon('activity')} 최근 진행 기록</h2><a href="#/task/${t.id}/log">전체 ${t.log.length}건</a></div>
        <div class="card-body"><ul class="timeline">${recent.map((e) => timelineItem(e))}</ul></div>
      </section>
      ${feedbackBox(t)}
    </div>
    <div>
      ${t.builds?.length ? html`<section class="card">
        <div class="card-head"><h2>${icon('file-text')} 컴파일된 PDF</h2><a href="#/task/${t.id}/builds">빌드 기록 ${t.builds.length}</a></div>
        <div class="card-body">${buildBox(t)}</div>
      </section>` : ''}
      <section class="card">
        <div class="card-head"><h2>${icon('list')} 작업 계획</h2><span class="muted">${plan.filter((p) => p.done).length}/${plan.length}</span></div>
        <div class="card-body">${planList(t)}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>${icon('alert')} 열린 문제</h2><a href="#/task/${t.id}/issues">전체 ${t.issues.length}건</a></div>
        ${openIssues.length ? openIssues.map(issueItem) : html`<div class="empty inline" style="padding:14px 18px">열린 문제가 없습니다</div>`}
      </section>
      <section class="card">
        <div class="card-head"><h2>${icon('package')} 결과물</h2><a href="#/task/${t.id}/artifacts">전체 ${t.artifacts.length}개</a></div>
        ${arts.length ? arts.map((a) => artRow(t.id, a)) : html`<div class="empty inline" style="padding:14px 18px">아직 결과물이 없습니다</div>`}
      </section>
    </div>
  </div>`;
}

function taskLog(t) {
  const items = [...t.log].reverse();
  return html`<div class="detail-grid">
    <div><section class="card"><div class="card-body"><ul class="timeline">${items.map((e) => timelineItem(e))}</ul></div></section></div>
    <div>${feedbackBox(t)}</div>
  </div>`;
}

function taskIssues(t) {
  const open = t.issues.filter((i) => i.status === 'open');
  const done = t.issues.filter((i) => i.status !== 'open');
  if (!t.issues.length) return html`<div class="card empty">${icon('check-circle')}<p>기록된 문제가 없습니다</p></div>`;
  return html`
    <h2 class="section-title">열린 문제 <span class="muted">${open.length}</span></h2>
    <div class="card">${open.length ? open.map(issueItem) : html`<div class="empty inline" style="padding:14px 18px">열린 문제가 없습니다</div>`}</div>
    ${done.length ? html`<h2 class="section-title" style="margin-top:24px">해결된 문제 <span class="muted">${done.length}</span></h2><div class="card">${done.map(issueItem)}</div>` : ''}`;
}

function taskArtifacts(t) {
  if (!t.artifacts.length) return html`<div class="card empty">${icon('package')}<p>아직 결과물이 없습니다</p></div>`;
  const items = [...t.artifacts].reverse().map((a) => ({ ...a, task_id: t.id }));
  return html`<div class="art-grid">${items.map(artCard)}</div>`;
}

function taskApprovals(t) {
  if (!t.approvals.length) return html`<div class="card empty">${icon('bell')}<p>승인 요청이 없습니다</p></div>`;
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
    <div class="page-head"><div><h1>승인 요청</h1><p class="sub">에이전트가 다음 단계(커밋·푸시, 계획 진행 등) 전에 확인을 요청한 항목</p></div></div>
    ${staticBanner()}
    <h2 class="section-title">결정 대기 <span class="muted">${pend.length}</span></h2>
    ${pend.length ? pend.map((n) => {
      const t = state.tasks.get(n.task_id);
      const ap = t?.approvals.find((a) => a.id === n.approval_id);
      return ap ? approvalCard(t, ap, { showTask: true }) : '';
    }) : html`<div class="card empty">${icon('check-circle')}<p>결정을 기다리는 요청이 없습니다</p></div>`}
    <h2 class="section-title" style="margin-top:28px">처리된 요청 <span class="muted">${history.length}</span></h2>
    ${history.length ? html`<div class="card">${history.map((a) => {
      const st = pick(APPROVAL_STATUS, a.status, 'pending');
      return html`<a class="art-row" href="#/task/${a.task_id}/approvals">
        <span class="ic tone-${st.tone}">${icon(st.icon)}</span>
        <div class="body"><div class="title">${a.title}</div>
          <div class="meta">${st.label} · ${a.task_id} ${a.task_title} · ${ago(a.decided_at)}${a.commit ? ` · 커밋 ${a.commit.slice(0, 8)}` : ''}${a.comment ? ` · "${a.comment.slice(0, 60)}${a.comment.length > 60 ? '…' : ''}"` : ''}</div></div>
        ${icon('chev')}</a>`;
    })}</div>` : html`<div class="card empty inline" style="padding:16px 18px">아직 처리된 요청이 없습니다</div>`}`;
}

// ====================================================================== 화면: 리뷰
function viewerHead(t, art) {
  const k = pick(ART_KIND, art.kind, 'file');
  const url = artifactUrl(t.id, art);
  return html`<div class="viewer-head">
    <div style="min-width:0"><h2>${art.title}</h2>
      <div class="meta"><span class="badge">${icon(k.icon)}${k.label}</span>${art.category === 'review' ? html`<span class="badge tone-violet">리뷰</span>` : ''}<a href="#/task/${t.id}">${t.id} · ${t.title}</a><span>${authorName(art.author)} · ${ago(art.created_at)}</span>${art.size ? html`<span>${fmtBytes(art.size)}</span>` : ''}</div>
    </div>
    <div class="actions">${url ? html`<a class="btn btn-sm" href="${url}" target="_blank" rel="noopener">${icon('external')} 새 탭</a>${art.path ? html`<a class="btn btn-sm" href="${url}" download>${icon('download')} 다운로드</a>` : ''}` : ''}</div>
  </div>`;
}

async function viewReviews({ rest }) {
  const reviews = state.index.reviews;
  const head = html`<div class="page-head"><div><h1>코드 리뷰</h1><p class="sub">AI가 작성한 리뷰 리포트 (Markdown · PDF)</p></div></div>`;
  if (!reviews.length) {
    return html`${head}<div class="card empty">${icon('eye')}<p>등록된 리뷰 리포트가 없습니다</p><p><code>devtrack review T-0001 review_report.md</code> 로 등록하면 여기에 표시됩니다</p></div>`;
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
          <div class="meta"><span class="badge">${pick(ART_KIND, r.kind, 'file').label}</span><span>${r.task_id}</span>${ago(r.created_at)}</div>
          <div class="meta">${r.task_title}</div>
          ${rs?.total ? html`<div class="meta">${rs.open ? sevChips(rs.by_severity) : html`<span class="badge tone-green">모두 처리됨</span>`}</div>` : ''}
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
        ${feedbackBox(t, '리뷰에 대한 의견 보내기')}
      </div>
    </div>`;
}

// ====================================================================== 화면: 분야 섹션 (논문 작성 / 데이터 분석)
function sectionHead(title, sub, newHref) {
  return html`<div class="page-head"><div><h1>${title}</h1><p class="sub">${sub}</p></div>
    <a class="btn btn-primary" href="${newHref}">${icon('plus')} 새 작업 지시</a></div>`;
}

function sectionStats(tasks, arts) {
  const n = (f) => tasks.filter(f).length;
  const pend = tasks.reduce((s, t) => s + (t.pending_approvals || 0), 0);
  const blocked = n((t) => t.status === 'blocked');
  const st = (v, label, cls = '') => html`<div class="st ${cls}"><b>${v}</b><span>${label}</span></div>`;
  return html`<div class="sec-stats">
    ${st(n((t) => ['in_progress', 'changes_requested'].includes(t.status)), '진행 중')}
    ${st(pend, '승인 대기', pend ? 'hot' : '')}
    ${st(blocked, '막힘', blocked ? 'bad' : '')}
    ${st(n((t) => t.status === 'queued'), '대기')}
    ${st(n((t) => t.status === 'done'), '완료')}
    ${st(arts.length, '결과물')}
  </div>`;
}

function sectionAttention(ids) {
  const pend = state.index.notifications.filter((n) => ids.has(n.task_id));
  if (!pend.length) return '';
  return html`<section class="card" style="margin-bottom:20px">
    <div class="card-head"><h2>${icon('bell')} 확인이 필요한 항목</h2></div>
    <div class="attn-list">${pend.map(attnApproval)}</div></section>`;
}

function sectionFeed(ids, n = 14) {
  const items = state.index.activity.filter((e) => ids.has(e.task_id)).slice(0, n);
  return html`<section class="card">
    <div class="card-head"><h2>${icon('activity')} 최근 진행</h2><a href="#/activity">전체 기록</a></div>
    ${items.length ? html`<ul class="feed">${items.map(feedItem)}</ul>` : html`<div class="empty">아직 기록이 없습니다</div>`}
  </section>`;
}

const DRAFT_KINDS = ['latex', 'pdf', 'markdown', 'text'];
const isBuildLog = (a) => !!a.build && a.kind !== 'pdf';
const isDraft = (a) => DRAFT_KINDS.includes(a.kind) && !isBuildLog(a);
const PAPER_GROUPS = {
  all: { label: '전체', test: () => true },
  pdf: { label: '컴파일된 PDF', test: (a) => a.kind === 'pdf' && !!a.build },
  draft: { label: '원고', test: isDraft },
  figure: { label: '그림', test: (a) => FIGURE_KINDS.includes(a.kind) },
  data: { label: '표 · 데이터', test: (a) => ['csv', 'notebook'].includes(a.kind) },
  other: { label: '기타', test: (a) => isBuildLog(a) || ![...DRAFT_KINDS, ...FIGURE_KINDS, 'csv', 'notebook'].includes(a.kind) },
};

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
  if (!e) return '컴파일 실패';
  return `${e.message}${e.line ? ` (${e.file || baseName(b.source)}:${e.line})` : ''}`;
}

function warnChips(w = {}) {
  const items = [];
  (w.undefined_citations || []).forEach((c) => items.push(html`<span class="wchip">인용 없음 <code>${c}</code></span>`));
  (w.undefined_references || []).forEach((r) => items.push(html`<span class="wchip">참조 없음 <code>${r}</code></span>`));
  if (w.overfull) items.push(html`<span class="wchip">Overfull box ${w.overfull}</span>`);
  (w.other || []).slice(0, 4).forEach((o) => items.push(html`<span class="wchip">${o.replace(/^LaTeX Warning:\s*/, '')}</span>`));
  return html`<div class="wchips">${items.length ? items : html`<span class="wchip ok">${icon('check')} 경고 없음</span>`}</div>`;
}

/** 논문 카드 / 작업 개요에 쓰는 '최신 PDF' 상자 */
function buildBox(t) {
  const info = buildInfo(t);
  if (!info) return html`<div class="muted mini-empty">아직 컴파일된 PDF 가 없습니다 <span class="mono">(devtrack latex)</span></div>`;
  const { last, ok } = info;
  const failed = last.status !== 'ok';
  return html`<div class="build-box">
    ${failed ? html`<a class="build-fail" href="#/task/${t.id}/builds">${icon('x-circle')}<span><b>최근 컴파일 실패</b> · 빌드 #${bnum(last)} · ${buildErrText(last)} · ${ago(last.created_at)}</span></a>` : ''}
    ${ok ? html`<a class="pdf-row" href="#/artifact/${t.id}/${ok.pdf}">
      <span class="pdf-ic">PDF</span>
      <div class="body"><div class="title">${baseName(ok.source).replace(/\.tex$/, '')}.pdf <span class="muted">${failed ? '마지막 성공 · ' : ''}빌드 #${bnum(ok)}</span></div>
        <div class="meta">${ok.pages ? `${ok.pages}쪽 · ` : ''}${warnCount(ok.warnings) ? html`<span class="warn-t">경고 ${warnCount(ok.warnings)}</span>` : '경고 없음'} · ${ago(ok.created_at)}</div></div>
      <span class="btn btn-sm btn-primary">${icon('eye')} PDF 보기</span></a>` : html`<div class="muted mini-empty">성공한 빌드가 아직 없습니다</div>`}
  </div>`;
}

function buildRow(t, b, { actions = true } = {}) {
  const ok = b.status === 'ok';
  const meta = [b.engine, b.pages ? `${b.pages}쪽` : '', b.duration != null ? `${b.duration}초` : '', authorName(b.author)].filter(Boolean).join(' · ');
  return html`<div class="build-row">
    <span class="ic tone-${ok ? 'green' : 'red'}">${icon(ok ? 'check' : 'x')}</span>
    <div class="body">
      <div class="title">빌드 #${bnum(b)} <span class="badge tone-${ok ? 'green' : 'red'}">${ok ? '성공' : '실패'}</span><span class="mono muted">${b.source}</span></div>
      <div class="meta">${meta} · ${ago(b.created_at)}</div>
      ${!ok && b.errors?.length ? html`<ul class="build-errors">${b.errors.map((e) => html`<li><code>${e.file || baseName(b.source)}${e.line ? `:${e.line}` : ''}</code> ${e.message}</li>`)}</ul>` : ''}
      ${ok ? warnChips(b.warnings) : ''}
    </div>
    ${actions ? html`<div class="actions">${b.pdf ? html`<a class="btn btn-sm btn-primary" href="#/artifact/${t.id}/${b.pdf}">${icon('eye')} PDF</a>` : ''}${b.log ? html`<a class="btn btn-sm" href="#/artifact/${t.id}/${b.log}">${icon('file')} 로그</a>` : ''}</div>` : ''}
  </div>`;
}

/** PDF/로그 뷰어 위에 붙는 빌드 정보 + 다른 빌드로 이동 */
function buildPanel(t, bid) {
  const b = (t.builds || []).find((x) => x.id === bid);
  if (!b) return '';
  const all = (t.builds || []).filter((x) => x.pdf || x.log);
  return html`<div class="build-panel">
    ${buildRow(t, b, { actions: false })}
    ${all.length > 1 ? html`<div class="bp-hist"><span class="muted">다른 빌드</span>${[...all].reverse().map((x) => html`<a class="bchip ${x.id === bid ? 'active' : ''} ${x.status}" href="#/artifact/${t.id}/${x.pdf || x.log}">${icon(x.status === 'ok' ? 'check' : 'x')}#${bnum(x)}</a>`)}</div>` : ''}
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
        <div class="th-badges">${statusBadge(t.status)}${s.pending_approvals ? html`<span class="badge tone-amber">${icon('bell')}승인 ${s.pending_approvals}</span>` : ''}${(t.tags || []).map((g) => html`<span class="badge">#${g}</span>`)}</div>
        <h3><a href="#/task/${t.id}"><span class="th-id">${t.id}</span>${t.title}</a></h3>
        ${now.text ? html`<div class="wc-now"><span class="lbl">${now.label}</span>${now.text}</div>` : ''}
        <div class="wc-meta">${t.agent} · 업데이트 ${ago(t.updated_at)}</div>
      </div>
      <div class="wc-prog"><div class="row"><span>${plan.length ? `작성 계획 ${plan.filter((p) => p.done).length}/${plan.length}` : '진행률'}</span><b>${t.progress || 0}%</b></div>${progressBar(t, true)}</div>
    </div>
    <div class="wc-body">
      <div>
        <div class="wc-label"><span>원고 구성 · 작성 계획</span></div>
        ${planList(t)}
      </div>
      <div>
        <div class="wc-label"><span>컴파일된 PDF</span>${t.builds?.length ? html`<a href="#/task/${t.id}/builds">빌드 기록 ${t.builds.length}</a>` : ''}</div>
        ${buildBox(t)}
        <div class="wc-label"><span>원고 파일</span>${drafts.length ? html`<a href="#/task/${t.id}/artifacts">전체 결과물</a>` : ''}</div>
        ${drafts.length ? drafts.map((a) => html`<a class="mini-row" href="#/artifact/${t.id}/${a.id}">${icon(pick(ART_KIND, a.kind, 'file').icon)}<span class="t">${a.title}</span>${a.kind === 'latex' ? html`<span class="badge tone-teal">문서 보기</span>` : ''}<span class="muted nowrap">${ago(a.created_at)}</span></a>`) : html`<div class="muted mini-empty">아직 원고가 없습니다</div>`}
        <div class="wc-label"><span>그림 ${figs.length}</span></div>
        ${figs.length ? html`<div class="thumbs">${figs.slice(0, 8).map((a) => html`<a class="thumb-sm" href="#/artifact/${t.id}/${a.id}" title="${a.title}"><img src="${artifactUrl(t.id, a)}" alt="${a.title}" loading="lazy"></a>`)}</div>` : html`<div class="muted mini-empty">아직 그림이 없습니다</div>`}
        <div class="wc-label"><span>연결된 데이터 분석</span><a href="#/new?type=analysis&related=${t.id}">${icon('plus')} 이 논문용 분석 지시</a></div>
        ${links.length ? html`<div>${links.map((l) => html`<a class="link-chip" href="#/task/${l.id}" title="${pick(STATUS, l.status, 'queued').label}">${sdot(l.status)}${l.id} ${l.title}</a>`)}</div>` : html`<div class="muted mini-empty">연결된 분석이 없습니다</div>`}
        ${issues.length ? html`<div class="wc-label"><span>열린 문제 ${issues.length}</span></div>${issues.slice(0, 3).map((i) => html`<a class="mini-row" href="#/task/${t.id}/issues"><span class="sev-dot sev-${i.severity}"></span><span class="t">${i.title}</span></a>`)}` : ''}
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
    ${sectionHead('논문 작성', '논문 원고 작성 과정과 원고 · 그림 · 표 등 결과물', '#/new?type=paper')}
    ${sectionStats(papers, arts)}
    ${sectionAttention(ids)}
    <div class="dash-grid">
      <div>
        <h2 class="section-title">작성 중인 논문 <span class="muted">${active.length}</span></h2>
        ${active.length ? active.map((s) => paperCard(state.tasks.get(s.id), s)) : html`<div class="card empty">${icon('book')}<p>작성 중인 논문이 없습니다</p><p><a href="#/new?type=paper">논문 작성 지시하기</a></p></div>`}
        ${finished.length ? html`<h2 class="section-title" style="margin-top:24px">완료된 논문 작업 <span class="muted">${finished.length}</span></h2><div class="task-grid">${finished.map((t) => taskCard(t, { thumbs: true }))}</div>` : ''}
        <h2 class="section-title" style="margin-top:24px">논문 결과물 <span class="muted">${arts.length}</span></h2>
        <div class="chips" style="margin-bottom:12px">${Object.entries(PAPER_GROUPS).map(([k, v]) => html`<a class="chip ${g === k ? 'active' : ''}" href="#/papers${k === 'all' ? '' : '?kind=' + k}">${v.label} <span class="n">${arts.filter(v.test).length}</span></a>`)}</div>
        ${shown.length ? html`<div class="art-grid">${shown.map(artCard)}</div>` : html`<div class="card empty inline" style="padding:16px 18px">해당하는 결과물이 없습니다</div>`}
      </div>
      ${sectionFeed(ids)}
    </div>`;
}

const ANALYSIS_FILTERS = {
  all: { label: '전체', test: () => true },
  analysis: { label: '데이터 분석', test: (t) => t.type === 'analysis' },
  visualization: { label: '시각화', test: (t) => t.type === 'visualization' },
  paper: { label: '논문용', test: (t) => linksOf(t.id).some((x) => x.type === 'paper') },
  active: { label: '활성', test: (t) => ACTIVE.includes(t.status) },
  done: { label: '완료', test: (t) => t.status === 'done' },
};

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
    ${sectionHead('데이터 분석', '데이터 · 그림 분석, 데이터 가시화, 논문에 쓰일 분석의 진행 과정과 결과물', '#/new?type=analysis')}
    ${sectionStats(all, idx.artifacts.filter((a) => allIds.has(a.task_id)))}
    ${sectionAttention(allIds)}
    <div class="chips" style="margin-bottom:16px">${Object.entries(ANALYSIS_FILTERS).map(([k, v]) => html`<a class="chip ${f === k ? 'active' : ''}" href="#/analysis${k === 'all' ? '' : '?f=' + k}">${v.label} <span class="n">${all.filter(v.test).length}</span></a>`)}</div>
    <div class="dash-grid">
      <div>
        <h2 class="section-title">분석 작업 <span class="muted">${tasks.length}</span></h2>
        ${tasks.length ? html`<div class="task-grid">${tasks.map((t) => taskCard(t, { thumbs: true, links: true }))}</div>` : html`<div class="card empty">${icon('bar')}<p>해당하는 분석 작업이 없습니다</p><p><a href="#/new?type=analysis">데이터 분석 지시하기</a></p></div>`}
        <h2 class="section-title" style="margin-top:24px">그림 · 시각화 <span class="muted">${figs.length}</span></h2>
        ${figs.length ? html`<div class="art-grid gallery">${figs.map(artCard)}</div>` : html`<div class="card empty inline" style="padding:16px 18px">아직 그림이 없습니다</div>`}
        <h2 class="section-title" style="margin-top:24px">데이터 · 노트북 · 리포트 <span class="muted">${others.length}</span></h2>
        ${others.length ? html`<div class="card">${others.map((a) => artRow(a.task_id, a, { showTask: true }))}</div>` : html`<div class="card empty inline" style="padding:16px 18px">아직 결과물이 없습니다</div>`}
      </div>
      ${sectionFeed(ids)}
    </div>`;
}

// ====================================================================== 화면: 결과물
const ART_GROUPS = {
  all: { label: '전체', test: () => true },
  doc: { label: '문서', test: (a) => ['markdown', 'pdf', 'text', 'latex'].includes(a.kind) },
  code: { label: '코드 · 변경사항', test: (a) => ['code', 'diff'].includes(a.kind) },
  figure: { label: '그림 · 시각화', test: (a) => ['image', 'html'].includes(a.kind) },
  data: { label: '데이터 · 노트북', test: (a) => ['csv', 'notebook'].includes(a.kind) },
  review: { label: '리뷰', test: (a) => a.category === 'review' },
  link: { label: '링크 · 기타', test: (a) => ['link', 'file'].includes(a.kind) },
};

function viewArtifacts({ q }) {
  const all = state.index.artifacts;
  const g = ART_GROUPS[q.kind] ? q.kind : 'all';
  const items = all.filter(ART_GROUPS[g].test);
  return html`
    <div class="page-head"><div><h1>결과물</h1><p class="sub">코드 변경, 리포트, 그림, 데이터 등 에이전트가 만든 모든 결과물</p></div></div>
    <div class="filter-bar"><div class="chips">${Object.entries(ART_GROUPS).map(([k, v]) => html`<a class="chip ${g === k ? 'active' : ''}" href="#/artifacts${k === 'all' ? '' : '?kind=' + k}">${v.label} <span class="n">${all.filter(v.test).length}</span></a>`)}</div></div>
    ${items.length ? html`<div class="art-grid">${items.map(artCard)}</div>` : html`<div class="card empty">${icon('package')}<p>결과물이 없습니다</p></div>`}`;
}

async function viewArtifact({ rest }) {
  const [tid, fid] = rest;
  const t = await loadTask(tid);
  const art = t.artifacts.find((a) => a.id === fid);
  if (!art) return viewNotFound();
  const linked = t.approvals.filter((a) => (a.artifacts || []).includes(art.id));
  return html`
    <div class="crumbs"><a href="#/${SECTION_OF_TYPE[t.type] || 'tasks'}">${SECTION_LABEL[SECTION_OF_TYPE[t.type] || 'tasks']}</a><span>/</span><a href="#/task/${t.id}">${t.id}</a><span>/</span><a href="#/task/${t.id}/artifacts">결과물</a><span>/</span><span class="mono">${art.id}</span></div>
    <section class="card" style="overflow:hidden">
      ${viewerHead(t, art)}
      ${art.build ? buildPanel(t, art.build) : ''}
      ${art.description ? html`<div class="card-body markdown-body compact" style="border-bottom:1px solid var(--border)">${raw(renderMarkdown(art.description))}</div>` : ''}
      <div data-av="${t.id}/${art.id}"></div>
    </section>
    ${linked.length ? html`<h2 class="section-title" style="margin-top:22px">연결된 승인 요청</h2>${linked.map((a) => approvalCard(t, a, { skip: [art.id] }))}` : ''}`;
}

// ====================================================================== 화면: 활동 기록
const ACT_FILTERS = {
  all: { label: '전체', test: () => true },
  step: { label: '진행', test: (e) => ['step', 'success', 'status', 'info'].includes(e.kind) },
  issue: { label: '문제 · 오류', test: (e) => ['issue', 'error', 'warning'].includes(e.kind) },
  approval: { label: '승인 · 결정', test: (e) => ['approval', 'decision'].includes(e.kind) },
  commit: { label: '커밋', test: (e) => e.kind === 'commit' },
  artifact: { label: '결과물', test: (e) => e.kind === 'artifact' },
  user: { label: '사용자', test: (e) => e.author === 'user' },
};

function viewActivity({ q }) {
  const f = ACT_FILTERS[q.f] ? q.f : 'all';
  const items = state.index.activity.filter(ACT_FILTERS[f].test);
  const groups = [];
  const today = new Date().toDateString();
  const yest = new Date(Date.now() - 86400000).toDateString();
  for (const e of items) {
    const d = new Date(e.ts);
    const key = d.toDateString();
    const label = key === today ? '오늘' : key === yest ? '어제' : d.toLocaleDateString('ko', { month: 'long', day: 'numeric', weekday: 'short' });
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, label, items: [] });
    groups[groups.length - 1].items.push(e);
  }
  return html`
    <div class="page-head"><div><h1>활동 기록</h1><p class="sub">모든 작업의 진행 기록 (최근 300건)</p></div></div>
    <div class="filter-bar"><div class="chips">${Object.entries(ACT_FILTERS).map(([k, v]) => html`<a class="chip ${f === k ? 'active' : ''}" href="#/activity${k === 'all' ? '' : '?f=' + k}">${v.label}</a>`)}</div></div>
    ${groups.length ? groups.map((g) => html`
      <h2 class="section-title" style="margin-top:18px">${g.label} <span class="muted">${g.items.length}</span></h2>
      <section class="card"><div class="card-body"><ul class="timeline">${g.items.map((e) => timelineItem(e, { taskLink: true }))}</ul></div></section>`)
    : html`<div class="card empty">기록이 없습니다</div>`}`;
}

// ====================================================================== 화면: 새 작업 지시
function viewNewTask({ q = {} } = {}) {
  const type = TYPES[q.type] ? q.type : 'code';
  const tasks = [...state.index.tasks].sort((a, b) => Number(b.type === 'paper') - Number(a.type === 'paper'));
  const relTask = tasks.find((t) => t.id === q.related);
  return html`
    <div class="crumbs"><a href="#/tasks">작업</a><span>/</span><span>새 작업 지시</span></div>
    <div class="page-head"><div><h1>새 작업 지시</h1><p class="sub">AI 에이전트에게 맡길 작업을 적어주세요. 에이전트는 진행 상황을 기록하고, 확인이 필요하면 알림으로 요청합니다.</p></div></div>
    ${state.mode !== 'live' ? html`<div class="alert alert-info">${icon('info')}<div class="alert-body"><b>정적 모드</b><p>등록을 누르면 Claude 세션에 붙여넣을 지시문이 만들어집니다.</p></div></div>` : ''}
    <form id="new-task-form" class="card card-pad" style="max-width:820px" autocomplete="off">
      <label class="field"><span>작업 제목 *</span><input type="text" name="title" required maxlength="200" placeholder="예: 논문 4장 실험 결과 그림 정리 및 LaTeX 표 작성"></label>
      <div class="field-row">
        <label class="field"><span>작업 유형</span><select name="type">${Object.entries(TYPES).map(([k, v]) => html`<option value="${k}" ${k === type ? raw('selected') : ''}>${v.label}</option>`)}</select></label>
        <label class="field"><span>담당 AI</span><select name="agent"><option>Claude</option><option>Codex</option><option>Gemini</option><option>기타</option></select></label>
        <label class="field"><span>우선순위</span><select name="priority"><option value="normal">보통</option><option value="high">높음</option><option value="low">낮음</option></select></label>
      </div>
      <div class="field-row">
        <label class="field"><span>대상 저장소 경로</span><input type="text" name="repo" placeholder="/ccs/home/user/project"><small>코드 작업일 때 결과를 커밋할 저장소</small></label>
        <label class="field"><span>브랜치</span><input type="text" name="branch" placeholder="(현재 브랜치)"></label>
      </div>
      <label class="field"><span>관련 작업 (선택)</span>
        <select name="related"><option value="">없음</option>${tasks.map((t) => html`<option value="${t.id}" ${t.id === q.related ? raw('selected') : ''}>${t.id} · ${pick(TYPES, t.type).label} · ${t.title}</option>`)}</select>
        <small>예: 논문에 쓸 데이터 분석이라면 해당 논문 작업을 선택하세요. 논문 작성 화면에 연결되어 표시됩니다.</small>
      </label>
      ${relTask ? html`<div class="alert alert-info" style="margin-bottom:14px">${icon('link')}<div class="alert-body"><b>${relTask.id} ${relTask.title}</b> 에 연결된 작업으로 등록됩니다.</div></div>` : ''}
      <label class="field"><span>지시 내용</span><textarea name="instruction" rows="9" placeholder="무엇을, 어떤 기준으로 해야 하는지 구체적으로 적어주세요. (markdown 지원)\n\n- 목표:\n- 참고 자료:\n- 완료 기준:\n- 승인이 필요한 시점:"></textarea></label>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <a class="btn btn-ghost" href="#/tasks">취소</a>
        <button class="btn btn-primary" type="submit">${icon('send')} ${state.mode === 'live' ? '작업 지시 등록' : '지시문 만들기'}</button>
      </div>
    </form>`;
}

async function submitNewTask(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.title = (data.title || '').trim();
  if (!data.title) return;
  const rel = data.related ? state.index.tasks.find((t) => t.id === data.related) : null;
  if (state.mode === 'live') {
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      const r = await api('api/tasks', { ...data, related: data.related ? [data.related] : [] });
      form.reset();
      toast({ title: '작업 지시를 등록했습니다', meta: `${r.task.id} — 에이전트가 devtrack inbox 로 확인합니다`, tone: 'green' });
      await poll({ force: true });
      location.hash = `#/task/${r.task.id}`;
    } catch (e) {
      toast({ title: '등록 실패', meta: e.message, tone: 'red' });
      btn.disabled = false;
    }
    return;
  }
  const typeLabel = TYPES[data.type]?.label || data.type;
  const cmd = [
    'devtrack new', shellQuote(data.title), '-t', data.type, '--agent', shellQuote(data.agent),
    data.priority !== 'normal' ? `--priority ${data.priority}` : '',
    data.repo ? `--repo ${shellQuote(data.repo)}` : '', data.branch ? `--branch ${shellQuote(data.branch)}` : '',
    data.related ? `--related ${data.related}` : '',
    data.instruction ? `-i ${shellQuote(data.instruction)}` : '',
  ].filter(Boolean).join(' ');
  const msg = [
    '[DevTrack 새 작업 지시]',
    `제목: ${data.title}`,
    `유형: ${typeLabel} (${data.type})`,
    data.repo ? `저장소: ${data.repo}${data.branch ? ` (브랜치 ${data.branch})` : ''}` : '',
    data.priority !== 'normal' ? `우선순위: ${data.priority === 'high' ? '높음' : '낮음'}` : '',
    rel ? `관련 작업: ${rel.id} ${rel.title} (devtrack new 에 --related ${rel.id})` : '',
    '',
    data.instruction ? `지시 내용:\n${data.instruction}` : '',
    '',
    'devtrack 으로 작업을 등록하고, 진행 단계·문제점·결과물을 기록하면서 진행해 주세요. 커밋/푸시 전에는 반드시 승인 요청을 올려 주세요.',
  ].filter((x, i, arr) => x !== '' || (arr[i - 1] !== '' && i > 0)).join('\n');
  openModal({
    title: 'Claude에게 보낼 작업 지시',
    body: html`<p>정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 지시문을 Claude 세션에 붙여넣으세요.</p>${copyBlock('Claude에게 보낼 메시지', msg)}${copyBlock('또는 터미널에서 직접 등록', cmd)}`,
  });
}

// ====================================================================== 화면: 오류
function viewNotFound() {
  return html`<div class="card empty">${icon('alert')}<p>페이지를 찾을 수 없습니다</p><p><a href="#/">대시보드로 이동</a></p></div>`;
}
function viewError(e) {
  return html`<div class="alert alert-red">${icon('alert')}<div class="alert-body"><b>화면을 그리는 중 오류가 발생했습니다</b><p>${e.message}</p></div></div>`;
}
function viewLoadError(e) {
  const file = location.protocol === 'file:';
  return html`<section class="card welcome">
    <h2>${icon('alert')} 데이터를 불러올 수 없습니다</h2>
    <p class="muted"><code>data/index.json</code> — ${e.message}</p>
    <ol>
      ${file ? html`<li><b>file:// 로 직접 연 경우 동작하지 않습니다.</b> 웹 서버로 열어주세요: <code>devtrack serve</code></li>` : ''}
      <li>로컬: <code>devtrack serve</code> 실행 후 <code>http://localhost:8765</code> 접속</li>
      <li>정적 호스팅(Frontier www): <code>devtrack publish --to ~/www/devtrack</code> 로 웹과 데이터를 함께 배포했는지 확인</li>
      <li>데이터가 아직 없다면 <code>devtrack init</code> 또는 첫 작업을 <code>devtrack new</code> 로 생성</li>
    </ol>
  </section>`;
}

// ====================================================================== 모달 / 복사
function copyBlock(label, text) {
  return html`<div class="copy-block"><div class="lbl">${label}</div><pre>${text}</pre><button class="btn btn-sm" data-action="copy" data-copy="${text}">${icon('copy')} 복사</button></div>`;
}

function openModal({ title, body }) {
  closeModal();
  const root = $('#modal-root');
  root.innerHTML = html`<div class="modal-back" data-action="modal-close-back"><div class="modal" role="dialog" aria-modal="true" aria-label="${title}">
    <div class="m-head"><h2>${title}</h2><button class="icon-btn" data-action="modal-close" aria-label="닫기">${icon('x')}</button></div>
    <div class="m-body">${body}</div>
    <div class="m-foot"><button class="btn" data-action="modal-close">닫기</button></div>
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
    toast({ title: '무엇을 수정해야 하는지 코멘트를 적어주세요', tone: 'orange' });
    ta?.focus();
    return;
  }
  const box = document.querySelector(`.choice-box[data-choices="${task}/${ap}"]`);
  const selected = box ? $$('input[type="checkbox"]', box).filter((i) => i.checked).map((i) => i.value) : null;
  if (box && decision === 'approve' && !selected.length) {
    toast({ title: '진행할 항목을 하나 이상 선택해주세요', tone: 'orange' });
    return;
  }
  if (state.mode !== 'live') {
    const t = state.tasks.get(task);
    const a = t?.approvals.find((x) => x.id === ap);
    const label = DECISION[decision].label;
    const sel = selected?.length ? selected : null;
    const cmd = `devtrack decide ${task} ${ap} ${decision}${sel ? ' --select ' + sel.join(' ') : ''}${comment ? ' -m ' + shellQuote(comment) : ''}`;
    const msg = `[DevTrack 결정] ${task} / ${ap} "${a?.title || ''}" → ${label}${sel ? `\n선택: ${sel.join(', ')}` : ''}${comment ? `\n코멘트: ${comment}` : ''}\n\n위 결정을 기록하고(${cmd}) 다음 단계를 진행해 주세요.`;
    openModal({
      title: `${label} — Claude에게 전달`,
      body: html`<p>이 페이지는 <b>정적 모드(읽기 전용)</b>라 웹에서 직접 저장할 수 없습니다. 아래 메시지를 Claude 세션에 붙여넣으면 결정이 기록되고 다음 단계가 진행됩니다.</p>${copyBlock('Claude에게 보낼 메시지', msg)}${copyBlock('또는 터미널에서 직접 기록', cmd)}`,
    });
    return;
  }
  const form = btn.closest('.decision-form');
  $$('button', form).forEach((b) => { b.disabled = true; });
  try {
    await api(`api/tasks/${task}/approvals/${ap}/decision`, selected ? { decision, comment, selected } : { decision, comment });
    if (ta) ta.value = '';
    toast({ title: `${DECISION[decision].label} 처리했습니다`, meta: `${task} ${ap} — 에이전트에게 전달됩니다`, tone: DECISION[decision].tone });
    await poll({ force: true });
  } catch (e) {
    toast({ title: '저장하지 못했습니다', meta: e.message, tone: 'red' });
    $$('button', form).forEach((b) => { b.disabled = false; });
  }
}

async function actComment(btn) {
  const task = btn.dataset.task;
  const ta = document.getElementById(`fb-${task}`);
  const message = ta?.value.trim();
  if (!message) { ta?.focus(); return; }
  if (state.mode !== 'live') {
    const cmd = `devtrack comment ${task} ${shellQuote(message)}`;
    const msg = `[DevTrack 메시지] ${task}\n${message}`;
    openModal({
      title: 'Claude에게 메시지 전달',
      body: html`<p>정적 모드에서는 웹에서 직접 저장할 수 없습니다. 아래 내용을 Claude 세션에 붙여넣으세요.</p>${copyBlock('Claude에게 보낼 메시지', msg)}${copyBlock('또는 터미널에서 직접 기록', cmd)}`,
    });
    return;
  }
  btn.disabled = true;
  try {
    await api(`api/tasks/${task}/comments`, { message });
    ta.value = '';
    toast({ title: '메시지를 보냈습니다', meta: `${task} — 에이전트가 devtrack inbox 로 확인합니다`, tone: 'green' });
    await poll({ force: true });
  } catch (e) {
    toast({ title: '보내지 못했습니다', meta: e.message, tone: 'red' });
    btn.disabled = false;
  }
}

function effectiveTheme() {
  return document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}
function syncThemeIcon() {
  $('#theme-btn').innerHTML = String(icon(effectiveTheme() === 'dark' ? 'sun' : 'moon'));
}

async function onAction(el, ev) {
  const a = el.dataset.action;
  switch (a) {
    case 'decide': return actDecide(el);
    case 'comment': return actComment(el);
    case 'choices': {
      const box = el.closest('.choice-box');
      $$('input[type="checkbox"]', box).forEach((i) => { i.checked = el.dataset.mode === 'all'; });
      box.dataset.dirty = '1';
      syncChoiceCount(box);
      return;
    }
    case 'copy': {
      const ok = await copyText(el.dataset.copy || '');
      const prev = el.innerHTML;
      el.innerHTML = ok ? String(icon('check')) + ' 복사됨' : '복사 실패';
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
        if (p === 'granted') toast({ title: '데스크톱 알림을 켰습니다', meta: '새 승인 요청이 오면 알려드립니다', tone: 'green' });
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
  document.addEventListener('change', (ev) => {
    const el = ev.target.closest('[data-change]');
    if (el?.dataset.change === 'type-filter') location.hash = tasksHref({ ...parseRoute().q, type: el.value });
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
  syncThemeIcon();
  bindEvents();
  state.mode = await detectMode();
  await poll({ force: true });
  setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
}

init();
