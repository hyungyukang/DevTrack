"""GitHub 이슈 우편함 — 정적 웹(공개 폴더)에서 보낸 지시 · 메시지 · 결정을 이 컴퓨터의 DevTrack 으로 가져온다.

정적 웹은 기록을 쓸 수 없으므로, 웹의 'GitHub 이슈로 보내기' 가 내용을 채운 새 이슈 페이지를 열고 사용자가 제출한다.
여기서는 비공개 저장소의 이슈 · 댓글을 읽어(사용자 본인 계정이 쓴 것만) 웹의 실시간 모드와 같은 Store 기록으로 옮기고,
처리 결과를 이슈에 답한다. `devtrack watch` · `inbox --wait` 가 기다리는 동안 몇십 초마다 확인한다 (Poller).

- 설정: 기록 폴더의 .mailbox.json (저장소 · 계정 — 비밀 아님). 토큰은 ~/.config/devtrack/github-token (0600) 또는
  $DEVTRACK_GITHUB_TOKEN — 기록 폴더(웹에 공개될 수 있음)에는 두지 않는다.
- 상태: 기록 폴더의 .agent/mailbox-state.json (처리한 이슈 · 댓글, 이슈 ↔ 작업, 마지막 확인 시각).
- 이슈 하나가 새 작업이면 그 작업의 '대화 이슈'로 남겨, 댓글 = 그 작업에 보내는 메시지, 승인 요청은 댓글로 알리고,
  작업이 끝나면 닫는다. 작업 번호(T-0041)로 시작하는 이슈는 한 번 처리하고 닫는다.
- 작업 삭제는 이슈로 하지 않는다 (되돌릴 수 없음).
- 파일: GitHub 의 끌어다 놓기 첨부(user-attachments)는 비공개 저장소에서 토큰으로 내려받을 수 없다 (API · body_html 모두 확인).
  그래서 파일은 우편함 저장소에 올린다 — 정적 웹의 '파일 올리기' 가 GitHub 업로드 화면을 지시 전용 폴더(inbox/<날짜>-<무작위>)로 열고,
  이슈 본문의 <!-- devtrack-files: 폴더 --> 로 그 폴더를 알린다. 새 작업 이슈는 그 폴더(없으면 inbox/issue-<번호>)를 작업이 끝날 때까지,
  메시지 이슈는 FOLDER_WAIT 동안 지켜보며 새로 올라온 파일을 그 작업의 '보낸 파일' 결과물로 올린다 (브랜치 머리가 바뀔 때만 폴더를 본다).
  글 속 저장소 파일 링크(…/blob/<브랜치>/<경로>)도 받는다. 토큰에 Contents: Read 필요.
"""

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from . import DevTrackError

