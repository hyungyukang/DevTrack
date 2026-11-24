"""DevTrack 저장소 — 작업(task) 단위 JSON 파일.

디렉토리 구조::

    <data>/index.json                    대시보드용 요약 (저장할 때마다 재생성)
    <data>/tasks/T-0001/task.json        작업 전체 기록 (지시, 계획, 로그, 문제, 결과물, 승인)
    <data>/tasks/T-0001/artifacts/...    결과물 파일

웹 화면은 이 파일들을 정적으로 읽기만 하므로 서버 없는 정적 호스팅
(예: 웹 서버의 공개 폴더)에서도 그대로 동작한다.
"""

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

from . import DevTrackError, access, gitops, mailbox, manuscript, models

# 데모 데이터 생성 시 시간을 조작하기 위한 훅 (callable -> datetime)
_clock = None


def now():
    t = _clock() if _clock else _dt.datetime.now()
    return t.astimezone().isoformat(timespec="seconds")


TASK_TYPES = {
    "code": "코드 개발",
    "review": "코드 리뷰",
    "paper": "논문 작성",
    "docreview": "문서 리뷰",
    "analysis": "데이터 분석",
    "visualization": "시각화",
    "model": "AI 모델 개발",
    "brainstorm": "브레인스토밍",
    "other": "기타",
}

STATUSES = {
    "queued": "대기",
    "in_progress": "진행 중",
    "awaiting_approval": "승인 대기",
    "changes_requested": "수정 요청",
    "blocked": "막힘",
    "done": "완료",
    "cancelled": "취소",
}

LOG_KINDS = (
    "info", "step", "success", "warning", "error", "issue",
    "user", "approval", "decision", "commit", "artifact", "status",
    "agent",  # 다른 AI 가 보낸 메시지 (devtrack send · 리뷰 요청 · 리뷰 결과) — 그 작업의 AI 받은 편지함으로 간다
)

# AI 끼리 리뷰 반복 (개발 작업의 review_loop)
REVIEW_ROUNDS_DEFAULT = 3
REVIEW_ROUNDS_MAX = 10
REVIEW_STATES = {"planned": "리뷰 전", "reviewing": "리뷰 중", "changes": "수정 필요", "approved": "통과",
                 "exhausted": "리뷰 횟수 다 씀", "rejected": "게재 불가"}
# 논문 · 문서 심사의 판정 (DOC_VERDICTS) — 저널 심사의 이름으로
JOURNAL_VERDICT_LABEL = {"ready": "게재 가능 (accept)", "minor_revision": "소폭 수정 (minor revision)",
                         "major_revision": "대폭 수정 (major revision)", "rework": "게재 불가 (reject)"}
REVIEW_DOC_TYPES = ("paper",)  # 이 유형의 작업은 코드 diff 대신 원고를 심사받는다
# 논문 작업의 원고 폴더 tasks/<T>/paper — 결과물(최신 .tex · .bib 와 원고가 부르는 그림)에서 DevTrack 이 다시 만든다
PAPER_TYPES = ("paper",)
PAPER_DIR = "paper"
PAPER_FIGURES = "figures"  # \graphicspath 도 그림 폴더도 없는 원고의 그림을 둘 곳 (권장: \graphicspath{{figures/}})
PAPER_SOURCE_EXT = (".tex", ".bib", ".sty", ".cls", ".bst")
_REVIEW_PASS = ("approve", "approved", "pass", "passed", "ok", "lgtm", "comment", "통과")
_REVIEW_CHANGES = ("changes", "change", "request_changes", "changes_requested", "fix", "reject", "수정", "수정필요")
SEVERITIES = ("low", "medium", "high", "critical")
PRIORITIES = ("low", "normal", "high")
APPROVAL_KINDS = ("general", "commit", "plan", "review", "result", "merge")
DECISIONS = {
    "approve": "approved", "approved": "approved", "ok": "approved",
    "reject": "rejected", "rejected": "rejected",
    "changes": "changes_requested", "changes_requested": "changes_requested", "revise": "changes_requested",
}
DECISION_LABEL = {"approved": "승인", "rejected": "거절", "changes_requested": "수정 요청"}

USER = "user"  # 사용자가 남긴 기록의 author 값

# 인계 메모 — 작업마다 '이 작업에서 알아낸 것' 을 짧게 (tasks/<T>/notes.md). 다른 작업 · 다음 대화가 이 작업을 참고할 때
# 기록 전체 · 리포트 · 파일을 다시 뒤지지 않고 먼저 읽는다 (devtrack show <T> --brief).
NOTE_FILE = "notes.md"
NOTE_PREV = "notes.prev.md"   # 바로 전 판 (AI 가 사용자가 고친 메모를 덮어썼을 때 되살릴 수 있게)
NOTE_SOFT = 6000              # 자 — 넘으면 줄이라고 알린다 (대략 2–3천 토큰)
NOTE_MAX = 40000
NOTE_STALE = 15               # 메모를 쓴 뒤 기록이 이만큼 쌓이면 고쳐 두라고 알린다
NOTE_SECTIONS = ("목적", "결론 (지금까지)", "핵심 숫자 (단위 · 기간 · 기준 포함)", "파일 · 데이터 위치", "재현 · 실행 명령",
                 "결정 · 사용자 지시", "주의할 점 (함정 · 실패한 시도)", "남은 일 · 열린 질문")

# 담당 AI 가 이 값이면 아직 정해지지 않은 작업 — 어느 AI 든 먼저 시작한 쪽이 맡는다
UNASSIGNED_AGENTS = ("", "누구나", "기타", "any", "*")


def agent_key(name):
    return " ".join(str(name or "").split()).lower()


def is_unassigned(name):
    return agent_key(name) in UNASSIGNED_AGENTS


def agent_matches(task_agent, agent):
    """agent 가 None 이면 모든 작업. 담당 AI 가 정해지지 않은 작업은 모든 AI 에게 해당한다."""
    return agent is None or is_unassigned(task_agent) or agent_key(task_agent) == agent_key(agent)

# 코드 리뷰 검토 항목(findings) — review-pr 스킬의 R1, R2… 와 같은 체계
FINDING_SEVERITIES = ("blocker", "major", "minor", "nit", "question")
FINDING_SEV_LABEL = {"blocker": "반드시 수정", "major": "수정 권장", "minor": "개선 제안", "nit": "사소함",
                     "question": "질문"}
FINDING_STATUSES = ("open", "fixed", "commented", "dismissed", "na")
VERDICTS = ("approve", "comment", "request_changes")
_SEV_ALIAS = {
    "critical": "blocker", "high": "major", "medium": "minor", "moderate": "minor", "low": "nit",
    "trivial": "nit", "style": "nit", "q": "question", "info": "question",
    "반드시 수정": "blocker", "필수": "blocker", "치명": "blocker", "심각": "blocker",
    "수정 권장": "major", "높음": "major", "중요": "major",
    "개선 제안": "minor", "보통": "minor", "중간": "minor", "사소함": "nit", "낮음": "nit", "질문": "question",
}
_STATUS_ALIAS = {
    "still open": "open", "still_open": "open", "new": "open", "pending": "open",
    "resolved": "fixed", "done": "fixed", "addressed": "fixed",
    "posted": "commented", "comment": "commented",
    "wontfix": "dismissed", "rejected": "dismissed", "skipped": "dismissed", "skip": "dismissed",
    "no longer applicable": "na", "not applicable": "na", "not_applicable": "na", "n/a": "na", "obsolete": "na",
}
_VERDICT_ALIAS = {"approved": "approve", "lgtm": "approve", "comments": "comment",
                  "request changes": "request_changes", "request-changes": "request_changes",
                  "changes": "request_changes", "changes_requested": "request_changes"}
_PR_KEYS = ("url", "repo", "number", "title", "author", "base", "head", "head_sha", "files", "additions",
            "deletions", "ci", "draft", "previous_sha")

# 문서 리뷰 — 리뷰한 문서(doc) · 종합 의견 · 점검 영역(checks). 검토 항목은 코드 리뷰와 같은 R1, R2…
DOC_KINDS = {"paper": "과학 논문", "report": "보고서", "proposal": "제안서", "thesis": "학위 논문", "other": "기타 문서"}
_DOC_KIND_ALIAS = {
    "논문": "paper", "과학 논문": "paper", "article": "paper", "manuscript": "paper", "journal": "paper",
    "conference": "paper", "보고서": "report", "제안서": "proposal", "학위 논문": "thesis", "학위논문": "thesis",
    "dissertation": "thesis", "기타": "other", "기타 문서": "other",
}
DOC_VERDICTS = ("ready", "minor_revision", "major_revision", "rework")
DOC_VERDICT_LABEL = {"ready": "제출 가능", "minor_revision": "소폭 수정", "major_revision": "대폭 보완",
                     "rework": "다시 작성"}
_DOC_VERDICT_ALIAS = {
    "accept": "ready", "accepted": "ready", "ok": "ready", "제출 가능": "ready",
    "minor": "minor_revision", "minor revision": "minor_revision", "minor-revision": "minor_revision", "소폭 수정": "minor_revision",
    "major": "major_revision", "major revision": "major_revision", "major-revision": "major_revision", "대폭 보완": "major_revision",
    "reject": "rework", "rewrite": "rework", "resubmit": "rework", "다시 작성": "rework",
}
CHECK_RATINGS = ("good", "fair", "poor", "na")
_CHECK_ALIAS = {
    "ok": "good", "pass": "good", "양호": "good", "좋음": "good",
    "partial": "fair", "warn": "fair", "warning": "fair", "보완": "fair", "보완 필요": "fair",
    "bad": "poor", "fail": "poor", "미흡": "poor", "n/a": "na", "none": "na", "해당 없음": "na",
}
_DOC_KEYS = ("title", "kind", "purpose", "audience", "authors", "pages", "words", "format", "language", "source",
             "synopsis")


def _norm_finding_id(value, fallback):
    s = str(value if value is not None else fallback).strip()
    m = re.match(r"^[A-Za-z]{0,3}\s*-?\s*(\d+)$", s)
    return "R{}".format(int(m.group(1))) if m else (s or "R{}".format(fallback))


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def normalize_finding(raw, n, base=None):
    """review-pr 스킬 JSON 등 다양한 표기를 받아들여 검토 항목 하나로 정리한다.
    base(같은 ID 의 기존 항목)가 있으면 주어진 필드만 덮어쓴다 — 상태만 갱신하는 것도 가능."""
    if not isinstance(raw, dict):
        raw = {"title": str(raw)}
    f = {"id": _norm_finding_id(_first(raw, "id", "number", "num"), n)}
    sev = _first(raw, "severity", "level", "priority")
    if sev is not None:
        sev = str(sev).strip().lower()
        f["severity"] = sev if sev in FINDING_SEVERITIES else _SEV_ALIAS.get(sev, "minor")
    if raw.get("status"):
        st = str(raw["status"]).strip().lower()
        f["status"] = st if st in FINDING_STATUSES else _STATUS_ALIAS.get(st, "open")
    conf = str(raw.get("confidence") or "").strip().lower()
    if conf in ("high", "medium", "low"):
        f["confidence"] = conf
    line = raw.get("line")
    if raw.get("start_line") and line and str(raw["start_line"]) != str(line):
        line = "{}-{}".format(raw["start_line"], line)
    title = _first(raw, "title", "summary", "one_line", "message")
    f.update({
        "title": str(title).strip() if title is not None else None,
        "plain": _first(raw, "plain", "plain_summary", "easy", "simple", "explanation"),
        "file": _first(raw, "file", "path"),
        "line": line,
        "category": _first(raw, "category", "type", "kind"),
        "url": _first(raw, "url", "link", "permalink"),
        "comment_url": raw.get("comment_url"),
        # 문서 리뷰: 위치(쪽 · 절) · 원문 인용 · 수정 제안
        "location": _first(raw, "location", "where") or _doc_location(raw.get("page"), raw.get("section")),
        "quote": _first(raw, "quote", "original", "excerpt"),
        "suggestion": _first(raw, "suggestion", "revised", "fix"),
    })
    merged = {"id": f["id"]}
    merged.update(base or {"severity": "minor", "title": "(제목 없음)", "status": "open"})
    merged.update({k: v for k, v in f.items() if v not in (None, "")})
    return merged

def _doc_location(page, section):
    bits = []
    if page not in (None, ""):
        bits.append("{}쪽".format(page) if re.match(r"^\d+([-–]\d+)?$", str(page).strip()) else str(page).strip())
    if section not in (None, ""):
        bits.append(str(section).strip())
    return " · ".join(bits) or None


def _norm_doc(raw):
    """문서 리뷰의 문서 정보 — 알려진 키만, 종류(kind)는 DOC_KINDS 로 맞춘다."""
    raw = dict(raw)
    if not raw.get("synopsis"):
        raw["synopsis"] = _first(raw, "abstract", "overview", "summary")
    doc = {k: raw[k] for k in _DOC_KEYS if raw.get(k) not in (None, "", [])}
    if "kind" in doc:
        k = str(doc["kind"]).strip().lower()
        doc["kind"] = k if k in DOC_KINDS else _DOC_KIND_ALIAS.get(k, "other")
    if "source" in doc:
        doc["source"] = normalize_item_id(doc["source"], "F")
    if isinstance(doc.get("authors"), (list, tuple)):
        doc["authors"] = ", ".join(str(a) for a in doc["authors"])
    for k in ("title", "purpose", "audience", "synopsis", "format", "language"):
        if k in doc:
            doc[k] = str(doc[k]).strip()
    return doc


def _norm_checks(raw):
    """점검 영역: [{area, rating, note}] 또는 {영역: rating | {rating, note}} → [{area, rating, note}]"""
    if isinstance(raw, dict):
        raw = [dict(v, area=k) if isinstance(v, dict) else {"area": k, "rating": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        raise DevTrackError("checks 는 목록이어야 합니다 (예: [{\"area\": \"논리\", \"rating\": \"fair\", \"note\": \"...\"}])")
    out = []
    for c in raw:
        if not isinstance(c, dict):
            c = {"area": str(c)}
        area = str(_first(c, "area", "name", "title") or "").strip()
        if not area:
            continue
        r = str(_first(c, "rating", "status", "grade") or "na").strip().lower()
        item = {"area": area, "rating": r if r in CHECK_RATINGS else _CHECK_ALIAS.get(r, "fair")}
        note = _first(c, "note", "comment", "detail")
        if note:
            item["note"] = str(note).strip()
        out.append(item)
    return out


_KIND_EXT = {
    "markdown": ("md", "markdown"),
    "pdf": ("pdf",),
    "docx": ("docx",),
    "image": ("png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"),
    "diff": ("diff", "patch"),
    "latex": ("tex", "bib", "sty", "cls", "bst"),
    "notebook": ("ipynb",),
    "csv": ("csv", "tsv"),
    "html": ("html", "htm"),
    "text": ("txt", "log", "out", "err"),
}
CODE_LANG = {
    "py": "python", "js": "javascript", "mjs": "javascript", "ts": "typescript", "jsx": "javascript",
    "tsx": "typescript", "java": "java", "kt": "kotlin", "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp",
    "cxx": "cpp", "hpp": "cpp", "cu": "cpp", "hip": "cpp", "f": "fortran", "f90": "fortran",
    "f95": "fortran", "f03": "fortran", "r": "r", "jl": "julia", "m": "matlab", "sh": "bash",
    "bash": "bash", "zsh": "bash", "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "ini",
    "ini": "ini", "cfg": "ini", "css": "css", "scss": "scss", "sql": "sql", "go": "go", "rs": "rust",
    "rb": "ruby", "php": "php", "xml": "xml", "cmake": "cmake", "mk": "makefile", "lua": "lua",
    "swift": "swift", "cs": "csharp", "tex": "latex", "bib": "latex", "sty": "latex", "cls": "latex",
}
_SPECIAL_NAMES = {"makefile": "makefile", "dockerfile": "dockerfile", "cmakelists.txt": "cmake"}


def detect_kind(name):
    base = os.path.basename(name).lower()
    if base in _SPECIAL_NAMES:
        return "code"
    ext = os.path.splitext(base)[1].lstrip(".")
    for kind, exts in _KIND_EXT.items():
        if ext in exts:
            return kind
    if ext in CODE_LANG:
        return "code"
    return "file"


def detect_lang(name):
    base = os.path.basename(name).lower()
    if base in _SPECIAL_NAMES:
        return _SPECIAL_NAMES[base]
    return CODE_LANG.get(os.path.splitext(base)[1].lstrip("."))


def pid_alive(pid):
    """이 컴퓨터에서 해당 프로세스가 살아 있는지 (권한 없는 다른 사용자의 프로세스도 살아 있는 것으로 본다)."""
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError):
        return False
    except OSError:
        return True
    return True


def _safe_name(name):
    base = os.path.basename(str(name).rstrip("/")) or "file"
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "file"
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)
    return (stem[:100] + ext[:12])


def normalize_task_id(value):
    s = str(value).strip().upper()
    m = re.match(r"^(?:T-?)?(\d+)$", s)
    if not m:
        raise DevTrackError("잘못된 작업 ID: {} (예: T-0001 또는 1)".format(value))
    return "T-{:04d}".format(int(m.group(1)))


def normalize_item_id(value, prefix):
    s = str(value).strip().upper()
    m = re.match(r"^(?:{}-?)?(\d+)$".format(prefix), s)
    if not m:
        raise DevTrackError("잘못된 ID: {} (예: {}-1 또는 1)".format(value, prefix))
    return "{}-{}".format(prefix, int(m.group(1)))


def mirror_tree(src, dst):
    """src 를 dst 로 복사 (크기/수정시각이 같은 파일은 건너뜀). 웹에서 읽을 수 있게 권한을 연다."""
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        rel = os.path.relpath(root, src)
        droot = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(droot, exist_ok=True)
        _chmod(droot, 0o755)
        for name in files:
            if name.startswith("."):
                continue
            s = os.path.join(root, name)
            d = os.path.join(droot, name)
            try:
                ss, ds = os.stat(s), os.stat(d)
                if ss.st_size == ds.st_size and int(ss.st_mtime) == int(ds.st_mtime):
                    continue
            except FileNotFoundError:
                pass
            shutil.copy2(s, d)
            _chmod(d, 0o644)


_WEB_REF = re.compile(r'((?:src|href)=")((?:js|css|vendor)/[^"?#]+)(")')


