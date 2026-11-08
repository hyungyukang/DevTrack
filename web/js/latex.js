// LaTeX 원고 → 읽기 쉬운 HTML 문서 (미리보기용; 완전한 TeX 구현이 아님)
//
// 지원: 제목/저자/초록, 절 제목, 문단, 강조/글꼴, 목록, 각주, 수식(KaTeX), 그림(\includegraphics → 업로드된 그림과 연결),
// 표(tabular/booktabs/multicolumn), \cite·\ref·\eqref 번호 연결, thebibliography, verbatim/lstlisting/minted,
// 정리(theorem류) 환경, \todo, 사용자 정의 매크로(\newcommand) 일부 전개.
import { esc } from './util.js';

const SECTION_TAGS = { part: 'h1', chapter: 'h1', section: 'h2', subsection: 'h3', subsubsection: 'h4' };
const MATH_ENVS = {
  equation: null, align: 'aligned', gather: 'gathered', multline: 'gathered', eqnarray: 'aligned',
  flalign: 'aligned', alignat: 'aligned', displaymath: null,
};
const THEOREMS = {
  theorem: 'Theorem', lemma: 'Lemma', proposition: 'Proposition', corollary: 'Corollary', definition: 'Definition',
  remark: 'Remark', example: 'Example', proof: 'Proof', assumption: 'Assumption', conjecture: 'Conjecture', note: 'Note',
};
const WRAP = {
  textbf: ['<strong>', '</strong>'], textit: ['<em>', '</em>'], emph: ['<em>', '</em>'], textsl: ['<em>', '</em>'],
  texttt: ['<code>', '</code>'], underline: ['<u>', '</u>'], uline: ['<u>', '</u>'], textsc: ['<span class="sc">', '</span>'],
  textsf: ['<span class="sf">', '</span>'], textrm: ['', ''], textup: ['', ''], textnormal: ['', ''], textmd: ['', ''],
  mbox: ['', ''], text: ['', ''], fbox: ['<span class="fbox">', '</span>'], textsuperscript: ['<sup>', '</sup>'],
  textsubscript: ['<sub>', '</sub>'], enquote: ['“', '”'], hl: ['<mark>', '</mark>'], sout: ['<s>', '</s>'],
  todo: ['<span class="tex-todo">TODO ', '</span>'], TODO: ['<span class="tex-todo">TODO ', '</span>'],
  fixme: ['<span class="tex-todo">FIXME ', '</span>'],
};
const GROUP_SWITCH = {
  bf: ['<strong>', '</strong>'], bfseries: ['<strong>', '</strong>'], it: ['<em>', '</em>'], itshape: ['<em>', '</em>'],
  em: ['<em>', '</em>'], sl: ['<em>', '</em>'], tt: ['<code>', '</code>'], ttfamily: ['<code>', '</code>'],
  sc: ['<span class="sc">', '</span>'], scshape: ['<span class="sc">', '</span>'],
};
const SYMBOLS = {
  '%': '%', '&': '&', $: '$', '#': '#', _: '_', '{': '{', '}': '}', ' ': ' ', ',': ' ', ';': ' ', ':': ' ',
  '!': '', '-': '', '/': '', '@': '', '\n': ' ', '\t': ' ',
  ldots: '…', dots: '…', textellipsis: '…', LaTeX: 'LaTeX', TeX: 'TeX', LaTeXe: 'LaTeX2ε', BibTeX: 'BibTeX',
  textendash: '–', textemdash: '—', textbackslash: '\\', S: '§', P: '¶', copyright: '©', textregistered: '®',
  texttrademark: '™', textdegree: '°', degree: '°', textasciitilde: '~', textasciicircum: '^', textbar: '|',
  textless: '<', textgreater: '>', quad: ' ', qquad: '  ', enspace: ' ', thinspace: ' ',
  ss: 'ß', ae: 'æ', AE: 'Æ', o: 'ø', O: 'Ø', aa: 'å', AA: 'Å', i: 'ı', l: 'ł', L: 'Ł', textmu: 'µ', textpm: '±',
  texttimes: '×', times: '×', pm: '±', checkmark: '✓', dag: '†', ddag: '‡', textbullet: '•', euro: '€', pounds: '£',
  etal: 'et al.', ie: 'i.e.', eg: 'e.g.', cf: 'cf.', etc: 'etc.',
};
const ACCENTS = { "'": '́', '`': '̀', '^': '̂', '"': '̈', '~': '̃', '=': '̄', '.': '̇',
  c: '̧', v: '̌', u: '̆', H: '̋', r: '̊', k: '̨' };
const IGNORE = new Set(('centering raggedright raggedleft noindent indent small footnotesize scriptsize tiny large Large '
  + 'LARGE huge Huge normalsize mdseries upshape slshape rmfamily sffamily normalfont clearpage newpage '
  + 'cleardoublepage pagebreak nopagebreak smallskip medskip bigskip hline toprule midrule bottomrule '
  + 'tableofcontents listoffigures listoftables frontmatter mainmatter backmatter protect relax hfill vfill null '
  + 'sloppy fussy selectfont onecolumn twocolumn FloatBarrier phantomsection appendix '
  + 'justifying allowbreak strut hrulefill dotfill unskip ignorespaces leavevmode').split(' '));