CONFIG_FILE = ".mailbox.json"
STATE_FILE = os.path.join(".agent", "mailbox-state.json")
TOKEN_ENV = "DEVTRACK_GITHUB_TOKEN"
API_ENV = "DEVTRACK_GITHUB_API"          # 시험용 (가짜 GitHub 서버)
DEFAULT_INTERVAL = 30                    # 초 — 시간당 120번 정도, GitHub 한도(5,000)에 한참 못 미친다
BOT_MARK = "<!-- devtrack-bot -->"       # devtrack 이 단 댓글 (토큰이 사용자 것이라 작성자로는 구분할 수 없다)
SITE_RE = re.compile(r"<!--\s*devtrack-site:\s*(\S+?)\s*-->")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)   # 웹이 넣은 표시 · 붙여 넣을 자리 (GitHub 화면에는 안 보임)
PASTE_HERE = "devtrack-paste-here"                  # 긴 본문을 클립보드로 보낸 이슈의 '여기에 붙여 넣기' 자리
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_KEEP = 2000                          # 기억해 둘 처리한 댓글 ID 수
ATTACH_MAX = 25 * 1024 * 1024            # 보낸 파일 하나의 최대 크기 (GitHub 웹에서 올릴 수 있는 크기와 같음)
ATTACH_COUNT = 10                        # 이슈 · 댓글 하나에서 받는 파일 수
MD_LINK_RE = re.compile(r"!?\[([^\]\n]*)\]\(\s*<?(https?://[^\s)>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
FOLDER_RE = re.compile(r"<!--\s*devtrack-files:\s*(inbox/[A-Za-z0-9._-]+)\s*-->")
FOLDER_WAIT = 2 * 3600                   # 메시지 이슈의 파일 폴더를 더 지켜보는 시간 (초) — 이슈를 먼저 보내고 파일을 나중에 올려도 받게
FOLDER_MAX = 20                          # 폴더 하나에서 한 번에 받는 파일 수
GH_ATTACH_RE = re.compile(r"^https://github\.com/(?:user-attachments/(?:files|assets)/|[^/]+/[^/]+/(?:files|assets)/\d+/)",
                          re.I)


def token_path():
    """cli.config_path() 와 같은 폴더 ($XDG_CONFIG_HOME/devtrack 또는 ~/.config/devtrack)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "devtrack", "github-token")


def load_token():
    """(토큰, 출처) — 없으면 (None, None)."""
    if os.environ.get(TOKEN_ENV, "").strip():
        return os.environ[TOKEN_ENV].strip(), "$" + TOKEN_ENV
    try:
        with open(token_path(), encoding="utf-8") as fh:
            tok = fh.read().strip()
    except OSError:
        return None, None
    return (tok, token_path()) if tok else (None, None)


def save_token(token):
    _write(token_path(), token.strip() + "\n", 0o600)


def forget_token():
    try:
        os.remove(token_path())
        return True
    except OSError:
        return False


def _write(path, text, mode):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else default
    except (OSError, ValueError):
        return default


def load_config(root):
    """기록 폴더의 우편함 설정 (없으면 None)."""
    cfg = _read_json(os.path.join(root, CONFIG_FILE), None)
    if not cfg or not REPO_RE.match(str(cfg.get("github") or "")):
        return None
    return cfg


def save_config(root, cfg):
    _write(os.path.join(root, CONFIG_FILE), json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", 0o644)


def remove_config(root):
    for name in (CONFIG_FILE, STATE_FILE):
        try:
            os.remove(os.path.join(root, name))
        except OSError:
            pass


def load_state(root):
    st = _read_json(os.path.join(root, STATE_FILE), {})
    st.setdefault("issues", {})       # "12": {task, kind: thread|oneoff, closed, notified: [A-1]}
    st.setdefault("comments", [])     # 처리했거나 건너뛴 댓글 ID
    st.setdefault("posted", [])       # devtrack 이 단 댓글 ID
    st.setdefault("ignored", [])      # 다른 사람이 쓴 이슈 번호 (한 번만 알린다)
    st.setdefault("folders", {})      # "inbox/…": {task, issue, seen: {경로: sha}, until(메시지 이슈) · error}
    return st


def save_state(root, st):
    st["comments"] = st["comments"][-MAX_KEEP:]
    st["posted"] = st["posted"][-MAX_KEEP:]
    st["ignored"] = st["ignored"][-MAX_KEEP:]
    _write(os.path.join(root, STATE_FILE), json.dumps(st, ensure_ascii=False, indent=1) + "\n", 0o644)


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------------------------- GitHub
class GitHubError(DevTrackError):
    def __init__(self, message, status=None):
        DevTrackError.__init__(self, message)
        self.status = status


class _GitHubOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """다른 곳으로 넘겨질 때 토큰(Authorization)은 GitHub 주소에만 따라 보낸다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
        if new is not None and not (host == "github.com" or host.endswith((".github.com", ".githubusercontent.com"))):
            new.remove_header("Authorization")
        return new


class GitHub(object):
    """GitHub REST API (urllib 만). 토큰은 Authorization 헤더로만 보낸다."""

    def __init__(self, token, repo, api=None, timeout=20):
        self.token = token
        self.repo = repo
        self.api = (api or os.environ.get(API_ENV) or "https://api.github.com").rstrip("/")
        self.timeout = timeout

    def _req(self, method, path, params=None, body=None):
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "devtrack",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw.decode("utf-8")) if raw else None, r.headers
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("message") or ""
            except (ValueError, OSError, AttributeError):
                msg = ""
            raise GitHubError("GitHub {} {} — {} {}".format(method, path, e.code, msg).strip(), e.code)
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError("GitHub 에 연결하지 못했습니다: {}".format(getattr(e, "reason", e)))
        except ValueError:
            raise GitHubError("GitHub 응답을 읽지 못했습니다 ({} {})".format(method, path))

    def _pages(self, path, params, limit=5):
        out = []
        params = dict(params, per_page=100)
        for page in range(1, limit + 1):
            params["page"] = page
            items, _ = self._req("GET", path, params)
            items = items or []
            out.extend(items)
            if len(items) < 100:
                break
        return out

    def _raw(self, path, params, limit):
        """파일 내용 (bytes) — limit 보다 크면 413 으로 본다."""
        url = self.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "devtrack",
        })
        try:
            with urllib.request.build_opener(_GitHubOnlyRedirect).open(req, timeout=max(self.timeout, 60)) as r:
                size = r.headers.get("Content-Length") or ""
                if size.isdigit() and int(size) > limit:
                    raise GitHubError("파일이 너무 큽니다", 413)
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            raise GitHubError("GitHub GET {} — {}".format(path, e.code), e.code)
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError("GitHub 에 연결하지 못했습니다: {}".format(getattr(e, "reason", e)))
        if len(data) > limit:
            raise GitHubError("파일이 너무 큽니다", 413)
        return data

    def download_path(self, path, ref, limit=None):
        return self._raw("/repos/{}/contents/{}".format(self.repo, urllib.parse.quote(path)), {"ref": ref}, limit or ATTACH_MAX)

    def download(self, rest, limit=None):
        """저장소 파일 → (경로, bytes). rest: 링크에서 뗀 [브랜치 …, 경로 …] — 브랜치 이름에 / 가 있을 수 있어 짧은 것부터 해 본다."""
        last = None
        for i in range(1, min(4, len(rest))):
            ref, path = "/".join(rest[:i]), "/".join(rest[i:])
            try:
                return path, self.download_path(path, ref, limit)
            except GitHubError as e:
                if e.status != 404:
                    raise
                last = e
        raise last or GitHubError("파일을 찾지 못했습니다", 404)

    def list_dir(self, path, ref, depth=2):
        """저장소 폴더의 파일 [{path, name, sha, size}] — 하위 폴더는 depth 단계까지. 폴더가 아직 없으면 []."""
        try:
            items = self._req("GET", "/repos/{}/contents/{}".format(self.repo, urllib.parse.quote(path)), {"ref": ref})[0]
        except GitHubError as e:
            if e.status == 404:
                return []
            raise
        out = []
        for it in items if isinstance(items, list) else [items]:
            if it.get("type") == "file":
                out.append({"path": it["path"], "name": it["name"], "sha": it.get("sha"), "size": it.get("size") or 0})
            elif it.get("type") == "dir" and depth > 1:
                out.extend(self.list_dir(it["path"], ref, depth - 1))
        return out

    def branch_head(self, branch):
        b = self._req("GET", "/repos/{}/branches/{}".format(self.repo, urllib.parse.quote(branch, safe="")))[0] or {}
        return (b.get("commit") or {}).get("sha")

    def can_read_files(self):
        """토큰에 저장소 파일 읽기(Contents: Read) 권한이 있는지 — 모르면 None (빈 저장소 등)."""
        try:
            self._req("GET", "/repos/{}/contents/".format(self.repo))
            return True
        except GitHubError as e:
            return False if e.status in (401, 403) else None

    def user(self):
        return self._req("GET", "/user")[0]

    def repo_info(self):
        return self._req("GET", "/repos/" + self.repo)[0]

    def issues_since(self, since):
        return self._pages("/repos/{}/issues".format(self.repo),
                           {"state": "all", "since": since, "sort": "updated", "direction": "asc"})

    def comments_since(self, since):
        return self._pages("/repos/{}/issues/comments".format(self.repo),
                           {"since": since, "sort": "updated", "direction": "asc"})

    def comment(self, number, body):
        return self._req("POST", "/repos/{}/issues/{}/comments".format(self.repo, number),
                         body={"body": body.rstrip() + "\n\n" + BOT_MARK})[0]

    def close(self, number, reason="completed"):
        return self._req("PATCH", "/repos/{}/issues/{}".format(self.repo, number),
                         body={"state": "closed", "state_reason": reason})[0]

    def react(self, comment_id, content="eyes"):
        return self._req("POST", "/repos/{}/issues/comments/{}/reactions".format(self.repo, comment_id),
                         body={"content": content})[0]

    def ensure_label(self, name, color, description):
        try:
            self._req("POST", "/repos/{}/labels".format(self.repo),
                      body={"name": name, "color": color, "description": description})
            return True
        except GitHubError as e:
            if e.status == 422:  # 이미 있음
                return False
            raise


# ---------------------------------------------------------------------------------------------- 읽기 규칙
DECISION_WORDS = (("수정 요청", "changes"), ("수정요청", "changes"), ("승인", "approve"), ("거절", "reject"),
                  ("request changes", "changes"), ("approve", "approve"), ("changes", "changes"),
                  ("reject", "reject"))
_DEC = "|".join(re.escape(w) for w, _ in DECISION_WORDS)
DECISION_RE = re.compile(r"^(?:({0})\s+(A-?\d+)|(A-?\d+)\s+({0}))\s*(?:[:：]\s*(.*))?$".format(_DEC), re.I)
WORD_CMD_RE = re.compile(r"^(완료|done|취소|cancel|삭제|delete|모델|model)\s*(?:[:：]\s*(.*))?$", re.I)
TITLE_TASK_RE = re.compile(r"^\s*\[?\s*(T-?\d{1,6})\s*\]?\s*[:\-—·]?\s*(.*)$", re.I)
HEADER_RE = re.compile(r"^\s*([^:：\n]{1,24}?)\s*[:：]\s*(.*?)\s*$")
NEW_ARGS_RE = re.compile(r"\(devtrack new 에 (.+)\)\s*$")
TRAILERS = ("위 결정을 기록하고", "이 작업을 취소로 기록하고", "사용자가 이 작업을 완료로 처리했습니다", "이 작업을 삭제하고",
            "이 지정을 기록해 주세요", "devtrack 으로 작업을 등록하고")