def write_versioned_index(web_root, dest):
    """공개한 index.html 의 화면 파일 주소에 내용 해시(?v=…)를 붙이고, js 모듈끼리의 import 는 import map 으로 같은 주소에 맞춘다.
    정적 웹 서버(Apache 등)는 Cache-Control 을 안 보내는 일이 많아 브라우저가 업데이트 후에도 예전 JS · CSS 를 계속 쓴다
    (devtrack serve 는 no-cache 를 보내므로 web/index.html 은 그대로 둔다). 바뀐 파일만 주소가 바뀌어 새로 받는다."""
    with open(os.path.join(web_root, "index.html"), encoding="utf-8") as fh:
        text = fh.read()
    seen = {}

    def versioned(rel):
        if rel not in seen:
            path = os.path.join(web_root, *rel.split("/"))
            if not os.path.isfile(path):
                seen[rel] = rel
            else:
                with open(path, "rb") as fh:
                    seen[rel] = "{}?v={}".format(rel, hashlib.sha1(fh.read()).hexdigest()[:10])
        return seen[rel]

    text = _WEB_REF.sub(lambda m: m.group(1) + versioned(m.group(2)) + m.group(3), text)
    jsdir = os.path.join(web_root, "js")
    names = sorted(n for n in os.listdir(jsdir) if n.endswith(".js")) if os.path.isdir(jsdir) else []
    at = text.find('<script type="module"')
    if names and at >= 0:  # import map 은 첫 module 스크립트보다 앞에 있어야 한다
        imports = dict(("./js/" + n, "./" + versioned("js/" + n)) for n in names)
        text = '{}<script type="importmap">{}</script>\n  {}'.format(
            text[:at], json.dumps({"imports": imports}, sort_keys=True), text[at:])
    out = os.path.join(dest, "index.html")
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=dest)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _chmod(out, 0o644)


def _chmod(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _sync_files(root, want):
    """root 폴더를 want {상대 이름: 원본 경로} 와 똑같이 맞춘다 (크기 · 수정시각이 같은 파일은 그대로, 없는 이름은 지움).
    want 가 비면 폴더를 지운다. DevTrack 이 관리하는 폴더(원고 폴더)에만 쓴다."""
    if not want:
        shutil.rmtree(root, ignore_errors=True)
        return
    for rel, src in want.items():
        dest = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            ss, ds = os.stat(src), os.stat(dest)
            if ss.st_size == ds.st_size and int(ss.st_mtime) == int(ds.st_mtime):
                continue
        except FileNotFoundError:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(dest))
        os.close(fd)
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        _chmod(dest, 0o644)
    _prune_tree(root, set(want))


def _prune_tree(root, keep):
    """root 아래에서 keep(상대 이름 집합)에 없는 파일과 빈 폴더를 지우고, 남은 폴더는 웹에서 읽을 수 있게 연다."""
    for d, dirs, files in os.walk(root, topdown=False):
        for name in files:
            rel = os.path.relpath(os.path.join(d, name), root).replace(os.sep, "/")
            if rel not in keep:
                os.remove(os.path.join(d, name))
        if d != root and not os.listdir(d):
            os.rmdir(d)
        else:
            _chmod(d, 0o755)


def same_dir(a, b):
    """두 경로가 같은 폴더인지 — 심볼릭 링크나 다른 이름(~/www → /autofs/…)으로 가리켜도 같으면 True."""
    try:
        return os.path.samefile(a, b)
    except OSError:  # 한쪽이 아직 없음
        return os.path.realpath(a) == os.path.realpath(b)


def _mirror_paper(src_task, dst_task):
    """공개 폴더의 원고 폴더에서 원본에 없는 파일을 지운다 (mirror_tree 는 지우지 않으므로 — 빠진 그림이 남지 않게)."""
    src, dst = os.path.join(src_task, PAPER_DIR), os.path.join(dst_task, PAPER_DIR)
    if same_dir(src, dst) or not os.path.isdir(dst):
        return
    if not os.path.isdir(src):
        shutil.rmtree(dst, ignore_errors=True)
        return
    keep = set()
    for d, _, files in os.walk(src):
        keep |= {os.path.relpath(os.path.join(d, f), src).replace(os.sep, "/") for f in files}
    _prune_tree(dst, keep)


def _paper_dir(ref_dir, gpaths):
    """그림을 둘 원고 폴더 안의 폴더: \\includegraphics 의 폴더 > 첫 \\graphicspath > figures/ (원고 폴더 밖이면 figures/)."""
    for cand in (ref_dir, gpaths[0] if gpaths else ""):
        if cand:
            rel = os.path.normpath(cand).replace(os.sep, "/").strip("/")
            if rel and rel != "." and not rel.startswith("..") and not os.path.isabs(cand):
                return rel
            break
    return PAPER_FIGURES


