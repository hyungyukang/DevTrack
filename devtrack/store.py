"""DevTrack 저장소 — 작업(task) 단위 JSON 파일.

디렉토리 구조::

    <data>/index.json                    대시보드용 요약 (저장할 때마다 재생성)
    <data>/tasks/T-0001/task.json        작업 전체 기록 (지시, 계획, 로그, 문제, 결과물, 승인)
    <data>/tasks/T-0001/artifacts/...    결과물 파일

웹 화면은 이 파일들을 정적으로 읽기만 하므로 서버 없는 정적 호스팅
(예: Frontier 의 www 디렉토리)에서도 그대로 동작한다.
"""

import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import sys
import tempfile

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

from . import DevTrackError, gitops

# 데모 데이터 생성 시 시간을 조작하기 위한 훅 (callable -> datetime)
_clock = None


def now():
    t = _clock() if _clock else _dt.datetime.now()
    return t.astimezone().isoformat(timespec="seconds")


TASK_TYPES = {
    "code": "코드 개발",
    "review": "코드 리뷰",
    "paper": "논문 작성",
    "analysis": "데이터 분석",
    "visualization": "시각화",
    "model": "AI 모델 개발",
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
)
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

_KIND_EXT = {
    "markdown": ("md", "markdown"),
    "pdf": ("pdf",),
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


def _chmod(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


class Store:
    def __init__(self, root, publish_dir=None):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.tasks_dir = os.path.join(self.root, "tasks")
        self.index_path = os.path.join(self.root, "index.json")
        self.publish_dir = os.path.abspath(os.path.expanduser(publish_dir)) if publish_dir else None

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

    def _write_json(self, path, obj):
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        _chmod(path, 0o644)

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
                    plan=None, related=None):
        if type not in TASK_TYPES:
            raise DevTrackError("알 수 없는 작업 유형: {} ({})".format(type, ", ".join(TASK_TYPES)))
        if status not in STATUSES:
            raise DevTrackError("알 수 없는 상태: {}".format(status))
        if priority not in PRIORITIES:
            raise DevTrackError("우선순위는 {} 중 하나여야 합니다".format(", ".join(PRIORITIES)))
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
            ids = self.task_ids()
            tid = "T-{:04d}".format((int(ids[-1][2:]) if ids else 0) + 1)
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
                "created_by": created_by or agent or "Claude",
                "created_at": ts,
                "updated_at": ts,
                "log": [],
                "issues": [],
                "artifacts": [],
                "approvals": [],
                "seq": {},
            }
            os.makedirs(os.path.join(self.tasks_dir, tid), exist_ok=True)
            if created_by == USER:
                self._log(task, "사용자가 새 작업을 지시했습니다", kind="user", author=USER)
                task["log"][-1]["ack"] = True  # 지시 자체는 queued 상태로 전달됨
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
            return rel

    def update_task(self, tid, status=None, progress=None, step=None, message=None, title=None,
                    author=None, kind="info"):
        with self.edit(tid) as t:
            if title:
                t["title"] = title
            if status:
                if status not in STATUSES:
                    raise DevTrackError("알 수 없는 상태: {} ({})".format(status, ", ".join(STATUSES)))
                old = t["status"]
                if old != status:
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

    def add_log(self, tid, message, kind="info", detail=None, author=None):
        with self.edit(tid) as t:
            return self._log(t, message, kind=kind, author=author, detail=detail)

    def add_user_comment(self, tid, message):
        if not message or not message.strip():
            raise DevTrackError("메시지가 비어 있습니다")
        with self.edit(tid) as t:
            return self._log(t, message.strip(), kind="user", author=USER)

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
        if log:
            label = "리뷰 리포트 등록" if category == "review" else "결과물 추가"
            self._log(t, "{}: {}".format(label, art["title"]), kind="artifact", author=author, ref=fid,
                      meta={"category": category, "kind": art["kind"]})
        return art

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

    def add_artifact(self, tid, **kw):
        with self.edit(tid) as t:
            return self._add_artifact(t, **kw)

    # ------------------------------------------------------------------ 승인
    def request_approval(self, tid, title, description="", kind=None, artifact_ids=None, commit=None,
                         author=None):
        tid = normalize_task_id(tid)
        action = None
        diff_text = None
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
            diff_text = gitops.capture_diff(repo, files)
            if not diff_text.strip():
                raise DevTrackError("커밋할 변경 사항이 없습니다: {}".format(repo))
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
            }
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
            t.setdefault("approvals", []).append(approval)
            if t["status"] != "awaiting_approval":
                t["status_before_approval"] = t["status"]
            t["status"] = "awaiting_approval"
            self._log(t, "승인 요청: " + title, kind="approval", author=author, ref=aid, meta={"kind": kind})
            return approval

    def decide(self, tid, aid, decision, comment="", by=USER):
        status = DECISIONS.get(str(decision).lower())
        if not status:
            raise DevTrackError("결정은 approve / changes / reject 중 하나여야 합니다")
        aid = normalize_item_id(aid, "A")
        with self.edit(tid) as t:
            ap = self._find(t.get("approvals", []), aid, "승인 요청")
            if ap["status"] != "pending":
                raise DevTrackError("이미 처리된 승인 요청입니다 ({}: {})".format(aid, ap["status"]))
            ap.update({"status": status, "decided_at": now(), "decided_by": by,
                       "comment": (comment or "").strip(), "agent_ack": False})
            self._log(t, "{}: {}".format(DECISION_LABEL[status], ap["title"]), kind="decision", author=by,
                      ref=aid, detail=ap["comment"] or None, meta={"decision": status})
            if not any(a["status"] == "pending" for a in t["approvals"]):
                t["status"] = "in_progress" if status == "approved" else "changes_requested"
            return ap

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
        current = gitops.fingerprint(gitops.capture_diff(repo, action.get("files")))
        if current != action.get("fingerprint") and not force:
            raise DevTrackError(
                "승인 요청 이후 변경 사항이 달라졌습니다. 새 승인 요청을 만들거나, "
                "달라진 내용을 확인했다면 --force 로 실행하세요."
            )
        if dry_run:
            return {"dry_run": True, "repo": repo, "branch": action.get("branch"), "message": action["message"],
                    "files": action.get("files") or [], "push": action.get("push", True)}
        result = gitops.commit_and_push(repo, action["message"], files=action.get("files"),
                                        branch=action.get("branch"), push=action.get("push", True),
                                        remote=action.get("remote", "origin"))
        with self.edit(tid) as t:
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
    def inbox(self, ack=False):
        """에이전트가 처리해야 할 사용자 입력: 새 작업 지시, 사용자 메시지, 승인/수정 결정."""
        items = []
        to_ack = {}
        for t in self.all_tasks():
            if t["status"] == "queued":
                items.append({"type": "new_task", "task_id": t["id"], "title": t["title"],
                              "instruction": t.get("instruction", ""), "task_type": t["type"],
                              "repo": t.get("repo") or {}, "ts": t["created_at"]})
            for e in t.get("log", []):
                if e.get("author") == USER and e.get("kind") == "user" and e.get("ack") is False:
                    items.append({"type": "comment", "task_id": t["id"], "title": t["title"],
                                  "id": e["id"], "message": e["message"], "ts": e["ts"]})
                    to_ack.setdefault(t["id"], []).append(("L", e["id"]))
            for ap in t.get("approvals", []):
                if ap["status"] != "pending" and ap.get("agent_ack") is False:
                    items.append({"type": "decision", "task_id": t["id"], "title": t["title"],
                                  "id": ap["id"], "approval_title": ap["title"], "decision": ap["status"],
                                  "comment": ap.get("comment", ""), "has_action": bool(ap.get("action")),
                                  "ts": ap.get("decided_at")})
                    to_ack.setdefault(t["id"], []).append(("A", ap["id"]))
        if ack:
            for tid, refs in to_ack.items():
                with self.edit(tid) as t:
                    for typ, rid in refs:
                        if typ == "L":
                            self._find(t["log"], rid, "기록")["ack"] = True
                        else:
                            self._find(t["approvals"], rid, "승인 요청")["agent_ack"] = True
        items.sort(key=lambda x: x.get("ts") or "")
        return items

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
                    "commit": (a.get("applied") or {}).get("commit"),
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
                "created_at": t["created_at"], "updated_at": t["updated_at"],
                "completed_at": t.get("completed_at"),
                "plan_total": len(plan), "plan_done": sum(1 for p in plan if p.get("done")),
                "open_issues": len(open_issues),
                "max_severity": max((SEVERITIES.index(i["severity"]) for i in open_issues), default=-1),
                "pending_approvals": len(pending), "artifact_count": len(t.get("artifacts", [])),
                "last_log": log[-1] if log else None,
            })
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
                                                   "created_at", "author", "size", "description", "lang", "build")})
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
        }
        os.makedirs(self.root, exist_ok=True)
        self._write_json(self.index_path, index)
        return index

    def _sync_publish(self, tid=None):
        if not self.publish_dir:
            return
        try:
            ddata = os.path.join(self.publish_dir, "data")
            if tid:
                mirror_tree(os.path.join(self.tasks_dir, tid), os.path.join(ddata, "tasks", tid))
            os.makedirs(ddata, exist_ok=True)
            shutil.copy2(self.index_path, os.path.join(ddata, "index.json"))
            _chmod(os.path.join(ddata, "index.json"), 0o644)
        except OSError as e:
            sys.stderr.write("[devtrack] 배포 디렉토리 동기화 실패 ({}): {}\n".format(self.publish_dir, e))

    def publish(self, web_root, dest):
        dest = os.path.abspath(os.path.expanduser(dest))
        self.ensure()
        mirror_tree(web_root, dest)
        mirror_tree(self.root, os.path.join(dest, "data"))
        return dest


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