PRIORITY_WORDS = {"높음": "high", "high": "high", "낮음": "low", "low": "low", "보통": "normal", "normal": "normal"}
CLEAR_WORDS = ("해제", "없음", "지정 해제", "clear", "none", "-")

# 이슈 본문 맨 위의 '키: 값' 줄 (웹의 붙여 넣기 메시지 · 손으로 쓴 이슈 둘 다)
HEADER_KEYS = {
    "제목": "title", "title": "title",
    "담당": "agent", "담당ai": "agent", "agent": "agent", "ai": "agent",
    "유형": "type", "type": "type", "종류": "type",
    "저장소": "repo", "repo": "repo", "repository": "repo",
    "브랜치": "branch", "branch": "branch",
    "우선순위": "priority", "priority": "priority",
    "관련작업": "related", "related": "related", "연결": "related",
    "모델": "model", "model": "model",
    "강도": "effort", "추론강도": "effort", "effort": "effort",
    "리뷰": "review", "심사": "review", "review": "review", "리뷰어": "review", "reviewer": "review",
    "리뷰횟수": "review_rounds", "reviewrounds": "review_rounds",
    "리뷰모델": "review_model", "reviewmodel": "review_model",
    "리뷰강도": "review_effort", "revieweffort": "review_effort",
    "심사원고": "review_doc", "원고": "review_doc", "reviewdoc": "review_doc",
    "투고처": "review_venue", "venue": "review_venue",
    "결과물": "artifact", "artifact": "artifact",
    "선택": "selected", "select": "selected", "selected": "selected",
}


def _key(k):
    return re.sub(r"\s+", "", k).lower()


def split_header(text):
    """맨 위의 알려진 '키: 값' 줄들 → (dict, 나머지 글). 알 수 없는 줄을 만나면 거기서 멈춘다."""
    lines = (text or "").split("\n")
    head = {}
    i = 0
    while i < len(lines):
        m = HEADER_RE.match(lines[i])
        if not m or _key(m.group(1)) not in HEADER_KEYS:
            break
        head[HEADER_KEYS[_key(m.group(1))]] = m.group(2)
        i += 1
    return head, "\n".join(lines[i:]).strip("\n")


def strip_trailer(text):
    """붙여 넣기 메시지의 AI 용 안내 문단을 뗀다 → (본문, 안내 문단). 보통 맨 끝이지만, 그 뒤에 파일 링크 · 덧붙인 말을
    적어 보내기도 하므로 끝에서 두 문단 안에 있으면 뗀다 (지시 내용 속의 같은 문장은 건드리지 않게 맨 끝 쪽만)."""
    paras = re.split(r"\n\s*\n", (text or "").strip())
    for i in range(len(paras) - 1, max(len(paras) - 3, -1), -1):
        if paras[i].strip().startswith(TRAILERS):
            return "\n\n".join(paras[:i] + paras[i + 1:]).strip(), paras[i]
    return (text or "").strip(), ""


def _task_id(s):
    m = re.match(r"^T-?(\d{1,6})$", s.strip(), re.I)
    return "T-{:04d}".format(int(m.group(1))) if m else None


def _item_id(s, prefix):
    m = re.match(r"^{}-?(\d+)$".format(prefix), s.strip(), re.I)
    return "{}-{}".format(prefix, int(m.group(1))) if m else None


def _decision(word):
    w = re.sub(r"\s+", " ", word.strip().lower())
    for k, v in DECISION_WORDS:
        if w == k.lower():
            return v
    return None


def _new_args(value):
    """'… (devtrack new 에 --model x --effort y)' 의 옵션 → dict."""
    m = NEW_ARGS_RE.search(value or "")
    if not m:
        return None
    try:
        words = shlex.split(m.group(1))
    except ValueError:
        return {}
    out, i = {}, 0
    names = {"--related": "related", "--model": "model", "--effort": "effort", "--review-by": "review",
             "--review-rounds": "review_rounds", "--review-model": "review_model", "--review-effort": "review_effort",
             "--review-doc": "review_doc", "--review-venue": "review_venue"}
    while i < len(words):
        w = words[i]
        if w == "--related":
            ids = []
            i += 1
            while i < len(words) and not words[i].startswith("--"):
                ids.append(words[i])
                i += 1
            out["related"] = ids
            continue
        if w in names and i + 1 < len(words):
            out[names[w]] = words[i + 1]
            i += 2
            continue
        i += 1
    return out


def task_fields(head, task_types):
    """'키: 값' 머리말 → create_task 인자. 웹의 붙여 넣기 메시지(괄호 속 devtrack new 옵션)와 손으로 쓴 값 둘 다."""
    f = {}
    for k in ("related", "model", "review"):
        args = _new_args(head.get(k, ""))
        if args:
            f.update(args)
    if "agent" in head:
        f["agent"] = head["agent"].strip()
    if "type" in head:
        v = head["type"].strip()
        m = re.search(r"\((\w+)\)\s*$", v)
        cand = m.group(1) if m else v
        rev = {label: key for key, label in task_types.items()}
        f["type"] = cand.lower() if cand.lower() in task_types else rev.get(cand, rev.get(v, v))
    if "repo" in head:
        m = re.match(r"^(.*?)(?:\s+\(브랜치\s+(.+)\))?$", head["repo"].strip())
        f["repo"] = m.group(1).strip()
        if m.group(2):
            f["branch"] = m.group(2).strip()
    if head.get("branch"):
        f["branch"] = head["branch"].strip()
    if "priority" in head:
        f["priority"] = PRIORITY_WORDS.get(head["priority"].strip().lower(), head["priority"].strip())
    if "related" in head and "related" not in f:
        f["related"] = re.findall(r"T-?\d{1,6}", head["related"], re.I)
    if "model" in head and "model" not in f:
        v = head["model"].strip()
        if v and v.lower() not in CLEAR_WORDS:
            f["model"] = v.split()[0]
    if "effort" in head and "effort" not in f and head["effort"].strip():
        f["effort"] = head["effort"].strip().lower()
    if "review" in head and "review" not in f:
        f["review"] = head["review"].strip().split()[0] if head["review"].strip() else ""
    for k in ("review_rounds", "review_model", "review_effort", "review_doc", "review_venue"):
        if k in head and k not in f and head[k].strip():
            f[k] = head[k].strip()
    if "related" in f:
        f["related"] = [t for t in (_task_id(x) for x in f["related"]) if t]
    return f


def _selected(text):
    return [s for s in re.split(r"[\s,]+", text or "") if s]


