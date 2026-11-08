"""devtrack 명령행 도구 — AI 에이전트가 진행상황을 기록하고, 승인 결과를 받아 처리한다.

생성 명령(new / issue / artifact / review / request)은 표준출력에 새 ID 만 출력하므로
``TID=$(devtrack new "...")`` 처럼 셸에서 바로 받아 쓸 수 있다.
"""

import argparse
import json
import os
import sys
import time

from . import DevTrackError, __version__, gitops
from .store import (
    APPROVAL_KINDS, LOG_KINDS, PRIORITIES, SEVERITIES, STATUSES, TASK_TYPES, USER,
    Store, normalize_item_id, normalize_task_id,
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
    _info("새 작업 생성: {} — {}".format(t["id"], t["title"]))
    _out(args, t, t["id"])


def cmd_start(store, args):
    t = store.update_task(args.task, status="in_progress", step=args.step, author=args.author)
    _out(args, t, "{} 진행 중".format(t["id"]))


def cmd_step(store, args):
    t = store.load(args.task)
    status = "in_progress" if t["status"] in ("queued", "changes_requested", "blocked") else None
    t = store.update_task(args.task, status=status, step=args.text, progress=args.progress, author=args.author)
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
    category = category or ("review" if getattr(args, "review", False) else "result")
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
                                 copy=not getattr(args, "no_copy", False), **common)
    else:
        raise DevTrackError("파일 경로, --url, --git-diff 중 하나를 지정하세요")
    _info("결과물 추가: {} — {} ({})".format(art["id"], art["title"], art["kind"]))
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
    paths = list(args.paths)
    if args.pdf:
        from . import pdf as pdfmod
        have = {os.path.splitext(os.path.abspath(p))[0] for p in paths if p.lower().endswith(".pdf")}
        for p in list(paths):
            if os.path.splitext(p)[1].lower() not in (".md", ".markdown"):
                continue
            if os.path.splitext(os.path.abspath(p))[0] in have:
                continue
            try:
                out = pdfmod.markdown_to_pdf(p)
                _info("PDF 변환: {}".format(out))
                paths.insert(paths.index(p) + 1, out)
            except DevTrackError as e:
                _info("경고: {}".format(e))
                store.add_log(args.task, "리뷰 리포트 PDF 변환 실패", kind="warning", detail=str(e), author=args.author)
    arts = []
    for i, path in enumerate(paths):
        title = args.title
        if title and i:
            title = "{} ({})".format(title, os.path.splitext(path)[1].lstrip(".").upper() or i + 1)
        art = store.add_artifact(args.task, path=path, kind=args.kind, title=title,
                                 description=desc if i == 0 else None, category="review", author=args.author)
        _info("리뷰 리포트 등록: {} — {} ({})".format(art["id"], art["title"], art["kind"]))
        arts.append(art)
    _out(args, arts, "\n".join(a["id"] for a in arts))
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


def _print_inbox(items):
    for it in items:
        if it["type"] == "new_task":
            print("[새 작업] {task_id} {title} ({task_type})".format(**it))
            if it.get("instruction"):
                print(_indent(it["instruction"]))
            print(_indent("→ devtrack start {task_id}".format(**it)))
        elif it["type"] == "comment":
            print("[메시지] {task_id} {id}: {message}".format(**it))
        elif it["type"] == "cancelled":
            print("[취소] {task_id} {title} — 사용자가 작업을 취소했습니다. 이 작업은 중단하세요.".format(**it))
            if it.get("reason"):
                print(_indent("사유: " + it["reason"]))
        elif it["type"] == "deleted":
            print("[삭제] {task_id} {title} — 사용자가 작업을 삭제했습니다. 이 작업은 중단하고 더 기록하지 마세요.".format(**it))
        else:
            print("[결정] {task_id} {id} '{approval_title}' → {decision}".format(**it))
            if it.get("selected"):
                print(_indent("선택: " + ", ".join(it["selected"])))
            if it.get("comment"):
                print(_indent("코멘트: " + it["comment"]))
            if it["decision"] == "approved" and it.get("has_action"):
                print(_indent("→ devtrack apply {task_id} {id}".format(**it)))


