// 결과물 뷰어: markdown, pdf, 이미지, diff, 코드/LaTeX, CSV, Jupyter 노트북, HTML, 링크
import { esc, html, raw, icon, fmtBytes, LS } from './util.js';
import { latexToHtml } from './latex.js';
import { L } from './i18n.js';

const MAX_TEXT = 2 * 1024 * 1024;     // 2MB 이상 텍스트는 잘라서 표시
const MAX_HL_LINES = 6000;            // 이보다 긴 diff 는 하이라이트 생략

if (window.hljs) window.hljs.configure({ ignoreUnescapedHTML: true });
if (window.DOMPurify) {
  window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'A') {
      const href = node.getAttribute('href') || '';
      if (href && !href.startsWith('#')) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    }
  });
}

export function artifactUrl(taskId, art) {
  if (art.path) return `data/tasks/${encodeURIComponent(taskId)}/${art.path.split('/').map(encodeURIComponent).join('/')}`;
  if (art.url) return art.url;
  return null;
}

export function renderMarkdown(text, { breaks = true } = {}) {
  if (!text) return '';
  if (!window.marked || !window.DOMPurify) return `<pre>${esc(text)}</pre>`;
  const out = window.marked.parse(String(text), { gfm: true, breaks });
  return window.DOMPurify.sanitize(out);
}

export function enhance(root) {
  if (!window.hljs) return;
  root.querySelectorAll('pre code:not(.hljs)').forEach((el) => {
    const m = /language-([\w+-]+)/.exec(el.className);
    if (m && !window.hljs.getLanguage(m[1])) el.classList.add('nohighlight');
    try { window.hljs.highlightElement(el); } catch { /* ignore */ }
  });
}

function highlight(text, lang) {
  const hl = window.hljs;
  if (hl && lang && hl.getLanguage(lang)) {
    try { return hl.highlight(text, { language: lang, ignoreIllegals: true }).value; } catch { /* fallthrough */ }
  }
  return esc(text);
}

async function fetchText(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  let text = await r.text();
  let truncated = false;
  if (text.length > MAX_TEXT) { text = text.slice(0, MAX_TEXT); truncated = true; }
  return { text, truncated };
}

const truncNote = (t) => (t ? `<div class="av-note">${L('파일이 커서 앞부분 {size}만 표시합니다. 전체는 다운로드해서 확인하세요.', { size: fmtBytes(MAX_TEXT) })}</div>` : '');

/**
 * LaTeX 원고: '문서 보기'(HTML 미리보기)와 '원본 코드' 전환.
 * opts.resolveImage(path) — \includegraphics 경로를 업로드된 그림과 연결, opts.pdfHref — 컴파일된 PDF 링크
 */
