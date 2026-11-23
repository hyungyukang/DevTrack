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
"""

import json
import os
import re
import shlex
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
    """붙여 넣기 메시지 끝의 AI 용 안내 문단을 뗀다 → (본문, 안내 문단)."""
    paras = re.split(r"\n\s*\n", (text or "").strip())
    if paras and paras[-1].strip().startswith(TRAILERS):
        return "\n\n".join(paras[:-1]).strip(), paras[-1]
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


# ---------------------------------------------------------------------------------------------- 기록에 반영
def apply(store, act, issue_no, user):
    """할 일을 Store 에 기록한다 (웹의 실시간 모드와 같은 호출). → (작업 ID, 답글 한 줄)."""
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
        return t["id"], "✅ **{}** 로 등록했습니다 — 담당: {} · 유형: {}".format(
            t["id"], t.get("agent") or "-", TASK_TYPES.get(t["type"], t["type"]))
    if not tid:
        raise DevTrackError("작업 번호를 찾지 못했습니다")
    if kind == "comment":
        if not (act.get("message") or "").strip():
            raise DevTrackError("보낼 내용이 없습니다")
        store.add_user_comment(tid, act["message"], artifact=act.get("artifact"))
        return tid, "✅ **{}** 에 메시지를 보냈습니다{}".format(tid, " (결과물 {})".format(act["artifact"])
                                                           if act.get("artifact") else "")
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
            if it.get("state") == "closed":  # 사용자가 GitHub 에서 닫았으면 더 알리지 않는다
                st["issues"][key]["closed"] = True
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
        try:
            tid, msg = apply(store, act, it["number"], user)
        except DevTrackError as e:
            st["issues"][key] = {"task": act.get("task"), "kind": "error", "closed": True}
            _say(gh, st, it["number"], "⚠️ 처리하지 못했습니다: {}".format(e))
            _close(gh, it["number"], "not_planned")
            done.append("#{} 처리 실패: {}".format(it["number"], e))
            continue
        if act["kind"] == "new_task":
            st["issues"][key] = {"task": tid, "kind": "thread", "closed": False, "notified": []}
            _say(gh, st, it["number"], "{}{}\n\n{}".format(msg, _link(cfg, st, tid), HELP))
        else:
            st["issues"][key] = {"task": tid, "kind": "oneoff", "closed": True}
            thread = next((n for n, v in st["issues"].items() if v.get("task") == tid and v.get("kind") == "thread"
                           and not v.get("closed")), None)
            more = "\n\n이어서 보낼 내용은 이 작업의 이슈 #{} 에 댓글로 달아도 됩니다.".format(thread) if thread else ""
            _say(gh, st, it["number"], "{}{}{}".format(msg, _link(cfg, st, tid), more))
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
            tid, msg = apply(store, act, num, user)
        except DevTrackError as e:
            _say(gh, st, num, "⚠️ 처리하지 못했습니다: {}".format(e))
            done.append("#{} 댓글 처리 실패: {}".format(num, e))
            continue
        try:
            gh.react(cid, "+1")
        except GitHubError:
            _say(gh, st, num, msg)
        done.append("#{} 댓글 → {}".format(num, msg.replace("✅ ", "").replace("**", "")))
    _follow_up(store, cfg, st, gh, done)


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
            done.append("#{} 닫음 ({} {})".format(num, tid, t["status"]))