def _paste(text, task_types):
    """웹의 붙여 넣기 메시지 ('[DevTrack …]' 로 시작) → 할 일. 해당하지 않으면 None."""
    text = (text or "").strip()
    first, _, rest = text.partition("\n")
    first = first.strip()
    if not first.startswith("[DevTrack "):
        return None
    body, trailer = strip_trailer(rest)
    if first.startswith("[DevTrack 새 작업 지시]"):
        head, remain = split_header(body)
        remain = re.sub(r"^\s*지시 내용\s*[:：]\s*\n?", "", remain).strip()
        f = task_fields(head, task_types)
        return dict(kind="new_task", title=(head.get("title") or "").strip(), instruction=remain, **f)
    m = re.match(r"^\[DevTrack 결정\]\s*(T-?\d+)\s*/\s*(A-?\d+)\b.*→\s*(\S.*?)\s*$", first)
    if m:
        sel, comment = None, ""
        lines = body.split("\n")
        for i, ln in enumerate(lines):
            if ln.startswith("선택:"):
                sel = _selected(ln[3:])
            elif ln.startswith("코멘트:"):
                comment = "\n".join([ln[4:].strip()] + lines[i + 1:]).strip()
                break
        return dict(kind="decide", task=_task_id(m.group(1)), approval=_item_id(m.group(2), "A"),
                    decision=_decision(m.group(3)), comment=comment, selected=sel)
    m = re.match(r"^\[DevTrack 작업 (취소|완료|삭제)\]\s*(T-?\d+)", first)
    if m:
        what, tid = m.group(1), _task_id(m.group(2))
        if what == "삭제":
            return dict(kind="refuse", task=tid, reason="작업 삭제는 GitHub 이슈로 하지 않습니다 — 되돌릴 수 없어 웹(실시간)이나 "
                                                         "터미널에서만 합니다 (devtrack delete {})".format(tid))
        label = "사유:" if what == "취소" else "메모:"
        note = body[len(label):].strip() if body.startswith(label) else body.strip()
        return dict(kind="cancel" if what == "취소" else "complete", task=tid, note=note)
    m = re.match(r"^\[DevTrack 메시지\]\s*(T-?\d+)(?:\s*·\s*결과물\s*(F-?\d+))?", first)
    if m:
        return dict(kind="comment", task=_task_id(m.group(1)),
                    artifact=_item_id(m.group(2), "F") if m.group(2) else None, message=body.strip())
    m = re.match(r"^\[DevTrack 모델 지정\]\s*(T-?\d+)", first)
    if m:
        c = re.search(r"\(devtrack model (.+?) --by-user\)", trailer or text)
        model = effort = None
        if c:
            try:
                words = shlex.split(c.group(1))[1:]
            except ValueError:
                words = []
            if "--clear" not in words:
                if "--effort" in words:
                    j = words.index("--effort")
                    effort = words[j + 1] if j + 1 < len(words) else None
                    words = words[:j] + words[j + 2:]
                model = words[0] if words else None
        return dict(kind="model", task=_task_id(m.group(1)), model=model, effort=effort)
    return dict(kind="unknown", reason="알 수 없는 DevTrack 메시지: {}".format(first[:60]))


def _command(line, rest, task):
    """첫 줄이 명령이면 그 할 일 (승인 A-2 · 수정 요청 A-2: … · 완료 · 취소 · 삭제 · 모델), 아니면 None."""
    line = line.strip()
    m = DECISION_RE.match(line)
    if m:
        word = m.group(1) or m.group(4)
        aid = m.group(2) or m.group(3)
        head, remain = split_header(rest)
        comment = "\n".join(x for x in [(m.group(5) or "").strip(), remain.strip()] if x)
        return dict(kind="decide", task=task, approval=_item_id(aid, "A"), decision=_decision(word),
                    comment=comment, selected=_selected(head["selected"]) if head.get("selected") else None)
    m = WORD_CMD_RE.match(line)
    if m:
        word = m.group(1).lower()
        note = "\n".join(x for x in [(m.group(2) or "").strip(), (rest or "").strip()] if x)
        if word in ("삭제", "delete"):
            return dict(kind="refuse", task=task, reason="작업 삭제는 GitHub 이슈로 하지 않습니다 — 되돌릴 수 없어 웹(실시간)이나 "
                                                          "터미널에서만 합니다 (devtrack delete {})".format(task))
        if word in ("모델", "model"):
            head, _ = split_header(rest)
            model = (head.get("model") or "").strip()
            effort = (head.get("effort") or "").strip().lower() or None
            if not model and not effort:
                return dict(kind="refuse", task=task, reason="모델 지정은 본문에 `모델: <이름>` · `강도: <강도>` 를 적어 주세요 "
                                                              "(해제는 `모델: 해제`)")
            return dict(kind="model", task=task, model=None if model.lower() in CLEAR_WORDS else model.split()[0] or None,
                        effort=effort)
        return dict(kind="complete" if word in ("완료", "done") else "cancel", task=task, note=note)
    return None


def parse_issue(title, body, labels=(), task_types=None, default_agent="Claude"):
    """새 이슈 → 할 일 dict (kind: new_task · comment · decide · complete · cancel · model · refuse · unknown)."""
    task_types = task_types or {}
    empty_paste = PASTE_HERE in (body or "")
    body = HTML_COMMENT_RE.sub("", body or "").strip()
    if empty_paste and not body:
        return dict(kind="refuse", reason="본문이 비어 있습니다 — 웹이 클립보드에 복사한 내용을 붙여 넣지 않고 제출했습니다. "
                                          "보내기 창에서 다시 'GitHub 이슈로 보내기' 를 눌러 새 이슈로 보내 주세요")
    act = _paste(body, task_types)
    if act:
        if act["kind"] == "new_task" and not act.get("title"):
            act["title"] = (title or "").strip()
        return _with_defaults(act, labels, task_types, default_agent)
    m = TITLE_TASK_RE.match(title or "")
    if m and _task_id(m.group(1)):
        tid, rest = _task_id(m.group(1)), m.group(2).strip()
        act = _command(rest, body, tid)
        if act:
            return act
        head, remain = split_header(body)
        msg = remain.strip() or re.sub(r"^(메시지|message)\s*[:：]?\s*", "", rest, flags=re.I).strip()
        if not msg:
            return dict(kind="refuse", task=tid, reason="보낼 내용이 없습니다 — 본문에 메시지를 적어 주세요")
        return dict(kind="comment", task=tid, message=msg,
                    artifact=_item_id(head["artifact"], "F") if head.get("artifact") else None)
    head, remain = split_header(body)
    f = task_fields(head, task_types)
    act = dict(kind="new_task", title=(head.get("title") or title or "").strip(), instruction=remain.strip(), **f)
    return _with_defaults(act, labels, task_types, default_agent)


def _with_defaults(act, labels, task_types, default_agent):
    if act["kind"] != "new_task":
        return act
    names = [str(x).strip() for x in labels or ()]
    low = [n.lower() for n in names]
    if not act.get("agent"):
        for n in names:
            if n.lower() in ("claude", "codex", "누구나", "anyone"):
                act["agent"] = {"claude": "Claude", "codex": "Codex", "anyone": "누구나"}.get(n.lower(), n)
                break
        else:
            act["agent"] = default_agent
    if not act.get("type"):
        act["type"] = next((n for n in low if n in task_types), "other")
    return act