function renderTexView(el, text, truncated, opts) {
  let mode = LS.get('texView', 'doc');
  let doc;
  try {
    doc = latexToHtml(text, { resolveImage: opts.resolveImage });
  } catch (e) {
    console.error(e);
    doc = null;
  }
  if (!doc) mode = 'src';
  const st = doc?.stats || {};
  const facts = [
    st.figures ? `${L('그림')} ${st.figures}` : '', st.tables ? `${L('표')} ${st.tables}` : '', st.equations ? `${L('수식')} ${st.equations}` : '',
    st.citations ? `${L('인용')} ${st.citations}` : '',
  ].filter(Boolean).join(' · ');
  const warns = [
    st.missingRefs ? `${L('해결 안 된 참조')} ${st.missingRefs}` : '', st.missingFigs ? `${L('찾지 못한 그림')} ${st.missingFigs}` : '',
  ].filter(Boolean).join(' · ');
  el.innerHTML = `${truncNote(truncated)}
    <div class="tex-bar">
      <div class="seg" role="tablist">
        <button type="button" data-tex="doc" class="${mode === 'doc' ? 'on' : ''}" ${doc ? '' : 'disabled'}>${icon('book')} ${L('문서 보기')}</button>
        <button type="button" data-tex="src" class="${mode === 'src' ? 'on' : ''}">${icon('code')} ${L('원본 코드')}</button>
      </div>
      <span class="tex-facts">${esc(facts)}${warns ? ` <span class="tex-warn">${esc(warns)}</span>` : ''}</span>
      ${opts.pdfHref ? `<a class="btn btn-sm" href="${esc(opts.pdfHref)}">${icon('file-text')} ${L('컴파일된 PDF')}</a>` : ''}
    </div>
    <div class="tex-pane" data-pane="doc" ${mode === 'doc' ? '' : 'hidden'}>
      ${doc ? `<div class="tex-paper"><article class="tex-doc">${doc.html}</article></div>
      <div class="tex-note">${L('LaTeX 미리보기입니다 — 그림·표·수식 번호와 레이아웃은 컴파일된 PDF 와 다를 수 있습니다.')}</div>` : ''}
    </div>
    <div class="tex-pane" data-pane="src" ${mode === 'src' ? '' : 'hidden'}>${renderCode(text, 'latex')}</div>`;
  el.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-tex]');
    if (b && !b.disabled) {
      const m = b.dataset.tex;
      el.querySelectorAll('[data-tex]').forEach((x) => x.classList.toggle('on', x === b));
      el.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== m; });
      LS.set('texView', m);
      return;
    }
    const j = ev.target.closest('[data-jump]');
    if (j) { // 참조/인용/각주 → 해당 위치로 스크롤 (해시 라우팅과 충돌하지 않게)
      ev.preventDefault();
      const target = el.querySelector(`#${CSS.escape(j.dataset.jump)}`);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.add('tex-flash');
        setTimeout(() => target.classList.remove('tex-flash'), 1500);
      }
    }
  });
}

/** el 안에 결과물을 렌더링한다. */
export async function renderArtifactInto(el, taskId, art, opts = {}) {
  const url = artifactUrl(taskId, art);
  el.classList.add('av', `av-${art.kind}`);
  if (!url) {
    el.innerHTML = html`<div class="av-external">${icon('folder')} ${L('웹으로 복사되지 않은 외부 파일입니다')}: <code>${art.external_path || '-'}</code></div>`.s;
    return;
  }
  el.innerHTML = `<div class="av-loading">${L('불러오는 중…')}</div>`;
  try {
    switch (art.kind) {
      case 'image':
        el.classList.add('av-image');
        el.innerHTML = html`<a href="${url}" target="_blank" rel="noopener"><img class="full" src="${url}" alt="${art.title || ''}"></a>`.s;
        break;
      case 'pdf':
        el.innerHTML = html`<iframe class="pdf-frame" src="${url}#view=FitH" title="${art.title || 'PDF'}"></iframe>`.s;
        break;
      case 'html':
        el.innerHTML = html`<iframe class="html-frame" sandbox="allow-scripts allow-popups" src="${url}" title="${art.title || 'HTML'}"></iframe>`.s;
        break;
      case 'docx':
        if (art.preview) {
          const { text, truncated } = await fetchText(artifactUrl(taskId, { path: art.preview }));
          el.innerHTML = `${truncNote(truncated)}<div class="av-note">${icon('info')} ${L('Word 문서에서 글자 · 표만 뽑아 보여 줍니다. 그림 · 수식 · 서식은 원본 파일을 내려받아 확인하세요.')}</div>
            <div class="markdown-body av-pad">${renderMarkdown(text, { breaks: false })}</div>`;
          break;
        }
        el.innerHTML = html`<div class="link-card">${icon('file')}<div>${L('Word 문서는 미리보기가 없습니다.')} <a href="${url}" download>${L('다운로드')}</a>${art.size ? ` (${fmtBytes(art.size)})` : ''}</div></div>`.s;
        break;
      case 'link':
        el.innerHTML = html`<div class="link-card">${icon('link')}<div><a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a></div></div>`.s;
        break;
      case 'markdown': {
        const { text, truncated } = await fetchText(url);
        el.innerHTML = `${truncNote(truncated)}<div class="markdown-body av-pad">${renderMarkdown(text, { breaks: false })}</div>`;
        enhance(el);
        break;
      }
      case 'diff': {
        const { text, truncated } = await fetchText(url);
        el.innerHTML = truncNote(truncated) + renderDiff(text);
        break;
      }
      case 'csv': {
        const { text, truncated } = await fetchText(url);
        const delim = /\.tsv$/i.test(art.path || '') ? '\t' : ',';
        el.innerHTML = truncNote(truncated) + renderCSV(text, delim);
        break;
      }
      case 'notebook': {
        const { text } = await fetchText(url);
        el.innerHTML = renderNotebook(JSON.parse(text));
        enhance(el);
        break;
      }
      case 'latex':
        if (/\.(tex|ltx)$/i.test(art.filename || art.path || '')) {
          const { text, truncated } = await fetchText(url);
          renderTexView(el, text, truncated, opts);
          break;
        }
      // falls through — .bib/.sty 등은 코드로 표시
      case 'code':
      case 'text': {
        const { text, truncated } = await fetchText(url);
        const lang = art.lang || (art.kind === 'latex' ? 'latex' : null);
        el.innerHTML = truncNote(truncated) + renderCode(text, lang);
        break;
      }
      default:
        el.innerHTML = html`<div class="link-card">${icon('file')}<div>${L('미리보기를 지원하지 않는 형식입니다.')} <a href="${url}" download>${L('다운로드')}</a>${art.size ? ` (${fmtBytes(art.size)})` : ''}</div></div>`.s;
    }
  } catch (e) {
    el.innerHTML = html`<div class="av-error">${icon('alert')} ${L('결과물을 불러오지 못했습니다')}: ${e.message}</div>`.s;
  }
}

