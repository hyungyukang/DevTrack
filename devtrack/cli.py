"""devtrack 명령행 도구 — AI 에이전트가 진행상황을 기록하고, 승인 결과를 받아 처리한다.

생성 명령(new / issue / artifact / review / request)은 표준출력에 새 ID 만 출력하므로
``TID=$(devtrack new "...")`` 처럼 셸에서 바로 받아 쓸 수 있다.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import DevTrackError, __version__, gitops
from .store import (
    APPROVAL_KINDS, DOC_KINDS, DOC_VERDICT_LABEL, LOG_KINDS, PRIORITIES, SEVERITIES, STATUSES, TASK_TYPES, USER,
    Store, is_unassigned, normalize_item_id, normalize_task_id, pid_alive,
    now as now_iso, _choice_labels,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_ROOT = os.path.join(REPO_ROOT, "web")


def default_data_dir():
    return os.environ.get("DEVTRACK_DATA") or os.path.join(REPO_ROOT, "data")


def _text(value, file_value):
    if file_value:
        if file_value == "-":
            return sys.stdin.read()
        with open(os.path.expanduser(file_value), encoding="utf-8") as fh:
            return fh.read()
    return value


def _out(args, obj, text=None):
    if args.json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif text is not None:
        print(text)


def _info(msg):
    sys.stderr.write(msg + "\n")


# ---------------------------------------------------------------------------- 명령 구현
def cmd_init(store, args):
    store.ensure()
    _out(args, {"data": store.root}, "데이터 디렉토리 준비 완료: {}".format(store.root))


def cmd_new(store, args):
    queued = args.queued
    t = store.create_task(
        args.title,
        instruction=_text(args.instruction, args.instruction_file) or "",
        type=args.type, agent=args.agent or args.author, repo=args.repo, branch=args.branch,
        repo_url=args.url, tags=args.tag, priority=args.priority,
        status="queued" if queued else "in_progress", created_by=USER if queued else None, plan=args.plan,
        related=args.related,
    )
    if not queued:  # AI 가 스스로 만들어 진행하는 작업 — 이 세션이 맡은 작업으로 적어 둔다
        _note_session(store, t["id"])
    _info("새 작업 생성: {} — {}".format(t["id"], t["title"]))
    _out(args, t, t["id"])


def cmd_start(store, args):
    t = store.update_task(args.task, status="in_progress", step=args.step, author=args.author,
                          claim=args.author, take=args.take)
    _out(args, t, "{} 진행 중".format(t["id"]))


def cmd_step(store, args):
    t = store.load(args.task)
    status = "in_progress" if t["status"] in ("queued", "changes_requested", "blocked") else None
    t = store.update_task(args.task, status=status, step=args.text, progress=args.progress, author=args.author,
                          claim=args.author if status else None, claim_queued_only=True)
    _out(args, t, "{} [{}%] {}".format(t["id"], t.get("progress", 0), args.text))


def cmd_status(store, args):
    if not (args.status or args.progress is not None or args.message):
        raise DevTrackError("변경할 상태, --progress, -m 중 하나 이상을 지정하세요")
    t = store.update_task(args.task, status=args.status, progress=args.progress, message=args.message,
                          author=args.author)
    _out(args, t, "{} {} {}%".format(t["id"], STATUSES[t["status"]], t.get("progress", 0)))


def cmd_link(store, args):
    rel = store.link_tasks(args.task, args.others, unlink=args.unlink, author=args.author)
    _out(args, rel, "{} 관련 작업: {}".format(normalize_task_id(args.task), ", ".join(rel) or "없음"))


def cmd_log(store, args):
    e = store.add_log(args.task, args.message, kind=args.kind, detail=_text(args.detail, args.detail_file),
                      author=args.author)
    _out(args, e, e["id"])


def cmd_plan(store, args):
    plan = store.set_plan(args.task, args.items, append=args.append, author=args.author)
    _out(args, plan, "\n".join("{:>2}. {}".format(i + 1, p["text"]) for i, p in enumerate(plan)))


def cmd_check(store, args):
    plan = store.check_plan(args.task, args.numbers, done=not args.undo, author=args.author)
    _out(args, plan, "\n".join("[{}] {:>2}. {}".format("x" if p["done"] else " ", i + 1, p["text"])
                               for i, p in enumerate(plan)))


def cmd_issue(store, args):
    i = store.add_issue(args.task, args.title, detail=_text(args.detail, args.detail_file),
                        severity=args.severity, author=args.author)
    _out(args, i, i["id"])


def cmd_resolve(store, args):
    i = store.resolve_issue(args.task, args.issue, note=args.message, author=args.author)
    _out(args, i, "{} 해결됨".format(i["id"]))


def cmd_artifact(store, args, category=None):
    if getattr(args, "source", False) and getattr(args, "review", False):
        raise DevTrackError("--source 와 --review 는 함께 쓸 수 없습니다")
    category = category or ("source" if getattr(args, "source", False) else
                            "review" if getattr(args, "review", False) else "result")
    common = dict(title=args.title, description=_text(args.desc, args.desc_file), category=category,
                  author=args.author)
    if getattr(args, "git_diff", None):
        repo = gitops.require_repo(os.path.expanduser(args.git_diff))
        text = gitops.capture_diff(repo, args.files)
        if not text.strip():
            raise DevTrackError("변경 사항이 없습니다: {}".format(repo))
        common["title"] = common["title"] or "변경사항 ({})".format(os.path.basename(repo))
        art = store.add_artifact(args.task, text=text, filename="changes.diff", kind="diff", **common)
    elif getattr(args, "url", None):
        art = store.add_artifact(args.task, url=args.url, kind=args.kind or "link", **common)
    elif args.path:
        art = store.add_artifact(args.task, path=args.path, kind=args.kind,
                                 copy=not getattr(args, "no_copy", False),
                                 replace_same_name=getattr(args, "replace", False), **common)
    else:
        raise DevTrackError("파일 경로, --url, --git-diff 중 하나를 지정하세요")
    if art.get("unchanged"):
        _info("변경 없음: {} — {} (같은 내용이 이미 등록됨)".format(art["id"], art["title"]))
    elif art.get("version", 1) > 1 and getattr(args, "replace", False):
        _info("결과물 갱신: {} — {} (v{})".format(art["id"], art["title"], art["version"]))
    else:
        _info("{}: {} — {} ({})".format("검토할 문서 등록" if category == "source" else "결과물 추가",
                                       art["id"], art["title"], art["kind"]))
    if art.get("kind") == "docx" and not art.get("unchanged"):
        _info("  Word 미리보기: {}".format("만듦 (웹에서 글자 · 표를 볼 수 있음)" if art.get("preview")
                                        else "만들지 못함 — 웹에서는 내려받아 보세요"))
    _out(args, art, art["id"])


def _load_json(path):
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise DevTrackError("JSON 을 읽을 수 없습니다 ({}): {}".format(path, e))


def cmd_review(store, args):
    if not args.paths and not args.findings:
        raise DevTrackError("리포트 파일 또는 --findings 중 하나는 지정하세요")
    if args.findings:
        rv = store.set_review(args.task, _load_json(args.findings), author=args.author)
        f = rv.get("findings", [])
        _info("검토 결과 기록: 항목 {}개 (열림 {})".format(len(f), sum(1 for x in f if x["status"] == "open")))
        if not args.paths:
            _out(args, rv, " ".join(x["id"] for x in f))
            return 0
    desc = _text(args.desc, args.desc_file)
    is_md = lambda p: os.path.splitext(p)[1].lower() in (".md", ".markdown")  # noqa: E731
    stem = lambda p: os.path.splitext(os.path.abspath(os.path.expanduser(p)))[0]  # noqa: E731
    given_pdf = {stem(p) for p in args.paths if p.lower().endswith(".pdf")}
    arts = []

    def register(path, title, description):
        before = {a["id"] for a in store.load(args.task).get("artifacts", [])}
        art = store.add_artifact(args.task, path=path, kind=args.kind, title=title, description=description,
                                 category="review", author=args.author, replace_same_name=True)
        if art.get("unchanged"):
            _info("변경 없음: {} — {} (같은 내용이 이미 등록됨)".format(art["id"], art["title"]))
        elif art["id"] in before:
            _info("리뷰 리포트 갱신: {} — {} (v{})".format(art["id"], art["title"], art["version"]))
        else:
            _info("리뷰 리포트 등록: {} — {} ({})".format(art["id"], art["title"], art["kind"]))
        arts.append(art)
        return art

    for i, path in enumerate(args.paths):
        title = args.title
        if title and i:
            title = "{} ({})".format(title, os.path.splitext(path)[1].lstrip(".").upper() or i + 1)
        art = register(path, title, desc if i == 0 else None)
        # --pdf: Markdown 이 새로 올라오거나 바뀌었을 때만 PDF 를 만든다 (같은 내용이면 PDF 도 그대로)
        if args.pdf and is_md(path) and stem(path) not in given_pdf and not art.get("unchanged"):
            from . import pdf as pdfmod
            try:
                out = pdfmod.markdown_to_pdf(path)
                _info("PDF 변환: {}".format(out))
                register(out, "{} (PDF)".format(args.title) if args.title else None, None)
            except DevTrackError as e:
                _info("경고: {}".format(e))
                store.add_log(args.task, "리뷰 리포트 PDF 변환 실패", kind="warning", detail=str(e),
                              author=args.author)
    _out(args, arts, "\n".join(a["id"] for a in arts))
    return 0


def cmd_text(store, args):
    """문서의 글자를 뽑는다 — Word(.docx)는 표준 라이브러리로, PDF 는 pdftotext 로 (문서 리뷰에서 AI 가 읽는 용도)."""
    path = os.path.expanduser(args.file)
    if not os.path.isfile(path):
        raise DevTrackError("파일을 찾을 수 없습니다: {}".format(args.file))
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        from .docx import docx_to_markdown
        text = docx_to_markdown(path)
    elif ext == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            raise DevTrackError("pdftotext 가 없습니다 (poppler-utils) — PDF 는 AI 가 직접 읽거나 pdftotext 를 설치하세요")
        try:
            text = subprocess.run([exe, "-layout", "-enc", "UTF-8", path, "-"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, check=True, timeout=args.timeout).stdout.decode("utf-8", "replace")
        except subprocess.CalledProcessError as e:
            raise DevTrackError("pdftotext 실패: {}".format(e.stderr.decode("utf-8", "replace").strip()))
        except subprocess.TimeoutExpired:
            raise DevTrackError("pdftotext 시간 초과 ({}초)".format(args.timeout))
    elif ext == ".doc":
        raise DevTrackError("옛 Word 형식(.doc)은 읽지 못합니다 — Word 에서 .docx 나 PDF 로 저장해 주세요")
    else:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    if args.output:
        with open(os.path.expanduser(args.output), "w", encoding="utf-8") as fh:
            fh.write(text)
        _info("저장: {} ({}자)".format(args.output, len(text)))
        _out(args, {"output": args.output, "chars": len(text)}, args.output)
    else:
        sys.stdout.write(text)
    return 0


def cmd_pdf(store, args):
    from . import pdf as pdfmod
    out = pdfmod.markdown_to_pdf(args.markdown, args.output, timeout=args.timeout)
    _out(args, {"pdf": out}, out)
    return 0


def cmd_latex(store, args):
    from . import latex
    task = store.load(args.task)
    tex_abs = os.path.abspath(os.path.expanduser(args.tex))
    repo = (task.get("repo") or {}).get("path")
    source = os.path.relpath(tex_abs, repo) if repo and tex_abs.startswith(repo.rstrip("/") + "/") else args.tex
    if args.pdf:
        result = latex.from_existing(args.tex, args.pdf)
    else:
        _info("LaTeX 컴파일 중: {}".format(args.tex))
        result = latex.compile_tex(args.tex, engine=args.engine, timeout=args.timeout)
    b = store.record_build(args.task, source, result, author=args.author)
    if b["status"] == "ok":
        wc = latex.warning_count(b["warnings"])
        _out(args, b, "{} 성공: {}쪽{} (PDF {})".format(b["id"], b.get("pages") or "?",
                                                       ", 경고 {}".format(wc) if wc else "", b.get("pdf")))
        return 0
    first = (b["errors"] or [{"message": "?"}])[0]
    _out(args, b, "{} 실패: {}{}".format(b["id"], first["message"],
                                         " (줄 {})".format(first["line"]) if first.get("line") else ""))
    return 1


def cmd_request(store, args):
    commit = None
    if args.commit:
        commit = {"repo": args.repo, "branch": args.branch, "message": _text(args.message, args.message_file),
                  "files": args.files, "push": not args.no_push, "remote": args.remote}
    ap = store.request_approval(args.task, args.title, description=_text(args.desc, args.desc_file) or "",
                                kind=args.kind, artifact_ids=args.artifact, commit=commit, author=args.author,
                                choices=args.choices, choices_label=args.choices_label)
    _info("승인 요청 등록: {} {} — 웹에 알림으로 표시됩니다".format(normalize_task_id(args.task), ap["id"]))
    _out(args, ap, ap["id"])


def cmd_decide(store, args):
    ap = store.decide(args.task, args.approval, args.decision, comment=args.message or "", by=args.by or USER,
                      selected=args.select)
    sel = " (선택: {})".format(", ".join(ap["selected"])) if ap.get("selected") else ""
    _out(args, ap, "{} {} → {}{}".format(normalize_task_id(args.task), ap["id"], ap["status"], sel))


def cmd_wait(store, args):
    tid = normalize_task_id(args.task)
    aid = normalize_item_id(args.approval, "A")
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        t = store.load(tid)
        ap = next((a for a in t.get("approvals", []) if a["id"] == aid), None)
        if ap is None:
            raise DevTrackError("승인 요청을 찾을 수 없습니다: {} {}".format(tid, aid))
        if ap["status"] != "pending":
            store.ack_decision(tid, aid)
            lines = [ap["status"]]
            if ap.get("selected") is not None:
                lines.append("선택: " + (", ".join(ap["selected"]) or "(없음)"))
                lines.extend(_choice_label_lines(_choice_labels(ap)))
            if ap.get("comment"):
                lines.append("코멘트: " + ap["comment"])
            _out(args, ap, "\n".join(lines))
            return 0 if ap["status"] == "approved" else 2
        if deadline and time.time() >= deadline:
            _out(args, ap, "pending")
            _info("시간 초과: 아직 결정되지 않았습니다")
            return 3
        time.sleep(max(1, args.interval))


def cmd_apply(store, args):
    r = store.apply_approval(args.task, args.approval, force=args.force, dry_run=args.dry_run, author=args.author)
    if r.get("dry_run"):
        _out(args, r, "[dry-run] {repo} ({branch}) 에 커밋 예정: {message}".format(**r))
        return 0
    if r.get("push_error"):
        _out(args, r, "커밋 {} 완료, 푸시 실패:\n{}".format(r["commit"][:8], r["push_error"]))
        return 1
    _out(args, r, "커밋 {}{} 완료".format(r["commit"][:8], " 및 푸시" if r["pushed"] else ""))
    return 0


def cmd_comment(store, args):
    e = store.add_user_comment(args.task, args.message)
    _out(args, e, e["id"])


def _format_inbox(items):
    out = []
    for it in items:
        if it["type"] == "new_task":
            who = "담당 미정 — 먼저 시작한 AI 가 맡음" if is_unassigned(it.get("agent")) else "담당 " + it["agent"]
            out.append("[새 작업] {} {} ({} · {})".format(it["task_id"], it["title"], it["task_type"], who))
            if it.get("instruction"):
                out.append(_indent(it["instruction"]))
            out.append(_indent("→ devtrack start {task_id}".format(**it)))
        elif it["type"] == "comment":
            out.append("[메시지] {task_id} {id}: {message}".format(**it))
        elif it["type"] == "cancelled":
            out.append("[취소] {task_id} {title} — 사용자가 작업을 취소했습니다. 이 작업은 중단하세요.".format(**it))
            if it.get("reason"):
                out.append(_indent("사유: " + it["reason"]))
        elif it["type"] == "deleted":
            out.append("[삭제] {task_id} {title} — 사용자가 작업을 삭제했습니다. 이 작업은 중단하고 더 기록하지 마세요.".format(**it))
        elif it["type"] == "completed":
            out.append("[완료 처리] {task_id} {title} — 사용자가 작업을 완료로 처리했습니다. 이 작업은 여기서 멈추세요.".format(**it))
            if it.get("reason"):
                out.append(_indent("메모: " + it["reason"]))
        else:
            out.append("[결정] {task_id} {id} '{approval_title}' → {decision}".format(**it))
            if it.get("selected"):
                out.append(_indent("선택: " + ", ".join(it["selected"])))
                out.extend(_indent(x) for x in _choice_label_lines(it.get("selected_labels")))
            if it.get("comment"):
                out.append(_indent("코멘트: " + it["comment"]))
            if it["decision"] == "approved" and it.get("has_action"):
                out.append(_indent("→ devtrack apply {task_id} {id}".format(**it)))
    return out


def _print_inbox(items, hint=True):
    for line in _format_inbox(items):
        print(line)
    # 웹에서 온 새 작업 · 메시지는 그 자체가 사용자의 지시 — 다른 일을 하던 AI 가 다시 묻지 않고 진행하도록 알려 준다
    # (--ack 로 처리 표시할 때는 이미 처리한 것이므로 생략)
    if hint and any(it["type"] in ("new_task", "comment") for it in items):
        tids = sorted(set(it["task_id"] for it in items if it["type"] == "comment"))
        print("→ 새 작업 · 메시지는 사용자에게 다시 묻지 말고 진행하세요. 다른 작업 중이면 메시지는 지금 단계를 마친 뒤, "
              "새 작업은 지금 작업이 끝나거나 승인 대기가 되면 온 순서대로.")
        if tids:
            print(_indent("메시지의 답은 그 작업에 기록하고 처리 표시: " + " · ".join(
                "devtrack inbox --task {} --ack".format(t) for t in tids)))


def _inbox_scope(args):
    """받은 편지함 범위: 기본은 나($DEVTRACK_AGENT / --as)에게 배정된 작업과 담당이 정해지지 않은 작업.
    devtrack watch 가 실행한 세션은 DEVTRACK_TASK 의 작업만 본다.
    --task 로 작업을 직접 고르면 담당 AI 와 상관없이 그 작업의 입력을 본다 (devtrack hook 이 안내하는 확인 명령)."""
    explicit = getattr(args, "task", None)
    agent = None if (getattr(args, "all_agents", False) or explicit) else args.author
    task = explicit or os.environ.get("DEVTRACK_TASK") or None
    return agent, task


class _Listening(object):
    """웹 입력을 기다리는 동안 '기다리는 중' 기록(.agent/listeners/)을 남겨 웹 화면이 보여 주게 한다.
    기록은 보조 기능이라 실패해도 기다리기는 계속한다. 이 프로세스를 띄운 쪽(예: Claude 대화)이 끝나 부모가
    바뀌면 깨울 대상이 없으므로 기록만 지운다 (기다리기 자체는 그대로)."""
    EVERY = 30  # 초: 기록 갱신 간격 (다른 컴퓨터에서 볼 때 끝났는지 판단하는 기준)

    def __init__(self, store, kind, agent, task=None, seen=None):
        self.store, self.kind, self.agent, self.task, self.seen = store, kind, agent or "", task, seen
        self.started = None
        self.last = 0.0
        self.state, self.running = "waiting", None
        self.parent = os.getppid()
        self.orphan = False

    def _write(self):
        try:
            self.store.touch_listener(self.kind, self.agent, task=self.task, started_at=self.started,
                                      state=self.state, running=self.running, seen=self.seen)
            self.started = self.started or now_iso()
        except (OSError, DevTrackError):
            pass
        self.last = time.time()

    def tick(self):
        if self.orphan:
            return
        if os.getppid() != self.parent:
            self.orphan = True
            self.store.drop_listener(self.kind, self.agent)
            return
        if time.time() - self.last >= self.EVERY:
            self._write()

    def set(self, state, running=None):
        self.state, self.running = state, running
        if not self.orphan:
            self._write()

    def __enter__(self):
        self.started = now_iso()
        self._write()
        return self

    def __exit__(self, *exc):
        self.store.drop_listener(self.kind, self.agent)
        return False


def _wait_for_new(store, agent, seen_name, task=None, timeout=0, interval=3, on_tick=None):
    """seen_name 기준으로 아직 보지 않은 입력이 생길 때까지 기다려 그 항목들을 돌려준다 (시간 초과면 None)."""
    deadline = time.time() + timeout if timeout else None
    last = object()
    while True:
        if on_tick:
            on_tick()
        token = store.change_token()
        if token != last:
            last = token
            items = store.inbox(agent=agent, task=task)
            seen = store.inbox_seen(seen_name)
            new = [it for it in items if store.inbox_key(it) not in seen]
            if new:
                store.mark_inbox_seen(seen_name, items, merge=bool(task))
                return new
        if deadline and time.time() >= deadline:
            return None
        time.sleep(max(1, interval))


def _inbox_wait(store, args):
    """아직 보지 않은 사용자 입력이 생길 때까지 기다린다 (백그라운드 실행용)."""
    if args.ack:
        raise DevTrackError("--wait 와 --ack 는 함께 쓸 수 없습니다 (처리한 뒤 devtrack inbox --ack)")
    agent, task = _inbox_scope(args)
    scope = task or ("{} 에게 배정된 작업".format(agent) if agent else "모든 AI 의 작업")
    _info("새 사용자 입력을 기다리는 중 ({} — 웹의 새 작업 지시 · 메시지 · 승인 결정) — 중단: Ctrl+C".format(scope))
    with _Listening(store, "wait", agent, task=task, seen=args.author) as listening:
        new = _wait_for_new(store, agent, args.author, task=task, timeout=args.timeout, interval=args.interval,
                            on_tick=listening.tick)
    if new is None:
        _out(args, [])
        _info("시간 초과: 새 입력이 없습니다")
        return 3
    if args.json:
        _out(args, new)
        return 0
    print("새 사용자 입력 {}건:".format(len(new)))
    _print_inbox(new)
    print("\n처리한 뒤 다시 `devtrack inbox --wait` 를 백그라운드로 실행해 다음 입력을 기다리세요.")
    return 0


def cmd_inbox(store, args):
    if args.wait:
        return _inbox_wait(store, args)
    agent, task = _inbox_scope(args)
    items = store.inbox(ack=args.ack, agent=agent, task=task)
    store.mark_inbox_seen(args.author, items, merge=bool(task))
    if args.json:
        _out(args, items)
        return 0
    if not items:
        print("처리할 사용자 입력이 없습니다.")
        if agent:
            others = len(store.inbox(task=task))
            if others:
                print("(다른 AI 에게 배정된 입력 {}건은 그 AI 가 처리합니다 — 모두 보기: devtrack inbox --all-agents)".format(
                    others))
        return 0
    _print_inbox(items, hint=not args.ack)
    return 0


# ---------------------------------------------------------------------------- 취소 · 삭제를 일하는 중인 AI 에 알리기
# 작업에 기록하는 명령 — 실행한 AI 세션을 그 작업에 적어 두고, 사용자가 취소 · 삭제한 작업이면 거절한다
TASK_WORK_CMDS = ("start", "step", "status", "link", "log", "plan", "check", "issue", "resolve", "artifact",
                  "review", "latex", "request", "apply", "done")
# 훅이 막지 않는 명령: devtrack 과 출력을 다듬는 정도만 (확인 기록 · 상태 보기는 할 수 있게)
HOOK_SAFE_CMDS = ("devtrack", "echo", "printf", "true", "cd", "head", "tail", "grep", "cat")
_SHELL_SEPARATORS = (";", "&&", "||", "|", "|&", "&", ";;")
_SHELL_REDIRECTS = (">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>", ">|")


SESSION_ENV = ("DEVTRACK_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def _session_id():
    """지금 이 명령을 실행한 AI 세션 (Claude Code · Codex 가 명령 환경에 넣어 준다)."""
    for name in SESSION_ENV:
        if os.environ.get(name):
            return os.environ[name]
    return None


def _note_session(store, tid):
    sid = _session_id()
    if not sid:
        return
    try:
        store.note_session_task(sid, tid)
    except (OSError, DevTrackError):
        pass  # 세션 기록은 보조 기능 — 실패해도 본래 명령은 진행


# 사용자가 작업을 멈추게 한 방식: 알림 종류 → (한 일, 사유를 부르는 말)
STOP_WORDS = {"cancelled": ("취소", "사유"), "deleted": ("삭제", "사유"), "completed": ("완료로 처리", "메모")}


def _stop_why(n):
    what, label = STOP_WORDS.get(n["type"], ("멈추게", "사유"))
    return what, " ({}: {})".format(label, n["reason"]) if n.get("reason") else ""


def _stop_text(n):
    what, why = _stop_why(n)
    return "{} ({}) 은(는) 사용자가 {}한 작업입니다{} — 이 작업은 지금 멈추고 더 진행하지 마세요. " \
           "확인 기록: devtrack inbox --task {} --ack".format(n["task_id"], n["title"], what, why, n["task_id"])


def _guard_task(store, args):
    """작업에 기록하기 전: 사용자가 취소 · 삭제했는데 아직 확인하지 않았으면 거절, 아니면 이 세션의 작업으로 적어 둔다."""
    n = store.stop_notice(args.task)
    if n:
        raise DevTrackError(_stop_text(n))
    _note_session(store, args.task)


def _shell_command(tool_input):
    """훅 입력에서 셸 명령 문자열 (Claude: tool_input.command, Codex: command 문자열 또는 [셸, -lc, 명령])."""
    if not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command", tool_input.get("cmd"))
    if isinstance(cmd, list):
        cmd = cmd[-1] if len(cmd) >= 3 and cmd[-2] in ("-c", "-lc") else " ".join(str(x) for x in cmd)
    return cmd if isinstance(cmd, str) else None


def _devtrack_only(command):
    """devtrack (과 출력을 다듬는 명령) 만 실행하는 셸 명령인가 — 멈춘 뒤 확인을 기록할 수 있게 훅이 통과시킨다."""
    if not command or "devtrack" not in command:
        return False
    try:
        lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return False
    expect_cmd, skip, seen = True, False, False
    for tok in tokens:
        if skip:
            skip = False
        elif tok in _SHELL_SEPARATORS:
            expect_cmd = True
        elif tok in _SHELL_REDIRECTS:
            skip = True
        elif expect_cmd:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
                continue  # 앞에 붙인 환경변수 (DEVTRACK_AGENT=Codex devtrack ...)
            name = os.path.basename(tok)
            if name not in HOOK_SAFE_CMDS:
                return False
            seen = seen or name == "devtrack"
            expect_cmd = False
    return seen


def _hook_reason(notices):
    lines = ["⛔ DevTrack: 사용자가 이 세션이 맡은 작업을 멈추게 했습니다."]
    for n in notices:
        what, why = _stop_why(n)
        lines.append("- {} 「{}」 — 사용자가 {}했습니다{}".format(n["task_id"], n["title"], what, why))
    acks = " ; ".join("devtrack inbox --task {} --ack".format(n["task_id"]) for n in notices)
    lines += [
        "이 작업을 하던 중이면 지금 바로 멈추세요. 파일을 고치거나 다른 명령을 더 실행하지 마세요.",
        "1) 다른 명령을 붙이지 말고 `{}` 만 실행해 확인을 기록하세요. 확인을 기록하기 전에는 devtrack 명령만 실행됩니다.".format(acks),
        "2) 사용자에게 멈췄다고 짧게 알리세요. 웹 지시 대기 모드였다면 다시 `devtrack inbox --wait` 를 백그라운드로 실행합니다.",
        "다른 작업을 하던 중이었다면 확인만 기록하고 그 작업을 계속하세요.",
    ]
    return "\n".join(lines)


def cmd_hook(store, args):
    """Claude Code · Codex 의 PreToolUse 훅. 이 세션이 맡은 작업을 사용자가 취소 · 삭제했으면 도구 실행을 막고 멈추라고
    알린다 (devtrack 명령만 통과). 훅 오류로 AI 작업이 막히면 안 되므로 문제가 생기면 그냥 통과시킨다."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict) or data.get("hook_event_name", "PreToolUse") != "PreToolUse":
            return 0
        sid = data.get("session_id") or _session_id()
        tids = store.session_tasks(sid) if sid else []
        if not tids:
            return 0
        notices = [n for n in (store.stop_notice(t) for t in tids) if n]
        if not notices or _devtrack_only(_shell_command(data.get("tool_input"))):
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": _hook_reason(notices)}}, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — 어떤 오류든 AI 를 막지 않는다
        return 0
    return 0