def parse_comment(body, task, task_types=None):
    """대화 이슈의 댓글 → 할 일."""
    task_types = task_types or {}
    body = HTML_COMMENT_RE.sub("", body or "").strip()
    if not body:
        return dict(kind="refuse", task=task, reason="댓글이 비어 있습니다")
    act = _paste(body, task_types)
    if act:
        return act
    first, _, rest = body.partition("\n")
    act = _command(first, rest, task)
    if act:
        return act
    head, remain = split_header(body)
    return dict(kind="comment", task=task, message=remain.strip() or body,
                artifact=_item_id(head["artifact"], "F") if head.get("artifact") else None)


# ---------------------------------------------------------------------------------------------- 보낸 파일
def repo_file(url, repo):
    """우편함 저장소의 파일 링크면 [브랜치 …, 경로 …] 조각, 아니면 None.
    github.com/<저장소>/blob|raw/<브랜치>/<경로> 와 raw.githubusercontent.com/<저장소>/[refs/heads/]<브랜치>/<경로>."""
    u = urllib.parse.urlsplit(url)
    parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
    host = (u.hostname or "").lower()
    own = repo.lower().split("/")
    if host in ("github.com", "www.github.com") and len(parts) >= 5 and [p.lower() for p in parts[:2]] == own \
            and parts[2] in ("blob", "raw"):
        rest = parts[3:]
    elif host == "raw.githubusercontent.com" and len(parts) >= 4 and [p.lower() for p in parts[:2]] == own:
        rest = parts[2:]
        if rest[:2] == ["refs", "heads"] and len(rest) >= 4:
            rest = rest[2:]
    else:
        return None
    return rest


def find_links(text):
    """글 속 링크 → [(글에서 바꿀 부분, 이름, 주소)] — 마크다운 링크는 통째로, 맨 주소는 주소만."""
    text = text or ""
    out, spans = [], []
    for m in MD_LINK_RE.finditer(text):
        out.append((m.group(0), m.group(1).strip(), m.group(2)))
        spans.append(m.span())
    for m in URL_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        url = m.group(0).rstrip(".,;:!?")
        out.append((url, "", url))
    return out


def file_links(text, repo):
    """받을 파일 링크와 받을 수 없는 GitHub 첨부 링크 → [(바꿀 부분, 이름, 주소, 조각 또는 None)]."""
    seen, out = set(), []
    for match, label, url in find_links(text):
        rest = repo_file(url, repo)
        if (rest or GH_ATTACH_RE.match(url)) and url not in seen:
            seen.add(url)
            out.append((match, label, url, rest))
    return out


def _file_error(e):
    if e.status in (401, 403):
        return "토큰에 저장소 파일 읽기 권한(Contents: Read)이 없습니다 — 토큰 설정에서 더해 주세요"
    if e.status == 404:
        return "파일을 찾지 못했습니다 (지워졌거나 링크가 틀림)"
    if e.status == 413:
        return "{}MB 를 넘습니다".format(ATTACH_MAX // (1024 * 1024))
    return str(e)


def fetch_files(gh, text, out_dir):
    """글 속 우편함 저장소 파일 링크를 out_dir 에 내려받는다 → (받은 것, 못 받은 것).
    받은 것 = {match, url, filename, path, size}, 못 받은 것 = {match, url, name, reason}. 다른 사이트 링크는 건드리지 않는다."""
    got, failed = [], []
    for match, label, url, rest in file_links(text, gh.repo):
        name = os.path.basename(rest[-1]) if rest else (label or urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1]))
        if not rest:
            failed.append(dict(match=match, url=url, name=name, reason="GitHub 첨부(끌어다 놓기)는 비공개 저장소라 내려받을 수 없습니다 — "
                                                                       "웹 보내기 창의 '파일 올리기' 나 작업 이슈 첫 답글의 "
                                                                       "파일 올리기 링크로 보내 주세요"))
            continue
        if len(got) >= ATTACH_COUNT:
            failed.append(dict(match=match, url=url, name=name, reason="한 번에 {}개까지 받습니다".format(ATTACH_COUNT)))
            continue
        try:
            fpath, data = gh.download(rest)
        except GitHubError as e:
            failed.append(dict(match=match, url=url, name=name, reason=_file_error(e)))
            continue
        fname = os.path.basename(fpath) or name
        d = os.path.join(out_dir, str(len(got) + 1))
        os.makedirs(d)
        dest = os.path.join(d, fname)
        with open(dest, "wb") as fh:
            fh.write(data)
        got.append(dict(match=match, url=url, filename=fname, path=dest, size=len(data)))
    return got, failed


def upload_url(repo, branch, folder):
    """GitHub 의 '파일 올리기' 화면 — 아직 없는 폴더면 올릴 때 그 이름으로 만들어진다."""
    return "https://github.com/{}/upload/{}/{}".format(repo, urllib.parse.quote(branch, safe=""), urllib.parse.quote(folder))


def thread_folder(number):
    """웹을 거치지 않고 쓴 새 작업 이슈의 파일 폴더."""
    return "inbox/issue-{}".format(number)


def fetch_folder(gh, folder, ref, out_dir, known=None):
    """폴더에 새로 올라온 파일(처음 보거나 바뀐 것)을 내려받는다 → (받은 것, 못 받은 것, 본 파일 {경로: sha})."""
    known = dict(known or {})
    got, failed = [], []
    for f in sorted(gh.list_dir(folder, ref), key=lambda x: x["path"]):
        if known.get(f["path"]) == f["sha"]:
            continue
        known[f["path"]] = f["sha"]
        name = f["name"]
        if name.startswith("."):  # .gitkeep 같은 숨은 파일
            continue
        if len(got) >= FOLDER_MAX:
            failed.append(dict(match=None, url=f["path"], name=name, reason="한 번에 {}개까지 받습니다".format(FOLDER_MAX)))
            continue
        if f["size"] > ATTACH_MAX:
            failed.append(dict(match=None, url=f["path"], name=name, reason=_file_error(GitHubError("", 413))))
            continue
        try:
            data = gh.download_path(f["path"], ref)
        except GitHubError as e:
            failed.append(dict(match=None, url=f["path"], name=name, reason=_file_error(e)))
            continue
        d = os.path.join(out_dir, "d{}".format(len(got) + 1))
        os.makedirs(d)
        dest = os.path.join(d, name)
        with open(dest, "wb") as fh:
            fh.write(data)
        got.append(dict(match=None, url=f["path"], filename=name, path=dest, size=len(data)))
    return got, failed, known


def link_files(text, tid, got, arts, failed):
    """글 속 링크를 결과물 링크(📎 F-1 이름)로 바꾼다. 글에 없던 링크(머리말 등에 있던 것)는 끝에 모아 붙인다."""
    out, extra = text or "", []
    for g, a in zip(got, arts):
        ref = "[📎 {} {}](#/artifact/{}/{})".format(a["id"], a.get("filename") or g["filename"], tid, a["id"])
        if g.get("match") and g["match"] in out:
            out = out.replace(g["match"], ref)
        else:
            extra.append(ref)
    for f in failed:
        note = "📎 {} (받지 못함)".format(f["name"] or f["url"])
        if f.get("match") and f["match"] in out:
            out = out.replace(f["match"], note)
        else:
            extra.append(note)
    if extra:
        out = "{}\n\n보낸 파일: {}".format(out.rstrip(), " · ".join(extra)).strip()
    return out