// ------------------------------------------------------------------ 코드
export function renderCode(text, lang) {
  const body = text.endsWith('\n') ? text.slice(0, -1) : text;
  const n = body.split('\n').length;
  let nums = '';
  for (let i = 1; i <= n; i++) nums += i + '\n';
  return `<div class="code-view"><pre class="ln">${nums}</pre><pre class="src"><code class="hljs">${highlight(body, lang)}</code></pre></div>`;
}

// ------------------------------------------------------------------ diff
const EXT_LANG = {
  py: 'python', js: 'javascript', mjs: 'javascript', ts: 'typescript', java: 'java', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp',
  hpp: 'cpp', cu: 'cpp', hip: 'cpp', f90: 'fortran', f: 'fortran', jl: 'julia', r: 'r', m: 'matlab', sh: 'bash', json: 'json',
  yaml: 'yaml', yml: 'yaml', tex: 'latex', bib: 'latex', md: 'markdown', html: 'xml', xml: 'xml', css: 'css', go: 'go', rs: 'rust',
  sql: 'sql', toml: 'ini', ini: 'ini', cmake: 'cmake', kt: 'kotlin', rb: 'ruby',
};
const langOf = (name) => EXT_LANG[(name || '').split('.').pop().toLowerCase()] || null;
const cleanName = (s) => s.replace(/\t.*$/, '').replace(/^"|"$/g, '').replace(/^[ab]\//, '');

export function parseDiff(text) {
  const lines = text.split('\n');
  const files = [];
  let file = null, hunk = null, o = 0, n = 0;
  const start = (name) => {
    file = { name, hunks: [], add: 0, del: 0, meta: [], isNew: false, isDeleted: false, binary: false };
    files.push(file);
    hunk = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith('diff --git ')) {
      const m = /^diff --git "?a\/(.*?)"? "?b\/(.*?)"?$/.exec(l);
      start(m ? m[2] : l.slice(11));
      continue;
    }
    if (l.startsWith('--- ') && (lines[i + 1] || '').startsWith('+++ ') && (!hunk || !file || hunk.remain <= 0)) {
      if (!file || file.hunks.length) start(null);
      const oldName = cleanName(l.slice(4));
      const newName = cleanName(lines[i + 1].slice(4));
      if (!file.name || file.name === '/dev/null') file.name = newName === '/dev/null' ? oldName : newName;
      if (oldName === '/dev/null') file.isNew = true;
      if (newName === '/dev/null') file.isDeleted = true;
      i++;
      continue;
    }
    const hm = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/.exec(l);
    if (hm && file) {
      o = +hm[1]; n = +hm[3];
      const oc = hm[2] == null ? 1 : +hm[2], nc = hm[4] == null ? 1 : +hm[4];
      hunk = { header: l, lines: [], remain: oc + nc };
      file.hunks.push(hunk);
      continue;
    }
    if (!file) continue;
    if (!hunk || hunk.remain <= 0) {
      if (l) {
        file.meta.push(l);
        if (l.startsWith('new file mode')) file.isNew = true;
        if (l.startsWith('deleted file mode')) file.isDeleted = true;
        if (l.startsWith('Binary files') || l.startsWith('GIT binary patch')) file.binary = true;
      }
      continue;
    }
    const c = l[0];
    if (c === '+') { hunk.lines.push({ t: 'add', o: '', n: n++, s: l.slice(1) }); file.add++; hunk.remain -= 1; }
    else if (c === '-') { hunk.lines.push({ t: 'del', o: o++, n: '', s: l.slice(1) }); file.del++; hunk.remain -= 1; }
    else if (c === '\\') { hunk.lines.push({ t: 'note', s: l }); }
    else { hunk.lines.push({ t: 'ctx', o: o++, n: n++, s: l.slice(1) }); hunk.remain -= 2; }
  }
  return files;
}