# ---------------------------------------------------------------------------- watch (AI 실행기)
# 앱에 들어 있는 Codex CLI 위치 (macOS). 여러 개면 가장 새 버전을 쓴다 — 오래된 CLI 는 새 모델을 못 쓴다
CODEX_BUNDLED = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
    "~/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
    "~/Applications/Codex.app/Contents/Resources/codex",
    "~/.vscode/extensions/openai.chatgpt-*/bin/*/codex",
)
NOTICE_TYPES = ("cancelled", "deleted", "completed")


def _codex_version(path):
    try:
        out = subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=10).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None


def find_codex():
    """Codex CLI 찾기: $DEVTRACK_CODEX > PATH 의 codex > 앱에 들어 있는 CLI 중 가장 새 버전. (경로, 버전) 을 돌려준다."""
    for c in (os.environ.get("DEVTRACK_CODEX"), shutil.which("codex")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c, _codex_version(c)
    import glob
    found = []
    for pat in CODEX_BUNDLED:
        for c in sorted(glob.glob(os.path.expanduser(pat))):
            if os.path.isfile(c) and os.access(c, os.X_OK):
                found.append((_codex_version(c) or (0,), c))
    if found:
        ver, c = max(found)
        return c, ver if ver != (0,) else None
    raise DevTrackError("codex 명령을 찾을 수 없습니다 — Codex CLI 를 설치하거나 DEVTRACK_CODEX 에 경로를 지정하세요")


def _watch_prompt(agent, tid, items):
    lines = [
        "DevTrack 에 {} 에게 온 새 입력이 있습니다. (devtrack watch 가 이 세션을 실행했습니다)".format(agent),
        "이번 실행에서는 {} 작업만 처리하세요. 기록 규칙은 AGENTS.md 의 DevTrack 규칙"
        "(또는 DevTrack 저장소의 docs/AGENT_PROTOCOL.md)을 따릅니다.".format(tid),
        "",
        "- 작업 내용과 지금까지의 기록: devtrack show {}".format(tid),
        "- [새 작업] 이면 devtrack start {} 로 시작하고 지시대로 진행합니다. 지시에 /review-pr 같은 스킬 호출이 있으면 "
        "그 이름의 스킬 지침(Codex: ~/.codex/skills/<이름>/SKILL.md, Claude: ~/.claude/skills/<이름>/SKILL.md)을 "
        "새로 읽고 따릅니다.".format(tid),
        "- [메시지] · [결정] 은 내용대로 처리합니다. 승인된 커밋 요청은 devtrack apply 로 반영합니다.",
        "- 처리한 입력은 devtrack inbox --ack 로 처리됨 표시합니다 (이 실행에서는 {} 의 입력만 보입니다).".format(tid),
        "- 사용자 결정이 필요하면 devtrack request 로 요청을 남기고 이번 실행을 끝내세요. devtrack wait 나 "
        "devtrack inbox --wait 로 기다리지 마세요 — 결정이 오면 devtrack watch 가 다시 실행합니다.",
        "- 마지막에 무엇을 했는지 두세 문장으로 요약해 주세요.",
        "",
        "[입력]",
    ]
    return "\n".join(lines + _format_inbox(items))


def _stop_reason(store, tid):
    try:
        t = store.load(tid)
    except DevTrackError:
        return "deleted"
    except (OSError, ValueError):
        return None
    if t.get("status") == "cancelled":
        return "cancelled"
    if t.get("status") == "done" and (t.get("completed") or {}).get("agent_ack") is False:
        return "completed"
    return None


def _terminate(proc):
    if proc.poll() is not None:
        return
    group = hasattr(os, "killpg")
    try:
        os.killpg(proc.pid, signal.SIGTERM) if group else proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL) if group else proc.kill()
        except OSError:
            pass
        proc.wait()
    except OSError:
        pass


