"""웹 접근 비밀번호 — 비밀번호를 넣은 브라우저만 기록(data/)을 읽게 한다 (`devtrack password`).

- 웹 화면은 비밀번호에서 PBKDF2-SHA256 으로 만든 열쇠(64자리 16진수)를 쿠키 devtrack_key 로 갖고 있는다.
  비밀번호와 열쇠는 어디에도 저장하지 않고, 기록 폴더의 .auth.json 에는 salt · 반복 횟수와 열쇠의 SHA-1(check) 만 둔다.
- 정적 웹: data/.htaccess (Apache) 가 쿠키의 SHA-1 이 check 와 같은 요청만 받는다. 화면은 공개 폴더의
  auth.json (salt · 반복 횟수 — 비밀 아님) 을 보고 비밀번호를 묻는다.
- devtrack serve: 같은 쿠키를 서버가 직접 확인한다 (data/ · api/).
.htaccess 를 읽지 않는 웹 서버(nginx, GitHub Pages 등)에서는 data/ 가 그대로 열려 있다 — 웹 화면이 이를 알아채 경고한다.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import unicodedata

from . import DevTrackError

AUTH_FILE = ".auth.json"   # 기록 폴더 (점으로 시작하므로 publish 가 복사하지 않는다)
SITE_FILE = "auth.json"    # 공개 폴더 · serve 가 화면에 주는 값 (비밀번호 여부 · salt · 반복 횟수 — 비밀 아님)
HTACCESS = ".htaccess"
COOKIE = "devtrack_key"
ITERATIONS = 600000        # PBKDF2-SHA256 반복 (OWASP 권장값) — 웹은 비밀번호를 넣을 때 한 번만 계산
MIN_LENGTH = 8
MARK = "# DevTrack password"
_COOKIE_RE = re.compile(r"(?:^|[; ])" + COOKIE + r"=([0-9a-f]{64})(?:;|$)")


def derive_key(password, salt, iterations):
    """웹 화면(lock.js 의 deriveKey)과 같은 계산 — 유니코드는 NFC 로 맞춘다 (입력기마다 한글 조합이 달라도 같은 열쇠)."""
    pw = unicodedata.normalize("NFC", password).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", pw, bytes.fromhex(salt), iterations, 32).hex()


def check_of(key):
    return hashlib.sha1(key.encode("ascii")).hexdigest()


def new_auth(password, iterations=ITERATIONS):
    if len(password) < MIN_LENGTH:
        raise DevTrackError("비밀번호는 {}자 이상이어야 합니다".format(MIN_LENGTH))
    salt = secrets.token_hex(16)
    return {"version": 1, "salt": salt, "iterations": iterations, "check": check_of(derive_key(password, salt, iterations))}


def load(root):
    """기록 폴더의 비밀번호 설정 (없으면 None). 망가진 파일은 오류 — 조용히 보호를 풀지 않는다."""
    path = os.path.join(root, AUTH_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            a = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise DevTrackError("비밀번호 설정 파일을 읽지 못했습니다 ({}): {} — devtrack password 로 다시 정하세요".format(path, e))
    ok = (isinstance(a, dict) and re.match(r"^[0-9a-f]{16,}$", str(a.get("salt") or ""))
          and isinstance(a.get("iterations"), int) and a["iterations"] >= 1000
          and re.match(r"^[0-9a-f]{40}$", str(a.get("check") or "")))
    if not ok:
        raise DevTrackError("비밀번호 설정 파일이 올바르지 않습니다 ({}) — devtrack password 로 다시 정하세요".format(path))
    return a


def site_info(auth):
    """웹 화면이 시작할 때 읽는 값 — 공개해도 되는 것만. 비밀번호가 없어도 만든다 (없는 파일을 찾는 404 가 콘솔에 남지 않게)."""
    if not auth:
        return {"version": 1, "password": False}
    return {"version": 1, "password": True, "salt": auth["salt"], "iterations": auth["iterations"], "cookie": COOKIE}


def cookie_ok(header, auth):
    m = _COOKIE_RE.search(header or "")
    return bool(m) and hmac.compare_digest(check_of(m.group(1)), auth["check"])


def htaccess_text(auth):
    return ("{mark} - written by `devtrack password`; do not edit (devtrack password --off removes it).\n"
            "# Only browsers that entered the DevTrack password (cookie {c}) may read this folder.\n"
            "Require expr \"%{{HTTP_COOKIE}} =~ /(?:^|[; ]){c}=([0-9a-f]{{64}})(?:;|$)/ && sha1($1) == '{check}'\"\n"
            ).format(mark=MARK, c=COOKIE, check=auth["check"])


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _write(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, 0o644)  # 웹 서버가 읽어야 한다
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_htaccess(data_dir, auth):
    """data_dir/.htaccess 를 쓰거나(auth) 지운다. DevTrack 이 만들지 않은 .htaccess 는 건드리지 않는다.
    돌려주는 값: 비밀번호를 걸 때는 그 파일 경로, 풀 때는 지웠으면 경로 (아니면 None)."""
    path = os.path.join(data_dir, HTACCESS)
    old = _read(path)
    if old is not None and MARK not in old:
        if auth:
            raise DevTrackError("{} 이(가) 이미 있습니다 (DevTrack 이 만든 파일이 아님) — 지우거나 다른 이름으로 옮긴 뒤 "
                                "다시 실행하세요".format(path))
        return None
    if auth:
        text = htaccess_text(auth)
        if old != text:
            _write(path, text)
        return path
    if old is not None:
        os.remove(path)
        return path
    return None


def write_site(dest, auth):
    """공개 폴더 dest 의 auth.json 과 data/.htaccess 를 비밀번호 설정에 맞춘다 (없으면 .htaccess 를 지움). 손댄 파일 목록."""
    site = os.path.join(dest, SITE_FILE)
    changed = []
    if auth:  # 기록을 먼저 막고, 그다음 화면이 비밀번호를 묻게 한다
        changed.append(write_htaccess(os.path.join(dest, "data"), auth))
    text = json.dumps(site_info(auth), sort_keys=True) + "\n"
    if _read(site) != text:
        _write(site, text)
        changed.append(site)
    if not auth:
        changed.append(write_htaccess(os.path.join(dest, "data"), None))
    return [p for p in changed if p]