// 인자를 버리는 명령: 이름 → 필수 인자 수 (선택 인자 [..] 는 자동으로 건너뜀)
const DROP = {
  vspace: 1, 'vspace*': 1, hspace: 1, 'hspace*': 1, setlength: 2, addtolength: 2, setcounter: 2, addtocounter: 2,
  bibliographystyle: 1, pagestyle: 1, thispagestyle: 1, usepackage: 1, RequirePackage: 1, documentclass: 1,
  graphicspath: 1, captionsetup: 1, hypersetup: 1, geometry: 1, nocite: 1, index: 1, glossary: 1, addcontentsline: 3,
  markboth: 2, markright: 1, fontsize: 2, linespread: 1, definecolor: 3, color: 1, pagenumbering: 1,
  enlargethispage: 1, cline: 1, cmidrule: 1, rule: 2, newtheorem: 2, 'newtheorem*': 2, newenvironment: 3,
  renewenvironment: 3, theoremstyle: 1, crefname: 3, Crefname: 3, newcounter: 1, numberwithin: 2, setstretch: 1,
  titleformat: 5, titlespacing: 4, 'titlespacing*': 4, sisetup: 1, tikzset: 1, pgfplotsset: 1,
};

// ------------------------------------------------------------------ 저수준 읽기
const isLetter = (ch) => /[A-Za-z@]/.test(ch || '');

function readName(s, i) { // s[i] === '\\'
  let j = i + 1;
  if (j >= s.length) return ['', j];
  if (isLetter(s[j])) {
    while (j < s.length && isLetter(s[j])) j++;
    let name = s.slice(i + 1, j);
    if (s[j] === '*') { name += '*'; j++; }
    return [name, j];
  }
  return [s[j], j + 1];
}

/** 제어어 뒤 공백 건너뛰기 (빈 줄=문단 구분은 남김) */
function skipCtrlSpace(s, j) {
  while (s[j] === ' ' || s[j] === '\t') j++;
  if (s[j] === '\n') {
    let k = j + 1;
    while (s[k] === ' ' || s[k] === '\t') k++;
    if (s[k] !== '\n') return k;
  }
  return j;
}

function skipWs(s, i) {
  let j = i;
  while (j < s.length && /[ \t\n]/.test(s[j])) j++;
  return j;
}