def _watch_log(store, tid, message, kind="info", detail=None, author=None):
    try:
        store.add_log(tid, message, kind=kind, detail=detail, author=author)
    except DevTrackError:
        pass  # 그사이 삭제된 작업


def _stamp(msg):
    print("[{}] {}".format(time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def _watch_run(store, args, agent, tid, items, codex, extra, default_cwd, tick=None):
    t = store.load(tid)
    repo = (t.get("repo") or {}).get("path")
    cwd = repo if repo and os.path.isdir(repo) else default_cwd
    prompt = _watch_prompt(agent, tid, items)
    tmpdir = tempfile.mkdtemp(prefix="devtrack-watch-")
    last_msg = os.path.join(tmpdir, "last-message.txt")
    if codex:
        cmd = [codex, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", cwd,
               "--add-dir", store.root]
        if store.publish_dir:
            cmd += ["--add-dir", store.publish_dir]
        if not args.no_network:  # 대화창이 없어 허락을 물을 수 없으므로 gh · git push 에 필요한 인터넷을 미리 허용
            cmd += ["-c", "sandbox_workspace_write.network_access=true"]
        cmd += ["-o", last_msg] + extra + [prompt]
    else:
        cmd = extra + [prompt]
    # watch 를 실행한 쪽(예: Claude 대화)의 세션 ID 가 넘어가면 실행한 AI 의 작업이 그 세션 것으로 적힌다
    env = {k: v for k, v in os.environ.items() if k not in SESSION_ENV}
    env.update({"DEVTRACK_AGENT": agent, "DEVTRACK_DATA": store.root, "DEVTRACK_TASK": tid, "DEVTRACK_WATCH": "1"})
    env["PATH"] = os.path.join(REPO_ROOT, "bin") + os.pathsep + env.get("PATH", "")

    _stamp("{} 실행 — {} {} (폴더: {})".format(agent, tid, t["title"], cwd))
    print(_indent("\n".join(_format_inbox(items))))
    sys.stdout.flush()
    stopped = None
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        _watch_log(store, tid, "{} 을(를) 실행하지 못했습니다".format(agent), kind="error", detail=str(e),
                   author=agent)
        _stamp("실행 실패: {}".format(e))
        return None
    try:
        while True:
            try:
                rc = proc.wait(timeout=max(1, args.interval))
                break
            except subprocess.TimeoutExpired:
                pass
            if tick:
                tick()
            stopped = _stop_reason(store, tid)
            if stopped:
                _terminate(proc)
                rc = None
                break
    except KeyboardInterrupt:
        _terminate(proc)
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    summary = ""
    try:
        with open(last_msg, encoding="utf-8", errors="replace") as fh:
            summary = fh.read().strip()
    except OSError:
        pass
    shutil.rmtree(tmpdir, ignore_errors=True)

    if stopped:
        # 취소 · 삭제 알림은 실행을 멈춘 것으로 처리됨
        store.ack_inbox([it for it in store.inbox(agent=agent, task=tid) if it["type"] in NOTICE_TYPES])
        if stopped in ("cancelled", "completed"):
            _watch_log(store, tid, "작업이 {} {} 실행을 중단했습니다".format(
                "취소되어" if stopped == "cancelled" else "완료로 처리되어", agent), kind="warning", author=agent)
        _stamp("{} — 사용자가 작업을 {} {} 실행을 중단했습니다".format(
            tid, {"cancelled": "취소해", "completed": "완료로 처리해"}.get(stopped, "삭제해"), agent))
        return None
    if rc != 0:
        _watch_log(store, tid, "{} 실행이 실패했습니다 (종료 코드 {}) — 이번 입력은 처리되지 않은 채 남아 있습니다".format(agent, rc),
                   kind="warning", detail=summary[:6000] or None, author=agent)
        _stamp("{} 실행 실패 (종료 코드 {}) — {}".format(agent, rc, tid))
        return rc
    try:
        store.ack_inbox(items)
    except DevTrackError:
        pass
    if summary:
        _watch_log(store, tid, "{} 실행 요약".format(agent), detail=summary[:6000], author=agent)
    try:
        still_queued = store.load(tid).get("status") == "queued"
    except DevTrackError:
        still_queued = False
    if still_queued and any(it["type"] == "new_task" for it in items):
        _watch_log(store, tid, "{} 실행이 끝났지만 작업이 아직 '대기' 상태입니다 — 시작하지 못한 것 같습니다".format(agent),
                   kind="warning", author=agent)
    _stamp("{} 실행 끝 — {}".format(agent, tid))
    return 0


_pid_alive = pid_alive


def cmd_watch(store, args):
    agent = (args.agent or ("Codex" if args.codex else args.author) or "").strip()
    if is_unassigned(agent):
        raise DevTrackError("--agent 에 AI 이름을 지정하세요 (예: --agent Codex)")
    extra = list(args.command or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    codex = None
    if args.codex:
        codex, ver = find_codex()
        _info("Codex CLI: {} ({}) · 인터넷 {}".format(codex, ".".join(map(str, ver)) if ver else "버전 확인 안 됨",
                                                    "차단 (--no-network)" if args.no_network else "허용"))
    if not codex and not extra:
        raise DevTrackError("실행할 AI 를 지정하세요: devtrack watch --codex   또는   "
                            "devtrack watch --agent 이름 -- 명령 [인자…]")
    default_cwd = os.path.abspath(os.path.expanduser(args.cwd)) if args.cwd else os.getcwd()
    if not os.path.isdir(default_cwd):
        raise DevTrackError("폴더를 찾을 수 없습니다: {}".format(default_cwd))
    store.ensure()
    pidfile = os.path.join(store.root, ".agent", "watch-{}.pid".format(re.sub(r"[^A-Za-z0-9._-]+", "_", agent)))
    try:
        with open(pidfile) as fh:
            other = int(fh.read().strip() or 0)
        if other and other != os.getpid() and _pid_alive(other):
            raise DevTrackError("{} 의 devtrack watch 가 이미 실행 중입니다 (PID {}) — 한 AI 에 하나만 실행하세요".format(
                agent, other))
    except (OSError, ValueError):
        pass
    os.makedirs(os.path.dirname(pidfile), exist_ok=True)
    with open(pidfile, "w") as fh:
        fh.write(str(os.getpid()))

    seen_name = agent + "@watch"
    # 켤 때마다 처음부터: 꺼져 있던 동안 쌓인 입력과, 지난번에 실패해 처리되지 않은 입력을 다시 처리한다
    store.mark_inbox_seen(seen_name, [])
    _info("devtrack watch — {} 에게 온 입력(새 작업 지시 · 메시지 · 결정)을 기다렸다가 {} 로 처리합니다".format(
        agent, "codex exec" if codex else extra[0]))
    _info("데이터: {} · 저장소 경로가 없는 작업의 실행 폴더: {} · 멈추기: Ctrl+C".format(store.root, default_cwd))
    listening = _Listening(store, "watch", agent, seen=seen_name).__enter__()
    try:
        while True:
            new = _wait_for_new(store, agent, seen_name, timeout=args.timeout, interval=args.interval,
                                on_tick=listening.tick)
            if new is None:
                _info("시간 초과: 새 입력이 없습니다")
                return 3
            notices = [it for it in new if it["type"] in NOTICE_TYPES]
            for line in _format_inbox(notices):
                _stamp(line)
            if notices:
                store.ack_inbox(notices)
            order = []
            for it in new:
                if it["type"] not in NOTICE_TYPES and it["task_id"] not in order:
                    order.append(it["task_id"])
            for tid in order:
                # 다시 확인: 앞선 실행이 이미 처리했을 수 있고, 같은 작업의 남은 입력도 함께 넘긴다
                pending = [it for it in store.inbox(agent=agent, task=tid) if it["type"] not in NOTICE_TYPES]
                if pending:
                    listening.set("running", tid)
                    try:
                        _watch_run(store, args, agent, tid, pending, codex, extra, default_cwd, tick=listening.tick)
                    finally:
                        listening.set("waiting")
            if args.once:
                return 0
            _info("다음 입력을 기다리는 중…")
    except KeyboardInterrupt:
        _info("\ndevtrack watch 를 멈췄습니다")
        return 130
    finally:
        listening.__exit__()
        try:
            with open(pidfile) as fh:
                if fh.read().strip() == str(os.getpid()):
                    os.remove(pidfile)
        except OSError:
            pass


def cmd_cancel(store, args):
    t = store.cancel_task(args.task, reason=args.message or "", author=args.author)
    n = len((t["log"][-1].get("meta") or {}).get("withdrawn") or [])
    _out(args, t, "{} 취소됨{}".format(t["id"], " (승인 요청 {}건 철회)".format(n) if n else ""))


def cmd_delete(store, args):
    t = store.load(args.task)
    if not args.yes:
        if not sys.stdin.isatty():
            raise DevTrackError("삭제는 확인이 필요합니다. 터미널에서 직접 실행하거나, "
                                "사용자가 삭제를 확인한 경우에만 --yes 를 붙이세요")
        pend = sum(1 for a in t.get("approvals", []) if a["status"] == "pending")
        _info("삭제할 작업: {} {} ({}, {})".format(t["id"], t["title"], TASK_TYPES.get(t["type"]),
                                                STATUSES.get(t["status"])))
        _info("  기록 {}건 · 결과물 {}개 · 승인 요청 {}건 (대기 {}) — 결과물 파일까지 모두 지워지며 되돌릴 수 없습니다".format(
            len(t.get("log", [])), len(t.get("artifacts", [])), len(t.get("approvals", [])), pend))
        _info("  기록을 남기려면 삭제 대신 `devtrack cancel {}` 을 쓰세요.".format(t["id"]))
        sys.stderr.write("정말 삭제하려면 작업 ID({})를 그대로 입력하세요: ".format(t["id"]))
        sys.stderr.flush()
        answer = sys.stdin.readline().strip().upper()
        if answer != t["id"]:
            _info("삭제하지 않았습니다 (입력한 ID 가 다름)")
            return 1
    r = store.delete_task(t["id"], author=args.author)
    _out(args, r, "{} 삭제됨{}".format(r["id"], " (연결 해제: {})".format(", ".join(r["unlinked"])) if r["unlinked"] else ""))
    return 0


def cmd_done(store, args):
    if args.by_user:
        t = store.complete_task(args.task, note=args.message or "", author=args.author, by_user=True)
        n = len((t["log"][-1].get("meta") or {}).get("withdrawn") or [])
        _out(args, t, "{} 완료 (사용자 요청){}".format(t["id"], " — 승인 요청 {}건 철회".format(n) if n else ""))
        return
    t = store.update_task(args.task, status="done", message=args.message, author=args.author)
    _out(args, t, "{} 완료".format(t["id"]))


def cmd_show(store, args):
    t = store.load(args.task)
    if args.json:
        _out(args, t)
        return 0
    repo = t.get("repo") or {}
    print("{}  {}".format(t["id"], t["title"]))
    print("  {} · {} · {}% · 에이전트 {}".format(TASK_TYPES.get(t["type"]), STATUSES.get(t["status"]),
                                             t.get("progress", 0), t.get("agent")))
    if repo:
        print("  저장소: {}{}".format(repo.get("path") or repo.get("url"),
                                    " ({})".format(repo["branch"]) if repo.get("branch") else ""))
    if t.get("related"):
        print("  관련 작업: " + ", ".join(t["related"]))
    if t.get("current_step"):
        print("  현재 단계: " + t["current_step"])
    if t.get("plan"):
        print("  계획:")
        for i, p in enumerate(t["plan"]):
            print("    [{}] {}. {}".format("x" if p["done"] else " ", i + 1, p["text"]))
    rv = t.get("review") or {}
    doc = rv.get("doc") or {}
    if doc:
        print("  리뷰한 문서: {}{}{}".format(doc.get("title") or "(제목 없음)",
                                       " ({})".format(DOC_KINDS.get(doc.get("kind"), doc.get("kind"))) if doc.get("kind") else "",
                                       " · 목적: " + doc["purpose"] if doc.get("purpose") else ""))
    if rv.get("checks"):
        print("  점검 영역: " + ", ".join("{} {}".format(c["area"], c["rating"]) for c in rv["checks"]))
    if rv.get("findings"):
        pr = rv.get("pr") or {}
        verdict = DOC_VERDICT_LABEL.get(rv.get("verdict"), rv.get("verdict"))
        print("  리뷰 결과{}{}:".format(" — PR #{}".format(pr["number"]) if pr.get("number") else "",
                                    " · 의견 {}".format(verdict) if verdict else ""))
        for f in rv["findings"]:
            loc = f.get("location") or "{}{}".format(f.get("file", ""), ":{}".format(f["line"]) if f.get("line") else "")
            print("    {} [{}{}] {}{}".format(f["id"], f["severity"], "" if f["status"] == "open" else ", " + f["status"],
                                           f["title"], "  ({})".format(loc) if loc else ""))
    issues = [i for i in t.get("issues", []) if i["status"] == "open"]
    if issues:
        print("  열린 문제:")
        for i in issues:
            print("    {} [{}] {}".format(i["id"], i["severity"], i["title"]))
    if t.get("approvals"):
        print("  승인 요청:")
        for a in t["approvals"]:
            print("    {} [{}] {}".format(a["id"], a["status"], a["title"]))
    if t.get("artifacts"):
        print("  결과물:")
        for a in t["artifacts"]:
            print("    {} [{}{}] {}".format(a["id"], a["kind"], {"review": ", 리뷰", "source": ", 검토할 문서"}.get(
                a.get("category"), ""), a["title"]))
    print("  최근 기록:")
    for e in t.get("log", [])[-args.lines:]:
        print("    {} [{}] {}: {}".format(e["ts"][5:16].replace("T", " "), e["kind"], e["author"], e["message"]))
    return 0


def cmd_list(store, args):
    tasks = store.all_tasks()
    if not args.all:
        tasks = [t for t in tasks if t["status"] not in ("done", "cancelled")] if not args.status else tasks
    if args.status:
        tasks = [t for t in tasks if t["status"] == args.status]
    if args.json:
        _out(args, [{k: t.get(k) for k in ("id", "title", "type", "status", "progress", "agent", "updated_at")}
                    for t in tasks])
        return 0
    if not tasks:
        print("작업이 없습니다.")
    for t in tasks:
        pend = sum(1 for a in t.get("approvals", []) if a["status"] == "pending")
        print("{}  {:<6} {:>3}%  {}{}".format(t["id"], STATUSES[t["status"]], t.get("progress", 0), t["title"],
                                             "  (승인 대기 {})".format(pend) if pend else ""))
    return 0


def cmd_serve(store, args):
    from .server import serve
    serve(store, WEB_ROOT, host=args.host, port=args.port, open_browser=args.open, verbose=args.verbose)


def cmd_publish(store, args):
    dest = args.to or os.environ.get("DEVTRACK_PUBLISH_DIR")
    if not dest:
        raise DevTrackError("배포할 디렉토리를 --to 또는 $DEVTRACK_PUBLISH_DIR 로 지정하세요")
    path = store.publish(WEB_ROOT, dest)
    _out(args, {"published": path}, "배포 완료: {}".format(path))


def cmd_demo(store, args):
    from .demo import build_demo
    build_demo(store, force=args.force)
    _out(args, {"data": store.root}, "데모 데이터 생성 완료: {}\n  devtrack --data {} serve".format(store.root, store.root))


def _choice_label_lines(labels):
    """고른 항목의 이름: ['  I1 — 격자 민감도 실험', ...]"""
    return ["  {} — {}".format(i, name) for i, name in labels or []]


def _indent(text, n=4):
    pad = " " * n
    return "\n".join(pad + line for line in str(text).splitlines())


# ---------------------------------------------------------------------------- 파서
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=argparse.SUPPRESS, metavar="DIR",
                        help="데이터 디렉토리 (기본: $DEVTRACK_DATA 또는 <저장소>/data)")
    common.add_argument("--as", dest="author", default=argparse.SUPPRESS, metavar="NAME",
                        help="기록 작성자 이름 (기본: $DEVTRACK_AGENT 또는 Claude)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="결과를 JSON 으로 출력")

    p = argparse.ArgumentParser(prog="devtrack", parents=[common],
                                description="AI 에이전트 개발 진행상황 기록 · 승인 도구")
    p.add_argument("--version", action="version", version="devtrack " + __version__)
    sub = p.add_subparsers(dest="cmd", metavar="<명령>")

    def add(name, func, help_, **kw):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_, **kw)
        sp.set_defaults(func=func)
        return sp

    add("init", cmd_init, "데이터 디렉토리 초기화")

    sp = add("new", cmd_new, "새 작업 생성 (작업 ID 출력)")
    sp.add_argument("title", help="작업 제목")
    sp.add_argument("-t", "--type", default="code", choices=list(TASK_TYPES), help="작업 유형")
    sp.add_argument("-i", "--instruction", help="사용자 지시 내용 (markdown)")
    sp.add_argument("--instruction-file", metavar="FILE", help="지시 내용을 파일에서 읽기 (- 는 stdin)")
    sp.add_argument("--repo", help="대상 저장소 경로 (git 이면 브랜치/원격 자동 감지)")
    sp.add_argument("--branch")
    sp.add_argument("--url", help="저장소 원격 URL")
    sp.add_argument("--agent", help="담당 AI 이름 (기본: 나 = $DEVTRACK_AGENT 또는 Claude, '누구나' = 먼저 시작한 AI)")
    sp.add_argument("--tag", action="append", help="태그 (여러 번 지정 가능)")
    sp.add_argument("--priority", default="normal", choices=PRIORITIES)
    sp.add_argument("--plan", nargs="+", metavar="STEP", help="초기 작업 계획 단계들")
    sp.add_argument("--related", nargs="+", metavar="T-ID", help="관련 작업 (예: 이 분석을 사용할 논문 작업)")
    sp.add_argument("--queued", action="store_true", help="사용자 지시로 '대기' 상태 생성")

    sp = add("start", cmd_start, "대기 중인 작업을 진행 중으로 전환 (다른 AI 에게 배정된 작업은 --take 로만)")
    sp.add_argument("task")
    sp.add_argument("step", nargs="?", help="시작하며 기록할 현재 단계")
    sp.add_argument("--take", action="store_true", help="다른 AI 에게 배정된 작업이라도 내가 맡기 (담당 AI 변경)")

    sp = add("step", cmd_step, "현재 진행 단계 기록 (지금 무엇을 하는지)")
    sp.add_argument("task")
    sp.add_argument("text")
    sp.add_argument("-p", "--progress", type=int, help="진행률 0-100")

    sp = add("status", cmd_status, "상태/진행률 변경")
    sp.add_argument("task")
    sp.add_argument("status", nargs="?", choices=list(STATUSES))
    sp.add_argument("-p", "--progress", type=int)
    sp.add_argument("-m", "--message", help="변경 사유")

    sp = add("link", cmd_link, "작업 연결 (예: 데이터 분석 → 논문 작업)")
    sp.add_argument("task")
    sp.add_argument("others", nargs="+", metavar="T-ID")
    sp.add_argument("--unlink", action="store_true", help="연결 해제")

    sp = add("log", cmd_log, "진행 기록 남기기")
    sp.add_argument("task")
    sp.add_argument("message")
    sp.add_argument("-k", "--kind", default="info", choices=[k for k in LOG_KINDS if k != "user"])
    sp.add_argument("-d", "--detail", help="상세 내용 (markdown)")
    sp.add_argument("--detail-file", metavar="FILE")

    sp = add("plan", cmd_plan, "작업 계획 설정")
    sp.add_argument("task")
    sp.add_argument("items", nargs="+", metavar="STEP")
    sp.add_argument("--append", action="store_true", help="기존 계획 뒤에 추가")

    sp = add("check", cmd_check, "계획 단계 완료 표시 (1부터 시작하는 번호)")
    sp.add_argument("task")
    sp.add_argument("numbers", nargs="+", type=int)
    sp.add_argument("--undo", action="store_true")

    sp = add("issue", cmd_issue, "문제점 기록 (문제 ID 출력)")
    sp.add_argument("task")
    sp.add_argument("title")
    sp.add_argument("-s", "--severity", default="medium", choices=SEVERITIES)
    sp.add_argument("-d", "--detail")
    sp.add_argument("--detail-file", metavar="FILE")

    sp = add("resolve", cmd_resolve, "문제 해결 표시")
    sp.add_argument("task")
    sp.add_argument("issue", help="문제 ID (예: I-1)")
    sp.add_argument("-m", "--message", help="해결 방법")

    sp = add("artifact", cmd_artifact, "결과물 추가 (결과물 ID 출력)")
    sp.add_argument("task")
    sp.add_argument("path", nargs="?", help="결과물 파일")
    sp.add_argument("--url", help="외부 링크 결과물 (PR, 문서 등)")
    sp.add_argument("--git-diff", metavar="REPO", help="저장소의 현재 변경사항을 diff 로 저장")
    sp.add_argument("--files", nargs="+", help="--git-diff 대상 경로 제한")
    sp.add_argument("-n", "--title")
    sp.add_argument("-k", "--kind", help="표시 형식 강제 (markdown, pdf, image, diff, code, latex, csv, ...)")
    sp.add_argument("-d", "--desc", help="설명 (markdown)")
    sp.add_argument("--desc-file", metavar="FILE")
    sp.add_argument("--review", action="store_true", help="리뷰 섹션에 표시")
    sp.add_argument("--source", action="store_true",
                    help="문서 리뷰의 검토할 원본 문서로 등록 (PDF · Word .docx · LaTeX — Word 는 웹 미리보기를 함께 만듦)")
    sp.add_argument("--no-copy", action="store_true", help="복사하지 않고 경로만 기록 (웹에서는 볼 수 없음)")
    sp.add_argument("--replace", action="store_true",
                    help="같은 이름의 결과물이 있으면 새로 추가하지 않고 새 버전으로 교체 (이전 버전 보관)")

    sp = add("review", cmd_review, "코드 · 문서 리뷰 리포트(markdown/pdf) 등록 · 검토 항목(R1, R2…) 기록 "
                                   "(같은 이름의 리포트를 다시 등록하면 새 버전으로 교체)")
    sp.add_argument("task")
    sp.add_argument("paths", nargs="*", metavar="FILE", help="리뷰 리포트 파일 (md, pdf — 여러 개 가능)")
    sp.add_argument("--pdf", action="store_true",
                    help="Markdown 리포트를 PDF 로도 만들어 함께 등록 (같은 폴더에 .pdf 생성, 헤드리스 Chrome 필요)")
    sp.add_argument("--findings", metavar="JSON",
                    help="검토 결과 JSON: {pr, verdict, summary, findings:[{id, severity, title, plain, file, line, "
                         "category, confidence, status}]} (- 는 stdin). 다시 실행하면 상태만 갱신. "
                         "문서 리뷰는 pr 대신 doc{title, kind, purpose, synopsis, ...}, checks[{area, rating, note}], "
                         "strengths[], 항목의 location · quote · suggestion")
    sp.add_argument("-n", "--title")
    sp.add_argument("-k", "--kind")
    sp.add_argument("-d", "--desc")
    sp.add_argument("--desc-file", metavar="FILE")

    sp = add("text", cmd_text, "문서의 글자 뽑기 — Word(.docx) → Markdown (표준 라이브러리), PDF → 글자 (pdftotext)")
    sp.add_argument("file", help=".docx · .pdf · .tex · .md 등")
    sp.add_argument("-o", "--output", help="저장할 파일 (없으면 표준출력)")
    sp.add_argument("--timeout", type=int, default=120, help="pdftotext 제한 시간 (초)")

    sp = add("pdf", cmd_pdf, "Markdown 문서를 PDF 로 변환 (헤드리스 Chrome/Chromium, 한글 글꼴 포함)")
    sp.add_argument("markdown", help="변환할 .md 파일")
    sp.add_argument("-o", "--output", help="PDF 경로 (기본: 같은 폴더, 같은 이름 .pdf)")
    sp.add_argument("--timeout", type=int, default=120)

    sp = add("latex", cmd_latex, "LaTeX 컴파일 → PDF 를 결과물로 기록 (실패하면 오류·로그 기록, exit 1)")
    sp.add_argument("task")
    sp.add_argument("tex", help="메인 .tex 파일")
    sp.add_argument("--engine", choices=["pdf", "xe", "lua"], default="pdf", help="pdflatex / xelatex / lualatex")
    sp.add_argument("--timeout", type=int, default=600, help="최대 컴파일 시간(초)")
    sp.add_argument("--pdf", metavar="FILE", help="컴파일하지 않고 이미 만든 PDF 를 기록 (같은 이름 .log 가 있으면 분석)")

    sp = add("request", cmd_request, "승인 요청 (웹 알림으로 표시, 승인 ID 출력)")
    sp.add_argument("task")
    sp.add_argument("title")
    sp.add_argument("-d", "--desc", help="요청 설명 (markdown)")
    sp.add_argument("--desc-file", metavar="FILE")
    sp.add_argument("-k", "--kind", choices=APPROVAL_KINDS)
    sp.add_argument("-a", "--artifact", nargs="+", metavar="F-ID", help="함께 검토할 결과물 ID")
    sp.add_argument("--choices", nargs="*", metavar="ID",
                    help="사용자가 웹에서 체크해 고를 항목. 값 없이 쓰면 검토 항목(findings) 중 열린 것 전체 "
                         "(예: PR 코멘트를 작성할 리뷰 항목). 'ID=설명' 으로 이름을 붙일 수 있음 "
                         "(예: 브레인스토밍 아이디어 \"I1=격자 민감도 실험\")")
    sp.add_argument("--choices-label", metavar="TEXT", help="선택 목록 제목 (기본: 진행할 항목 선택)")
    sp.add_argument("--commit", action="store_true", help="승인되면 commit/push 할 변경사항 (diff 자동 첨부)")
    sp.add_argument("--repo", help="커밋할 저장소 (기본: 작업의 저장소)")
    sp.add_argument("--branch", help="커밋할 브랜치 (기본: 현재 브랜치)")
    sp.add_argument("-m", "--message", help="커밋 메시지")
    sp.add_argument("--message-file", metavar="FILE")
    sp.add_argument("--files", nargs="+", help="커밋할 파일 제한 (기본: 모든 변경)")
    sp.add_argument("--no-push", action="store_true", help="커밋만 하고 푸시하지 않음")
    sp.add_argument("--remote", default="origin")

    sp = add("decide", cmd_decide, "승인 요청에 대한 사용자 결정 기록")
    sp.add_argument("task")
    sp.add_argument("approval", help="승인 ID (예: A-1)")
    sp.add_argument("decision", choices=["approve", "changes", "reject"])
    sp.add_argument("-m", "--message", help="코멘트")
    sp.add_argument("--select", nargs="+", metavar="ID", help="선택 항목이 있는 요청에서 고른 항목 (예: R1 R3)")
    sp.add_argument("--by", help="결정자 (기본: user)")

    sp = add("wait", cmd_wait, "승인 결정을 기다림 (승인 0, 수정/거절 2, 시간초과 3)")
    sp.add_argument("task")
    sp.add_argument("approval")
    sp.add_argument("--timeout", type=int, default=0, help="최대 대기 초 (0 = 무제한)")
    sp.add_argument("--interval", type=int, default=15, help="확인 간격 초")

    sp = add("apply", cmd_apply, "승인된 커밋 요청 실행 (commit + push)")
    sp.add_argument("task")
    sp.add_argument("approval")
    sp.add_argument("--force", action="store_true", help="승인 이후 변경사항이 달라져도 실행")
    sp.add_argument("--dry-run", action="store_true")

    sp = add("comment", cmd_comment, "사용자 메시지 기록 (사용자 → 에이전트)")
    sp.add_argument("task")
    sp.add_argument("message")

    sp = add("inbox", cmd_inbox, "에이전트가 처리할 사용자 입력 확인 (새 지시, 메시지, 결정)")
    sp.add_argument("--ack", action="store_true", help="확인한 메시지/결정을 처리됨으로 표시")
    sp.add_argument("--wait", action="store_true",
                    help="아직 보지 않은 입력이 생길 때까지 기다렸다가 출력 (에이전트가 백그라운드로 실행해 두면 웹 지시에 바로 반응)")
    sp.add_argument("--timeout", type=int, default=0, help="--wait 최대 대기 초 (0 = 무제한, 시간 초과 시 exit 3)")
    sp.add_argument("--interval", type=int, default=3, help="--wait 확인 간격 초")
    sp.add_argument("--task", metavar="T-ID", help="이 작업의 입력만, 담당 AI 와 상관없이 (기본: $DEVTRACK_TASK)")
    sp.add_argument("--all-agents", action="store_true", help="다른 AI 에게 배정된 작업의 입력까지 모두 보기")

    add("hook", cmd_hook, "AI 도구 실행 전 확인 (Claude Code · Codex 의 PreToolUse 훅) — 하던 작업을 사용자가 "
                          "취소 · 삭제했으면 멈추게 한다. 훅 입력(JSON)을 표준입력으로 받는다")

    sp = add("watch", cmd_watch, "AI 실행기 — 웹에서 온 입력(새 작업 · 메시지 · 결정)마다 AI 를 실행해 처리 (예: Codex)")
    sp.add_argument("--codex", action="store_true", help="Codex 로 처리 (codex exec 실행, 담당 AI 이름 Codex)")
    sp.add_argument("--agent", help="담당 AI 이름 — 이 AI 에게 배정된 작업만 처리 (기본: --codex 면 Codex, 아니면 $DEVTRACK_AGENT)")
    sp.add_argument("--cwd", metavar="DIR", help="저장소 경로가 없는 작업을 실행할 폴더 (기본: 현재 폴더)")
    sp.add_argument("--no-network", action="store_true",
                    help="--codex: 실행하는 명령의 인터넷 사용을 막음 (기본은 gh · git push 를 위해 허용)")
    sp.add_argument("--once", action="store_true", help="들어온 입력을 한 번 처리하고 끝내기")
    sp.add_argument("--timeout", type=int, default=0, help="새 입력을 기다리는 최대 초 (0 = 무제한, 시간 초과 시 exit 3)")
    sp.add_argument("--interval", type=int, default=3, help="확인 간격 초")
    sp.add_argument("command", nargs=argparse.REMAINDER, metavar="-- 명령",
                    help="실행할 AI 명령 (프롬프트를 마지막 인자로 붙여 실행). --codex 와 함께 쓰면 codex exec 에 더할 옵션")

    sp = add("cancel", cmd_cancel, "작업 취소 (기록은 남김, 대기 중인 승인 요청은 철회)")
    sp.add_argument("task")
    sp.add_argument("-m", "--message", help="취소 사유")

    sp = add("delete", cmd_delete, "작업 삭제 — 기록과 결과물 파일을 완전히 지움 (되돌릴 수 없음, 작업 ID 를 다시 입력해 확인)")
    sp.add_argument("task")
    sp.add_argument("--yes", action="store_true", help="확인 질문 없이 삭제 (사용자가 이미 확인한 경우에만)")

    sp = add("done", cmd_done, "작업 완료")
    sp.add_argument("task")
    sp.add_argument("-m", "--message", help="완료 요약 (--by-user 이면 사용자의 메모)")
    sp.add_argument("--by-user", action="store_true",
                    help="사용자가 끝나지 않은 작업을 완료로 처리한 것을 기록 ('[DevTrack 작업 완료]' 메시지) — "
                         "결정을 기다리던 승인 요청은 철회")

    sp = add("show", cmd_show, "작업 상세 보기")
    sp.add_argument("task")
    sp.add_argument("-n", "--lines", type=int, default=10, help="최근 기록 줄 수")

    sp = add("list", cmd_list, "작업 목록")
    sp.add_argument("--status", choices=list(STATUSES))
    sp.add_argument("--all", action="store_true", help="완료/취소 작업 포함")

    sp = add("serve", cmd_serve, "로컬 웹 서버 실행 (웹에서 승인/피드백/작업 지시 가능)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true", help="브라우저 열기")
    sp.add_argument("-v", "--verbose", action="store_true", help="요청 로그 출력")

    sp = add("publish", cmd_publish, "정적 웹사이트 배포 (예: 웹 서버의 공개 폴더)")
    sp.add_argument("--to", metavar="DIR", help="배포 디렉토리 (기본: $DEVTRACK_PUBLISH_DIR)")

    sp = add("demo", cmd_demo, "예시 데이터 생성")
    sp.add_argument("--force", action="store_true", help="기존 데이터 디렉토리를 비우고 생성")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    args.data = getattr(args, "data", None) or default_data_dir()
    args.author = getattr(args, "author", None) or os.environ.get("DEVTRACK_AGENT") or "Claude"
    args.json = getattr(args, "json", False)
    publish_dir = os.environ.get("DEVTRACK_PUBLISH_DIR") if args.cmd not in ("publish", "demo") else None
    store = Store(args.data, publish_dir=publish_dir)
    try:
        if args.cmd in TASK_WORK_CMDS:
            _guard_task(store, args)
        rc = args.func(store, args)
        return rc or 0
    except DevTrackError as e:
        _info("오류: {}".format(e))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