def _inbox_wait(store, args):
    """아직 보지 않은 사용자 입력이 생길 때까지 기다린다 (백그라운드 실행용)."""
    if args.ack:
        raise DevTrackError("--wait 와 --ack 는 함께 쓸 수 없습니다 (처리한 뒤 devtrack inbox --ack)")
    deadline = time.time() + args.timeout if args.timeout else None
    _info("새 사용자 입력을 기다리는 중 (웹의 새 작업 지시 · 메시지 · 승인 결정) — 중단: Ctrl+C")
    last = object()
    while True:
        token = store.change_token()
        if token != last:
            last = token
            items = store.inbox()
            seen = store.inbox_seen(args.author)
            new = [it for it in items if store.inbox_key(it) not in seen]
            if new:
                store.mark_inbox_seen(args.author, items)
                if args.json:
                    _out(args, new)
                    return 0
                print("새 사용자 입력 {}건:".format(len(new)))
                _print_inbox(new)
                print("\n처리한 뒤 다시 `devtrack inbox --wait` 를 백그라운드로 실행해 다음 입력을 기다리세요.")
                return 0
        if deadline and time.time() >= deadline:
            _out(args, [])
            _info("시간 초과: 새 입력이 없습니다")
            return 3
        time.sleep(max(1, args.interval))


def cmd_inbox(store, args):
    if args.wait:
        return _inbox_wait(store, args)
    items = store.inbox(ack=args.ack)
    store.mark_inbox_seen(args.author, items)
    if args.json:
        _out(args, items)
        return 0
    if not items:
        print("처리할 사용자 입력이 없습니다.")
        return 0
    _print_inbox(items)
    return 0


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
    if rv.get("findings"):
        pr = rv.get("pr") or {}
        print("  리뷰 결과{}{}:".format(" — PR #{}".format(pr["number"]) if pr.get("number") else "",
                                    " · 의견 {}".format(rv["verdict"]) if rv.get("verdict") else ""))
        for f in rv["findings"]:
            loc = "{}{}".format(f.get("file", ""), ":{}".format(f["line"]) if f.get("line") else "")
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
            print("    {} [{}{}] {}".format(a["id"], a["kind"], ", 리뷰" if a.get("category") == "review" else "",
                                         a["title"]))
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
    sp.add_argument("--agent", help="작업 수행 에이전트 이름")
    sp.add_argument("--tag", action="append", help="태그 (여러 번 지정 가능)")
    sp.add_argument("--priority", default="normal", choices=PRIORITIES)
    sp.add_argument("--plan", nargs="+", metavar="STEP", help="초기 작업 계획 단계들")
    sp.add_argument("--related", nargs="+", metavar="T-ID", help="관련 작업 (예: 이 분석을 사용할 논문 작업)")
    sp.add_argument("--queued", action="store_true", help="사용자 지시로 '대기' 상태 생성")

    sp = add("start", cmd_start, "대기 중인 작업을 진행 중으로 전환")
    sp.add_argument("task")
    sp.add_argument("step", nargs="?", help="시작하며 기록할 현재 단계")

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
    sp.add_argument("--no-copy", action="store_true", help="복사하지 않고 경로만 기록 (웹에서는 볼 수 없음)")

    sp = add("review", cmd_review, "코드 리뷰 리포트(markdown/pdf) 등록 · 검토 항목(R1, R2…) 기록")
    sp.add_argument("task")
    sp.add_argument("paths", nargs="*", metavar="FILE", help="리뷰 리포트 파일 (md, pdf — 여러 개 가능)")
    sp.add_argument("--pdf", action="store_true",
                    help="Markdown 리포트를 PDF 로도 만들어 함께 등록 (같은 폴더에 .pdf 생성, 헤드리스 Chrome 필요)")
    sp.add_argument("--findings", metavar="JSON",
                    help="검토 결과 JSON: {pr, verdict, summary, findings:[{id, severity, title, plain, file, line, "
                         "category, confidence, status}]} (- 는 stdin). 다시 실행하면 상태만 갱신")
    sp.add_argument("-n", "--title")
    sp.add_argument("-k", "--kind")
    sp.add_argument("-d", "--desc")
    sp.add_argument("--desc-file", metavar="FILE")

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
                         "(예: PR 코멘트를 작성할 리뷰 항목)")
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

    sp = add("cancel", cmd_cancel, "작업 취소 (기록은 남김, 대기 중인 승인 요청은 철회)")
    sp.add_argument("task")
    sp.add_argument("-m", "--message", help="취소 사유")

    sp = add("delete", cmd_delete, "작업 삭제 — 기록과 결과물 파일을 완전히 지움 (되돌릴 수 없음, 작업 ID 를 다시 입력해 확인)")
    sp.add_argument("task")
    sp.add_argument("--yes", action="store_true", help="확인 질문 없이 삭제 (사용자가 이미 확인한 경우에만)")

    sp = add("done", cmd_done, "작업 완료")
    sp.add_argument("task")
    sp.add_argument("-m", "--message", help="완료 요약")

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

    sp = add("publish", cmd_publish, "정적 웹사이트 배포 (예: Frontier www 디렉토리)")
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
        rc = args.func(store, args)
        return rc or 0
    except DevTrackError as e:
        _info("오류: {}".format(e))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
