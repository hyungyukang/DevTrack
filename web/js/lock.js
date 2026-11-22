// 웹 비밀번호 (devtrack password) — auth.json 이 있으면 비밀번호를 넣은 브라우저만 기록(data/)을 읽는다.
// 비밀번호에서 PBKDF2 로 만든 열쇠를 쿠키에 두고, 웹 서버(Apache 의 data/.htaccess · devtrack serve)가 그 쿠키를 확인한다.
// 열쇠 계산은 devtrack/access.py 의 derive_key 와 같아야 한다.
import { html, icon } from './util.js';
import { L, getLang, setLang } from './i18n.js';

const COOKIE = 'devtrack_key';
const MAX_AGE = 400 * 24 * 3600; // 브라우저가 허용하는 최대 (Chrome 400일) — 사이트를 열 때마다 다시 늘린다
const PROBE = 'data/index.json';
let locked = false;

export const isLocked = () => locked;

const cookiePath = () => location.pathname.replace(/[^/]*$/, '') || '/';

function readKey() {
  const m = document.cookie.match(/(?:^|;\s*)devtrack_key=([0-9a-f]{64})(?:;|$)/);
  return m ? m[1] : null;
}

function writeKey(key) {
  const base = `${COOKIE}=${key}; Path=${cookiePath()}; Max-Age=${MAX_AGE}`;
  // HTML 결과물은 sandbox iframe(고유 출처)이라, 그 안에서 옆 파일(그림 등)을 부를 때는 SameSite=None 쿠키만 실린다.
  // None 은 Secure 가 있어야 하므로 https · localhost 에서만 — 브라우저가 받지 않으면 Lax 로
  if (window.isSecureContext) {
    document.cookie = `${base}; SameSite=None; Secure`;
    if (readKey() === key) return;
  }
  document.cookie = `${base}; SameSite=Lax`;
}

/** 이 브라우저의 열쇠를 지운다 (잠그기) */
export function forgetKey() {
  const base = `${COOKIE}=; Path=${cookiePath()}; Max-Age=0`;
  if (window.isSecureContext) document.cookie = `${base}; SameSite=None; Secure`;
  document.cookie = base;
}

async function probe() {
  try {
    const r = await fetch(`${PROBE}?_=${Date.now()}`, { method: 'HEAD', cache: 'no-store' });
    return r.status;
  } catch {
    return 0;
  }
}

const okStatus = (s) => s >= 200 && s < 300;

async function deriveKey(password, auth) {
  const enc = new TextEncoder();
  const base = await crypto.subtle.importKey('raw', enc.encode(password.normalize('NFC')), 'PBKDF2', false, ['deriveBits']);
  const salt = new Uint8Array(auth.salt.match(/../g).map((h) => parseInt(h, 16)));
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: auth.iterations }, base, 256);
  return Array.from(new Uint8Array(bits), (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * 시작할 때 확인. state:
 *  'open'        비밀번호가 없거나, 이 브라우저가 이미 넣었음 (auth = 비밀번호 설정이 있으면 그 값)
 *  'locked'      비밀번호를 물어야 함
 *  'unprotected' 비밀번호는 정했는데 웹 서버가 data/ 를 막지 않음 (.htaccess 를 읽지 않는 서버) — 열되 경고
 */
export async function checkAccess() {
  let auth = null;
  try {
    const r = await fetch(`auth.json?_=${Date.now()}`, { cache: 'no-store' });
    if (r.ok) auth = await r.json();
  } catch { /* 비밀번호 없음 */ }
  if (!auth?.password || !/^[0-9a-f]{16,}$/.test(auth.salt || '') || !(auth.iterations > 0)) return { state: 'open', auth: null };
  const key = readKey();
  const st = await probe();
  if (okStatus(st)) {
    // 열쇠 없이 열리면 웹 서버가 data/ 를 막지 않는 것 (열쇠를 넣은 뒤에 서버를 바꾼 경우는 알 수 없다)
    if (!key) return { state: 'unprotected', auth };
    writeKey(key); // 열 때마다 기한을 늘려, 사이트 데이터를 지우기 전에는 다시 묻지 않게
    return { state: 'open', auth };
  }
  if (st !== 401 && st !== 403) return { state: 'open', auth }; // 연결 오류 등 — 평소처럼 불러오다 오류를 보여 준다
  return { state: 'locked', auth };
}

/** 비밀번호 화면. 맞는 비밀번호를 넣으면 페이지를 다시 불러온다. */
export function showLock(auth) {
  locked = true;
  document.body.classList.add('locked');
  let root = document.getElementById('lock-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'lock-root';
    document.body.appendChild(root);
  }
  const draw = (error = '') => {
    const other = getLang() === 'en' ? 'ko' : 'en';
    root.innerHTML = html`
      <form class="lock-card" id="lock-form" autocomplete="on">
        <div class="lock-top">
          <span class="lock-brand"><span class="brand-mark">${icon('lock')}</span> DevTrack</span>
          <button type="button" class="icon-btn lang-btn" data-lock-lang="${other}" title="${other === 'en' ? 'Switch to English' : '한국어로 보기'}">${other === 'en' ? 'EN' : '한'}</button>
        </div>
        <h1>${L('비밀번호를 넣으세요')}</h1>
        <p class="lock-sub">${L('이 사이트는 비밀번호로 보호되어 있습니다.')}</p>
        <input type="text" name="username" autocomplete="username" value="DevTrack" hidden>
        <label class="field"><span>${L('비밀번호')}</span>
          <input type="password" name="password" autocomplete="current-password" required></label>
        ${error ? html`<p class="lock-error" role="alert">${icon('alert')} ${error}</p>` : ''}
        <button class="btn btn-primary lock-go" type="submit">${L('열기')}</button>
        <p class="form-note">${L('한 번 넣으면 이 브라우저에서는 쿠키 · 사이트 데이터를 지울 때까지 다시 묻지 않습니다.')}</p>
      </form>`.s;
    const form = root.querySelector('form');
    const input = form.elements.password;
    input.focus();
    root.querySelector('[data-lock-lang]').onclick = (ev) => {
      setLang(ev.currentTarget.dataset.lockLang);
      draw();
    };
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const pw = input.value;
      if (!pw) return;
      const go = form.querySelector('.lock-go');
      go.disabled = true;
      go.textContent = L('확인 중…');
      let msg;
      try {
        if (!window.crypto?.subtle) throw new Error(L('이 주소에서는 브라우저가 암호 기능을 막습니다 — https:// 나 localhost 주소로 여세요'));
        writeKey(await deriveKey(pw, auth));
        const st = await probe();
        if (okStatus(st)) { location.reload(); return; }
        forgetKey();
        msg = st === 401 || st === 403 ? L('비밀번호가 맞지 않습니다') : `${L('서버에 연결하지 못했습니다')} (${st || 'network'})`;
      } catch (e) {
        msg = e.message || String(e);
      }
      draw(msg);
    };
  };
  draw();
}