function readGroup(s, i) {
  const j = skipWs(s, i);
  if (s[j] !== '{') {
    if (s[j] === '\\') { const [nm, e] = readName(s, j); return ['\\' + nm, e]; }
    return [s[j] || '', Math.min(j + 1, s.length)];
  }
  let depth = 0;
  for (let k = j; k < s.length; k++) {
    const ch = s[k];
    if (ch === '\\') { k++; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') { depth--; if (depth === 0) return [s.slice(j + 1, k), k + 1]; }
  }
  return [s.slice(j + 1), s.length];
}

function readOpt(s, i) {
  const j = skipWs(s, i);
  if (s[j] !== '[') return [null, i];
  let depth = 0;
  for (let k = j + 1; k < s.length; k++) {
    const ch = s[k];
    if (ch === '\\') { k++; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (ch === ']' && depth === 0) return [s.slice(j + 1, k), k + 1];
  }
  return [null, i];
}

function skipOpts(s, i) {
  let j = i;
  for (;;) { const [o, e] = readOpt(s, j); if (o == null) return j; j = e; }
}

function findEnvEnd(s, from, name) {
  const b = `\\begin{${name}}`;
  const e = `\\end{${name}}`;
  let depth = 1;
  let k = from;
  while (k < s.length) {
    const nb = s.indexOf(b, k);
    const ne = s.indexOf(e, k);
    if (ne < 0) return [s.length, s.length];
    if (nb >= 0 && nb < ne) { depth++; k = nb + b.length; continue; }
    depth--;
    if (depth === 0) return [ne, ne + e.length];
    k = ne + e.length;
  }
  return [s.length, s.length];
}

function findUnescaped(s, str, from) {
  for (let k = from; k < s.length; k++) {
    if (s[k] === '\\') { if (s.startsWith(str, k) && str[0] === '\\') return k; k++; continue; }
    if (s.startsWith(str, k)) return k;
  }
  return -1;
}

/** 중괄호/환경 깊이 0 에서만 나누기: sep = '&' 또는 'row'(\\) */
function splitTop(str, sep) {
  const parts = [];
  let depth = 0, env = 0, last = 0;
  for (let k = 0; k < str.length; k++) {
    const ch = str[k];
    if (ch === '\\') {
      if (sep === 'row' && str[k + 1] === '\\' && depth === 0 && env === 0) {
        parts.push(str.slice(last, k));
        const [o, e] = readOpt(str, k + 2);
        last = o != null ? e : k + 2;
        k = last - 1;
        continue;
      }
      if (str.startsWith('\\begin{', k)) env++;
      else if (str.startsWith('\\end{', k)) env--;
      k++;
      continue;
    }
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (sep === '&' && ch === '&' && depth === 0 && env === 0) { parts.push(str.slice(last, k)); last = k + 1; }
  }
  parts.push(str.slice(last));
  return parts;
}

function splitItems(body) {
  const items = [];
  let env = 0, depth = 0, start = -1, label = null;
  for (let k = 0; k < body.length; k++) {
    const ch = body[k];
    if (ch === '\\') {
      if (body.startsWith('\\begin{', k)) env++;
      else if (body.startsWith('\\end{', k)) env--;
      else if (env === 0 && depth === 0 && body.startsWith('\\item', k) && !isLetter(body[k + 5])) {
        if (start >= 0) items.push({ label, text: body.slice(start, k) });
        const [o, e] = readOpt(body, k + 5);
        label = o;
        start = o != null ? e : k + 5;
        k = start - 1;
        continue;
      }
      k++;
      continue;
    }
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
  }
  if (start >= 0) items.push({ label, text: body.slice(start) });
  return items;
}

const slug = (k) => 'tex-' + String(k).replace(/[^A-Za-z0-9_-]/g, '_');
const safeUrl = (u) => (/^(https?:|mailto:)/i.test(String(u).trim()) ? String(u).trim() : null);

function fmtText(raw) {
  const t = raw.replace(/[ \t\n]+/g, ' ')
    .replace(/---/g, '—').replace(/--/g, '–')
    .replace(/``/g, '“').replace(/''/g, '”').replace(/`/g, '‘').replace(/'/g, '’');
  return esc(t);
}

// ------------------------------------------------------------------ 변환기
class Converter {
  constructor(opts) {
    this.opts = opts;
    this.labels = {};
    this.counters = { figure: 0, table: 0, equation: 0 };
    this.cites = [];
    this.footnotes = [];
    this.verbs = [];
    this.macros = {};      // KaTeX 용
    this.userMacros = {};  // 본문 전개용
    this.meta = {};
    this.current = null;   // 그림/표 캡션·라벨 수집
    this.depth = 0;
    this.hasBib = false;
    this.missingFigs = [];
  }

  math(tex, display) {
    const k = window.katex;
    if (k) {
      try {
        return k.renderToString(tex, { displayMode: display, throwOnError: false, strict: 'ignore', trust: false,
          output: 'html', macros: { ...this.macros } });
      } catch { /* 아래 원문 표시 */ }
    }
    return `<code class="tex-math-raw">${esc(tex)}</code>`;
  }

  blocks(s) { return this.assemble(this.parse(s), true); }
  inline(s) { return this.assemble(this.parse(s), false); }

  assemble(nodes, blockMode) {
    if (!blockMode) return nodes.map((n) => (n.t === 'text' ? fmtText(n.v) : n.t === 'par' ? ' ' : n.v)).join('');
    let out = '', para = '';
    const flush = () => {
      if (para.replace(/<br>|\s| /g, '').length) out += `<p>${para.trim()}</p>`;
      para = '';
    };
    for (const n of nodes) {
      if (n.t === 'block') { flush(); out += n.v; }
      else if (n.t === 'par') flush();
      else para += n.t === 'text' ? fmtText(n.v) : n.v;
    }
    flush();
    return out;
  }

  parse(s) {
    const nodes = [];
    let buf = '';
    const flush = () => { if (buf) { nodes.push({ t: 'text', v: buf }); buf = ''; } };
    const push = (...ns) => { flush(); nodes.push(...ns); };
    let i = 0;
    while (i < s.length) {
      const c = s[i];
      if (c === '\\') {
        const r = this.command(s, i);
        if (r.text != null) buf += r.text;
        if (r.nodes) push(...r.nodes);
        i = r.end;
        continue;
      }
      if (c === '$') {
        if (s[i + 1] === '$') {
          const end = s.indexOf('$$', i + 2);
          if (end < 0) { buf += '$$'; i += 2; continue; }
          push({ t: 'block', v: `<div class="tex-eq">${this.math(s.slice(i + 2, end), true)}</div>` });
          i = end + 2;
          continue;
        }
        const end = findUnescaped(s, '$', i + 1);
        if (end < 0) { buf += '$'; i++; continue; }
        push({ t: 'html', v: this.math(s.slice(i + 1, end), false) });
        i = end + 1;
        continue;
      }
      if (c === '{') {
        const [content, end] = readGroup(s, i);
        push({ t: 'html', v: this.group(content) });
        i = end;
        continue;
      }
      if (c === '}') { i++; continue; }
      if (c === '~') { buf += ' '; i++; continue; } // nbsp 는 본문 글꼴에서 폭이 넓게 보여 일반 공백으로
      if (c === '&') { buf += ' '; i++; continue; }
      if (c === '\u0002' || c === '\u0003') { // verbatim 자리표시자
        const end = s.indexOf(c, i + 1);
        const v = this.verbs[+s.slice(i + 1, end)] || '';
        push({ t: c === '\u0002' ? 'block' : 'html', v });
        i = end + 1;
        continue;
      }
      if (c === '\n') {
        let j = i + 1;
        while (s[j] === ' ' || s[j] === '\t') j++;
        if (s[j] === '\n') {
          push({ t: 'par' });
          i = skipWs(s, j);
          continue;
        }
        buf += ' ';
        i++;
        continue;
      }
      buf += c;
      i++;
    }
    flush();
    return nodes;
  }

  group(content) {
    const m = /^\s*\\([A-Za-z]+)(?![A-Za-z])/.exec(content);
    if (m && GROUP_SWITCH[m[1]]) {
      const [a, b] = GROUP_SWITCH[m[1]];
      return a + this.inline(content.slice(m[0].length)) + b;
    }
    return this.inline(content);
  }

  command(s, i) {
    const [name, after] = readName(s, i);
    const word = isLetter(name[0]);
    const next = word ? skipCtrlSpace(s, after) : after;
    const arg = (from) => readGroup(s, from);

    if (name === '\\') { // 줄바꿈 \\[len]
      const [o, e] = readOpt(s, after);
      return { nodes: [{ t: 'html', v: '<br>' }], end: o != null ? e : after };
    }
    if (name === '[' || name === '(') {
      const close = name === '[' ? '\\]' : '\\)';
      const end = s.indexOf(close, after);
      if (end < 0) return { text: name, end: after };
      const tex = s.slice(after, end);
      const v = name === '[' ? { t: 'block', v: `<div class="tex-eq">${this.math(tex, true)}</div>` } : { t: 'html', v: this.math(tex, false) };
      return { nodes: [v], end: end + 2 };
    }
    if (ACCENTS[name] && (!word || s[after] === '{')) { // \'e, \"{o}, \c{c} …
      const [a, e] = arg(after);
      const letter = a.replace(/^\\i$/, 'ı').replace(/^\\j$/, 'ȷ');
      return { text: (letter + ACCENTS[name]).normalize('NFC'), end: e };
    }
    if (Object.prototype.hasOwnProperty.call(SYMBOLS, name)) return { text: SYMBOLS[name], end: word ? next : after };

    if (name === 'begin') {
      const [env, e] = arg(after);
      return this.env(env.trim(), s, e);
    }
    if (name === 'end') { const [, e] = arg(after); return { end: e }; }
    if (name === 'par') return { nodes: [{ t: 'par' }], end: next };
    if (name === 'newline' || name === 'linebreak') return { nodes: [{ t: 'html', v: '<br>' }], end: next };
    if (name === 'item') { const [o, e] = readOpt(s, after); return { text: (o != null ? o : '•') + ' ', end: o != null ? e : next }; }

    const base = name.replace(/\*$/, '');
    if (SECTION_TAGS[base] || base === 'paragraph' || base === 'subparagraph') {
      let j = skipOpts(s, after);
      const [title, e] = arg(j);
      j = e;
      const titleHtml = this.inline(title);
      let id = '';
      const k = skipWs(s, j);
      if (s.startsWith('\\label', k)) {
        const [key, e2] = arg(k + 6);
        this.labels[key] = { kind: 'sec', title: titleHtml };
        id = ` id="${slug(key)}"`;
        j = e2;
      }
      if (base === 'paragraph' || base === 'subparagraph') {
        return { nodes: [{ t: 'par' }, { t: 'html', v: `<strong class="tex-runin"${id}>${titleHtml}</strong> ` }], end: j };
      }
      const tag = SECTION_TAGS[base];
      return { nodes: [{ t: 'block', v: `<${tag} class="tex-h"${id}>${titleHtml}</${tag}>` }], end: j };
    }
    if (WRAP[name]) {
      const j = skipOpts(s, after);
      const [content, e] = arg(j);
      const [a, b] = WRAP[name];
      return { nodes: [{ t: 'html', v: a + this.inline(content) + b }], end: e };
    }
    if (name === 'textcolor' || name === 'colorbox') {
      const [, e1] = arg(skipOpts(s, after));
      const [content, e2] = arg(e1);
      return { nodes: [{ t: 'html', v: this.inline(content) }], end: e2 };
    }
    if (name === 'resizebox' || name === 'scalebox' || name === 'rotatebox' || name === 'adjustbox') {
      let j = skipOpts(s, after);
      const n = name === 'resizebox' ? 2 : 1;
      for (let x = 0; x < n; x++) j = arg(j)[1];
      j = skipOpts(s, j);
      const [content, e] = arg(j);
      return { nodes: [{ t: 'html', v: this.inline(content) }], end: e };
    }
    if (name === 'caption' || name === 'subcaption') {
      const j = skipOpts(s, after);
      const [content, e] = arg(j);
      if (this.current) { this.current.caption = this.inline(content); return { end: e }; }
      return { nodes: [{ t: 'block', v: `<p class="tex-caption">${this.inline(content)}</p>` }], end: e };
    }
    if (name === 'label') {
      const [key, e] = arg(after);
      if (this.current) { this.current.label = key; return { end: e }; }
      this.labels[key] = this.labels[key] || { kind: 'other' };
      return { nodes: [{ t: 'html', v: `<span id="${slug(key)}"></span>` }], end: e };
    }
    if (/^(ref|eqref|autoref|cref|Cref|pageref|nameref|vref|cpageref)$/.test(name)) {
      const [keys, e] = arg(after);
      const type = name === 'eqref' ? 'eq' : /^(autoref|cref|Cref|vref)$/.test(name) ? 'auto' : 'ref';
      const out = keys.split(',').map((k) => `\u0001${type}|${k.trim()}\u0001`).join(', ');
      return { nodes: [{ t: 'html', v: out }], end: e };
    }
    if (/^(cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|supercite|Cite|Citep|Citet)\*?$/.test(name)) {
      const j = skipOpts(s, after);
      const [keys, e] = arg(j);
      const links = keys.split(',').map((k) => k.trim()).filter(Boolean).map((k) => {
        if (!this.cites.includes(k)) this.cites.push(k);
        return `<a class="tex-cite" data-jump="${slug('cite-' + k)}" title="${esc(k)}">${this.cites.indexOf(k) + 1}</a>`;
      });
      return { nodes: [{ t: 'html', v: `<span class="tex-citeg">[${links.join(', ')}]</span>` }], end: e };
    }
    if (name === 'footnote' || name === 'thanks') {
      const j = skipOpts(s, after);
      const [content, e] = arg(j);
      this.footnotes.push(this.inline(content));
      const n = this.footnotes.length;
      return { nodes: [{ t: 'html', v: `<sup class="tex-fn"><a data-jump="tex-fn-${n}">${n}</a></sup>` }], end: e };
    }
    if (name === 'url') {
      const [u, e] = arg(after);
      const href = safeUrl(u);
      return { nodes: [{ t: 'html', v: href ? `<a href="${esc(href)}">${esc(u)}</a>` : `<code>${esc(u)}</code>` }], end: e };
    }
    if (name === 'href') {
      const [u, e1] = arg(after);
      const [t, e2] = arg(e1);
      const href = safeUrl(u);
      const inner = this.inline(t);
      return { nodes: [{ t: 'html', v: href ? `<a href="${esc(href)}">${inner}</a>` : inner }], end: e2 };
    }
    if (name === 'includegraphics') {
      const j = skipOpts(s, after);
      const [path, e] = arg(j);
      return { nodes: [{ t: 'html', v: this.image(path.trim()) }], end: e };
    }
    if (name === 'subfloat') {
      const [cap, e1] = readOpt(s, after);
      const [content, e2] = arg(e1);
      return { nodes: [{ t: 'html', v: this.subfigure(content, cap) }], end: e2 };
    }
    if (name === 'input' || name === 'include' || name === 'subfile' || name === 'import') {
      const [p, e] = arg(after);
      return { nodes: [{ t: 'block', v: `<div class="tex-include">📄 <code>${esc(p)}</code> 포함 — 미리보기에서는 펼치지 않습니다</div>` }], end: e };
    }
    if (name === 'maketitle') return { nodes: [{ t: 'block', v: this.titleBlock() }], end: next };
    if (name === 'title' || name === 'author' || name === 'date' || name === 'affiliation' || name === 'institute') {
      const [, e] = arg(skipOpts(s, after));
      return { end: e }; // collectMeta 에서 처리
    }
    if (name === 'bibliography' || name === 'printbibliography') {
      const j = name === 'bibliography' ? arg(after)[1] : skipOpts(s, after);
      return { nodes: [{ t: 'block', v: '\u0001bib|\u0001' }], end: j };
    }
    if (name === 'newcommand' || name === 'renewcommand' || name === 'providecommand' || name === 'def'
        || name === 'DeclareMathOperator' || name === 'DeclareRobustCommand' || name === 'newcommand*' || name === 'renewcommand*') {
      return { end: this.skipDefinition(s, name, after) };
    }
    if (DROP[name] != null) {
      let j = skipOpts(s, after);
      for (let x = 0; x < DROP[name]; x++) { j = skipOpts(s, j); j = arg(j)[1]; }
      return { end: name === 'cmidrule' ? j : skipOpts(s, j) };
    }
    if (IGNORE.has(name) || GROUP_SWITCH[name]) return { end: next };

    const um = this.userMacros[name];
    if (um && this.depth < 30) {
      let j = after;
      const args = [];
      for (let x = 0; x < um.n; x++) { const [a, e] = arg(j); args.push(a); j = e; }
      const body = um.body.replace(/#(\d)/g, (_, d) => args[+d - 1] ?? '');
      this.depth++;
      const nodes = this.parse(body);
      this.depth--;
      return { nodes, end: um.n ? j : next };
    }
    // 알 수 없는 명령: 바로 뒤 { } 인자가 있으면 그 내용만 표시
    if (s[after] === '{') {
      const [content, e] = arg(after);
      return { nodes: [{ t: 'html', v: this.inline(content) }], end: e };
    }
    return { end: word ? next : after };
  }

  skipDefinition(s, name, after) {
    let j = after;
    if (name === 'def') {
      const [nm, e] = readName(s, skipWs(s, j));
      j = e;
      const params = /^[^{]*/.exec(s.slice(j))[0];
      j += params.length;
      const [body, e2] = readGroup(s, j);
      const n = (params.match(/#\d/g) || []).length;
      this.define('\\' + nm, n, body);
      return e2;
    }
    const [target, e1] = readGroup(s, j);
    j = e1;
    if (name === 'DeclareMathOperator') {
      const [body, e2] = readGroup(s, j);
      this.macros[target.trim()] = `\\operatorname{${body}}`;
      return e2;
    }
    const [n, e2] = readOpt(s, j);
    j = n != null ? e2 : j;
    const [dflt, e3] = readOpt(s, j);
    j = dflt != null ? e3 : j;
    const [body, e4] = readGroup(s, j);
    this.define(target.trim(), n != null ? parseInt(n, 10) || 0 : 0, body, dflt != null);
    return e4;
  }

  define(cs, n, body, hasDefault = false) {
    if (!/^\\[A-Za-z@]+$/.test(cs)) return;
    this.macros[cs] = body;
    if (!hasDefault) this.userMacros[cs.slice(1)] = { n, body };
  }

  image(path) {
    const r = this.opts.resolveImage ? this.opts.resolveImage(path) : null;
    if (r && r.kind === 'image') {
      return `<a class="tex-img" href="${esc(r.href || r.url)}" title="${esc(r.title || path)}"><img src="${esc(r.url)}" alt="${esc(path)}" loading="lazy"></a>`;
    }
    if (r) return `<a class="tex-fig-file" href="${esc(r.href || r.url)}">🖼 ${esc(path)} 열기</a>`;
    this.missingFigs.push(path);
    return `<span class="tex-fig-missing">🖼 <code>${esc(path)}</code><small>업로드된 그림 없음</small></span>`;
  }

  subfigure(content, cap) {
    const parent = this.current;
    const letter = parent ? String.fromCharCode(97 + (parent.sub = (parent.sub || 0) + 1) - 1) : '';
    const prev = this.current;
    this.current = { kind: 'subfigure', caption: cap != null ? this.inline(cap) : null, label: null };
    const inner = this.inline(content);
    const cur = this.current;
    this.current = prev;
    if (cur.label && parent) this.labels[cur.label] = { kind: 'figure', num: `${parent.num}${letter}` };
    return `<span class="tex-subfig"${cur.label ? ` id="${slug(cur.label)}"` : ''}>${inner}<span class="tex-subcap">(${letter}) ${cur.caption || ''}</span></span>`;
  }

  titleBlock() {
    const m = this.meta;
    if (!m.title && !m.author) return '';
    return `<header class="tex-title">${m.title ? `<h1>${this.inline(m.title)}</h1>` : ''}${m.author ? `<div class="tex-author">${this.inline(m.author.replace(/\\and\b/g, ' · '))}</div>` : ''}${m.date && !/\\today/.test(m.date) ? `<div class="tex-date">${this.inline(m.date)}</div>` : ''}</header>`;
  }

  env(name, s, start) {
    const [endIdx, after] = findEnvEnd(s, start, name);
    let body = s.slice(start, endIdx);
    const base = name.replace(/\*$/, '');
    const starred = name.endsWith('*');
    const take = (n, opt = true) => { // 환경 인자 읽기
      let j = 0;
      const args = [];
      if (opt) j = skipOpts(body, j);
      for (let x = 0; x < n; x++) { const [a, e] = readGroup(body, j); args.push(a); j = e; }
      body = body.slice(j);
      return args;
    };
    const block = (v) => ({ nodes: [{ t: 'block', v }], end: after });

    if (base === 'document') return { nodes: this.parse(body), end: after };
    if (base === 'abstract') return block(`<div class="tex-abstract"><div class="h">Abstract</div>${this.blocks(body)}</div>`);
    if (base === 'itemize' || base === 'enumerate' || base === 'description' || base === 'compactitem' || base === 'compactenum') {
      take(0);
      const items = splitItems(body);
      if (base === 'description') {
        return block(`<dl class="tex-dl">${items.map((it) => `<dt>${this.inline(it.label || '')}</dt><dd>${this.blocks(it.text)}</dd>`).join('')}</dl>`);
      }
      const tag = base === 'enumerate' || base === 'compactenum' ? 'ol' : 'ul';
      return block(`<${tag} class="tex-list">${items.map((it) => `<li${it.label != null ? ' class="tex-li-label"' : ''}>${it.label != null ? `<span class="tex-li-mark">${this.inline(it.label)}</span>` : ''}${this.blocks(it.text)}</li>`).join('')}</${tag}>`);
    }
    if (base === 'figure' || base === 'wrapfigure' || base === 'SCfigure' || base === 'algorithm') {
      if (base === 'wrapfigure') take(2); else take(0);
      return block(this.float(base === 'algorithm' ? 'algorithm' : 'figure', body));
    }
    if (base === 'subfigure') {
      take(1);
      return { nodes: [{ t: 'html', v: this.subfigure(body, null) }], end: after };
    }
    if (base === 'table' || base === 'sidewaystable' || base === 'wraptable') {
      if (base === 'wraptable') take(2); else take(0);
      return block(this.float('table', body));
    }
    if (base === 'tabular' || base === 'tabularx' || base === 'longtable' || base === 'tabulary' || base === 'array') {
      const args = base === 'tabularx' || base === 'tabulary' || starred ? take(2) : take(1);
      return block(this.tabular(args[args.length - 1], body));
    }
    if (Object.prototype.hasOwnProperty.call(MATH_ENVS, base) || base === 'math') {
      if (base === 'alignat') take(1, false);
      if (base === 'math') return { nodes: [{ t: 'html', v: this.math(body, false) }], end: after };
      return block(this.mathEnv(base, starred, body));
    }
    if (base === 'center' || base === 'flushleft' || base === 'flushright') {
      const align = base === 'center' ? 'center' : base === 'flushleft' ? 'left' : 'right';
      return block(`<div style="text-align:${align}">${this.blocks(body)}</div>`);
    }
    if (base === 'quote' || base === 'quotation' || base === 'verse') return block(`<blockquote>${this.blocks(body)}</blockquote>`);
    if (THEOREMS[base]) {
      const [title] = readOpt(body, 0);
      if (title != null) body = body.slice(readOpt(body, 0)[1]);
      const head = `${THEOREMS[base]}${title != null ? ` (${this.inline(title)})` : ''}.`;
      return block(`<div class="tex-thm tex-${base}"><span class="tex-thm-h">${head}</span> ${this.blocks(body)}${base === 'proof' ? '<span class="tex-qed">∎</span>' : ''}</div>`);
    }
    if (base === 'thebibliography') {
      take(1, false);
      this.hasBib = true;
      const entries = [];
      const re = /\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}/g;
      const marks = [];
      let m;
      while ((m = re.exec(body))) marks.push({ key: m[1].trim(), start: m.index, end: re.lastIndex });
      marks.forEach((mk, x) => {
        const text = body.slice(mk.end, x + 1 < marks.length ? marks[x + 1].start : body.length);
        if (!this.cites.includes(mk.key)) this.cites.push(mk.key);
        entries.push({ key: mk.key, html: this.inline(text) });
      });
      entries.sort((a, b) => this.cites.indexOf(a.key) - this.cites.indexOf(b.key));
      return block(`<section class="tex-refs"><h2 class="tex-h">References</h2><ol>${entries.map((en) => `<li id="${slug('cite-' + en.key)}" value="${this.cites.indexOf(en.key) + 1}">${en.html}</li>`).join('')}</ol></section>`);
    }
    if (base === 'algorithmic') {
      const text = body.replace(/\\(State|STATE|Statex)\b/g, '\n').replace(/\\(Require|REQUIRE)\b/g, '\nRequire:')
        .replace(/\\(Ensure|ENSURE)\b/g, '\nEnsure:').replace(/\\(If|IF)\b/g, '\nif').replace(/\\(EndIf|ENDIF)\b/g, '\nend if')
        .replace(/\\(For|FOR)\b/g, '\nfor').replace(/\\(EndFor|ENDFOR)\b/g, '\nend for').replace(/\\(While|WHILE)\b/g, '\nwhile')
        .replace(/\\(EndWhile|ENDWHILE)\b/g, '\nend while').replace(/\\(Return|RETURN)\b/g, '\nreturn');
      return block(`<pre class="tex-verb">${esc(text.trim())}</pre>`);
    }
    if (base === 'minipage') { take(1); return block(`<div class="tex-minipage">${this.blocks(body)}</div>`); }
    if (base === 'multicols') { take(1, false); return block(this.blocks(body)); }
    if (base === 'tikzpicture' || base === 'pgfpicture' || base === 'axis') {
      return block('<div class="tex-fig-missing">TikZ 그림 — 미리보기에서는 그리지 않습니다</div>');
    }
    // 알 수 없는 환경: 내용만 표시
    take(0);
    return block(`<div class="tex-env">${this.blocks(body)}</div>`);
  }

  float(kind, body) {
    const counter = kind === 'table' ? 'table' : 'figure';
    const num = ++this.counters[counter];
    const prev = this.current;
    this.current = { kind, num, caption: null, label: null, sub: 0 };
    const inner = this.blocks(body);
    const cur = this.current;
    this.current = prev;
    if (cur.label) this.labels[cur.label] = { kind: counter, num };
    const name = kind === 'table' ? 'Table' : kind === 'algorithm' ? 'Algorithm' : 'Figure';
    const cap = cur.caption != null ? `<figcaption><b>${name} ${num}.</b> ${cur.caption}</figcaption>` : '';
    const id = cur.label ? ` id="${slug(cur.label)}"` : '';
    return kind === 'table'
      ? `<figure class="tex-float tex-table"${id}>${cap}<div class="tex-float-body">${inner}</div></figure>`
      : `<figure class="tex-float tex-figure"${id}><div class="tex-float-body">${inner}</div>${cap}</figure>`;
  }

  tabular(spec, body) {
    const aligns = [];
    const sp = spec.replace(/[@!<>]\{[^}]*\}/g, '').replace(/\*\{(\d+)\}\{([^}]*)\}/g, (_, n, cols) => cols.repeat(+n))
      .replace(/[pmb]\{[^}]*\}/g, 'l').replace(/\|/g, '').replace(/\s/g, '');
    for (const ch of sp) aligns.push(ch === 'r' || ch === 'S' ? 'right' : ch === 'c' ? 'center' : 'left');
    const ruleRe = /^\s*((?:\\(?:hline|toprule|midrule|bottomrule|hhline\{[^}]*\}|cline\{[^}]*\}|cmidrule(?:\([^)]*\))?\{[^}]*\}|addlinespace(?:\[[^\]]*\])?|specialrule\{[^}]*\}\{[^}]*\}\{[^}]*\})\s*)*)/;
    const rows = [];
    for (const raw of splitTop(body, 'row')) {
      const m = ruleRe.exec(raw);
      const rules = m ? m[1] : '';
      const rest = raw.slice(m ? m[0].length : 0); // m[0] 은 앞 공백까지 포함
      const midAbove = /\\(midrule|hline|cmidrule)/.test(rules);
      if (!rest.trim()) { if (rows.length) rows[rows.length - 1].ruleBelow = true; continue; }
      rows.push({ cells: splitTop(rest, '&'), midAbove });
    }
    let head = 0;
    const firstMid = rows.findIndex((r, x) => x > 0 && r.midAbove);
    if (firstMid > 0 && firstMid <= 3) head = firstMid;
    const cell = (c, x, tag) => {
      const mc = /^\s*\\multicolumn\s*\{(\d+)\}\s*\{([^}]*)\}/.exec(c);
      if (mc) {
        const [content] = readGroup(c, mc.index + mc[0].length);
        const al = /r/.test(mc[2]) ? 'right' : /c/.test(mc[2]) ? 'center' : 'left';
        return { html: `<${tag} colspan="${mc[1]}" style="text-align:${al}">${this.inline(content)}</${tag}>`, span: +mc[1] };
      }
      const mr = /^\s*\\multirow\s*\{[^}]*\}\s*\{[^}]*\}/.exec(c);
      const text = mr ? readGroup(c, mr.index + mr[0].length)[0] : c;
      return { html: `<${tag} style="text-align:${aligns[x] || 'left'}">${this.inline(text.trim())}</${tag}>`, span: 1 };
    };
    const renderRow = (r, tag) => {
      let x = 0;
      const cells = r.cells.map((c) => { const out = cell(c, x, tag); x += out.span; return out.html; });
      return `<tr class="${r.midAbove ? 'rule-above' : ''}">${cells.join('')}</tr>`;
    };
    const thead = head ? `<thead>${rows.slice(0, head).map((r) => renderRow(r, 'th')).join('')}</thead>` : '';
    const tbody = `<tbody>${rows.slice(head).map((r) => renderRow(r, 'td')).join('')}</tbody>`;
    return `<div class="tex-tabular-wrap"><table class="tex-tabular">${thead}${tbody}</table></div>`;
  }

  mathEnv(base, starred, body) {
    const labels = [];
    let tex = body.replace(/\\label\s*\{([^}]*)\}/g, (_, k) => { labels.push(k.trim()); return ''; })
      .replace(/\\(nonumber|notag)\b/g, '');
    if (base === 'eqnarray') tex = tex.replace(/&\s*([^&]*?)\s*&/g, '&$1 ');
    const wrap = MATH_ENVS[base];
    if (wrap) tex = `\\begin{${wrap}}${tex}\\end{${wrap}}`;
    const numbered = !starred && base !== 'displaymath';
    let num = null;
    if (numbered) {
      num = ++this.counters.equation;
      labels.forEach((k) => { this.labels[k] = { kind: 'equation', num }; });
    }
    const id = labels[0] ? ` id="${slug(labels[0])}"` : '';
    return `<div class="tex-eq"${id}>${this.math(tex.trim(), true)}${num ? `<span class="tex-eqno">(${num})</span>` : ''}</div>`;
  }

  resolve(html) {
    const NAMES = { figure: 'Figure', table: 'Table', equation: 'Eq.', sec: 'Section' };
    let missing = 0;
    const refsHtml = () => {
      if (!this.cites.length || this.hasBib) return '';
      return `<section class="tex-refs"><h2 class="tex-h">References</h2><p class="tex-note-inline">BibTeX 항목은 미리보기에서 인용 키로만 표시됩니다.</p><ol>${this.cites.map((k) => `<li id="${slug('cite-' + k)}"><code>${esc(k)}</code></li>`).join('')}</ol></section>`;
    };
    let bibPlaced = false;
    html = html.replace(/\u0001(\w+)\|([^\u0001]*)\u0001/g, (_, type, key) => {
      if (type === 'bib') { bibPlaced = true; return refsHtml(); }
      const L = this.labels[key];
      if (!L || L.kind === 'other' && L.num == null) {
        if (!L) missing++;
        return L ? `<a class="tex-ref" data-jump="${slug(key)}">↗</a>` : `<span class="tex-ref missing" title="정의되지 않은 참조: ${esc(key)}">??</span>`;
      }
      const txt = L.kind === 'sec' ? `“${L.title}”` : String(L.num);
      let label = txt;
      if (type === 'eq') label = `(${txt})`;
      else if (type === 'auto') label = L.kind === 'equation' ? `Eq. (${txt})` : `${NAMES[L.kind] || ''} ${txt}`.trim();
      return `<a class="tex-ref" data-jump="${slug(key)}">${label}</a>`;
    });
    if (!bibPlaced) html += refsHtml();
    this.missingRefs = missing;
    return html;
  }
}

function stripComments(s) {
  return s.replace(/^[ \t]*%.*\n/gm, '').replace(/(^|[^\\])%.*$/gm, '$1');
}

function extractVerbatim(s, conv) {
  s = s.replace(/\\begin\{(verbatim\*?|lstlisting|minted|Verbatim|comment)\}((?:\[[^\]\n]*\])?)((?:\{[^}\n]*\})?)([\s\S]*?)\\end\{\1\}/g,
    (m, env, opt, arg, body) => {
      if (env === 'comment') return '';
      if (env !== 'minted' && arg) body = arg + body; // {…} 는 minted 의 언어 인자일 때만
      const lang = env === 'minted' ? arg.slice(1, -1) : (/language=([\w+]+)/i.exec(opt) || [])[1];
      let code = esc(body.replace(/^\n/, '').replace(/\s+$/, ''));
      const hl = window.hljs;
      if (hl && lang && hl.getLanguage(lang.toLowerCase())) {
        try { code = hl.highlight(body.replace(/^\n/, '').replace(/\s+$/, ''), { language: lang.toLowerCase(), ignoreIllegals: true }).value; } catch { /* 원문 */ }
      }
      conv.verbs.push(`<pre class="tex-verb"><code class="hljs">${code}</code></pre>`);
      return `\u0002${conv.verbs.length - 1}\u0002`;
    });
  return s.replace(/\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?([^A-Za-z\s{])(.*?)\1/g, (m, d, body) => {
    conv.verbs.push(`<code>${esc(body)}</code>`);
    return `\u0003${conv.verbs.length - 1}\u0003`;
  });
}

function collectMeta(s, conv) {
  for (const key of ['title', 'author', 'date']) {
    const re = new RegExp(`\\\\${key}\\s*(?=[\\[{])`);
    const m = re.exec(s);
    if (m) {
      const j = skipOpts(s, m.index + m[0].length);
      conv.meta[key] = readGroup(s, j)[0];
    }
  }
  const re = /\\(newcommand\*?|renewcommand\*?|providecommand|DeclareMathOperator|def)(?![A-Za-z])/g;
  let m;
  while ((m = re.exec(s))) {
    try { conv.skipDefinition(s, m[1].replace(/\*$/, ''), m.index + m[0].length); } catch { /* 무시 */ }
  }
}

/**
 * LaTeX 원고를 HTML 로 변환한다.
 * @param {string} src
 * @param {{resolveImage?: (path: string) => ({url, kind, title, href}|null)}} opts
 */
export function latexToHtml(src, opts = {}) {
  const conv = new Converter(opts);
  let s = String(src).replace(/\r\n?/g, '\n');
  s = extractVerbatim(s, conv);
  s = stripComments(s);
  collectMeta(s, conv);
  const b = s.indexOf('\\begin{document}');
  let body = s;
  if (b >= 0) {
    const e = s.indexOf('\\end{document}', b);
    body = s.slice(b + '\\begin{document}'.length, e < 0 ? s.length : e);
  }
  let html = conv.blocks(body);
  if (conv.footnotes.length) {
    html += `<section class="tex-footnotes"><ol>${conv.footnotes.map((f, x) => `<li id="tex-fn-${x + 1}">${f}</li>`).join('')}</ol></section>`;
  }
  html = conv.resolve(html);
  if (window.DOMPurify) html = window.DOMPurify.sanitize(html);
  return {
    html,
    stats: {
      figures: conv.counters.figure, tables: conv.counters.table, equations: conv.counters.equation,
      citations: conv.cites.length, missingRefs: conv.missingRefs || 0, missingFigs: conv.missingFigs.length,
      title: conv.meta.title ? conv.inline(conv.meta.title).replace(/<[^>]+>/g, '') : null,
    },
  };
}