class Store:
    def __init__(self, root, publish_dir=None):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.tasks_dir = os.path.join(self.root, "tasks")
        self.index_path = os.path.join(self.root, "index.json")
        self.publish_dir = os.path.abspath(os.path.expanduser(publish_dir)) if publish_dir else None
        self._paper_dirty = set()  # 원고 · 그림 결과물이 바뀐 작업 — 저장할 때 원고 폴더를 다시 만든다
        self.paper_status = {}     # 이번 실행에서 다시 만든 원고 폴더의 상태 (paper_folder 의 반환값)

    # ------------------------------------------------------------------ 기본 I/O
    def ensure(self):
        os.makedirs(self.tasks_dir, exist_ok=True)
        if not os.path.exists(self.index_path):
            self.rebuild_index()

    @contextlib.contextmanager
    def locked(self):
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, ".lock"), "a+") as fh:
            got = False
            if fcntl:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    got = True
                except OSError:
                    # 일부 병렬 파일시스템(Lustre 등)은 flock 미지원 — 잠금 없이 진행
                    pass
            try:
                yield
            finally:
                if got:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def _write_json(self, path, obj, mode=0o644):
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            _chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def task_dir(self, tid):
        return os.path.join(self.tasks_dir, normalize_task_id(tid))

    def task_ids(self):
        if not os.path.isdir(self.tasks_dir):
            return []
        ids = [n for n in os.listdir(self.tasks_dir)
               if re.match(r"^T-\d{4,}$", n) and os.path.exists(os.path.join(self.tasks_dir, n, "task.json"))]
        return sorted(ids, key=lambda x: int(x[2:]))

    def load(self, tid):
        tid = normalize_task_id(tid)
        path = os.path.join(self.tasks_dir, tid, "task.json")
        if not os.path.exists(path):
            gone = next((d for d in self.deleted_tasks() if d.get("id") == tid), None)
            if gone:
                raise DevTrackError("{} ({}) 은(는) 삭제된 작업입니다".format(tid, gone.get("title") or ""))
            raise DevTrackError("작업을 찾을 수 없습니다: {}".format(tid))
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def all_tasks(self):
        out = []
        for tid in self.task_ids():
            try:
                out.append(self.load(tid))
            except (ValueError, OSError) as e:
                sys.stderr.write("[devtrack] {} 읽기 실패: {}\n".format(tid, e))
        return out

    def _save(self, task):
        task["updated_at"] = now()
        self._write_json(os.path.join(self.tasks_dir, task["id"], "task.json"), task)
        self.rebuild_index()
        if task["id"] in self._paper_dirty:
            self._paper_dirty.discard(task["id"])
            self._refresh_papers(task)
        self._sync_publish(task["id"])

    @contextlib.contextmanager
    def edit(self, tid):
        tid = normalize_task_id(tid)
        with self.locked():
            task = self.load(tid)
            yield task
            self._save(task)

    @staticmethod
    def _next(task, prefix):
        seq = task.setdefault("seq", {})
        seq[prefix] = seq.get(prefix, 0) + 1
        return "{}-{}".format(prefix, seq[prefix])

    def _log(self, task, message, kind="info", author=None, detail=None, ref=None, meta=None):
        entry = {
            "id": self._next(task, "L"),
            "ts": now(),
            "kind": kind if kind in LOG_KINDS else "info",
            "author": author or task.get("agent") or "agent",
            "message": message,
        }
        if detail:
            entry["detail"] = detail
        if ref:
            entry["ref"] = ref
        if meta:
            entry["meta"] = meta
        if entry["author"] == USER and kind == "user":
            entry["ack"] = False
        task.setdefault("log", []).append(entry)
        return entry

    @staticmethod
    def _find(items, item_id, what):
        for it in items:
            if it.get("id") == item_id:
                return it
        raise DevTrackError("{}을(를) 찾을 수 없습니다: {}".format(what, item_id))

    # ------------------------------------------------------------------ 작업
    def create_task(self, title, instruction="", type="code", agent="Claude", repo=None, branch=None,
                    repo_url=None, tags=None, priority="normal", status="in_progress", created_by=None,
                    plan=None, related=None, model=None, effort=None, review=None):
        """review: 끝나면 다른 AI 에게 리뷰받기 {"reviewer", "rounds", "model", "effort"} (review_request 가 쓴다)."""
        if type not in TASK_TYPES:
            raise DevTrackError("알 수 없는 작업 유형: {} ({})".format(type, ", ".join(TASK_TYPES)))
        if status not in STATUSES:
            raise DevTrackError("알 수 없는 상태: {}".format(status))
        if priority not in PRIORITIES:
            raise DevTrackError("우선순위는 {} 중 하나여야 합니다".format(", ".join(PRIORITIES)))
        model, effort = models.normalize_model(model), models.normalize_effort(effort)
        models.check_effort(agent, model, effort)
        review_plan = None
        if review and review.get("reviewer"):
            review_plan = _norm_review_plan(review, type)
        repo_info = {}
        if repo:
            repo_info["path"] = os.path.abspath(os.path.expanduser(repo))
            if gitops.is_repo(repo_info["path"]):
                branch = branch or gitops.current_branch(repo_info["path"])
                repo_url = repo_url or gitops.remote_url(repo_info["path"])
        if branch:
            repo_info["branch"] = branch
        if repo_url:
            repo_info["url"] = repo_url
        self.ensure()
        with self.locked():
            used = [int(i[2:]) for i in self.task_ids()]
            used += [int(d["id"][2:]) for d in self.deleted_tasks() if re.match(r"^T-\d+$", d.get("id", ""))]
            tid = "T-{:04d}".format(max(used, default=0) + 1)
            related = self._check_related(related, tid)
            ts = now()
            task = {
                "id": tid,
                "title": title.strip(),
                "type": type,
                "status": status,
                "priority": priority,
                "agent": agent or "Claude",
                "instruction": instruction or "",
                "repo": repo_info,
                "progress": 0,
                "current_step": "",
                "plan": [{"text": p, "done": False} for p in (plan or [])],
                "tags": list(tags or []),
                "related": related,
                "model": model,  # 이 작업을 처리할 AI 모델 · 추론 강도 (None = 어떤 모델이든)
                "effort": effort,
                "created_by": created_by or agent or "Claude",
                "created_at": ts,
                "updated_at": ts,
                "log": [],
                "issues": [],
                "artifacts": [],
                "approvals": [],
                "seq": {},
            }
            if review_plan:  # 끝나면 이 AI 에게 리뷰받는다 (devtrack review-request)
                task["review_loop"] = dict(review_plan, state="planned", history=[])
            os.makedirs(os.path.join(self.tasks_dir, tid), exist_ok=True)
            if created_by == USER:
                self._log(task, "사용자가 새 작업을 지시했습니다", kind="user", author=USER)
                task["log"][-1]["ack"] = True  # 지시 자체는 queued 상태로 전달됨
            elif status == "queued" and created_by and agent_key(created_by) != agent_key(task["agent"]):
                # 다른 AI 가 맡긴 작업 (예: 리뷰 요청) — 맡은 AI 의 받은 편지함에 새 작업으로 간다
                self._log(task, "{} 이(가) {} 에게 맡긴 작업입니다".format(created_by, task["agent"]), kind="info",
                          author=created_by)
            else:
                self._log(task, "작업을 시작했습니다", kind="status", author=task["agent"], meta={"to": status})
            if plan:
                self._log(task, "작업 계획 수립 ({}단계)".format(len(plan)), kind="step", author=task["agent"])
            if related:
                self._log(task, "관련 작업 연결: " + self._related_label(related), kind="info",
                          author=created_by or task["agent"])
            self._save(task)
        return task

    def _check_related(self, ids, self_id=None):
        out = []
        for r in ids or []:
            rid = normalize_task_id(r)
            if rid == self_id:
                raise DevTrackError("작업을 자기 자신과 연결할 수 없습니다: {}".format(rid))
            if not os.path.exists(os.path.join(self.tasks_dir, rid, "task.json")):
                raise DevTrackError("연결할 작업을 찾을 수 없습니다: {}".format(rid))
            if rid not in out:
                out.append(rid)
        return out

    def _related_label(self, ids):
        parts = []
        for rid in ids:
            try:
                parts.append("{} {}".format(rid, self.load(rid)["title"]))
            except (DevTrackError, ValueError, OSError):
                parts.append(rid)
        return ", ".join(parts)

    def link_tasks(self, tid, others, unlink=False, author=None):
        """작업 간 연결 (예: 데이터 분석 → 그 결과를 쓰는 논문 작업). 화면에서는 양방향으로 표시된다."""
        tid = normalize_task_id(tid)
        ids = self._check_related(others, tid)
        with self.edit(tid) as t:
            rel = t.setdefault("related", [])
            changed = []
            for rid in ids:
                if unlink and rid in rel:
                    rel.remove(rid)
                    changed.append(rid)
                elif not unlink and rid not in rel:
                    rel.append(rid)
                    changed.append(rid)
            if changed:
                self._log(t, ("관련 작업 연결 해제: " if unlink else "관련 작업 연결: ") + self._related_label(changed),
                          kind="info", author=author)
                self._paper_dirty.add(tid)  # 연결된 작업의 그림도 원고 폴더에 쓰이므로 다시 만든다
        for rid in changed if unlink else []:  # 연결을 푼 논문 작업은 그쪽 그림이 빠지도록
            try:
                if self.load(rid).get("type") in PAPER_TYPES:
                    self.rebuild_paper(rid)
            except (DevTrackError, OSError, ValueError):
                pass
        return rel

    def update_task(self, tid, status=None, progress=None, step=None, message=None, title=None,
                    author=None, kind="info", claim=None, take=False, claim_queued_only=False):
        """claim: 이 이름의 AI 가 작업을 맡는다 (담당이 다른 AI 면 take=True 일 때만 가져옴).
        claim_queued_only: 아직 '대기' 인 작업일 때만 담당을 확인한다 (step 으로 시작하는 경우)."""
        with self.edit(tid) as t:
            if claim and (t["status"] == "queued" or not claim_queued_only):
                self._claim(t, claim, take)
            if title:
                t["title"] = title
            if status:
                if status not in STATUSES:
                    raise DevTrackError("알 수 없는 상태: {} ({})".format(status, ", ".join(STATUSES)))
                old = t["status"]
                if old != status and status == "cancelled":
                    self._apply_cancel(t, message or "", author)
                    message = None
                elif old != status:
                    t["status"] = status
                    if status == "done":
                        t["progress"] = 100
                        t["current_step"] = ""
                        t["completed_at"] = now()
                    self._log(t, "상태 변경: {} → {}".format(STATUSES.get(old, old), STATUSES[status]),
                              kind="status", author=author, meta={"from": old, "to": status}, detail=message)
                    message = None
            if progress is not None:
                t["progress"] = max(0, min(100, int(progress)))
            if step:
                t["current_step"] = step
                self._log(t, step, kind="step", author=author, meta={"progress": t.get("progress")})
            if message:
                self._log(t, message, kind=kind, author=author)
            return t

    def set_model(self, tid, model=None, effort=None, author=None):
        """작업을 처리할 AI 모델 · 추론 강도를 정한다 (둘 다 None 이면 지정 해제). 바뀐 경우만 기록한다."""
        model, effort = models.normalize_model(model), models.normalize_effort(effort)
        with self.edit(tid) as t:
            old = models.task_spec(t)
            models.check_effort(t.get("agent"), model, effort)
            if old != (model, effort):
                t["model"], t["effort"] = model, effort
                label = models.spec_label(model, effort)
                self._log(t, "모델 지정: " + label if label else "모델 지정 해제 — 어떤 모델이든 처리", kind="info",
                          author=author, meta={"model": model, "effort": effort,
                                               "from": {"model": old[0], "effort": old[1]}})
            return t

    def _claim(self, t, agent, take=False):
        cur = t.get("agent")
        if agent_key(cur) == agent_key(agent):
            return
        if is_unassigned(cur):
            t["agent"] = agent
            self._log(t, "{} 이(가) 작업을 맡았습니다".format(agent), kind="info", author=agent,
                      meta={"agent": agent})
            return
        if not take:
            raise DevTrackError("{0} 은(는) {1} 에게 배정된 작업입니다 — {1} 이(가) 처리합니다. "
                                "대신 맡으려면: devtrack start {0} --take".format(t["id"], cur))
        t["agent"] = agent
        self._log(t, "담당 AI 변경: {} → {}".format(cur, agent), kind="info", author=agent,
                  meta={"agent": agent, "from": cur})

    # ------------------------------------------------------------------ 취소 / 삭제
    @property
    def deleted_path(self):
        # 점(.)으로 시작하므로 publish / 웹 서버에서 제외된다
        return os.path.join(self.root, ".deleted.json")

    def deleted_tasks(self):
        try:
            with open(self.deleted_path, encoding="utf-8") as fh:
                return json.load(fh).get("deleted", [])
        except (OSError, ValueError):
            return []

    @staticmethod
    def _withdraw_pending(t, author, comment):
        """결정을 기다리던 승인 요청을 철회한다 (작업을 취소하거나 완료로 처리할 때). 철회한 ID 목록을 돌려준다."""
        withdrawn = []
        for ap in t.get("approvals", []):
            if ap["status"] == "pending":
                ap.update({"status": "withdrawn", "decided_at": now(), "decided_by": author,
                           "comment": comment, "agent_ack": True})
                withdrawn.append(ap["id"])
        return withdrawn

    def _apply_cancel(self, t, reason, author):
        old = t["status"]
        withdrawn = self._withdraw_pending(t, author, "작업 취소로 철회")
        t["status"] = "cancelled"
        t["cancelled"] = {"at": now(), "by": author, "reason": reason, "was": old,
                          "agent_ack": author != USER}  # 사용자가 취소하면 에이전트에게 알린다
        msg = "작업 취소" + (": " + reason if reason else "")
        if withdrawn:
            msg += " (승인 요청 {} 철회)".format(", ".join(withdrawn))
        self._log(t, msg, kind="status", author=author,
                  meta={"from": old, "to": "cancelled", "withdrawn": withdrawn})

    def cancel_task(self, tid, reason="", author=None):
        with self.edit(tid) as t:
            if t["status"] == "cancelled":
                raise DevTrackError("이미 취소된 작업입니다: {}".format(t["id"]))
            self._apply_cancel(t, (reason or "").strip(), author)
            return t

    def complete_task(self, tid, note="", author=None, by_user=False):
        """끝나지 않은 작업이라도 사용자가 완료로 처리한다. 결정을 기다리던 승인 요청은 철회하고, 사용자가 웹에서
        처리하면 담당 AI 에게 멈추라고 알린다 (받은 편지함 · 훅 · watch — 취소와 같은 방식). 아직 '대기' 였어도 알린다:
        AI 가 막 가져가 시작하기 직전일 수 있다.
        by_user: 사용자의 요청을 AI 가 대신 기록하는 경우 (정적 모드의 '[DevTrack 작업 완료]' 메시지) — 이미 아는 AI 가
        기록하므로 따로 알리지 않는다."""
        with self.edit(tid) as t:
            old = t["status"]
            if old == "done":
                raise DevTrackError("이미 완료된 작업입니다: {}".format(t["id"]))
            note = (note or "").strip()
            withdrawn = self._withdraw_pending(t, author, "작업 완료 처리로 철회")
            t["status"] = "done"
            t["progress"] = 100
            t["current_step"] = ""
            t["completed_at"] = now()
            user = author == USER or by_user
            t["completed"] = {"at": t["completed_at"], "by": author, "by_user": user, "note": note, "was": old,
                              "agent_ack": author != USER}
            msg = ("사용자가 완료로 처리" if user else "완료 처리") + (": " + note if note else "")
            if withdrawn:
                msg += " (승인 요청 {} 철회)".format(", ".join(withdrawn))
            self._log(t, msg, kind="status", author=author,
                      meta={"from": old, "to": "done", "withdrawn": withdrawn, "by_user": user})
            return t

    def delete_task(self, tid, author=None):
        """작업과 결과물 파일을 완전히 지운다 (되돌릴 수 없음). 다른 작업의 연결도 정리한다."""
        tid = normalize_task_id(tid)
        touched = []
        with self.locked():
            t = self.load(tid)
            for other in self.all_tasks():
                if other["id"] != tid and tid in (other.get("related") or []):
                    other["related"] = [r for r in other["related"] if r != tid]
                    self._log(other, "관련 작업 {} 이(가) 삭제되어 연결을 해제했습니다".format(tid), kind="info",
                              author=author)
                    other["updated_at"] = now()
                    self._write_json(os.path.join(self.tasks_dir, other["id"], "task.json"), other)
                    touched.append(other["id"])
            shutil.rmtree(self.task_dir(tid))
            items = [d for d in self.deleted_tasks() if d.get("id") != tid]
            items.append({"id": tid, "title": t["title"], "type": t["type"], "agent": t.get("agent"),
                          "status": t["status"], "at": now(), "by": author, "ack": author != USER})
            self._write_json(self.deleted_path, {"deleted": items})
            self.rebuild_index()
        if self.publish_dir and not self.publish_is_root():
            shutil.rmtree(os.path.join(self.publish_dir, "data", "tasks", tid), ignore_errors=True)
            for oid in touched:
                self._sync_publish(oid)
            self._sync_publish()
        return {"id": tid, "title": t["title"], "artifacts": len(t.get("artifacts", [])), "unlinked": touched}

    def add_log(self, tid, message, kind="info", detail=None, author=None):
        with self.edit(tid) as t:
            return self._log(t, message, kind=kind, author=author, detail=detail)

    # ------------------------------------------------------------------ 인계 메모
    def note_path(self, tid, prev=False):
        return os.path.join(self.tasks_dir, normalize_task_id(tid), NOTE_PREV if prev else NOTE_FILE)

    def read_note(self, tid):
        try:
            with open(self.note_path(tid), encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def note_template(self, tid):
        t = self.load(tid)
        repo = t.get("repo") or {}
        base = "기준: {} · ".format(now()[:10])
        if repo.get("path"):
            base += "저장소 {}{} @<커밋> · ".format(repo["path"], " ({})".format(repo["branch"]) if repo.get("branch") else "")
        base += "데이터 <경로 · 버전>"
        lines = ["# {} 인계 메모 — {}".format(t["id"], t["title"]), base, ""]
        for sec in NOTE_SECTIONS:
            lines += ["## " + sec, "- ", ""]
        return "\n".join(lines)

    def set_note(self, tid, text, author=None):
        """인계 메모를 쓴다 (빈 글이면 지운다). 바로 전 판은 notes.prev.md 로 남긴다. → (task, 전 판을 쓴 사람)."""
        text = (text or "").replace("\r\n", "\n").strip()
        if len(text) > NOTE_MAX:
            raise DevTrackError("인계 메모가 너무 깁니다 ({:,}자, 최대 {:,}자) — 결론 · 위치 · 명령 위주로 줄이고, 긴 내용은 "
                                "리포트 결과물로 올린 뒤 메모에는 그 ID 를 적으세요".format(len(text), NOTE_MAX))
        with self.edit(tid) as t:
            path = self.note_path(tid)
            old = t.get("note") or {}
            if os.path.exists(path):
                shutil.copy2(path, self.note_path(tid, prev=True))
            if text:
                fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                _chmod(tmp, 0o644)
                os.replace(tmp, path)
                t["note"] = {"updated_at": now(), "by": author or t.get("agent") or "agent", "chars": len(text),
                             "version": int(old.get("version") or 0) + 1}
                self._log(t, "{}인계 메모를 {} ({:,}자)".format("사용자가 " if author == USER else "",
                                                          "고쳤습니다" if old else "남겼습니다", len(text)),
                          author=author, meta={"note_version": t["note"]["version"]})
            else:
                if os.path.exists(path):
                    os.remove(path)
                t["note"] = None
                self._log(t, "인계 메모를 지웠습니다", author=author)
        if not text and self.publish_dir and not self.publish_is_root():  # 동기화는 지운 파일을 따라 지우지 않는다
            try:
                os.remove(os.path.join(self.publish_dir, "data", "tasks", t["id"], NOTE_FILE))
            except OSError:
                pass
        return t, old.get("by")

    def note_stale(self, t):
        """메모를 쓴 뒤 쌓인 기록 수 (메모가 없으면 None)."""
        n = t.get("note") or {}
        if not n.get("updated_at"):
            return None
        return sum(1 for e in t.get("log", []) if e.get("ts", "") > n["updated_at"]
                   and not (e.get("meta") or {}).get("note_version"))

    def add_user_comment(self, tid, message, artifact=None, attachments=None):
        """사용자 메시지 (사용자 → 담당 AI). artifact: 이 메시지가 가리키는 결과물 (결과물 화면의 수정 요청 · 추가 지시) —
        결과물은 새 버전이 올라와도 ID 가 같으므로 ref 로 남겨 두면 그 결과물의 기록으로 이어진다.
        attachments: 이 메시지와 함께 보낸 파일의 결과물 ID (add_sent_files) — 받은 편지함에 파일 경로가 붙는다."""
        if not message or not message.strip():
            raise DevTrackError("메시지가 비어 있습니다")
        sent = {"attachments": list(attachments)} if attachments else {}
        with self.edit(tid) as t:
            if not artifact:
                return self._log(t, message.strip(), kind="user", author=USER, meta=sent or None)
            fid = normalize_item_id(artifact, "F")
            art = self._find(t.get("artifacts", []), fid, "결과물")
            return self._log(t, message.strip(), kind="user", author=USER, ref=fid,
                             meta=dict(sent, artifact=fid, artifact_title=art.get("title"),
                                       version=art.get("version", 1)))

    def add_sent_files(self, tid, files, issue=None, rewrite=None, author=USER):
        """사용자가 보낸 파일(GitHub 우편함)을 '보낸 파일' 결과물(분류 source, sent)로 올린다 → 결과물 목록.
        files: [{"path": 내려받은 파일, "filename": 이름}]. rewrite: 새 작업이면 (결과물 목록 → 링크를 결과물 번호로 바꾼 지시) —
        같은 저장에서 지시를 바꿔, AI 가 링크만 있는 지시를 먼저 보는 일이 없게 한다."""
        with self.edit(tid) as t:
            arts = []
            for f in files:
                old = next((a for a in reversed(t.get("artifacts", []))
                            if a.get("sent") and a.get("filename") == f["filename"] and a.get("path")), None)
                if old:  # 같은 이름을 다시 보내면 그 보낸 파일의 새 버전 (고친 원고를 다시 올린 경우)
                    a = self._new_version(t, old, f["path"], author=author, log=False)
                    if a.get("unchanged"):
                        continue
                    self._log(t, "보낸 파일 새 버전 (v{}): {}".format(a["version"], a["title"]), kind="artifact", author=author,
                              ref=a["id"], meta={"category": "source", "kind": a["kind"], "sent": True, "version": a["version"]})
                else:
                    a = self._add_artifact(t, path=f["path"], filename=f["filename"], category="source", author=author,
                                           extra={"sent": {"via": "github", "issue": issue}}, log=False)
                    self._log(t, "보낸 파일: {}".format(a["title"]), kind="artifact", author=author, ref=a["id"],
                              meta={"category": "source", "kind": a["kind"], "sent": True})
                arts.append(a)
            if rewrite:
                t["instruction"] = rewrite(arts)
                if arts:
                    t["sent_files"] = [a["id"] for a in arts]
            return arts

    def _sent_list(self, t, ids):
        """받은 편지함에 붙일 보낸 파일 (ID · 이름 · 파일 경로 · 종류)."""
        out = []
        for fid in ids or []:
            a = next((x for x in t.get("artifacts", []) if x.get("id") == fid), None)
            if a:
                out.append({"id": fid, "filename": a.get("filename"), "kind": a.get("kind"),
                            "file": self._artifact_file(t["id"], a)})
        return out

    # ------------------------------------------------------------------ AI 끼리 메시지 · 리뷰 반복
    def _agent_message(self, t, message, author, meta=None, kind_label=None):
        """작업 t 의 담당 AI 에게 다른 AI 의 메시지를 남긴다 (받은 편지함의 [AI 메시지])."""
        e = self._log(t, message, kind="agent", author=author, meta=dict(meta or {}, to=t.get("agent")))
        e["ack"] = False
        return e

    def send_agent_message(self, tid, message, author=None):
        """다른 AI 가 맡은 작업에 메시지를 보낸다 — 그 AI 가 처리한다 (사용자 지시가 아님)."""
        if not message or not message.strip():
            raise DevTrackError("메시지가 비어 있습니다")
        with self.edit(tid) as t:
            if t["status"] == "cancelled":
                raise DevTrackError("취소된 작업입니다: {}".format(t["id"]))
            if author and agent_key(author) == agent_key(t.get("agent")):
                raise DevTrackError("{} 은(는) {} 이(가) 맡은 작업입니다 — 다른 AI 가 맡은 작업에 보내세요".format(
                    t["id"], t.get("agent")))
            return self._agent_message(t, message.strip(), author)

    def review_request(self, tid, reviewer=None, rounds=None, model=None, effort=None, note="", files=None,
                       author=None, same_session=False, doc=None, venue=None, response=None):
        """개발 작업의 지금 변경(빌드 결과물 제외)을 다른 AI 에게 리뷰받는다. 첫 차례에는 리뷰 작업을 만들어 리뷰어에게 맡기고,
        다음 차례에는 같은 리뷰 작업에 [AI 메시지] 로 다시 요청한다 (리뷰어는 같은 대화로 이어 감).
        차례마다 전체 diff 와, 두 번째부터는 지난 차례 이후 바뀐 부분 diff 를 리뷰 작업의 결과물로 붙인다.
        리뷰어는 같은 AI · 같은 모델이어도 되지만, 리뷰는 개발한 대화와 다른 대화가 한다 (cli 의 _own_review).
        same_session: 개발한 대화가 리뷰해도 됨 (사용자가 그렇게 하라고 한 경우만).
        논문 작업(REVIEW_DOC_TYPES)이나 doc(원고 파일)을 준 작업은 문서 모드 — 저널 심사위원 관점의 심사 (_doc_review_request)."""
        tid = normalize_task_id(tid)
        t = self.load(tid)
        loop = dict(t.get("review_loop") or {})
        plan = {k: loop.get(k) for k in ("reviewer", "rounds", "model", "effort", "doc", "venue")}
        for k, v in (("reviewer", reviewer), ("rounds", rounds), ("model", model), ("effort", effort), ("doc", doc),
                     ("venue", venue)):
            if v not in (None, ""):
                plan[k] = v
        if not plan.get("reviewer"):
            raise DevTrackError("리뷰어 AI 를 정하세요 (예: devtrack review-request {} --reviewer Codex)".format(tid))
        plan = _norm_review_plan(plan, t.get("type"))
        same_session = bool(same_session or loop.get("same_session"))
        history = list(loop.get("history") or [])
        if history and not history[-1].get("verdict"):
            raise DevTrackError("{}차 리뷰를 기다리는 중입니다 ({}) — 결과는 [AI 메시지] 로 옵니다".format(
                history[-1]["round"], history[-1].get("task")))
        used = len(history) - int(loop.get("cycle_start") or 0)  # 커밋이 반영되면 횟수를 새로 센다
        if used >= plan["rounds"]:
            raise DevTrackError("리뷰를 {}번 모두 받았습니다 — 남은 지적과 함께 사용자에게 결정을 요청하세요 "
                                "(devtrack request {} \"…\"). 사용자가 더 받으라고 하면 --rounds 로 늘립니다".format(
                                    plan["rounds"], tid))
        if plan["kind"] == "doc":
            return self._doc_review_request(t, loop, plan, history, note, author, same_session, response)
        repo = (t.get("repo") or {}).get("path")
        if not repo:
            raise DevTrackError("{} 에 저장소 경로가 없습니다 — 코드 리뷰는 저장소가 있는 작업만 받을 수 있습니다 "
                                "(논문 · 문서는 --doc <원고 파일>)".format(tid))
        repo = gitops.require_repo(repo)
        files = list(files or loop.get("files") or [])
        changes = gitops.capture_changes(repo, files)
        if not changes["diff"].strip():
            raise DevTrackError("리뷰할 변경이 없습니다: {}{}".format(repo, " (빌드 결과물만 바뀜: {})".format(
                gitops.excluded_text(changes["excluded"])) if changes["excluded"] else ""))
        tree = gitops.snapshot_tree(repo, changes["paths"])
        prev = history[-1] if history else None
        if prev and prev.get("tree") == tree:
            raise DevTrackError("{}차 리뷰 이후 바뀐 코드가 없습니다 — 지적을 고친 뒤 다시 요청하세요".format(prev["round"]))
        since = gitops.diff_trees(repo, prev["tree"], tree) if prev and prev.get("tree") else None
        n = len(history) + 1
        stat = gitops.diffstat(changes["diff"])
        who = author or t.get("agent")
        rtid = loop.get("task")
        alive = False
        if rtid:
            try:
                alive = self.load(rtid).get("status") != "cancelled"
            except DevTrackError:
                alive = False
        created = not alive
        if created:
            rt = self.create_task("{} 리뷰: {}".format(tid, t["title"]), type="review", agent=plan["reviewer"],
                                  repo=repo, status="queued", related=[tid], model=plan["model"],
                                  effort=plan["effort"], created_by=who,
                                  instruction=_review_instruction(t, plan, n, note))
            rtid = rt["id"]
        with self.edit(rtid) as r:
            r["review_of"] = {"task": tid, "title": t["title"], "agent": t.get("agent"), "model": t.get("model"),
                              "effort": t.get("effort"), "same_session": same_session}
            r["model"], r["effort"] = plan["model"], plan["effort"]
            r["review_round"] = n
            art = self._add_artifact(r, text=changes["diff"], filename="round{}.diff".format(n), kind="diff",
                                     title="{}차 리뷰할 변경 ({})".format(n, tid), category="source", author=who,
                                     description="빌드 결과물 제외: " + gitops.excluded_text(changes["excluded"])
                                     if changes["excluded"] else None)
            since_art = None
            if since and since.strip():
                since_art = self._add_artifact(r, text=since, filename="round{}-since-{}.diff".format(n, n - 1),
                                               kind="diff", title="{}차: 지난 리뷰 이후 바뀐 부분".format(n),
                                               category="source", author=who)
            if created:  # 지시에 리뷰 작업 ID 를 넣는다 (아직 아무도 시작하지 않은 대기 작업)
                r["instruction"] = _review_instruction(t, plan, n, note, rtid)
            if not created:
                if r["status"] in ("done", "blocked", "changes_requested"):
                    old = r["status"]
                    r["status"] = "in_progress"
                    self._log(r, "상태 변경: {} → {}".format(STATUSES.get(old, old), STATUSES["in_progress"]),
                              kind="status", author=who, meta={"from": old, "to": "in_progress"})
                msg = "{}차 리뷰 요청 ({} — {}): 수정한 변경 {}{} · 파일 {}개 +{} −{}{}".format(
                    n, tid, t.get("agent"), art["id"],
                    " (지난 리뷰 이후 바뀐 부분 {})".format(since_art["id"]) if since_art else "",
                    stat["files"], stat["additions"], stat["deletions"],
                    " — " + note.strip() if note and note.strip() else "")
                self._agent_message(r, msg, who, meta={"review_request": {
                    "round": n, "task": tid, "diff": art["id"], "since": since_art["id"] if since_art else None}})
        with self.edit(tid) as t:
            loop = dict(t.get("review_loop") or {})
            loop.update(plan)
            loop.update(task=rtid, state="reviewing", round=n, files=files, same_session=same_session)
            entry = {"round": n, "task": rtid, "requested_at": now(), "requested_by": who, "diff": art["id"],
                     "since": since_art["id"] if since_art else None, "stat": stat, "excluded": changes["excluded"],
                     "tree": tree, "verdict": None}
            loop["history"] = history + [entry]
            t["review_loop"] = loop
            self._log(t, "{}차 리뷰 요청 → {}{} ({}) · 파일 {}개 +{} −{}".format(
                n, plan["reviewer"], " · " + models.spec_label(plan["model"], plan["effort"])
                if plan["model"] or plan["effort"] else "", rtid, stat["files"], stat["additions"], stat["deletions"]),
                kind="info", author=who, ref=rtid,
                detail="빌드 결과물 제외: " + gitops.excluded_text(changes["excluded"], limit=20)
                if changes["excluded"] else None)
        return dict(entry, rounds=plan["rounds"], reviewer=plan["reviewer"], created=created)

    def _review_task(self, t, plan, n, who, rtype, title, instruction, repo=None):
        """이 개발 작업의 리뷰 작업 — 살아 있으면 그대로, 없거나 취소됐으면 새로 만든다 → (ID, 새로 만들었는지)."""
        rtid = (t.get("review_loop") or {}).get("task")
        if rtid:
            try:
                if self.load(rtid).get("status") != "cancelled":
                    return rtid, False
            except DevTrackError:
                pass
        rt = self.create_task(title, type=rtype, agent=plan["reviewer"], repo=repo, status="queued", related=[t["id"]],
                              model=plan["model"], effort=plan["effort"], created_by=who, instruction=instruction)
        return rt["id"], True

    def _snapshot_path(self, rtid, n):
        # 점(.)으로 시작하는 폴더는 publish · 웹에서 빠진다 — 차례 사이 diff 를 만드는 데만 쓴다
        return os.path.join(self.tasks_dir, rtid, ".snapshots", "round{}.json".format(n))

    def _doc_review_request(self, t, loop, plan, history, note, author, same_session, response):
        """논문 · 문서 원고를 저널 심사위원 관점으로 심사받는다. 차례마다 원고(PDF · 원문)를, 두 번째부터는 지난 심사 이후
        바뀐 부분과 저자의 답변서(response to reviewers)를 리뷰 작업에 붙인다."""
        tid = t["id"]
        if not plan.get("doc"):
            raise DevTrackError("심사할 원고 파일을 정하세요 (예: devtrack review-request {} --doc paper/main.tex)".format(tid))
        path = _doc_path(t, plan["doc"])
        plan = dict(plan, doc=path)  # 상대 경로는 확인한 절대 경로로 남긴다 (다른 폴더에서 실행해도 같은 원고)
        snap = manuscript.snapshot(path)
        if not snap["files"] and not snap["pdf"]:
            raise DevTrackError("원고에 글이 없습니다: {}".format(path))
        dig = manuscript.digest(snap)
        prev = history[-1] if history else None
        if prev and prev.get("digest") == dig:
            raise DevTrackError("{}차 심사 이후 원고가 바뀌지 않았습니다 — 지적을 반영한 뒤 다시 요청하세요".format(prev["round"]))
        if prev and not response:
            raise DevTrackError("2차 심사부터는 답변서(response to reviewers)가 필요합니다 — 지적(R1, R2…)마다 어떻게 고쳤는지, "
                                "고치지 않았다면 그 이유를 적은 파일을 --response 로 주세요")
        if response and not os.path.isfile(os.path.expanduser(response)):
            raise DevTrackError("답변서 파일을 찾을 수 없습니다: {}".format(response))
        answered = None
        if prev and response:  # 지난 차례의 열린 항목(질문 · 요청)마다 답이 있어야 심사 · 답변 기록이 빠짐없이 남는다
            answered = sorted((k for k, v in response_sections(_read_response(response))[1].items() if v),
                              key=lambda x: int(x[1:]))
            missing = [i for i in prev.get("open") or [] if i not in answered]
            if missing:
                raise DevTrackError(
                    "답변서에 {}차 심사의 항목 {} 에 대한 답이 없습니다 — 항목마다 '## R1 — 제목' 처럼 항목 ID 로 제목을 달고, "
                    "질문에는 답을, 요청에는 어떻게 고쳤는지(고치지 않았다면 그 이유)를 적어 주세요".format(
                        prev["round"], " · ".join(missing)))
        n = len(history) + 1
        who = author or t.get("agent")
        figs = snap.get("figures") or {}
        fig_sha = dict((k, v["sha"]) for k, v in figs.items())
        rtid, created = self._review_task(t, plan, n, who, "docreview", "{} 심사: {}".format(tid, t["title"]),
                                          _doc_review_instruction(t, plan, n, note, path, figures=len(figs)))
        prev_files, prev_figs = None, {}
        if prev:
            try:
                with open(self._snapshot_path(prev.get("task") or rtid, prev["round"]), encoding="utf-8") as fh:
                    prev_snap = json.load(fh)
                prev_files, prev_figs = prev_snap.get("files"), prev_snap.get("figures") or {}
            except (OSError, ValueError):
                prev_files = None
        figchg = manuscript.figure_changes(prev_figs, fig_sha) if prev else None
        since = manuscript.diff(prev_files, snap["files"]) if prev_files is not None else None
        stat = gitops.diffstat(since) if since else None
        ext = {"latex": ".tex", "word": ".md", "pdf": ".txt"}.get(snap["kind"], os.path.splitext(snap["main"])[1] or ".txt")
        with self.edit(rtid) as r:
            r["review_of"] = {"task": tid, "title": t["title"], "agent": t.get("agent"), "model": t.get("model"),
                              "effort": t.get("effort"), "same_session": same_session, "kind": "doc",
                              "venue": plan.get("venue"), "doc": path}
            r["model"], r["effort"] = plan["model"], plan["effort"]
            r["review_round"] = n
            ids = {}
            if snap["kind"] in ("word", "pdf") or snap["pdf"]:
                src = snap["pdf"] if snap["kind"] != "word" else snap["source"]
                ids["file"] = self._add_artifact(r, path=src, title="{}차 원고 ({})".format(
                    n, "Word" if snap["kind"] == "word" else "PDF"), category="source", author=who)["id"]
            if snap["files"]:
                ids["text"] = self._add_artifact(
                    r, text=manuscript.combined_text(snap), filename="round{}-manuscript{}".format(n, ext),
                    title="{}차 원고 원문{}".format(n, " (LaTeX 파일 {}개)".format(len(snap["files"]))
                                               if snap["kind"] == "latex" else ""),
                    category="source", author=who)["id"]
            if since and since.strip():
                ids["since"] = self._add_artifact(r, text=since, filename="round{}-since-{}.diff".format(n, n - 1),
                                                  kind="diff", title="{}차: 지난 심사 이후 바뀐 부분".format(n),
                                                  category="source", author=who)["id"]
            if response:
                ids["response"] = self._add_artifact(r, path=os.path.expanduser(response),
                                                     title="{}차 답변서 (response to reviewers)".format(n),
                                                     category="source", author=who)["id"]
            if figs:
                ids["figures"] = self._attach_figures(r, figs, n, who)
            rv = dict(r.get("review") or {})
            rv["doc"] = dict(rv.get("doc") or {}, **_norm_doc({
                "title": t["title"], "kind": "paper" if t.get("type") == "paper" else "other",
                "purpose": "{} 투고 — 저널 심사위원 관점의 심사".format(plan["venue"]) if plan.get("venue")
                else "저널 투고 — 심사위원 관점의 심사",
                "audience": "저널 심사위원 · 편집자", "source": ids.get("file") or ids.get("text")}))
            r["review"] = rv
            if created:
                r["instruction"] = _doc_review_instruction(t, plan, n, note, path, rtid, figures=len(figs),
                                                           art_dir=os.path.join(self.tasks_dir, rtid, "artifacts"))
            else:
                if r["status"] in ("done", "blocked", "changes_requested"):
                    old = r["status"]
                    r["status"] = "in_progress"
                    self._log(r, "상태 변경: {} → {}".format(STATUSES.get(old, old), STATUSES["in_progress"]),
                              kind="status", author=who, meta={"from": old, "to": "in_progress"})
                parts = ["고친 원고 " + (ids.get("file") or ids.get("text"))]
                if ids.get("since"):
                    parts.append("지난 심사 이후 바뀐 부분 {} (+{} −{})".format(ids["since"], stat["additions"],
                                                                         stat["deletions"]))
                if ids.get("response"):
                    parts.append("답변서 " + ids["response"])
                if figs:
                    parts.append(_figure_change_label(len(figs), figchg))
                msg = "{}차 심사 요청 ({} — {}): {}{}".format(n, tid, t.get("agent"), " · ".join(parts),
                                                          " — " + note.strip() if note and note.strip() else "")
                self._agent_message(r, msg, who, meta={"review_request": dict(ids, round=n, task=tid)})
        self._write_json(self._snapshot_path(rtid, n), {"round": n, "source": path, "kind": snap["kind"],
                                                        "files": snap["files"], "figures": fig_sha})
        with self.edit(tid) as t:
            loop = dict(t.get("review_loop") or {})
            loop.update(plan)
            loop.update(task=rtid, state="reviewing", round=n, same_session=same_session)
            entry = {"round": n, "task": rtid, "requested_at": now(), "requested_by": who, "diff": ids.get("file") or ids.get("text"),
                     "manuscript": ids.get("text"), "file": ids.get("file"), "since": ids.get("since"),
                     "response": ids.get("response"), "stat": stat, "digest": dig, "verdict": None}
            if figs or snap.get("missing_figures"):
                entry["figures"] = dict(figchg or {}, count=len(figs), missing=snap.get("missing_figures") or [])
            if answered is not None:
                entry["answered"] = answered
            loop["history"] = history + [entry]
            t["review_loop"] = loop
            self._log(t, "{}차 심사 요청 → {}{} ({}) · 원고 {}".format(
                n, plan["reviewer"], " · " + models.spec_label(plan["model"], plan["effort"])
                if plan["model"] or plan["effort"] else "", rtid, os.path.basename(path)),
                kind="info", author=who, ref=rtid)
            rec = self._save_review_record(t, who)
            if rec:
                entry["record"] = rec["id"]
        return dict(entry, rounds=plan["rounds"], reviewer=plan["reviewer"], created=created, kind="doc",
                    excluded=[], stat=stat or {"files": len(snap["files"]), "additions": 0, "deletions": 0})

    def _attach_figures(self, r, figs, n, who):
        """심사 작업에 원고 그림을 붙인다 — 그림마다 결과물 하나(figure = 원고 폴더 기준 이름), 바뀐 그림은 새 버전."""
        have = dict((a["figure"], a) for a in r.get("artifacts", []) if a.get("figure"))
        out, added, changed = [], 0, 0
        for rel in sorted(figs):
            a = have.get(rel)
            if a:
                if not self._new_version(r, a, figs[rel]["path"], author=who, log=False).get("unchanged"):
                    changed += 1
            else:
                a = self._add_artifact(r, path=figs[rel]["path"], filename=os.path.basename(rel),
                                       title="그림: {}".format(rel), category="source", author=who,
                                       extra={"figure": rel}, log=False)
                added += 1
            out.append(a["id"])
        if added or changed:
            self._log(r, "{}차 원고 그림 {}개 (새로 붙임 {} · 바뀜 {})".format(n, len(out), added, changed),
                      kind="artifact", author=who, meta={"figures": out})
        return out

    def allow_same_session_review(self, rtid, author=None):
        """이 리뷰 작업은 개발한 대화가 리뷰해도 된다고 적어 둔다 (사용자가 그렇게 하라고 한 경우만)."""
        with self.edit(rtid) as r:
            of = r.get("review_of")
            if not of:
                raise DevTrackError("{} 은(는) review-request 로 만든 리뷰 작업이 아닙니다".format(r["id"]))
            if not of.get("same_session"):
                of["same_session"] = True
                self._log(r, "개발한 대화가 리뷰해도 됨 (사용자 허락)", kind="info", author=author)
            return r

    def task_sessions(self, tid):
        """이 작업에 기록한 AI 세션들 (.agent/sessions)."""
        tid = normalize_task_id(tid)
        d = os.path.join(self.root, ".agent", "sessions")
        found = []
        try:
            names = os.listdir(d)
        except OSError:
            return found
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    data = json.load(fh)
                if tid in (data.get("tasks") or []):
                    found.append(data.get("session") or name[:-5])
            except (OSError, ValueError, AttributeError):
                continue
        return found

    def review_done(self, rtid, verdict=None, summary="", author=None):
        """리뷰 작업의 이번 차례 결과(통과 / 수정 필요)를 개발 작업의 AI 에게 [AI 메시지] 로 보낸다."""
        rtid = normalize_task_id(rtid)
        r = self.load(rtid)
        tid = (r.get("review_of") or {}).get("task")
        if not tid:
            raise DevTrackError("{} 은(는) devtrack review-request 로 만든 리뷰 작업이 아닙니다 — "
                                "PR 리뷰 등은 devtrack review 로 기록합니다".format(rtid))
        t = self.load(tid)
        loop = t.get("review_loop") or {}
        history = loop.get("history") or []
        cur = history[-1] if history else None
        if not cur or cur.get("task") != rtid or cur.get("verdict"):
            raise DevTrackError("{} 에 결과를 기다리는 리뷰 차례가 없습니다".format(rtid))
        rv = r.get("review") or {}
        doc = loop.get("kind") == "doc"
        v = _doc_verdict(verdict or rv.get("verdict")) if doc else _review_verdict(verdict or rv.get("verdict"))
        if not v:
            raise DevTrackError("판정을 정하세요: --verdict accept · minor · major · reject" if doc else
                                "판정을 정하세요: --verdict approve (통과) 또는 changes (수정 필요)")
        passed = v in ("approve", "ready")
        open_ = [f for f in rv.get("findings") or [] if f.get("status") == "open"]
        if not passed and not open_:
            raise DevTrackError("수정이 필요한 판정인데 열린 검토 항목이 없습니다 — 먼저 devtrack review {} --findings 로 "
                                "R1, R2… 를 남기세요".format(rtid))
        n, total = cur["round"], loop.get("rounds") or REVIEW_ROUNDS_DEFAULT
        used = n - int(loop.get("cycle_start") or 0)
        state = ("approved" if passed else "rejected" if v == "rework" else "exhausted" if used >= total
                 else "changes")
        who = author or r.get("agent")
        items = " · ".join("{} {}".format(f["id"], FINDING_SEV_LABEL.get(f.get("severity"), f.get("severity")))
                           for f in open_[:12]) + (" 외 {}개".format(len(open_) - 12) if len(open_) > 12 else "")
        head = "{}차 리뷰 {} — {} ({}){}".format(n, "통과" if v == "approve" else "결과: 수정 필요", who, rtid,
                                               ": " + summary.strip() if summary and summary.strip() else "")
        if doc:
            head = "{}차 심사: {} — {} ({}){}".format(n, JOURNAL_VERDICT_LABEL.get(v, v), who, rtid,
                                                  ": " + summary.strip() if summary and summary.strip() else "")
        if doc and state == "approved":
            msg = head + ("\n남은 의견: " + items if open_ else "") + \
                "\n심사를 통과했습니다 — 사용자에게 결과를 알리세요 (devtrack request {} \"심사 통과: …\" -k result).".format(tid)
        elif doc and state == "changes":
            msg = head + "\n열린 항목: {}\n심사평과 항목은 devtrack show {} 에서 봅니다. 원고를 고친 뒤 지적마다 어떻게 고쳤는지(고치지 " \
                         "않았다면 그 이유)를 적은 답변서(response to reviewers)를 만들어 devtrack review-request {} --response " \
                         "<답변서.md> 로 다시 심사를 요청하세요 (남은 횟수 {}번).".format(items, rtid, tid, total - used)
        elif doc and state == "rejected":
            msg = head + "\n열린 항목: {}\n게재 불가 판정입니다 — 사용자에게 결정을 요청하세요 (devtrack request {} \"심사 결과: " \
                         "게재 불가\" -k result: 크게 고쳐 다시 심사 · 다른 투고처 · 중단).".format(items, tid)
        elif doc:
            msg = head + "\n남은 항목: {}\n심사를 {}번 모두 받았습니다 — 남은 지적과 함께 사용자에게 결정을 요청하세요 " \
                         "(devtrack request {} \"남은 심사 지적 처리\": 이대로 마무리 · 더 고치기 · 심사 더 받기).".format(
                             items, total, tid)
        elif state == "approved":
            msg = head + ("\n남은 의견: " + items if open_ else "") + \
                "\n이제 커밋 승인을 요청하세요 (devtrack request {} \"…\" --commit -m \"…\").".format(tid)
        elif state == "changes":
            msg = head + "\n열린 항목: {}\n내용은 devtrack show {} 에서 보고, 고친 뒤 devtrack review-request {} 로 " \
                         "다시 리뷰를 요청하세요 (남은 횟수 {}번).".format(items, rtid, tid, total - used)
        else:
            msg = head + "\n남은 항목: {}\n리뷰를 {}번 모두 받았습니다 — 남은 지적과 함께 사용자에게 결정을 요청하세요 " \
                         "(devtrack request {} \"남은 리뷰 지적 처리\": 이대로 커밋 · 더 고치기 · 리뷰 더 받기).".format(
                             items, total, tid)
        result = {"round": n, "verdict": v, "state": state, "open": [f["id"] for f in open_], "task": rtid,
                  "summary": (summary or "").strip() or None}
        with self.edit(rtid) as r:
            rv = dict(r.get("review") or {})
            rv["verdict"] = v
            if summary and summary.strip():
                rv["summary"] = summary.strip()
            rv["updated_at"] = now()
            r["review"] = rv
            rnd = dict(result, at=now(), by=who)
            if doc:  # 이 차례의 질문 · 요청을 그대로 남긴다 (항목은 같은 ID 끼리 합쳐지므로 — 심사 · 답변 기록용)
                rnd["findings"] = [dict(f) for f in rv.get("findings") or []]
            r.setdefault("review_rounds", []).append(rnd)
            self._log(r, "{}차 {} 결과: {} (열린 항목 {}개) → {}".format(
                n, "심사" if doc else "리뷰", JOURNAL_VERDICT_LABEL.get(v) if doc else "통과" if passed else "수정 필요",
                len(open_), tid), kind="success" if passed else "warning", author=who)
            if state in ("approved", "exhausted", "rejected"):
                old = r["status"]
                if old != "done":
                    r.update(status="done", progress=100, current_step="", completed_at=now())
                    self._log(r, "상태 변경: {} → {}".format(STATUSES.get(old, old), STATUSES["done"]), kind="status",
                              author=who, meta={"from": old, "to": "done"})
            else:
                if r["status"] == "queued":
                    r["status"] = "in_progress"
                r["current_step"] = "{}차 {} 끝 — {} 의 수정을 기다리는 중".format(n, "심사" if doc else "리뷰", t.get("agent"))
        with self.edit(tid) as t:
            loop = t["review_loop"]
            loop["history"][-1].update(verdict=v, open=result["open"], done_at=now(), summary=result["summary"],
                                       by=who)
            loop["state"] = state
            rec = self._save_review_record(t, who) if doc else None
            if rec and state == "changes":
                msg += ("\n답변서는 열린 항목마다 '## R1 — 제목' 처럼 항목 ID 로 제목을 달아, 질문에는 답을, 요청에는 어떻게 고쳤는지"
                        "(고치지 않았다면 그 이유)를 씁니다 — 빠진 항목이 있으면 다시 요청이 거절됩니다. 질문 · 요청과 답변은 "
                        "심사 · 답변 기록 {} 에 차례별로 모입니다.").format(rec["id"])
            elif rec:
                msg += "\n알릴 때 심사 · 답변 기록 {0} (심사위원의 질문 · 요청과 저자 답변, 차례별)을 함께 첨부하세요: " \
                       "devtrack request {1} \"…\" -k result -a {0}".format(rec["id"], tid)
                result["record"] = rec["id"]
            self._agent_message(t, msg, who, meta={"review_result": result})
        return result

    def _save_review_record(self, t, who):
        """논문 · 문서 심사의 '심사 · 답변 기록'(review_response_record.md)을 논문 작업의 결과물로 만들거나 새 버전으로 고친다.
        심사 판정과 답변서가 올 때마다 부른다 (잠금 안에서, t 는 편집 중인 작업). → 결과물"""
        loop = t.get("review_loop") or {}
        if loop.get("kind") != "doc" or not loop.get("history"):
            return None
        text = self._review_record_text(t)
        title = "심사 · 답변 기록 ({}차까지)".format(loop["history"][-1]["round"])
        art = next((a for a in t.get("artifacts", []) if a.get("review_record") and a.get("path")), None)
        if art:
            fd, tmp = tempfile.mkstemp(prefix=".tmp-record-", suffix=".md", dir=self.task_dir(t["id"]))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                art = self._new_version(t, art, tmp, title=title, author=who)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        else:
            art = self._add_artifact(t, text=text, filename="review_response_record.md", title=title, author=who,
                                     description="심사위원의 질문 · 요청(R1, R2…)과 저자가 답변서로 답한 내용을 차례별로 모은 기록. "
                                                 "DevTrack 이 심사 판정과 답변서가 올 때마다 고친다.",
                                     extra={"review_record": True})
        loop["record"] = art["id"]
        return art

    def _review_record_text(self, t):
        """심사 · 답변 기록 (Markdown): 차례마다 판정 · 심사위원의 질문과 요청(그 차례의 열린 항목) · 다음 답변서의 답 · 해결 여부."""
        loop = t.get("review_loop") or {}
        hist = loop.get("history") or []
        cache = {}

        def rtask(rid):
            if rid not in cache:
                try:
                    cache[rid] = self.load(rid)
                except (DevTrackError, OSError, ValueError):
                    cache[rid] = {"id": rid}
            return cache[rid]

        def answers(h):
            """그 차례 요청에 붙은 답변서 → (답변서 결과물 ID, 총평, {항목 ID: 답})"""
            if not h or not h.get("response"):
                return None, "", {}
            r = rtask(h["task"])
            a = next((x for x in r.get("artifacts", []) if x.get("id") == h["response"]), None)
            path = a and self._artifact_file(r["id"], a)
            try:
                general, secs = response_sections(_read_response(path)) if path else ("", {})
            except (DevTrackError, OSError):
                general, secs = "", {}
            return h["response"], general, secs

        def items(h):
            """그 차례에 열려 있던 질문 · 요청 (판정 때 떠 둔 항목, 없으면 지금 항목)"""
            r = rtask(h["task"])
            snap = next((x for x in reversed(r.get("review_rounds") or [])
                         if x.get("round") == h["round"] and x.get("findings") is not None), None)
            pool = snap["findings"] if snap else (r.get("review") or {}).get("findings") or []
            byid = dict((f["id"], f) for f in pool)
            return [byid.get(i) or {"id": i, "title": "(내용 없음)"} for i in h.get("open") or []]

        def quote(text):
            return "\n".join("  > " + line if line.strip() else "  >" for line in str(text).strip().splitlines())

        def spec(model, effort):
            s = models.spec_label(model, effort)
            return " ({})".format(s) if s else ""

        state = loop.get("state")
        final = state in ("approved", "exhausted", "rejected")
        reports = []
        for rid in sorted({h["task"] for h in hist if h.get("task")}):
            reports += ["{} 의 {}{}".format(rid, a["id"], " (v{})".format(a["version"]) if a.get("version", 1) > 1 else "")
                        for a in rtask(rid).get("artifacts", []) if a.get("category") == "review"]
        out = ["# 심사 · 답변 기록 — {}".format(t["title"]), "",
               "심사위원이 남긴 **질문 · 요청**(R1, R2…)과 저자가 답변서(response to reviewers)로 **답한 내용**을 차례별로 모은 기록입니다.",
               "DevTrack 이 심사 판정과 답변서가 올 때마다 새 버전으로 고칩니다.", "",
               "| 항목 | 내용 |", "|---|---|",
               "| 논문 작업 | {} |".format(t["id"]),
               "| 저자 | {}{} |".format(t.get("agent") or "AI", spec(t.get("model"), t.get("effort"))),
               "| 심사위원 | {}{} |".format(loop.get("reviewer") or "—", spec(loop.get("model"), loop.get("effort"))),
               "| 투고처 | {} |".format(loop.get("venue") or "—"),
               "| 원고 | `{}` |".format(loop.get("doc") or "—"),
               "| 상태 | {} ({}차까지, 최대 {}번) |".format(DOC_LOOP_STATES.get(state, state or "—"), len(hist),
                                                    loop.get("rounds") or REVIEW_ROUNDS_DEFAULT),
               "| 심사평 (referee report) | {} |".format(" · ".join(reports) or "—"), "",
               "## 한눈에 보기", "",
               "| 차례 | 판정 | 질문 · 요청 | 저자 답변 | 다음 심사에서 해결 |", "|---|---|---|---|---|"]
        rounds = []
        for i, h in enumerate(hist):
            nxt = hist[i + 1] if i + 1 < len(hist) else None
            its = items(h) if h.get("verdict") else []
            rid, general, secs = answers(nxt)
            judged = bool(nxt and nxt.get("verdict"))
            fixed = [f for f in its if judged and f["id"] not in (nxt.get("open") or [])]
            q = sum(1 for f in its if f.get("severity") == "question")
            out.append("| {}차 | {} | {} | {} | {} |".format(
                h["round"], JOURNAL_VERDICT_LABEL.get(h.get("verdict"), "심사 중") if h.get("verdict") else "심사 중",
                "{}개{}".format(len(its), " (질문 {})".format(q) if q else "") if h.get("verdict") else "—",
                "{}/{}".format(sum(1 for f in its if secs.get(f["id"])), len(its)) if nxt and its else "—",
                "{}/{}".format(len(fixed), len(its)) if judged and its else "—"))
            rounds.append((h, nxt, its, rid, general, secs, judged))
        for h, nxt, its, rid, general, secs, judged in rounds:
            n = h["round"]
            out += ["", "## {}차 심사 — {}".format(n, JOURNAL_VERDICT_LABEL.get(h.get("verdict"), "심사 중")
                                               if h.get("verdict") else "심사 중"), ""]
            meta = ["요청 {} ({})".format((h.get("requested_at") or "")[:10], h.get("requested_by") or "—")]
            if h.get("done_at"):
                meta.append("판정 {} ({})".format(h["done_at"][:10], h.get("by") or loop.get("reviewer") or "—"))
            out.append(" · ".join(meta))
            if h.get("summary"):
                out += ["", "**심사위원 요약:** {}".format(h["summary"])]
            if not h.get("verdict"):
                out += ["", "심사위원이 심사하는 중입니다."]
                continue
            if not its:
                out += ["", "열린 질문 · 요청이 없습니다."]
            if general:
                out += ["", "**저자 총평 ({}차 답변서 {}):**".format(nxt["round"], rid), "", quote(general).replace("  >", ">")]
            for f in its:
                sev = f.get("severity")
                out += ["", "### {} · {} — {}".format(f["id"], FINDING_SEV_LABEL.get(sev, sev or "—"),
                                                      f.get("title") or "(제목 없음)"), ""]
                if f.get("location"):
                    out.append("- **위치:** {}".format(f["location"]))
                if f.get("quote"):
                    out += ["- **원고 원문:**", quote(f["quote"])]
                if f.get("plain"):
                    out.append("- **{}:** {}".format("질문" if sev == "question" else "심사위원 설명", f["plain"]))
                if f.get("suggestion"):
                    out.append("- **{}:** {}".format("심사위원 제안" if sev == "question" else "요청 (고칠 방법)",
                                                     f["suggestion"]))
                ans = secs.get(f["id"])
                if nxt and ans:
                    out += ["- **저자 답변 ({}차 답변서 {}):**".format(nxt["round"], rid), quote(ans)]
                elif nxt:
                    out.append("- **저자 답변:** 답변서에 이 항목이 없습니다")
                else:
                    out.append("- **저자 답변:** {}".format("없음 (마지막 차례)" if final else "아직 없음 — 다음 답변서에서 답합니다"))
                if judged:
                    out.append("- **{}차 심사 결과:** {}".format(
                        nxt["round"], "아직 열림" if f["id"] in (nxt.get("open") or []) else "해결됨"))
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------ 계획
    def set_plan(self, tid, items, append=False, author=None):
        with self.edit(tid) as t:
            new = [{"text": s, "done": False} for s in items]
            t["plan"] = (t.get("plan") or []) + new if append else new
            self._log(t, ("계획 단계 추가" if append else "작업 계획 수립") + " ({}단계)".format(len(t["plan"])),
                      kind="step", author=author, detail="\n".join("- " + s for s in items))
            return t["plan"]

    def check_plan(self, tid, numbers, done=True, author=None):
        with self.edit(tid) as t:
            plan = t.get("plan") or []
            texts = []
            for n in numbers:
                n = int(n)
                if not 1 <= n <= len(plan):
                    raise DevTrackError("계획 단계 번호 범위를 벗어났습니다: {} (1~{})".format(n, len(plan)))
                plan[n - 1]["done"] = done
                if done:
                    plan[n - 1]["done_at"] = now()
                texts.append(plan[n - 1]["text"])
            if plan:
                t["progress"] = round(100 * sum(1 for p in plan if p["done"]) / len(plan))
            self._log(t, ("단계 완료: " if done else "단계 완료 취소: ") + ", ".join(texts),
                      kind="success" if done else "info", author=author, meta={"progress": t["progress"]})
            return plan

    # ------------------------------------------------------------------ 문제점
    def add_issue(self, tid, title, detail=None, severity="medium", author=None):
        if severity not in SEVERITIES:
            raise DevTrackError("심각도는 {} 중 하나여야 합니다".format(", ".join(SEVERITIES)))
        with self.edit(tid) as t:
            iid = self._next(t, "I")
            issue = {"id": iid, "title": title, "detail": detail or "", "severity": severity,
                     "status": "open", "created_at": now(), "author": author or t.get("agent")}
            t.setdefault("issues", []).append(issue)
            self._log(t, "문제 발견: " + title, kind="issue", author=author, ref=iid, detail=detail,
                      meta={"severity": severity})
            return issue

    def resolve_issue(self, tid, iid, note=None, author=None):
        iid = normalize_item_id(iid, "I")
        with self.edit(tid) as t:
            issue = self._find(t.get("issues", []), iid, "문제")
            issue["status"] = "resolved"
            issue["resolved_at"] = now()
            if note:
                issue["resolution"] = note
            self._log(t, "문제 해결: " + issue["title"], kind="success", author=author, ref=iid, detail=note)
            return issue

    # ------------------------------------------------------------------ 결과물
    def _add_artifact(self, t, path=None, url=None, text=None, filename=None, title=None, kind=None,
                      description=None, category="result", copy=True, author=None, lang=None,
                      extra=None, log=True):
        fid = self._next(t, "F")
        art = {"id": fid, "category": category, "created_at": now(), "author": author or t.get("agent")}
        name = filename or (os.path.basename(path.rstrip("/")) if path else None)
        if path:
            src = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(src):
                raise DevTrackError("파일을 찾을 수 없습니다: {}".format(path))
            if os.path.isdir(src):
                raise DevTrackError("디렉토리는 결과물로 추가할 수 없습니다 (zip 으로 묶거나 파일별로 추가): {}".format(path))
            if copy:
                rel = "artifacts/{}_{}".format(fid, _safe_name(name))
                dest = os.path.join(self.tasks_dir, t["id"], rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)
                _chmod(dest, 0o644)
                art["path"] = rel
                art["size"] = os.path.getsize(dest)
            else:
                art["external_path"] = src
                art["size"] = os.path.getsize(src)
        elif text is not None:
            rel = "artifacts/{}_{}".format(fid, _safe_name(name or "output.txt"))
            dest = os.path.join(self.tasks_dir, t["id"], rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text)
            _chmod(dest, 0o644)
            art["path"] = rel
            art["size"] = os.path.getsize(dest)
        elif url:
            art["url"] = url
        else:
            raise DevTrackError("파일 경로, 텍스트, URL 중 하나가 필요합니다")
        if name:
            art["filename"] = name
        art["kind"] = kind or (detect_kind(name) if name else "link")
        lang = lang or (detect_lang(name) if name else None)
        if lang:
            art["lang"] = lang
        art["title"] = title or name or url
        if description:
            art["description"] = description
        if extra:
            art.update(extra)
        t.setdefault("artifacts", []).append(art)
        if art.get("kind") == "docx" and art.get("path"):
            self._docx_preview(t, art)
        self._mark_paper(t, art)
        if log:
            label = {"review": "리뷰 리포트 등록", "source": "검토할 문서 등록"}.get(category, "결과물 추가")
            self._log(t, "{}: {}".format(label, art["title"]), kind="artifact", author=author, ref=fid,
                      meta={"category": category, "kind": art["kind"]})
        return art

    def _mark_paper(self, t, art):
        """원고(.tex · .bib …)나 그림 결과물이면 저장할 때 원고 폴더를 다시 만들게 표시 (연결된 논문 작업 것도)."""
        ext = os.path.splitext(art.get("filename") or "")[1].lower()
        if art.get("category", "result") == "result" and not art.get("build") and \
                (ext in PAPER_SOURCE_EXT or ext in manuscript.FIGURE_EXT):
            self._paper_dirty.add(t["id"])

    # ------------------------------------------------------------------ 논문 원고 폴더
    def _artifact_file(self, tid, a):
        if a.get("path"):
            return os.path.join(self.tasks_dir, tid, a["path"])
        return a.get("external_path")

    def _paper_arts(self, t):
        """원고 폴더에 쓸 수 있는 결과물 (분류 result, 빌드 결과물 제외, 파일이 있는 것)."""
        return [a for a in t.get("artifacts", []) if a.get("category", "result") == "result" and not a.get("build")
                and a.get("filename") and self._artifact_file(t["id"], a)
                and os.path.isfile(self._artifact_file(t["id"], a))]

    @staticmethod
    def _latest_by_name(arts):
        """같은 파일 이름이면 가장 최근에 올리거나 고친 것 → {파일 이름: 결과물}."""
        best = {}
        for i, a in enumerate(arts):
            key = (_ts_key(a.get("updated_at") or a.get("created_at")), i)
            if a["filename"] not in best or key >= best[a["filename"]][0]:
                best[a["filename"]] = (key, a)
        return {name: a for name, (_, a) in best.items()}

    def _linked_ids(self, t, tasks):
        """연결된 작업 (양쪽 방향 — 웹의 관련 작업 표시와 같음)."""
        ids = set(t.get("related") or [])
        ids |= {o["id"] for o in tasks.values() if t["id"] in (o.get("related") or [])}
        ids.discard(t["id"])
        return ids

    def paper_folder(self, t, tasks=None):
        """논문 작업의 원고 폴더(tasks/<T>/paper)를 결과물에서 다시 만든다 (잠금은 부르는 쪽에서).
        - 최신 .tex · .bib · .sty · .cls · .bst 결과물을 원래 파일 이름으로
        - 원고가 \\includegraphics 로 부르는 그림을 같은 이름(확장자 무관)의 그림 결과물에서 — 이 작업 > 연결된 작업 순 —
          LaTeX 가 찾는 폴더(\\includegraphics 의 폴더 > 첫 \\graphicspath > figures/)에
        → {"task", "dir", "files", "figures", "refs", "missing": [{"ref", "reason", "found"}]}. 폴더는 DevTrack 이 관리한다
        (직접 넣은 파일은 다음에 다시 만들 때 지워진다)."""
        tid = t["id"]
        if tasks is None:
            tasks = {x["id"]: x for x in self.all_tasks()}
        tasks = dict(tasks, **{tid: t})
        root = os.path.join(self.tasks_dir, tid, PAPER_DIR)
        mine = self._paper_arts(t)
        want, files = {}, []
        for name, a in sorted(self._latest_by_name(
                [a for a in mine if os.path.splitext(a["filename"])[1].lower() in PAPER_SOURCE_EXT]).items()):
            rel = _safe_name(name)
            if rel not in want:
                want[rel] = self._artifact_file(tid, a)
                files.append(rel)
        gpaths, refs, bibs = [], [], []
        for rel in files:
            if rel.lower().endswith(".tex"):
                with open(want[rel], encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                p, r = manuscript.graphics(text)
                gpaths += [x for x in p if x not in gpaths]
                refs += [x for x in r if x not in refs]
                bibs += [b for b in manuscript.bibliographies(text) if b not in bibs]
        # 그림 결과물: 이름(확장자 · F-n 번호 뺌) → [(순위, 작업, 결과물)] — 이 작업 0, 연결된 작업 1
        by_stem = {}
        groups = [(0, t)] + [(1, tasks[x]) for x in sorted(self._linked_ids(t, tasks)) if x in tasks]
        for rank, owner in groups:
            arts = mine if owner is t else self._paper_arts(owner)
            figs = [a for a in arts if os.path.splitext(a["filename"])[1].lower() in manuscript.FIGURE_EXT]
            for name, a in self._latest_by_name(figs).items():
                by_stem.setdefault(manuscript.graphic_stem(name).lower(), []).append((rank, owner["id"], a))
        figures, missing = [], []
        for ref in refs:
            cands = by_stem.get(manuscript.graphic_stem(ref).lower())
            if not cands:
                missing.append({"ref": ref, "reason": "none", "found": []})
                continue
            best = min(c[0] for c in cands)
            ref_dir = os.path.dirname(ref.replace("\\", "/"))
            folder = _paper_dir(ref_dir, gpaths)
            for _, owner_id, a in [c for c in cands if c[0] == best]:
                rel = "{}/{}".format(folder, _safe_name(a["filename"]))
                if rel not in want:
                    want[rel] = self._artifact_file(owner_id, a)
                    figures.append(rel)
        _sync_files(root, want)
        for ref in refs:  # LaTeX 가 실제로 찾을 수 있는지 (확장자를 적었는데 다른 형식만 있으면 못 찾음)
            if any(m["ref"] == ref for m in missing) or manuscript.find_graphic(root, ref, gpaths):
                continue
            found = [f for f in figures if manuscript.graphic_stem(f).lower() == manuscript.graphic_stem(ref).lower()]
            if found and not any(os.path.splitext(f)[1].lower() in manuscript.GRAPHIC_EXT for f in found):
                reason = "format"  # SVG · GIF 등 — \includegraphics 로 바로 못 씀
            elif os.path.splitext(ref)[1].lower() in manuscript.GRAPHIC_EXT:
                reason = "ext"
            else:
                reason = "path"
            missing.append({"ref": ref, "reason": reason, "found": found})
        status = {"task": tid, "dir": root, "files": files, "figures": figures, "refs": refs, "missing": missing,
                  "graphicspath": gpaths, "missing_bib": [b for b in bibs if b not in want]}
        self.paper_status[tid] = status
        return status

    def _refresh_papers(self, t):
        """이 작업(논문이면)과 연결된 논문 작업의 원고 폴더를 다시 만든다. 실패해도 저장은 계속 (경고만)."""
        tasks = {x["id"]: x for x in self.all_tasks()}
        tasks[t["id"]] = t
        targets = [t] if t.get("type") in PAPER_TYPES else []
        targets += [tasks[x] for x in sorted(self._linked_ids(t, tasks)) if tasks[x].get("type") in PAPER_TYPES]
        for p in targets:
            try:
                self.paper_folder(p, tasks)
            except (OSError, ValueError) as e:
                sys.stderr.write("[devtrack] {} 원고 폴더를 만들지 못했습니다: {}\n".format(p["id"], e))
                continue
            if p["id"] != t["id"]:
                self._sync_publish(p["id"])

    def rebuild_paper(self, tid):
        """논문 작업의 원고 폴더를 지금 결과물로 다시 만든다 (devtrack paper)."""
        tid = normalize_task_id(tid)
        with self.locked():
            t = self.load(tid)
            if t.get("type") not in PAPER_TYPES:
                raise DevTrackError("{} 은(는) 논문 작업이 아닙니다 — 원고 폴더는 논문 작업(-t paper)에만 만듭니다".format(tid))
            status = self.paper_folder(t)
            self._sync_publish(tid)
            return status

    def _docx_preview(self, t, art):
        """Word 문서의 글자 · 표를 Markdown 으로 뽑아 웹 미리보기(art['preview'])로 둔다. 실패해도 결과물 등록은 계속."""
        from .docx import docx_to_markdown
        src = os.path.join(self.tasks_dir, t["id"], art["path"])
        rel = art["path"] + ".md"
        try:
            text = docx_to_markdown(src)
        except Exception as e:  # noqa: BLE001 — 손상된 파일 등 어떤 이유든 미리보기만 건너뛴다
            art.pop("preview", None)
            sys.stderr.write("[devtrack] Word 미리보기를 만들지 못했습니다 ({}): {}\n".format(art.get("filename"), e))
            return
        dest = os.path.join(self.tasks_dir, t["id"], rel)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
        _chmod(dest, 0o644)
        art["preview"] = rel

    # ------------------------------------------------------------------ LaTeX 빌드
    def record_build(self, tid, source, result, author=None):
        """LaTeX 빌드 결과 기록. 성공하면 PDF, 실패하면 로그를 결과물로 첨부한다."""
        from .latex import warning_count
        ok = result.get("status") == "ok"
        stem = os.path.splitext(os.path.basename(source))[0] or "main"
        with self.edit(tid) as t:
            bid = self._next(t, "B")
            n = bid.split("-")[1]
            build = {
                "id": bid, "source": source, "engine": result.get("engine"),
                "status": "ok" if ok else "failed", "pages": result.get("pages"),
                "duration": result.get("duration"), "errors": result.get("errors") or [],
                "warnings": result.get("warnings") or {}, "created_at": now(),
                "author": author or t.get("agent"),
            }
            if ok and result.get("pdf"):
                art = self._add_artifact(t, path=result["pdf"], filename=stem + ".pdf", kind="pdf", author=author,
                                         title="{}.pdf (빌드 #{})".format(stem, n), extra={"build": bid}, log=False)
                build["pdf"] = art["id"]
            if not ok and result.get("log_text"):
                art = self._add_artifact(t, text=result["log_text"], filename=stem + ".log", kind="text",
                                         title="{}.log (빌드 #{} 실패)".format(stem, n), extra={"build": bid},
                                         author=author, log=False)
                build["log"] = art["id"]
            t.setdefault("builds", []).append(build)
            w = build["warnings"]
            if ok:
                wc = warning_count(w)
                msg = "LaTeX 컴파일 성공: {}.pdf ({}쪽{})".format(
                    stem, build["pages"] or "?", ", 경고 {}".format(wc) if wc else "")
                lines = []
                if w.get("undefined_citations"):
                    lines.append("- 정의되지 않은 인용: " + ", ".join("`{}`".format(c) for c in w["undefined_citations"]))
                if w.get("undefined_references"):
                    lines.append("- 정의되지 않은 참조: " + ", ".join("`{}`".format(r) for r in w["undefined_references"]))
                if w.get("overfull"):
                    lines.append("- Overfull box {}개".format(w["overfull"]))
                self._log(t, msg, kind="success", author=author, ref=bid, detail="\n".join(lines) or None,
                          meta={"build": bid, "pdf": build.get("pdf")})
            else:
                e = (build["errors"] or [{"message": "알 수 없는 오류"}])[0]
                where = " ({}:{})".format(e.get("file") or source, e["line"]) if e.get("line") else ""
                detail = "\n".join("- `{}{}` {}".format(x.get("file") or "", ":{}".format(x["line"]) if x.get("line") else "",
                                                        x["message"]) for x in build["errors"])
                self._log(t, "LaTeX 컴파일 실패: {}{}".format(e["message"], where), kind="error", author=author,
                          ref=bid, detail=detail or None, meta={"build": bid})
            return build

    def add_artifact(self, tid, replace_same_name=False, **kw):
        """replace_same_name: 같은 작업 · 같은 분류에 같은 파일 이름의 결과물이 있으면 새 결과물을 만들지 않고
        그 결과물의 새 버전으로 교체한다 (이전 파일은 versions 에 남김). 내용이 같으면 그대로 둔다."""
        with self.edit(tid) as t:
            path = kw.get("path")
            if replace_same_name and path and kw.get("copy", True):
                name = kw.get("filename") or os.path.basename(path.rstrip("/"))
                old = next((a for a in reversed(t.get("artifacts", []))
                            if a.get("path") and a.get("filename") == name
                            and a.get("category") == kw.get("category", "result")), None)
                if old:
                    return self._new_version(t, old, path, title=kw.get("title"),
                                             description=kw.get("description"), author=kw.get("author"))
            return self._add_artifact(t, **kw)

    def _new_version(self, t, art, path, title=None, description=None, author=None, log=True):
        src = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(src):
            raise DevTrackError("파일을 찾을 수 없습니다: {}".format(path))
        cur = os.path.join(self.tasks_dir, t["id"], art["path"])
        try:
            with open(src, "rb") as a, open(cur, "rb") as b:
                if a.read() == b.read():
                    return dict(art, unchanged=True)
        except OSError:
            pass
        n = art.get("version", 1)
        stem = os.path.basename(art["path"])
        old_rel = "artifacts/{}_v{}_{}".format(art["id"], n, stem[len(art["id"]) + 1:] if stem.startswith(art["id"] + "_") else stem)
        if os.path.exists(cur):
            os.replace(cur, os.path.join(self.tasks_dir, t["id"], old_rel))
            art.setdefault("versions", []).append({
                "version": n, "path": old_rel, "size": art.get("size"),
                "created_at": art.get("updated_at") or art.get("created_at"),
                "author": art.get("updated_by") or art.get("author")})
        shutil.copyfile(src, cur)
        _chmod(cur, 0o644)
        art.update({"version": n + 1, "size": os.path.getsize(cur), "updated_at": now(),
                    "updated_by": author or t.get("agent")})
        if title:
            art["title"] = title
        if description:
            art["description"] = description
        if art.get("kind") == "docx":
            self._docx_preview(t, art)
        self._mark_paper(t, art)
        if log:
            label = {"review": "리뷰 리포트 갱신", "source": "검토할 문서 갱신"}.get(art.get("category"), "결과물 갱신")
            self._log(t, "{} (v{}): {}".format(label, n + 1, art["title"]), kind="artifact", author=author,
                      ref=art["id"], meta={"category": art.get("category"), "kind": art.get("kind"), "version": n + 1})
        return art

    # ------------------------------------------------------------------ 코드 리뷰 검토 항목
    def set_review(self, tid, data, author=None):
        """리뷰 대상 PR 정보 · 종합 의견 · 검토 항목(R1, R2…)을 기록한다. 주어진 키만 갱신."""
        if isinstance(data, list):
            data = {"findings": data}
        if not isinstance(data, dict):
            raise DevTrackError("검토 결과는 JSON 객체 또는 항목 목록이어야 합니다")
        with self.edit(tid) as t:
            rv = dict(t.get("review") or {})
            prev = {f["id"]: f for f in rv.get("findings", [])}
            if isinstance(data.get("pr"), dict):
                pr = {k: data["pr"][k] for k in _PR_KEYS if data["pr"].get(k) not in (None, "")}
                rv["pr"] = dict(rv.get("pr") or {}, **pr)
            if isinstance(data.get("doc"), dict):
                rv["doc"] = dict(rv.get("doc") or {}, **_norm_doc(data["doc"]))
            if data.get("verdict"):
                v = str(data["verdict"]).strip().lower()
                if t.get("type") == "docreview" or rv.get("doc"):  # 문서 리뷰: 제출 가능 / 소폭 수정 / 대폭 보완 / 다시 작성
                    v = v if v in DOC_VERDICTS else _DOC_VERDICT_ALIAS.get(v)
                    allowed = DOC_VERDICTS
                else:
                    v = v if v in VERDICTS else _VERDICT_ALIAS.get(v)
                    allowed = VERDICTS
                if not v:
                    raise DevTrackError("verdict 는 {} 중 하나여야 합니다".format(", ".join(allowed)))
                rv["verdict"] = v
            for key in ("summary", "verdict_reason"):
                if data.get(key):
                    rv[key] = str(data[key]).strip()
            if "checks" in data:  # 점검 영역은 통째로 교체
                rv["checks"] = _norm_checks(data["checks"] or [])
            if "strengths" in data:
                items = data["strengths"] or []
                rv["strengths"] = [str(x).strip() for x in (items if isinstance(items, list) else [items]) if str(x).strip()]
            if "findings" in data:
                items = data["findings"] or []
                if not isinstance(items, list):
                    raise DevTrackError("findings 는 목록이어야 합니다")
                merged = dict(prev)  # 같은 ID 는 갱신, 새 ID 는 뒤에 추가, 언급 안 된 항목은 유지
                seen = set()
                for i, raw in enumerate(items):
                    rid = _norm_finding_id(_first(raw, "id", "number", "num") if isinstance(raw, dict) else None,
                                           len(prev) + i + 1)
                    if rid in seen:
                        raise DevTrackError("검토 항목 ID 가 중복됩니다: {}".format(rid))
                    seen.add(rid)
                    merged[rid] = normalize_finding(raw, len(prev) + i + 1, base=prev.get(rid))
                rv["findings"] = list(merged.values())
            rv["updated_at"] = now()
            t["review"] = rv
            findings = rv.get("findings", [])
            open_ = [f for f in findings if f["status"] == "open"]
            by_sev = ["{} {}".format(FINDING_SEV_LABEL[s], n) for s in FINDING_SEVERITIES
                      for n in [sum(1 for f in open_ if f["severity"] == s)] if n]
            changed = ["{} {}".format(f["id"], f["status"]) for f in findings
                       if f["id"] in prev and prev[f["id"]].get("status") != f["status"]]
            msg = "검토 결과 기록: 항목 {}개".format(len(findings))
            if by_sev:
                msg += " — " + " · ".join(by_sev)
            if changed:
                msg += " (상태 변경: {})".format(", ".join(changed))
            self._log(t, msg, kind="info", author=author, meta={"review": True})
            return rv

    # ------------------------------------------------------------------ 승인
    @staticmethod
    def _build_choices(t, choices):
        """choices: [] = 검토 항목 중 열린 것 전체, [ID · "ID=설명" · {id,label}, ...] = 지정 항목.
        "ID=설명" 은 검토 항목이 아닌 선택지(예: 브레인스토밍 아이디어)에 이름을 붙인다."""
        findings = {f["id"]: f for f in (t.get("review") or {}).get("findings", [])}
        if not choices:
            src = [f for f in findings.values() if f.get("status") == "open"]
            if not src:
                raise DevTrackError("선택할 검토 항목이 없습니다. 먼저 devtrack review <TID> --findings FILE 로 기록하세요")
        else:
            src = []
            for c in choices:
                if isinstance(c, dict):
                    src.append(dict(c, id=str(c.get("id") or "").strip()))
                    continue
                cid = str(c).strip()
                label = None
                if "=" in cid:
                    cid, label = (x.strip() for x in cid.split("=", 1))
                fid = _norm_finding_id(cid, cid)
                hit = findings.get(fid) or findings.get(cid)
                if label:
                    src.append(dict(hit or {}, id=hit["id"] if hit else cid, label=label))
                else:
                    src.append(hit or {"id": cid, "title": cid})
        out, seen = [], set()
        for c in src:
            if not c.get("id") or c["id"] in seen:
                raise DevTrackError("선택 항목 ID 가 비었거나 중복됩니다: {}".format(c.get("id")))
            seen.add(c["id"])
            item = {"id": c["id"], "label": c.get("label") or c.get("title") or c["id"]}
            for k in ("severity", "plain", "file", "line"):
                if c.get(k) not in (None, ""):
                    item[k] = c[k]
            item["default"] = bool(c.get("default", c.get("severity") in ("blocker", "major")))
            out.append(item)
        return out

    @staticmethod
    def _match_choices(ap, selected):
        ids = [c["id"] for c in ap.get("choices") or []]
        lookup = {i.lower(): i for i in ids}
        out = []
        for s in selected:
            s = str(s).strip()
            hit = lookup.get(s.lower()) or lookup.get(_norm_finding_id(s, s).lower())
            if not hit:
                raise DevTrackError("선택 목록에 없는 항목입니다: {} (가능: {})".format(s, ", ".join(ids)))
            if hit not in out:
                out.append(hit)
        return [i for i in ids if i in out]  # 원래 순서 유지

    def request_approval(self, tid, title, description="", kind=None, artifact_ids=None, commit=None,
                         author=None, choices=None, choices_label=None, skip_review=False):
        """skip_review: 리뷰받기로 한 작업이라도 리뷰 없이 커밋 승인을 요청 (사용자가 그렇게 하라고 한 경우만)."""
        tid = normalize_task_id(tid)
        action = None
        diff_text = None
        review_note = None
        if commit is not None:
            task = self.load(tid)
            repo = commit.get("repo") or (task.get("repo") or {}).get("path")
            if not repo:
                raise DevTrackError("커밋 승인 요청에는 저장소 경로(--repo)가 필요합니다")
            repo = gitops.require_repo(os.path.expanduser(repo))
            message = (commit.get("message") or "").strip()
            if not message:
                raise DevTrackError("커밋 승인 요청에는 커밋 메시지(-m)가 필요합니다")
            files = list(commit.get("files") or [])
            changes = gitops.capture_changes(repo, files)
            diff_text = changes["diff"]
            if not diff_text.strip():
                raise DevTrackError("커밋할 변경 사항이 없습니다: {}{}".format(
                    repo, " (빌드 결과물만 바뀜: {})".format(gitops.excluded_text(changes["excluded"]))
                    if changes["excluded"] else ""))
            action = {
                "type": "git_commit_push",
                "repo": repo,
                "branch": commit.get("branch") or gitops.current_branch(repo),
                "remote": commit.get("remote") or "origin",
                "remote_url": gitops.remote_url(repo, commit.get("remote") or "origin"),
                "message": message,
                "files": files,
                "push": bool(commit.get("push", True)),
                "fingerprint": gitops.fingerprint(diff_text),
                "stat": gitops.diffstat(diff_text),
                "paths": changes["paths"],
                "excluded": changes["excluded"],  # diff · 커밋에서 뺀 빌드 결과물
            }
            review_note = self._commit_review_check(task, repo, changes, skip_review)
            kind = kind or "commit"
        kind = kind or "general"
        if kind not in APPROVAL_KINDS:
            raise DevTrackError("승인 유형은 {} 중 하나여야 합니다".format(", ".join(APPROVAL_KINDS)))
        with self.edit(tid) as t:
            ids = []
            for a in artifact_ids or []:
                fid = normalize_item_id(a, "F")
                self._find(t.get("artifacts", []), fid, "결과물")
                ids.append(fid)
            aid = self._next(t, "A")
            if diff_text is not None:
                art = self._add_artifact(t, text=diff_text, filename="{}_changes.diff".format(aid),
                                         title="커밋 변경사항 ({})".format(aid), kind="diff", author=author)
                ids.insert(0, art["id"])
            approval = {
                "id": aid,
                "title": title,
                "description": description or "",
                "kind": kind,
                "status": "pending",
                "created_at": now(),
                "author": author or t.get("agent"),
                "artifacts": ids,
            }
            if action:
                approval["action"] = action
            if review_note:
                approval["review"] = review_note
            if choices is not None:
                approval["choices"] = self._build_choices(t, choices)
                approval["choices_label"] = (choices_label or "").strip() or "진행할 항목 선택"
            t.setdefault("approvals", []).append(approval)
            if t["status"] != "awaiting_approval":
                t["status_before_approval"] = t["status"]
            t["status"] = "awaiting_approval"
            self._log(t, "승인 요청: " + title, kind="approval", author=author, ref=aid, meta={"kind": kind})
            return approval

    def decide(self, tid, aid, decision, comment="", by=USER, selected=None):
        status = DECISIONS.get(str(decision).lower())
        if not status:
            raise DevTrackError("결정은 approve / changes / reject 중 하나여야 합니다")
        aid = normalize_item_id(aid, "A")
        if isinstance(selected, str):
            selected = [s for s in re.split(r"[\s,]+", selected) if s]
        with self.edit(tid) as t:
            ap = self._find(t.get("approvals", []), aid, "승인 요청")
            if ap["status"] != "pending":
                raise DevTrackError("이미 처리된 승인 요청입니다 ({}: {})".format(aid, ap["status"]))
            picked = None
            if ap.get("choices"):
                picked = self._match_choices(ap, selected or [])
                if status == "approved" and not picked:
                    raise DevTrackError("승인하려면 항목을 하나 이상 선택하세요 ({})".format(
                        ", ".join(c["id"] for c in ap["choices"])))
            elif selected:
                raise DevTrackError("이 승인 요청에는 선택할 항목이 없습니다")
            ap.update({"status": status, "decided_at": now(), "decided_by": by,
                       "comment": (comment or "").strip(), "agent_ack": False})
            msg = "{}: {}".format(DECISION_LABEL[status], ap["title"])
            if picked is not None:
                ap["selected"] = picked
                if picked:
                    msg += " (선택: {})".format(", ".join(picked))
            self._log(t, msg, kind="decision", author=by,
                      ref=aid, detail=ap["comment"] or None, meta={"decision": status})
            if not any(a["status"] == "pending" for a in t["approvals"]):
                t["status"] = "in_progress" if status == "approved" else "changes_requested"
            return ap

    @staticmethod
    def _commit_review_check(task, repo, changes, skip_review):
        """리뷰받기로 한 작업은 리뷰를 통과한 코드 그대로일 때만 커밋 승인을 요청할 수 있다. 승인 요청에 붙일 리뷰 요약을 돌려준다."""
        loop = task.get("review_loop") or {}
        if not loop.get("reviewer"):
            return None
        if loop.get("kind") == "doc":  # 논문 · 문서 심사는 커밋을 막지 않는다 (초안도 자주 커밋함) — 요약만 붙인다
            last = (loop.get("history") or [{}])[-1]
            return {"reviewer": loop["reviewer"], "kind": "doc", "task": loop.get("task"), "round": last.get("round", 0),
                    "rounds": loop.get("rounds"), "state": loop.get("state") or "planned",
                    "verdict": last.get("verdict"), "open": last.get("open") or [], "skipped": False}
        history = loop.get("history") or []
        last = history[-1] if history else {}
        state = loop.get("state") or "planned"
        note = {"reviewer": loop["reviewer"], "model": loop.get("model"), "effort": loop.get("effort"),
                "task": loop.get("task"), "round": last.get("round", 0), "rounds": loop.get("rounds"),
                "state": state, "open": last.get("open") or [], "skipped": bool(skip_review)}
        if skip_review:
            return note
        hint = " (사용자가 리뷰 없이 커밋하라고 했으면 --skip-review)"
        if state == "approved":
            if gitops.snapshot_tree(repo, changes["paths"]) != last.get("tree"):
                raise DevTrackError("{}차 리뷰를 통과한 뒤 코드가 바뀌었습니다 — devtrack review-request {} 로 바뀐 부분을 "
                                    "다시 리뷰받으세요{}".format(last.get("round"), task["id"], hint))
            return note
        if state == "exhausted":
            return note
        raise DevTrackError("아직 리뷰를 통과하지 않았습니다 ({}) — devtrack review-request {} 로 {} 의 리뷰를 "
                            "받으세요{}".format(REVIEW_STATES.get(state, state), task["id"], loop["reviewer"], hint))

    def apply_approval(self, tid, aid, force=False, dry_run=False, author=None):
        tid = normalize_task_id(tid)
        aid = normalize_item_id(aid, "A")
        t = self.load(tid)
        ap = self._find(t.get("approvals", []), aid, "승인 요청")
        if ap["status"] != "approved":
            raise DevTrackError("승인되지 않은 요청입니다 ({}: {})".format(aid, ap["status"]))
        if ap.get("applied"):
            raise DevTrackError("이미 적용된 요청입니다 (commit {})".format(ap["applied"].get("commit", "")[:10]))
        action = ap.get("action") or {}
        if action.get("type") != "git_commit_push":
            raise DevTrackError("실행할 작업(commit/push)이 연결되지 않은 승인 요청입니다")
        repo = gitops.require_repo(action["repo"])
        changes = gitops.capture_changes(repo, action.get("files"))
        current = gitops.fingerprint(changes["diff"])
        if current != action.get("fingerprint") and not force:
            raise DevTrackError(
                "승인 요청 이후 변경 사항이 달라졌습니다. 새 승인 요청을 만들거나, "
                "달라진 내용을 확인했다면 --force 로 실행하세요."
            )
        if dry_run:
            return {"dry_run": True, "repo": repo, "branch": action.get("branch"), "message": action["message"],
                    "files": action.get("files") or [], "push": action.get("push", True)}
        # 커밋은 diff 에 넣은 파일만 (빌드 결과물은 빼고, 다른 스테이징은 풀어 둔다)
        result = gitops.commit_and_push(repo, action["message"], files=action.get("files"),
                                        branch=action.get("branch"), push=action.get("push", True),
                                        remote=action.get("remote", "origin"), paths=changes["paths"])
        with self.edit(tid) as t:
            loop = t.get("review_loop")
            if loop and loop.get("reviewer") and loop.get("history"):
                # 리뷰받은 코드가 커밋됨 — 이 작업의 다음 변경은 다시 리뷰받고, 횟수도 새로 센다
                loop.update(state="planned", cycle_start=len(loop["history"]))
            ap = self._find(t["approvals"], aid, "승인 요청")
            ap["applied"] = {"at": now(), "commit": result["commit"], "branch": result["branch"],
                             "pushed": result["pushed"], "push_error": result["push_error"], "by": author}
            ap["agent_ack"] = True
            short = result["commit"][:8]
            subject = action["message"].splitlines()[0]
            if result["push_error"]:
                self._log(t, "커밋 완료, 푸시 실패: {} ({})".format(subject, short), kind="error",
                          author=author, ref=aid, detail=result["push_error"])
            else:
                self._log(t, "커밋{} 완료: {} ({})".format(" 및 푸시" if result["pushed"] else "", subject, short),
                          kind="commit", author=author, ref=aid,
                          meta={"commit": result["commit"], "branch": result["branch"],
                                "remote_url": action.get("remote_url")})
        return result

    def ack_decision(self, tid, aid):
        aid = normalize_item_id(aid, "A")
        with self.edit(tid) as t:
            ap = self._find(t.get("approvals", []), aid, "승인 요청")
            ap["agent_ack"] = True
            return ap

    # ------------------------------------------------------------------ 받은 편지함 (에이전트용)
    def _comment_artifact(self, t, e):
        """결과물에 대해 보낸 사용자 메시지면 그 결과물 정보 (받은 편지함 항목에 붙임). 그사이 결과물이 지워졌으면 보낼 때의 제목만."""
        fid = (e.get("meta") or {}).get("artifact")
        if not fid:
            return {}
        a = next((x for x in t.get("artifacts", []) if x.get("id") == fid), None)
        out = {"artifact": fid, "artifact_title": (a or {}).get("title") or e["meta"].get("artifact_title") or "",
               "artifact_version": e["meta"].get("version")}
        if a:
            out.update(artifact_latest=a.get("version", 1), artifact_category=a.get("category") or "result",
                       artifact_filename=a.get("filename"), artifact_file=self._artifact_file(t["id"], a),
                       artifact_url=a.get("url"))
        else:
            out["artifact_missing"] = True
        return out

    def inbox(self, ack=False, agent=None, task=None):
        """에이전트가 처리해야 할 사용자 입력: 새 작업 지시, 사용자 메시지, 승인/수정 결정, 취소/삭제/완료 처리 알림.

        agent: 이 AI 에게 배정된 작업(또는 담당이 정해지지 않은 작업)의 입력만 (None 이면 전부)
        task: 이 작업의 입력만
        ack: 돌려준 항목을 처리됨으로 표시 (다른 AI · 다른 작업의 항목은 건드리지 않음)"""
        task = normalize_task_id(task) if task else None
        items = []
        for t in self.all_tasks():
            if (task and t["id"] != task) or not agent_matches(t.get("agent"), agent):
                continue
            of = t.get("review_of") or {}
            base = {"task_id": t["id"], "title": t["title"], "agent": t.get("agent"),
                    "model": t.get("model"), "effort": t.get("effort"),
                    "review_of": of.get("task"), "review_same_session": bool(of.get("same_session"))}
            c = t.get("cancelled") or {}
            if t["status"] == "cancelled" and c.get("agent_ack") is False:
                items.append(dict(base, type="cancelled", reason=c.get("reason", ""), ts=c.get("at")))
            d = t.get("completed") or {}
            if t["status"] == "done" and d.get("agent_ack") is False:
                items.append(dict(base, type="completed", reason=d.get("note", ""), ts=d.get("at")))
            if t["status"] == "queued":
                items.append(dict(base, type="new_task", instruction=t.get("instruction", ""), task_type=t["type"],
                                  repo=t.get("repo") or {}, ts=t["created_at"], related=t.get("related") or [],
                                  review=_loop_summary(t.get("review_loop")),
                                  attachments=self._sent_list(t, t.get("sent_files"))))
            for e in t.get("log", []):
                if e.get("author") == USER and e.get("kind") == "user" and e.get("ack") is False:
                    items.append(dict(base, type="comment", id=e["id"], message=e["message"], ts=e["ts"],
                                      attachments=self._sent_list(t, (e.get("meta") or {}).get("attachments")),
                                      **self._comment_artifact(t, e)))
                elif e.get("kind") == "agent" and e.get("ack") is False:
                    items.append(dict(base, type="agent_message", id=e["id"], message=e["message"],
                                      sender=e.get("author"), ts=e["ts"]))
            for ap in t.get("approvals", []):
                if ap["status"] != "pending" and ap.get("agent_ack") is False:
                    items.append(dict(base, type="decision", id=ap["id"], approval_title=ap["title"],
                                      decision=ap["status"], comment=ap.get("comment", ""),
                                      has_action=bool(ap.get("action")), selected=ap.get("selected"),
                                      selected_labels=_choice_labels(ap), ts=ap.get("decided_at")))
        for d in self.deleted_tasks():
            if d.get("ack") is False and (not task or d.get("id") == task) and agent_matches(d.get("agent"), agent):
                items.append({"type": "deleted", "task_id": d["id"], "title": d.get("title", ""),
                              "agent": d.get("agent"), "ts": d.get("at")})
        if ack:
            self.ack_inbox(items)
        items.sort(key=lambda x: x.get("ts") or "")
        return items

    def ack_inbox(self, items):
        """주어진 받은 편지함 항목만 처리됨으로 표시한다 (새 작업은 시작하면 저절로 빠지므로 표시할 것 없음)."""
        by_task, deleted = {}, set()
        for it in items:
            if it["type"] == "deleted":
                deleted.add(it["task_id"])
            elif it["type"] != "new_task":
                by_task.setdefault(it["task_id"], []).append(it)
        for tid, its in by_task.items():
            if not os.path.exists(os.path.join(self.tasks_dir, tid, "task.json")):
                continue  # 그사이 삭제됨
            with self.edit(tid) as t:
                for it in its:
                    if it["type"] in ("comment", "agent_message"):
                        e = next((x for x in t.get("log", []) if x.get("id") == it["id"]), None)
                        if e is not None:
                            e["ack"] = True
                    elif it["type"] == "cancelled":
                        if t.get("cancelled"):
                            t["cancelled"]["agent_ack"] = True
                    elif it["type"] == "completed":
                        if t.get("completed"):
                            t["completed"]["agent_ack"] = True
                    elif it["type"] == "decision":
                        ap = next((x for x in t.get("approvals", []) if x.get("id") == it["id"]), None)
                        if ap is not None:
                            ap["agent_ack"] = True
        if deleted:
            with self.locked():
                rest = self.deleted_tasks()
                for d in rest:
                    if d.get("id") in deleted:
                        d["ack"] = True
                self._write_json(self.deleted_path, {"deleted": rest})

    # ------------------------------------------------------------------ 받은 편지함 대기 (inbox --wait)
    @staticmethod
    def inbox_key(it):
        if it["type"] == "new_task":
            return "task:" + it["task_id"]
        if it["type"] == "comment":
            return "comment:{}:{}".format(it["task_id"], it["id"])
        if it["type"] == "agent_message":
            return "agent:{}:{}".format(it["task_id"], it["id"])
        if it["type"] in ("cancelled", "deleted", "completed"):
            return "{}:{}:{}".format(it["type"], it["task_id"], it.get("ts") or "")
        return "decision:{}:{}:{}".format(it["task_id"], it["id"], it.get("ts") or "")

    def _seen_path(self, agent):
        # 점(.)으로 시작하는 디렉토리는 publish / 웹 서버에서 제외된다
        return os.path.join(self.root, ".agent", "inbox-seen-{}.json".format(_safe_name(agent or "agent")))

    def inbox_seen(self, agent):
        try:
            with open(self._seen_path(agent), encoding="utf-8") as fh:
                return set(json.load(fh).get("seen", []))
        except (OSError, ValueError):
            return set()

    def mark_inbox_seen(self, agent, items, merge=False):
        """에이전트가 본 항목을 기록한다. 현재 받은 편지함에 남아 있는 항목만 유지.
        merge: 일부(예: 한 작업)만 본 경우 — 기존 기록에 더한다."""
        keys = {self.inbox_key(it) for it in items}
        if merge:
            keys |= self.inbox_seen(agent)
        keys = sorted(keys)
        self._write_json(self._seen_path(agent), {"agent": agent, "updated_at": now(), "seen": keys})

    # ------------------------------------------------------------------ AI 세션 ↔ 작업 (devtrack hook)
    # 대화형 AI(Claude Code · Codex)는 단계 사이에만 inbox 를 보므로, 작업 도중의 취소 · 삭제를 모른다.
    # 기록 명령을 실행한 세션을 작업별로 적어 두고, 도구 실행 전 훅이 그 세션의 작업만 확인한다.
    def _session_path(self, session):
        return os.path.join(self.root, ".agent", "sessions", _safe_name(session) + ".json")

    def session_tasks(self, session):
        try:
            with open(self._session_path(session), encoding="utf-8") as fh:
                return [t for t in json.load(fh).get("tasks", []) if isinstance(t, str)]
        except (OSError, ValueError, AttributeError):
            return []

    def note_session_task(self, session, tid, keep_days=30):
        """AI 세션이 이 작업을 하고 있다고 적어 둔다. 오래된 세션 기록은 이때 정리한다."""
        tid = normalize_task_id(tid)
        tasks = self.session_tasks(session)
        if tid in tasks:
            return
        path = self._session_path(session)
        self._write_json(path, {"session": session, "updated_at": now(), "tasks": tasks + [tid]})
        cutoff = time.time() - keep_days * 86400
        d = os.path.dirname(path)
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                if name.endswith(".json") and os.path.getmtime(p) < cutoff:
                    os.unlink(p)
            except OSError:
                pass

    def stop_notice(self, tid):
        """사용자가 취소 · 삭제 · 완료 처리했는데 AI 가 아직 확인(inbox --ack)하지 않은 작업이면 그 내용, 아니면 None.
        이미 끝난(완료) 작업을 지운 경우는 멈출 일이 없으므로 제외한다."""
        tid = normalize_task_id(tid)
        path = os.path.join(self.tasks_dir, tid, "task.json")
        if os.path.exists(path):
            t = self.load(tid)
            c = t.get("cancelled") or {}
            if t.get("status") == "cancelled" and c.get("agent_ack") is False and c.get("was") != "done":
                return {"type": "cancelled", "task_id": tid, "title": t.get("title", ""),
                        "reason": c.get("reason") or "", "at": c.get("at")}
            d = t.get("completed") or {}
            if t.get("status") == "done" and d.get("agent_ack") is False:
                return {"type": "completed", "task_id": tid, "title": t.get("title", ""),
                        "reason": d.get("note") or "", "at": d.get("at")}
            return None
        for d in self.deleted_tasks():
            if d.get("id") == tid and d.get("ack") is False and d.get("status") != "done":
                return {"type": "deleted", "task_id": tid, "title": d.get("title", ""), "reason": "",
                        "at": d.get("at")}
        return None

    # ------------------------------------------------------------------ devtrack watch 가 작업별로 이어 가는 AI 세션
    # 같은 작업의 다음 입력(메시지 · 결정)을 같은 대화로 이어 가도록 실행기가 세션 ID 를 적어 둔다 (점 디렉토리 — 배포 안 됨)
    def _runner_path(self, agent):
        return os.path.join(self.root, ".agent", "watch-sessions-{}.json".format(_safe_name(agent or "agent")))

    def _runner_sessions(self, agent):
        try:
            with open(self._runner_path(agent), encoding="utf-8") as fh:
                data = json.load(fh).get("tasks", {})
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def runner_session(self, agent, tid):
        info = self._runner_sessions(agent).get(normalize_task_id(tid))
        return info if isinstance(info, dict) and info.get("id") else None

    def set_runner_session(self, agent, tid, info):
        """info 가 None 이면 지운다. 지운 작업(.deleted)의 기록은 이때 함께 정리한다."""
        tid = normalize_task_id(tid)
        with self.locked():
            data = self._runner_sessions(agent)
            if info:
                data[tid] = dict(info, updated_at=now())
            else:
                data.pop(tid, None)
            data = {k: v for k, v in data.items() if os.path.exists(os.path.join(self.tasks_dir, k, "task.json"))}
            self._write_json(self._runner_path(agent), {"agent": agent, "tasks": data})

    # ------------------------------------------------------------------ 웹 입력을 기다리는 AI (inbox --wait · watch)
    # 기다리는 프로세스가 .agent/listeners/ 에 기록을 남기고 주기적으로 갱신한다. 웹(로컬 서버)은 이것으로
    # "지금 이 AI 가 웹 지시를 기다리는지"를 보여 준다. 점(.) 디렉토리라 배포 · 웹 서버에는 나가지 않는다.
    LISTENER_STALE = 120  # 초: 다른 컴퓨터의 기록은 이 시간 안에 갱신되지 않으면 끝난 것으로 본다

    def _listener_path(self, kind, agent, pid):
        return os.path.join(self.root, ".agent", "listeners",
                            "{}-{}-{}.json".format(kind, _safe_name(agent or "all"), pid))

    def touch_listener(self, kind, agent, task=None, started_at=None, state="waiting", running=None, seen=None,
                       model=None, effort=None):
        """이 프로세스가 웹 입력을 기다린다고 적어 둔다 (kind: wait | watch, agent: 빈 값이면 모든 AI).
        state 는 waiting, 또는 watch 가 AI 를 실행하는 동안 running (running = 그 작업 ID).
        seen 은 이 프로세스가 '이미 본 입력' 기록 이름 — 본 입력으로는 다시 깨어나지 않으므로 웹이 구분한다.
        model · effort: 대기 중인 대화의 모델 · 강도 (모르면 None). 실행기가 작업마다 지정 모델로 띄우면 model 이 '*'."""
        data = {"kind": kind, "agent": agent or "", "pid": os.getpid(), "host": socket.gethostname(),
                "task": task or None, "state": state, "running": running, "seen": seen,
                "model": model or None, "effort": effort or None,
                "started_at": started_at or now(), "updated_at": now()}
        self._write_json(self._listener_path(kind, agent, os.getpid()), data)
        return data

    def drop_listener(self, kind, agent):
        try:
            os.unlink(self._listener_path(kind, agent, os.getpid()))
        except OSError:
            pass

    def listeners(self):
        """지금 웹 입력을 기다리는 AI 목록. 이 컴퓨터의 기록은 프로세스가 살아 있는지로, 다른 컴퓨터의 기록은
        마지막 갱신 시각으로 판단하고, 끝난 기록은 지운다."""
        d = os.path.join(self.root, ".agent", "listeners")
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return []
        host, out = socket.gethostname(), []
        for name in names:
            if not name.endswith(".json") or name.startswith("."):
                continue
            path = os.path.join(d, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                age = time.time() - os.path.getmtime(path)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("host") == host and data.get("pid"):
                alive = pid_alive(data["pid"]) and age < 86400
            else:
                alive = age <= self.LISTENER_STALE
            if not alive:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            item = {k: data.get(k) for k in ("kind", "agent", "task", "state", "running", "model", "effort",
                                              "started_at", "updated_at")}
            # 이미 본 새 작업 지시: 이 프로세스는 이 지시로는 다시 깨어나지 않는다 (예: 실행했지만 시작하지 못한 작업)
            seen = self.inbox_seen(data["seen"]) if data.get("seen") else set()
            item["seen_tasks"] = sorted(k.split(":", 1)[1] for k in seen if k.startswith("task:"))
            out.append(item)
        return out

    def change_token(self):
        """index.json 은 모든 저장 때 원자적으로 교체되므로 (inode, mtime, size) 로 변경을 감지한다."""
        try:
            st = os.stat(self.index_path)
            return (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    # ------------------------------------------------------------------ 인덱스 / 배포
    def rebuild_index(self):
        tasks = self.all_tasks()
        summaries, notifications, activity, reviews, artifacts, approvals = [], [], [], [], [], []
        for t in tasks:
            for a in t.get("approvals", []):
                approvals.append({
                    "task_id": t["id"], "task_title": t["title"], "id": a["id"], "title": a["title"],
                    "kind": a.get("kind", "general"), "status": a["status"], "created_at": a["created_at"],
                    "decided_at": a.get("decided_at"), "comment": a.get("comment", ""),
                    "commit": (a.get("applied") or {}).get("commit"), "selected": a.get("selected"),
                })
            open_issues = [i for i in t.get("issues", []) if i.get("status") == "open"]
            pending = [a for a in t.get("approvals", []) if a.get("status") == "pending"]
            plan = t.get("plan") or []
            log = t.get("log") or []
            summaries.append({
                "id": t["id"], "title": t["title"], "type": t["type"], "status": t["status"],
                "priority": t.get("priority", "normal"), "agent": t.get("agent"),
                "progress": t.get("progress", 0), "current_step": t.get("current_step", ""),
                "repo": t.get("repo") or {}, "tags": t.get("tags") or [], "related": t.get("related") or [],
                "model": t.get("model"), "effort": t.get("effort"),
                "review_loop": _loop_summary(t.get("review_loop")), "review_of": (t.get("review_of") or {}).get("task"),
                "created_at": t["created_at"], "updated_at": t["updated_at"],
                "completed_at": t.get("completed_at"),
                "plan_total": len(plan), "plan_done": sum(1 for p in plan if p.get("done")),
                "open_issues": len(open_issues),
                "max_severity": max((SEVERITIES.index(i["severity"]) for i in open_issues), default=-1),
                "pending_approvals": len(pending), "artifact_count": len(t.get("artifacts", [])),
                "last_log": log[-1] if log else None,
            })
            if t.get("review"):
                summaries[-1]["review"] = _review_summary(t["review"])
            for a in pending:
                notifications.append({
                    "task_id": t["id"], "task_title": t["title"], "approval_id": a["id"],
                    "title": a["title"], "kind": a.get("kind", "general"), "created_at": a["created_at"],
                    "author": a.get("author"),
                })
            for e in log:
                activity.append(dict(e, task_id=t["id"], task_title=t["title"]))
            for a in t.get("artifacts", []):
                item = {"task_id": t["id"], "task_title": t["title"], "task_type": t["type"]}
                item.update({k: a.get(k) for k in ("id", "title", "kind", "category", "path", "url",
                                                   "created_at", "author", "size", "description", "lang", "build",
                                                   "version", "updated_at", "preview", "filename", "sent")})
                artifacts.append(item)
                if a.get("category") == "review":
                    reviews.append(item)
        activity.sort(key=lambda e: _ts_key(e["ts"]), reverse=True)
        events = [e for e in activity if _is_event(e)][:60]
        notifications.sort(key=lambda n: _ts_key(n["created_at"]), reverse=True)
        reviews.sort(key=lambda r: _ts_key(r["created_at"]), reverse=True)
        artifacts.sort(key=lambda r: _ts_key(r["created_at"]), reverse=True)
        summaries.sort(key=lambda s: _ts_key(s["updated_at"]), reverse=True)
        approvals.sort(key=lambda a: _ts_key(a.get("decided_at") or a["created_at"]), reverse=True)
        counts = {k: 0 for k in STATUSES}
        for s in summaries:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        index = {
            "generated_at": now(),
            "version": 1,
            "counts": counts,
            "tasks": summaries,
            "notifications": notifications,
            "approvals": approvals,
            "events": events,
            "activity": activity[:300],
            "reviews": reviews,
            "artifacts": artifacts,
            "models": models.catalog(),  # 웹의 모델 선택 목록 (Codex 는 이 컴퓨터의 Codex 가 받아 둔 목록)
            "mailbox": self.mailbox_info(),  # 정적 웹의 'GitHub 이슈로 보내기' (devtrack mailbox)
        }
        os.makedirs(self.root, exist_ok=True)
        self._write_json(self.index_path, index)
        return index

    def publish_is_root(self, dest=None):
        """공개 폴더의 data/ 가 기록 폴더 자체인지 — 그러면 웹이 기록을 바로 읽으므로 복사하지 않는다."""
        dest = dest or self.publish_dir
        return bool(dest) and same_dir(os.path.join(dest, "data"), self.root)

    def _sync_publish(self, tid=None):
        if not self.publish_dir or self.publish_is_root():
            return
        try:
            ddata = os.path.join(self.publish_dir, "data")
            if tid:
                mirror_tree(os.path.join(self.tasks_dir, tid), os.path.join(ddata, "tasks", tid))
                _mirror_paper(os.path.join(self.tasks_dir, tid), os.path.join(ddata, "tasks", tid))
            os.makedirs(ddata, exist_ok=True)
            shutil.copy2(self.index_path, os.path.join(ddata, "index.json"))
            _chmod(os.path.join(ddata, "index.json"), 0o644)
        except OSError as e:
            sys.stderr.write("[devtrack] 배포 디렉토리 동기화 실패 ({}): {}\n".format(self.publish_dir, e))

    def publish(self, web_root, dest):
        dest = os.path.abspath(os.path.expanduser(dest))
        self.ensure()
        auth = self.auth()  # 망가진 비밀번호 설정이면 공개하기 전에 멈춘다
        os.makedirs(dest, exist_ok=True)
        if auth:  # 기록을 복사하기 전에 막는다
            access.write_site(dest, auth)
        mirror_tree(web_root, dest)
        write_versioned_index(web_root, dest)
        if not self.publish_is_root(dest):  # 기록 폴더가 곧 dest/data 이면 이미 그 자리에 있다
            mirror_tree(self.root, os.path.join(dest, "data"))
            for tid in self.task_ids():
                _mirror_paper(self.task_dir(tid), os.path.join(dest, "data", "tasks", tid))
        self.sync_access(dest)
        return dest

    # ------------------------------------------------------------------ 웹 접근 비밀번호 (access.py)
    def mailbox_info(self):
        """웹이 알아야 할 우편함 설정 — 저장소 이름만 (토큰은 기록 폴더에 두지 않는다)."""
        cfg = mailbox.load_config(self.root)
        if not cfg:
            return None
        return {"github": cfg["github"], "files": cfg.get("files"),
                "branch": cfg.get("branch") or mailbox.load_state(self.root).get("branch") or "main"}

    def auth(self):
        """비밀번호 설정 (없으면 None, 망가졌으면 DevTrackError)."""
        return access.load(self.root)

    def set_password(self, password):
        a = access.new_auth(password)
        a["set_at"] = now()
        with self.locked():  # 웹 서버가 읽을 일이 없으므로 나만 읽게
            self._write_json(os.path.join(self.root, access.AUTH_FILE), a, mode=0o600)
        return a

    def clear_password(self):
        path = os.path.join(self.root, access.AUTH_FILE)
        with self.locked():
            if not os.path.exists(path):
                return False
            os.remove(path)
        return True

    def sync_access(self, dest=None):
        """비밀번호 설정을 웹 쪽 파일에 맞춘다 — 기록 폴더의 .htaccess (기록 폴더가 웹에서 바로 열리는 곳이어도 막히게)와
        공개 폴더(dest 또는 publish_dir)의 auth.json · data/.htaccess. 손댄 파일 목록."""
        auth = self.auth()
        changed = [access.write_htaccess(self.root, auth)]
        target = dest or self.publish_dir
        if target:
            changed += access.write_site(target, auth)
        return sorted(set(p for p in changed if p))


def _norm_review_plan(plan, task_type=None):
    """리뷰 계획 정리. kind: 'doc' (논문 작업이거나 원고 파일을 준 경우 — 저널 심사) · 'code' (코드 diff 리뷰)."""
    reviewer = " ".join(str(plan.get("reviewer") or "").split())
    if not reviewer or is_unassigned(reviewer):
        raise DevTrackError("리뷰어 AI 이름을 정하세요 (예: Codex) — '누구나' 는 리뷰어가 될 수 없습니다")
    try:
        rounds = int(plan.get("rounds") or REVIEW_ROUNDS_DEFAULT)
    except (TypeError, ValueError):
        raise DevTrackError("리뷰 횟수는 숫자여야 합니다: {}".format(plan.get("rounds")))
    if not 1 <= rounds <= REVIEW_ROUNDS_MAX:
        raise DevTrackError("리뷰 횟수는 1 – {}번이어야 합니다".format(REVIEW_ROUNDS_MAX))
    model, effort = models.normalize_model(plan.get("model")), models.normalize_effort(plan.get("effort"))
    models.check_effort(reviewer, model, effort)
    doc = str(plan.get("doc") or "").strip() or None
    venue = " ".join(str(plan.get("venue") or "").split()) or None
    kind = "doc" if doc or task_type in REVIEW_DOC_TYPES or plan.get("kind") == "doc" else "code"
    return {"reviewer": reviewer, "rounds": rounds, "model": model, "effort": effort, "kind": kind, "doc": doc,
            "venue": venue}


def _doc_path(t, doc):
    """원고 경로: 절대 경로 · ~ 는 그대로, 상대 경로는 작업의 저장소(없으면 지금 폴더) 기준."""
    p = os.path.expanduser(doc)
    if not os.path.isabs(p):
        p = os.path.join((t.get("repo") or {}).get("path") or os.getcwd(), p)
    return os.path.abspath(p)


DOC_LOOP_STATES = {"planned": "심사 전", "reviewing": "심사 중", "changes": "저자가 고치는 중", "approved": "게재 가능 — 심사 통과",
                   "exhausted": "심사 횟수를 다 씀", "rejected": "게재 불가"}
# 답변서의 항목 시작: '## R1 — 제목' 같은 제목 줄, 또는 '**R1 (…).** 답…' · '- **R1** …' 처럼 굵은 글씨로 시작하는 줄
_RESPONSE_HEAD = re.compile(r"^\s{0,3}(?:(#{1,6})\s*(?:\*\*|__)?|(?:[-*+]\s+)?(\*\*|__))\s*\[?R\s*-?\s*0*(\d+)(?!\d)")


def response_sections(text):
    """답변서(response to reviewers) → (총평, {"R1": 답, …}). 항목은 항목 ID 로 시작하는 제목 · 굵은 글씨 줄부터
    다음 항목 · 같은 단계 이상의 다른 제목까지. 제목 줄 자체는 빼고, 굵은 글씨 줄은 닫는 ** 뒤의 글부터 답으로 친다."""
    general, secs, cur, level = [], {}, None, 7
    for line in (text or "").splitlines():
        m = _RESPONSE_HEAD.match(line)
        if m:
            cur = "R{}".format(int(m.group(3)))
            level = len(m.group(1)) if m.group(1) else 7
            body = secs.setdefault(cur, [])
            if m.group(2):
                rest = line[m.end():]
                close = rest.find(m.group(2))
                rest = (rest[close + 2:] if close >= 0 else rest).lstrip(" .:)]—–-")
                if rest.strip():
                    body.append(rest)
            continue
        h = re.match(r"^\s{0,3}(#{1,6})\s", line)
        if cur and h and len(h.group(1)) <= level:  # 같은 단계 이상의 다른 제목 → 그 항목 끝
            cur = None
        (secs[cur] if cur else general).append(line)
    while general and (not general[0].strip() or re.match(r"^\s{0,3}#\s", general[0])):  # 맨 앞의 문서 제목은 총평에서 뺀다
        general.pop(0)
    return "\n".join(general).strip(), dict((k, "\n".join(v).strip()) for k, v in secs.items())


def _read_response(path):
    """답변서 글: Markdown · 텍스트는 그대로, Word 는 Markdown 으로, PDF 는 pdftotext 로."""
    p = os.path.expanduser(path)
    ext = os.path.splitext(p)[1].lower()
    if ext == ".docx":
        from .docx import docx_to_markdown
        return docx_to_markdown(p)
    if ext == ".pdf":
        text = manuscript.pdf_text(p)
        if text is None:
            raise DevTrackError("PDF 답변서의 글자를 읽지 못했습니다 (pdftotext 없음) — Markdown · 텍스트 · Word 로 주세요: {}".format(path))
        return text
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _doc_verdict(value):
    """심사 판정 → DOC_VERDICTS (accept · minor · major · reject, approve = 게재 가능, changes = 대폭 수정)."""
    s = str(value or "").strip().lower()
    if s in DOC_VERDICTS:
        return s
    s2 = s.replace("_", " ")
    return _DOC_VERDICT_ALIAS.get(s) or _DOC_VERDICT_ALIAS.get(s2) or {
        "approve": "ready", "approved": "ready", "pass": "ready", "게재 가능": "ready", "changes": "major_revision",
        "request_changes": "major_revision", "minor_revision": "minor_revision", "major_revision": "major_revision",
        "게재 불가": "rework", "rejected": "rework"}.get(s)


def _review_verdict(value):
    s = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if s in _REVIEW_PASS:
        return "approve"
    if s.replace("_", "") in [x.replace("_", "") for x in _REVIEW_CHANGES]:
        return "request_changes"
    return None


def _review_instruction(t, plan, n, note="", rtid=None):
    dev = t.get("agent") or "AI"
    spec = models.spec_label(t.get("model"), t.get("effort"))
    lines = [
        "{} 「{}」 의 코드 변경을 리뷰해 주세요. 개발: {}{} · 리뷰는 최대 {}번 (이번이 {}차).".format(
            t["id"], t["title"], dev, " (" + spec + ")" if spec else "", plan["rounds"], n),
        "",
        "- 리뷰할 변경: 이 작업의 결과물 \"{}차 리뷰할 변경\" (diff — 빌드 결과물은 빠져 있음). 저장소 폴더에서 코드를 직접 봐도 됩니다.".format(n),
        "- 개발 작업의 지시와 기록: devtrack show {}".format(t["id"]),
        "- 파일은 고치지 말고 리뷰만 합니다. 커밋 · 푸시도 하지 않습니다.",
        "- 검토 항목은 devtrack review {} --findings <JSON> 으로 R1, R2… 를 남깁니다 ".format(rtid or "<이 작업>") +
        "(severity: blocker · major · minor · nit · question, file, line, title, plain = 비개발자도 알 수 있는 설명, suggestion = 고칠 방법).",
        "  다음 차례에는 같은 ID 의 status 를 fixed · open 으로 갱신하고, 새로 찾은 문제는 새 ID 로 더합니다.",
        "- 끝나면 devtrack review-done {} --verdict approve (통과) 또는 changes (수정 필요) -m \"한두 줄 요약\" 으로 "
        "결과를 보냅니다. 결과는 {} 에게 가고, 고친 뒤의 다음 차례 요청은 이 작업으로 옵니다.".format(rtid or "<이 작업>", dev),
    ]
    if note and note.strip():
        lines += ["", "개발 AI 의 메모: " + note.strip()]
    return "\n".join(lines)


def _loop_summary(loop):
    if not loop or not loop.get("reviewer"):
        return None
    last = (loop.get("history") or [{}])[-1]
    return {"reviewer": loop["reviewer"], "model": loop.get("model"), "effort": loop.get("effort"),
            "rounds": loop.get("rounds"), "state": loop.get("state"), "round": last.get("round", 0),
            "task": loop.get("task"), "open": len(last.get("open") or []), "kind": loop.get("kind") or "code",
            "venue": loop.get("venue"), "doc": loop.get("doc"), "verdict": last.get("verdict")}


def _figure_change_label(count, chg):
    """'그림 23개 (바뀜 2: a.png, b.png · 새 1 · 빠짐 1)' — 심사 요청 메시지용."""
    if not chg or not any(chg.values()):
        return "그림 {}개{}".format(count, " (바뀐 그림 없음)" if chg is not None else "")
    parts = []
    for key, label in (("changed", "바뀜"), ("new", "새 그림"), ("removed", "빠짐")):
        names = chg.get(key) or []
        if names:
            shown = ", ".join(os.path.basename(x) for x in names[:5]) + (" 외 {}개".format(len(names) - 5) if len(names) > 5 else "")
            parts.append("{} {}: {}".format(label, len(names), shown))
    return "그림 {}개 ({})".format(count, " · ".join(parts))


def _doc_review_instruction(t, plan, n, note, path, rtid=None, figures=0, art_dir=None):
    dev = t.get("agent") or "AI"
    spec = models.spec_label(t.get("model"), t.get("effort"))
    rt = rtid or "<이 작업>"
    venue = plan.get("venue") or "투고할 저널"
    lines = [
        "{} 「{}」 의 원고를 {} 의 심사위원(reviewer)으로서 심사해 주세요. 저자: {}{} · 심사는 최대 {}번 (이번이 {}차).".format(
            t["id"], t["title"], venue, dev, " (" + spec + ")" if spec else "", plan["rounds"], n),
        "",
        "- 원고: 이 작업의 결과물 \"{}차 원고\" (PDF · Word 가 있으면 그것을, 원문은 \"{}차 원고 원문\"). 원본 파일: {}".format(n, n, path),
    ]
    if figures:
        lines.append("- 그림: 원고가 \\includegraphics 로 부르는 그림 {}개를 이 작업의 결과물 \"그림: …\" 으로 붙였습니다{}. "
                     "그림 파일을 직접 열어 보고 내용 · 축 · 단위 · 범례가 맞는지, 캡션 · 본문의 설명과 맞는지도 봅니다 "
                     "(바뀐 그림은 같은 결과물의 새 버전).".format(figures, " (파일: {}/F-번호_이름)".format(art_dir)
                                                                if art_dir else ""))
    lines += [
        "- 저자의 작업 지시와 기록: devtrack show {}".format(t["id"]),
        "- 실제 저널 심사처럼 봅니다: 연구의 새로움과 선행 연구 대비 기여, 방법의 타당성 · 재현성, 결과가 결론을 뒷받침하는지, "
        "그림 · 표, 구성 · 문장, 투고처 규정. 근거 없는 칭찬이나 막연한 지적 대신 위치와 고칠 방법을 적습니다.",
        "- 원고는 고치지 않고 심사만 합니다.",
        "- 심사 항목은 devtrack review {} --findings <JSON> 으로 R1, R2… 를 남깁니다 (severity: blocker = 반드시 고칠 주요 지적 · major · "
        "minor · nit · question, location = 절 · 쪽 · 그림 · 표, quote = 원고 원문 그대로, title, plain = 비전문가도 알 수 있는 설명, "
        "suggestion = 고칠 방법 — 원고의 언어로).".format(rt),
        "- 심사평(referee report)을 원고의 언어로 써서 함께 올립니다: 편집자에게 보내는 요약 · 주요 지적 · 세부 지적 "
        "(devtrack review {} report.md --pdf --findings f.json — PDF 를 만들지 못하면 --pdf 없이).".format(rt),
        "- 다음 차례에는 저자의 답변서(response to reviewers)와 \"지난 심사 이후 바뀐 부분\" 을 보고, 같은 ID 의 status 를 "
        "fixed · open 으로 갱신하고 새로 찾은 문제는 새 ID 로 더합니다.",
        "- 끝나면 devtrack review-done {} --verdict accept (게재 가능) · minor (소폭 수정) · major (대폭 수정) · reject (게재 불가) "
        "-m \"한두 줄 요약\" 으로 판정을 보냅니다. 판정은 {} 에게 가고, 고친 원고와 답변서로 다음 심사 요청이 이 작업으로 옵니다.".format(rt, dev),
    ]
    if note and note.strip():
        lines += ["", "저자의 메모: " + note.strip()]
    return "\n".join(lines)


def _choice_labels(ap):
    """고른 항목의 이름 (ID 와 다른 것만) — inbox 에 'I1 — 격자 민감도 실험' 처럼 보여 준다."""
    names = {c["id"]: c.get("label") for c in ap.get("choices") or []}
    return [[i, names[i]] for i in ap.get("selected") or [] if names.get(i) and names[i] != i]


def _review_summary(rv):
    findings = rv.get("findings", [])
    open_ = [f for f in findings if f.get("status") == "open"]
    pr = rv.get("pr") or {}
    doc = rv.get("doc") or {}
    out = {
        "total": len(findings), "open": len(open_),
        "by_severity": {s: sum(1 for f in open_ if f.get("severity") == s) for s in FINDING_SEVERITIES},
        "verdict": rv.get("verdict"), "pr": {k: pr.get(k) for k in ("url", "number", "repo") if pr.get(k)},
    }
    if doc:
        out["doc"] = {k: doc.get(k) for k in ("title", "kind") if doc.get(k)}
    return out


def _ts_key(ts):
    try:
        return _dt.datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _is_event(e):
    """알림 패널에 보여줄 '사용자가 알아야 할' 이벤트 (사용자 자신의 행동은 제외)."""
    if e.get("author") == USER:
        return False
    k = e.get("kind")
    if k in ("approval", "issue", "error", "commit"):
        return True
    if k == "status" and (e.get("meta") or {}).get("to") in ("done", "blocked"):
        return True
    if k == "artifact" and (e.get("meta") or {}).get("category") == "review":
        return True
    return False