export function renderDiff(text) {
  const files = parseDiff(text);
  if (!files.length) return `<div class="av-pad">${renderCode(text, 'diff')}</div>`;
  const add = files.reduce((s, f) => s + f.add, 0);
  const del = files.reduce((s, f) => s + f.del, 0);
  const totalLines = files.reduce((s, f) => s + f.hunks.reduce((a, h) => a + h.lines.length, 0), 0);
  const doHL = totalLines <= MAX_HL_LINES;
  let out = `<div class="diff-summary"><b>${L('{n}개 파일', { n: files.length })}</b> ${L('변경')} <span class="add">+${add}</span> <span class="del">−${del}</span></div>`;
  for (const f of files) {
    const lang = doHL ? langOf(f.name) : null;
    const tag = f.isNew ? `<span class="badge tone-green">${L('새 파일')}</span>` : f.isDeleted ? `<span class="badge tone-red">${L('삭제')}</span>` : f.binary ? `<span class="badge">${L('바이너리')}</span>` : '';
    out += `<details class="diff-file" open data-key="diff:${esc(f.name)}"><summary><svg class="icon chev" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg><span class="name">${esc(f.name || L('(이름 없음)'))}</span>${tag}<span class="stat"><span class="add">+${f.add}</span> <span class="del">−${f.del}</span></span></summary>`;
    if (!f.hunks.length) {
      out += `<div class="diff-meta">${esc(f.meta.join('\n') || L('내용 변경 없음'))}</div></details>`;
      continue;
    }
    out += '<div class="diff-table-wrap"><table class="diff"><tbody>';
    for (const h of f.hunks) {
      out += `<tr class="hunk"><td class="ln"></td><td class="ln"></td><td class="code">${esc(h.header)}</td></tr>`;
      for (const ln of h.lines) {
        if (ln.t === 'note') { out += `<tr class="note"><td class="ln"></td><td class="ln"></td><td class="code">${esc(ln.s)}</td></tr>`; continue; }
        const sign = ln.t === 'add' ? '+' : ln.t === 'del' ? '−' : ' ';
        out += `<tr class="${ln.t}"><td class="ln">${ln.o}</td><td class="ln">${ln.n}</td><td class="code"><span class="sign">${sign}</span>${lang ? highlight(ln.s, lang) : esc(ln.s)}</td></tr>`;
      }
    }
    out += '</tbody></table></div></details>';
  }
  return out;
}