def files_summary(arts, failed):
    """답글에 붙일 보낸 파일 결과 (없으면 빈 글)."""
    lines = []
    if arts:
        lines.append("📎 보낸 파일 {}개를 결과물로 올렸습니다: {}".format(
            len(arts), " · ".join("{} {}{}".format(a["id"], a.get("filename"), " (v{})".format(a["version"])
                                                   if a.get("version", 1) > 1 else "") for a in arts)))
    for f in failed:
        lines.append("⚠️ 받지 못한 파일 **{}** — {}".format(f["name"] or f["url"], f["reason"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- 기록에 반영
def apply(store, act, issue_no, user, got=None, failed=None):
    """할 일을 Store 에 기록한다 (웹의 실시간 모드와 같은 호출). → (작업 ID, 답글 한 줄).
    got · failed: fetch_files 의 결과 — 새 작업 · 메시지면 '보낸 파일' 결과물로 올리고 글의 링크를 결과물 번호로 바꾼다."""
    got, failed = got or [], failed or []
    from .store import TASK_TYPES, PRIORITIES
    kind = act.get("kind")
    tid = act.get("task")
    if kind in ("refuse", "unknown"):
        raise DevTrackError(act.get("reason") or "처리할 수 없는 내용입니다")
    if kind == "new_task":
        if not act.get("title"):
            raise DevTrackError("제목이 없습니다")
        if act.get("type") not in TASK_TYPES:
            raise DevTrackError("알 수 없는 유형: {} — {} 중 하나".format(act.get("type"), ", ".join(TASK_TYPES)))
        if act.get("priority", "normal") not in PRIORITIES:
            raise DevTrackError("우선순위는 높음 · 보통 · 낮음 중 하나")
        review = None
        if act.get("review"):
            rounds = act.get("review_rounds")
            review = {"reviewer": act["review"], "rounds": int(rounds) if str(rounds or "").isdigit() else None,
                      "model": act.get("review_model"), "effort": act.get("review_effort"),
                      "doc": act.get("review_doc"), "venue": act.get("review_venue")}
        t = store.create_task(act["title"], instruction=act.get("instruction") or "", type=act["type"],
                              agent=act.get("agent") or "Claude", repo=act.get("repo") or None,
                              branch=act.get("branch") or None, priority=act.get("priority") or "normal",
                              status="queued", created_by=user, related=act.get("related") or [],
                              model=act.get("model") or None, effort=act.get("effort") or None, review=review,
                              tags=["issue-{}".format(issue_no)])
        arts = []
        if got or failed:
            arts = store.add_sent_files(t["id"], got, issue=issue_no, rewrite=lambda a: link_files(
                act.get("instruction") or "", t["id"], got, a, failed))
        return t["id"], "\n".join(x for x in ["✅ **{}** 로 등록했습니다 — 담당: {} · 유형: {}".format(
            t["id"], t.get("agent") or "-", TASK_TYPES.get(t["type"], t["type"])), files_summary(arts, failed)] if x)
    if not tid:
        raise DevTrackError("작업 번호를 찾지 못했습니다")
    if kind == "comment":
        if not (act.get("message") or "").strip():
            raise DevTrackError("보낼 내용이 없습니다")
        store.load(tid)  # 없는 작업이면 파일을 올리기 전에 멈춘다
        arts = store.add_sent_files(tid, got, issue=issue_no) if got else []
        message = link_files(act["message"], tid, got, arts, failed) if (got or failed) else act["message"]
        store.add_user_comment(tid, message, artifact=act.get("artifact"), attachments=[a["id"] for a in arts])
        return tid, "\n".join(x for x in ["✅ **{}** 에 메시지를 보냈습니다{}".format(
            tid, " (결과물 {})".format(act["artifact"]) if act.get("artifact") else ""), files_summary(arts, failed)] if x)
    if kind == "decide":
        if not act.get("decision"):
            raise DevTrackError("결정을 읽지 못했습니다 (승인 · 수정 요청 · 거절)")
        if act["decision"] == "changes" and not (act.get("comment") or "").strip():
            raise DevTrackError("수정 요청에는 무엇을 고칠지 적어 주세요 (`수정 요청 A-1: <이유>`)")
        store.decide(tid, act["approval"], act["decision"], comment=act.get("comment") or "", by=user,
                     selected=act.get("selected"))
        word = {"approve": "승인", "changes": "수정 요청", "reject": "거절"}[act["decision"]]
        return tid, "✅ **{} {}** — {} 으로 기록했습니다".format(tid, act["approval"], word)
    if kind == "complete":
        store.complete_task(tid, note=act.get("note") or "", author=user)
        return tid, "✅ **{}** 를 완료로 처리했습니다".format(tid)
    if kind == "cancel":
        store.cancel_task(tid, reason=act.get("note") or "", author=user)
        return tid, "✅ **{}** 를 취소했습니다".format(tid)
    if kind == "model":
        store.set_model(tid, act.get("model"), act.get("effort"), author=user)
        return tid, "✅ **{}** 의 모델을 {} 로 정했습니다".format(
            tid, " · ".join(x for x in (act.get("model"), act.get("effort")) if x) or "지정 해제")
    raise DevTrackError(act.get("reason") or "처리할 수 없는 내용입니다")


HELP = ("이 이슈에 댓글을 달면 이 작업에 보내는 메시지가 됩니다 — `승인 A-1` · `수정 요청 A-1: <이유>` · `거절 A-1: <이유>` · "
        "`완료` · `취소` 도 됩니다. 작업이 끝나면 이 이슈를 닫습니다.")


class Poller(object):
    """`devtrack watch` · `inbox --wait` 가 기다리는 동안 부르는 우편함 확인 (설정이 없으면 아무것도 하지 않는다).
    여러 대기 프로세스가 함께 켜져 있어도 상태 파일의 시각으로 한 번만 확인한다. 오류는 같은 내용이면 한 번만 알린다."""

    def __init__(self, store, user="user", out=None, interval=None):
        self.store = store
        self.user = user
        self.out = out or (lambda s: sys.stderr.write(s + "\n"))
        self.interval = interval
        self._next = 0
        self._said = None

    def maybe(self):
        if time.time() < self._next:
            return None
        cfg = load_config(self.store.root)
        if not cfg:
            self._next = time.time() + 60
            return None
        every = int(self.interval or cfg.get("interval") or DEFAULT_INTERVAL)
        self._next = time.time() + every
        try:
            r = poll(self.store, cfg=cfg, user=self.user, every=every)
        except Exception as e:  # 대기는 계속되어야 한다
            r = {"error": str(e)}
        if r is None:
            return None
        err = r.get("error")
        if err and err != self._said:
            self.out("[devtrack] GitHub 우편함 확인 실패: {}".format(err))
        elif not err and self._said:
            self.out("[devtrack] GitHub 우편함 다시 연결됨")
        self._said = err
        for line in r.get("done", []):
            self.out("[devtrack] GitHub 우편함: {}".format(line))
        return r


def _lease(store, every, force):
    """이번에 확인할 차례인지 (다른 대기 프로세스가 방금 확인했거나 확인 중이면 False)."""
    with store.locked():
        st = load_state(store.root)
        now = time.time()
        if not force and (now - st.get("last_poll", 0) < every - 1 or now < st.get("busy_until", 0)):
            return False
        st["last_poll"] = now
        st["busy_until"] = now + 300
        save_state(store.root, st)
    return True


def poll(store, cfg=None, user="user", every=DEFAULT_INTERVAL, force=False, gh=None):
    """우편함을 한 번 확인한다. → {"done": [한 줄 …], "error": …} · 차례가 아니면 None."""
    cfg = cfg or load_config(store.root)
    if not cfg:
        return None
    token, _ = load_token()
    if not token and gh is None:
        return {"error": "GitHub 토큰이 없습니다 — devtrack mailbox {} 로 다시 설정하세요".format(cfg["github"]), "done": []}
    if not _lease(store, every, force):
        return None
    gh = gh or GitHub(token, cfg["github"])
    st = load_state(store.root)
    done = []
    try:
        _poll(store, cfg, st, gh, user, done)
        st["last_error"] = None
    except GitHubError as e:
        st["last_error"] = str(e)
        st["last_error_at"] = iso_now()
        return {"error": str(e), "done": done}
    finally:
        with store.locked():
            cur = load_state(store.root)
            st["last_poll"] = cur.get("last_poll", st.get("last_poll"))
            st["busy_until"] = 0
            save_state(store.root, st)
    return {"done": done}


def _site(cfg, st):
    s = (cfg.get("site") or st.get("site") or "").strip()
    return s if s.endswith("/") or not s else s + "/"


def _link(cfg, st, tid, page=""):
    s = _site(cfg, st)
    return " — {}#/task/{}{}".format(s, tid, page) if s else ""


def _linked(msg, link):
    """답글의 첫 줄(처리 결과) 끝에 웹 링크를 붙인다 — 그 아래 줄은 보낸 파일 결과."""
    first, sep, rest = msg.partition("\n")
    return first + link + sep + rest


def _poll(store, cfg, st, gh, user, done):
    from .store import TASK_TYPES
    login = cfg.get("login") or ""
    since_i = st.get("since_issues") or cfg.get("since") or iso_now()
    since_c = st.get("since_comments") or cfg.get("since") or iso_now()
    issues = [i for i in gh.issues_since(since_i) if "pull_request" not in i]
    comments = gh.comments_since(since_c)
    ignored = set(st["ignored"])
    for it in sorted(issues, key=lambda i: i.get("number", 0)):
        key = str(it["number"])
        st["since_issues"] = max(st.get("since_issues") or "", it.get("updated_at") or "")
        if key in st["issues"]:
            if it.get("state") == "closed" and st["issues"][key].get("kind") == "thread":  # 사용자가 GitHub 에서 닫았으면 더 알리지 않는다
                st["issues"][key]["closed"] = True
                _drop_folders(st, key)
            continue
        if it["number"] in ignored:
            continue
        if ((it.get("user") or {}).get("login") or "") != login:
            st["ignored"].append(it["number"])
            done.append("#{} 는 다른 계정({})이 쓴 이슈라 건너뜀".format(it["number"], (it.get("user") or {}).get("login")))
            continue
        if it.get("state") == "closed":
            st["ignored"].append(it["number"])
            continue
        m = SITE_RE.search(it.get("body") or "")
        if m:
            st["site"] = m.group(1)
        labels = [(x.get("name") if isinstance(x, dict) else x) for x in it.get("labels") or []]
        act = parse_issue(it.get("title"), it.get("body"), labels, TASK_TYPES, cfg.get("default_agent") or "Claude")
        fm = FOLDER_RE.search(it.get("body") or "")
        folder = fm.group(1) if fm else None
        try:
            tid, msg, seen = _apply_with_files(store, gh, act, it["number"], user, it.get("body"),
                                               branch=_branch(cfg, st, gh) if folder else None, folder=folder)
        except DevTrackError as e:
            st["issues"][key] = {"task": act.get("task"), "kind": "error", "closed": True}
            _say(gh, st, it["number"], "⚠️ 처리하지 못했습니다: {}".format(e))
            _close(gh, it["number"], "not_planned")
            done.append("#{} 처리 실패: {}".format(it["number"], e))
            continue
        if act["kind"] == "new_task":
            st["issues"][key] = {"task": tid, "kind": "thread", "closed": False, "notified": []}
            tf = folder or thread_folder(it["number"])
            if cfg.get("files") is not False:
                st["folders"][tf] = {"task": tid, "issue": it["number"], "seen": seen or {}}
            _say(gh, st, it["number"], "{}\n\n{}".format(_linked(msg, _link(cfg, st, tid)), _help(cfg, st, gh, tf)))
        else:
            st["issues"][key] = {"task": tid, "kind": "oneoff", "closed": True}
            if folder and act["kind"] == "comment" and cfg.get("files") is not False:
                st["folders"][folder] = {"task": tid, "issue": it["number"], "seen": seen or {},
                                         "until": time.time() + FOLDER_WAIT}
            thread = next((n for n, v in st["issues"].items() if v.get("task") == tid and v.get("kind") == "thread"
                           and not v.get("closed")), None)
            more = "\n\n이어서 보낼 내용은 이 작업의 이슈 #{} 에 댓글로 달아도 됩니다.".format(thread) if thread else ""
            _say(gh, st, it["number"], "{}{}".format(_linked(msg, _link(cfg, st, tid)), more))
            _close(gh, it["number"], "completed")
        done.append("#{} → {}".format(it["number"], msg.replace("✅ ", "").replace("**", "")))
    seen = set(st["comments"]) | set(st["posted"])
    for c in sorted(comments, key=lambda c: c.get("created_at") or ""):
        st["since_comments"] = max(st.get("since_comments") or "", c.get("updated_at") or "")
        cid = c.get("id")
        if cid in seen or BOT_MARK in (c.get("body") or ""):
            continue
        st["comments"].append(cid)
        seen.add(cid)
        num = (c.get("issue_url") or "").rstrip("/").rsplit("/", 1)[-1]
        info = st["issues"].get(num)
        if not info or info.get("kind") != "thread":
            continue
        if ((c.get("user") or {}).get("login") or "") != login:
            done.append("#{} 의 댓글은 다른 계정이 써서 건너뜀".format(num))
            continue
        act = parse_comment(c.get("body"), info["task"], TASK_TYPES)
        try:
            tid, msg, _ = _apply_with_files(store, gh, act, num, user, c.get("body"))
        except DevTrackError as e:
            _say(gh, st, num, "⚠️ 처리하지 못했습니다: {}".format(e))
            done.append("#{} 댓글 처리 실패: {}".format(num, e))
            continue
        try:
            gh.react(cid, "+1")
            if "\n" in msg:  # 보낸 파일 결과는 반응만으로는 알 수 없다
                _say(gh, st, num, msg)
        except GitHubError:
            _say(gh, st, num, msg)
        done.append("#{} 댓글 → {}".format(num, msg.replace("✅ ", "").replace("**", "")))
    _watch_folders(store, cfg, st, gh, done)
    _follow_up(store, cfg, st, gh, done)


def _apply_with_files(store, gh, act, number, user, body, branch=None, folder=None):
    """apply + 보낸 파일 → (작업 ID, 답글, 폴더에서 본 파일 {경로: sha} 또는 None).
    새 작업 · 메시지면 글 속 저장소 파일 링크와 폴더(파일 올리기)의 파일을 내려받아 함께 넘기고, 다른 할 일이면 파일은 받지 않는다고 덧붙인다."""
    text = HTML_COMMENT_RE.sub("", body or "")
    links = file_links(text, gh.repo)
    if act.get("kind") not in ("new_task", "comment"):
        tid, msg = apply(store, act, number, user)
        if folder or any(rest for _, _, _, rest in links):
            msg += "\n📎 파일은 새 작업 · 메시지에서만 받습니다 — 파일은 메시지로 다시 보내 주세요"
        return tid, msg, None
    if not links and not folder:
        tid, msg = apply(store, act, number, user)
        return tid, msg, None
    tmp = tempfile.mkdtemp(prefix="devtrack-mail-")
    try:
        got, failed = fetch_files(gh, text, tmp) if links else ([], [])
        seen = None
        if folder:
            try:
                more, bad, seen = fetch_folder(gh, folder, branch or "main", tmp)
                got, failed = got + more, failed + bad
            except GitHubError as e:
                failed.append(dict(match=None, url=folder, name=folder + "/", reason=_file_error(e)))
        tid, msg = apply(store, act, number, user, got=got, failed=failed)
        return tid, msg, seen
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _branch(cfg, st, gh):
    """우편함 저장소의 기본 브랜치 (설정 → 지난번에 알아 둔 것 → GitHub 에 물어봄)."""
    b = cfg.get("branch") or st.get("branch")
    if not b:
        try:
            b = (gh.repo_info() or {}).get("default_branch") or "main"
        except GitHubError:
            return "main"
        st["branch"] = b
    return b


def _help(cfg, st, gh, folder):
    """새 작업 이슈의 첫 답글 — 댓글로 할 수 있는 일과, 이 작업에 파일을 보내는 '파일 올리기' 주소."""
    if cfg.get("files") is False:
        return HELP
    return HELP + ("\n\n📎 이 작업에 파일을 보내려면 [파일 올리기]({}) 로 파일을 끌어다 놓고 **Commit changes** 를 누르세요 — "
                   "올라온 파일은 이 작업에 메시지로 들어갑니다.".format(upload_url(gh.repo, _branch(cfg, st, gh), folder)))


def _drop_folders(st, number):
    for k in [k for k, v in st["folders"].items() if str(v.get("issue")) == str(number)]:
        del st["folders"][k]


def _watch_folders(store, cfg, st, gh, done):
    """지켜보는 폴더(파일 올리기)에 새로 올라온 파일을 그 작업에 메시지로 보낸다. 저장소의 브랜치 머리가 바뀌었을 때만 폴더를 본다."""
    folders = st["folders"]
    for k in [k for k, v in folders.items() if v.get("until") and v["until"] < time.time()]:
        del folders[k]
    active = [(k, v) for k, v in folders.items() if not v.get("error")]
    if not active:
        return
    branch = _branch(cfg, st, gh)
    head = gh.branch_head(branch)
    if head and head == st.get("head"):
        return
    for folder, info in active:
        tmp = tempfile.mkdtemp(prefix="devtrack-mail-")
        try:
            try:
                got, failed, seen = fetch_folder(gh, folder, branch, tmp, info.get("seen"))
            except GitHubError as e:
                if e.status not in (401, 403):
                    raise
                info["error"] = _file_error(e)
                _say(gh, st, info["issue"], "⚠️ 올린 파일을 받지 못했습니다 ({}) — {}".format(folder, info["error"]))
                continue
            info["seen"] = seen
            if not got and not failed:
                continue
            tid = info["task"]
            try:
                arts = store.add_sent_files(tid, got, issue=info["issue"]) if got else []
                store.add_user_comment(tid, link_files("", tid, got, arts, failed),
                                       attachments=[a["id"] for a in arts])
            except DevTrackError as e:  # 작업이 지워진 경우 등
                info["error"] = str(e)
                continue
            summary = files_summary(arts, failed)
            _say(gh, st, info["issue"], "**{}** 에 파일이 왔습니다{}\n{}".format(tid, _link(cfg, st, tid), summary))
            done.append("#{} 폴더 {} → {}: {}".format(info["issue"], folder, tid, summary.replace("**", "")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    st["head"] = head


def _say(gh, st, number, text):
    try:
        c = gh.comment(number, text)
        if c and c.get("id"):
            st["posted"].append(c["id"])
    except GitHubError as e:
        st["last_error"] = str(e)


def _close(gh, number, reason):
    try:
        gh.close(number, reason)
    except GitHubError:
        pass


def _follow_up(store, cfg, st, gh, done):
    """대화 이슈: 새 승인 요청은 댓글로 알리고, 작업이 끝나면 닫는다."""
    for num, info in st["issues"].items():
        if info.get("kind") != "thread" or info.get("closed"):
            continue
        tid = info["task"]
        try:
            t = store.load(tid)
        except DevTrackError:
            _say(gh, st, num, "🗑 **{}** 이(가) 삭제되어 이 이슈를 닫습니다.".format(tid))
            _close(gh, num, "not_planned")
            info["closed"] = True
            _drop_folders(st, num)
            continue
        notified = info.setdefault("notified", [])
        for a in t.get("approvals", []):
            if a.get("status") != "pending" or a["id"] in notified:
                continue
            notified.append(a["id"])
            choices = a.get("choices") or []
            pick = ("\n선택할 항목: {}\n고를 때는 두 번째 줄에 `선택: {}` 처럼 적습니다.".format(
                ", ".join("{}{}".format(x.get("id"), " ({})".format(x.get("label") or x.get("title"))
                                        if x.get("label") or x.get("title") else "")
                          for x in choices if isinstance(x, dict)),
                ", ".join(x.get("id") for x in choices[:2] if isinstance(x, dict))) if choices else "")
            _say(gh, st, num, "🔔 **{}** 승인 요청: {}{}{}\n\n답글로 `승인 {}` · `수정 요청 {}: <이유>` · `거절 {}: <이유>`".format(
                a["id"], a.get("title") or "", _link(cfg, st, tid, "/approvals"), pick, a["id"], a["id"], a["id"]))
            done.append("#{} 에 {} 승인 요청을 알림".format(num, a["id"]))
        if t.get("status") in ("done", "cancelled"):
            word = "완료되었습니다 🏁" if t["status"] == "done" else "취소되었습니다"
            _say(gh, st, num, "**{}** 이(가) {}{}".format(tid, word, _link(cfg, st, tid)))
            _close(gh, num, "completed" if t["status"] == "done" else "not_planned")
            info["closed"] = True
            _drop_folders(st, num)
            done.append("#{} 닫음 ({} {})".format(num, tid, t["status"]))