// ------------------------------------------------------------------ CSV
function parseCSV(text, delim) {
  const rows = [];
  let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += c;
    } else if (c === '"') q = true;
    else if (c === delim) { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; if (rows.length > 2001) break; }
    else if (c !== '\r') field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

export function renderCSV(text, delim = ',') {
  const rows = parseCSV(text, delim);
  if (!rows.length) return `<div class="empty">${L('빈 파일')}</div>`;
  const [head, ...body] = rows;
  const shown = body.slice(0, 2000);
  const isNum = (v) => v !== '' && !isNaN(Number(v));
  let out = `<div class="diff-summary"><b>${L('{n}행', { n: body.length >= 2000 ? '2000+' : body.length })}</b> × ${L('{n}열', { n: head.length })}</div><div class="csv-wrap"><table class="csv"><thead><tr><th class="rn">#</th>`;
  out += head.map((h) => `<th>${esc(h)}</th>`).join('') + '</tr></thead><tbody>';
  shown.forEach((r, i) => {
    out += `<tr><td class="rn">${i + 1}</td>` + head.map((_, j) => `<td class="${isNum(r[j] ?? '') ? 'num' : ''}">${esc(r[j] ?? '')}</td>`).join('') + '</tr>';
  });
  return out + '</tbody></table></div>';
}

// ------------------------------------------------------------------ Jupyter 노트북
const joinSrc = (s) => (Array.isArray(s) ? s.join('') : s || '');
const stripAnsi = (s) => s.replace(/\x1b\[[0-9;]*m/g, '');

function renderOutput(o) {
  if (o.output_type === 'stream') return `<pre class="nb-out">${esc(joinSrc(o.text))}</pre>`;
  if (o.output_type === 'error') return `<pre class="nb-out err">${esc(stripAnsi((o.traceback || []).join('\n')))}</pre>`;
  const d = o.data || {};
  if (d['image/png']) return `<img src="data:image/png;base64,${joinSrc(d['image/png']).replace(/[^A-Za-z0-9+/=]/g, '')}" alt="">`;
  if (d['image/jpeg']) return `<img src="data:image/jpeg;base64,${joinSrc(d['image/jpeg']).replace(/[^A-Za-z0-9+/=]/g, '')}" alt="">`;
  if (d['image/svg+xml']) return `<img src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(joinSrc(d['image/svg+xml']))}" alt="">`;
  if (d['text/html'] && window.DOMPurify) return `<div class="nb-out">${window.DOMPurify.sanitize(joinSrc(d['text/html']))}</div>`;
  if (d['text/markdown']) return `<div class="nb-out markdown-body">${renderMarkdown(joinSrc(d['text/markdown']))}</div>`;
  if (d['text/plain']) return `<pre class="nb-out">${esc(joinSrc(d['text/plain']))}</pre>`;
  return '';
}

export function renderNotebook(nb) {
  const lang = nb?.metadata?.kernelspec?.language || nb?.metadata?.language_info?.name || 'python';
  const cells = nb?.cells || [];
  if (!cells.length) return `<div class="empty">${L('빈 노트북')}</div>`;
  return '<div class="nb">' + cells.map((c) => {
    const src = joinSrc(c.source);
    if (c.cell_type === 'markdown') return `<div class="nb-cell"><div class="markdown-body">${renderMarkdown(src, { breaks: false })}</div></div>`;
    if (c.cell_type === 'code') {
      const outs = (c.outputs || []).map(renderOutput).join('');
      return `<div class="nb-cell nb-code"><div class="nb-prompt">In [${c.execution_count ?? ' '}]:</div><pre class="nb-src"><code class="hljs">${highlight(src, lang)}</code></pre>${outs ? `<div class="nb-prompt out"></div><div class="nb-outputs">${outs}</div>` : ''}</div>`;
    }
    return `<div class="nb-cell"><pre class="nb-src">${esc(src)}</pre></div>`;
  }).join('') + '</div>';
}

export { raw };
