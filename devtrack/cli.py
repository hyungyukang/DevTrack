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
import threading
import time
import uuid

from . import DevTrackError, __version__, access, gitops, mailbox, models
from .store import (
    APPROVAL_KINDS, DOC_KINDS, DOC_VERDICT_LABEL, LOG_KINDS, PRIORITIES, SEVERITIES, STATUSES, TASK_TYPES, USER,
    REVIEW_ROUNDS_DEFAULT, REVIEW_ROUNDS_MAX, Store, agent_key, is_unassigned, normalize_item_id, normalize_task_id,
    NOTE_SOFT, NOTE_STALE,
    pid_alive,
    now as now_iso, _choice_labels,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_ROOT = os.path.join(REPO_ROOT, "web")


# ---------------------------------------------------------------------------- 기록 위치 · 설정 파일
# 기록 폴더 · 웹 공개 폴더는 --data > 환경 변수 > 설정 파일 > 기본값 순서로 정한다. 환경 변수는 그것을 정한 셸에서 켠
# 프로세스에만 있으므로, 먼저 켠 serve · watch, 다른 터미널 · tmux, VS Code 의 AI · 훅은 모를 수 있다 — 설정 파일은 모두에 적용된다
CONFIG_KEYS = {"data": ("DEVTRACK_DATA", "기록 폴더"), "publish": ("DEVTRACK_PUBLISH_DIR", "웹 공개 폴더")}
SRC_CONFIG = "설정 파일"
SRC_DEFAULT = "기본값"


def config_path():
    """설정 파일: $XDG_CONFIG_HOME/devtrack/config.json (기본 ~/.config/devtrack/config.json)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "devtrack", "config.json")


def load_config():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _save_config(cfg):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _repo_data():
    return os.path.join(REPO_ROOT, "data")


def _real(path):
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def data_location(explicit=None):
    """기록 폴더와 그것을 정한 곳: (경로, '--data' · '$DEVTRACK_DATA' · '설정 파일' · '기본값')."""
    if explicit:
        return explicit, "--data"
    if os.environ.get("DEVTRACK_DATA"):
        return os.environ["DEVTRACK_DATA"], "$DEVTRACK_DATA"
    saved = load_config().get("data")
    if saved:
        return saved, SRC_CONFIG
    return _repo_data(), SRC_DEFAULT


def default_data_dir():
    return data_location()[0]


def publish_location(data_root):
    """웹 공개 폴더와 정한 곳 ((None, None) = 없음). 설정 파일의 값은 설정한 기록 폴더(없으면 기본 폴더)를 쓸 때만
    — --data 로 연 예시 · 시험 기록이 실제 공개 폴더로 나가지 않게."""
    if os.environ.get("DEVTRACK_PUBLISH_DIR"):
        return os.environ["DEVTRACK_PUBLISH_DIR"], "$DEVTRACK_PUBLISH_DIR"
    cfg = load_config()
    if cfg.get("publish") and _real(data_root) == _real(cfg.get("data") or _repo_data()):
        return cfg["publish"], SRC_CONFIG
    return None, None


def _store_summary(root):
    """기록 폴더의 작업 수 · 마지막 기록 시각. 작업이 없으면 None."""
    tasks = os.path.join(root, "tasks")
    try:
        names = [n for n in os.listdir(tasks) if re.match(r"^T-\d{4,}$", n)]
    except OSError:
        return None
    times = []
    for n in names:
        try:
            times.append(os.path.getmtime(os.path.join(tasks, n, "task.json")))
        except OSError:
            pass
    return {"tasks": len(times), "updated": max(times)} if times else None


def other_data_dirs(root):
    """지금 쓰는 곳 말고 작업이 있는 기록 폴더 — 환경 변수 · 설정 파일 · 기본값이 가리키는 곳 중에서.
    [{"path", "source", "tasks", "updated"}]"""
    seen = {_real(root)}
    out = []
    for path, src in ((os.environ.get("DEVTRACK_DATA"), "$DEVTRACK_DATA"), (load_config().get("data"), SRC_CONFIG),
                      (_repo_data(), SRC_DEFAULT)):
        if not path or _real(path) in seen:
            continue
        seen.add(_real(path))
        info = _store_summary(_real(path))
        if info:
            out.append(dict(info, path=_real(path), source=src))
    return out


def _clock(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _other_lines(others):
    return ["    {} ({}) — 작업 {}개, 마지막 기록 {}".format(o["path"], o["source"], o["tasks"], _clock(o["updated"]))
            for o in others]


def _warn_other_data(store, args):
    """다른 기록 폴더에도 작업이 있으면 알린다 — 기록 폴더를 모르는 프로세스가 따로 쓰고 있을 수 있다.
    --data 로 일부러 고른 폴더(예시 · 시험)에서는 알리지 않는다."""
    if getattr(args, "data_from", None) == "--data":
        return
    try:
        others = other_data_dirs(store.root)
    except OSError:
        return
    if not others:
        return
    _info("⚠ 다른 기록 폴더에도 작업이 있습니다 (지금 쓰는 곳: {} — {})".format(store.root, args.data_from))
    for line in _other_lines(others):
        _info(line)
    _info("  기록 폴더를 모르는 프로세스가 따로 쓰고 있을 수 있습니다 — devtrack config 로 확인하세요")


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
    _out(args, {"data": store.root}, "데이터 디렉토리 준비 완료: {} ({})".format(store.root, args.data_from))
    sys.stdout.flush()
    _warn_other_data(store, args)


def _config_show(store, args):
    cfg = load_config()
    pub, pub_from = publish_location(store.root)
    others = other_data_dirs(store.root)
    obj = {"data": {"path": store.root, "source": args.data_from},
           "publish": {"path": pub and os.path.abspath(os.path.expanduser(pub)), "source": pub_from},
           "config": config_path(), "saved": cfg, "others": others}
    if args.json:
        return _out(args, obj)
    lines = ["기록 폴더:    {} ({})".format(store.root, args.data_from),
             "웹 공개 폴더: {}".format("{} ({})".format(obj["publish"]["path"], pub_from) if pub else "없음"),
             "설정 파일:    {}{}".format(config_path(), "" if os.path.exists(config_path()) else " (아직 없음)")]
    for key, (env, label) in sorted(CONFIG_KEYS.items()):
        if os.environ.get(env) and cfg.get(key) and _real(os.environ[env]) != _real(cfg[key]):
            lines.append("⚠ 이 셸의 ${} 가 설정 파일의 {}({})보다 먼저 쓰입니다 — 이 변수가 없는 프로세스는 설정 파일 쪽을 "
                         "씁니다. 셸 설정(~/.bashrc 등)에서 그 줄을 지우거나 같은 경로로 맞추세요".format(env, label, cfg[key]))
    if others:
        lines.append("⚠ 다른 기록 폴더에도 작업이 있습니다:")
        lines += _other_lines(others)
        lines.append("  기록 폴더를 모르는 프로세스(먼저 켠 serve · watch, 다른 터미널 · tmux, VS Code 의 AI · 훅 등)가 "
                     "따로 쓴 기록일 수 있습니다.")
        lines.append("  내용 보기: devtrack --data <그 폴더> list")
    if args.data_from not in (SRC_CONFIG, "--data") and not cfg.get("data") and (
            others or args.data_from == "$DEVTRACK_DATA"):
        lines.append("모든 프로세스가 같은 곳을 쓰게 하려면: devtrack config data {}".format(shlex.quote(store.root)))
    print("\n".join(lines))


def cmd_config(store, args):
    if not args.key:
        if args.value or args.unset:
            raise DevTrackError("정할 항목을 쓰세요: devtrack config data <폴더>   ·   devtrack config publish <폴더>")
        return _config_show(store, args)
    env, label = CONFIG_KEYS[args.key]
    cfg = load_config()
    if args.unset:
        if args.value:
            raise DevTrackError("--unset 과 값은 함께 쓸 수 없습니다")
        had = cfg.pop(args.key, None)
        _save_config(cfg)
        return _out(args, {"key": args.key, "value": None, "config": config_path()},
                    "{} 설정을 지웠습니다{}".format(label, "" if had else " (원래 없었음)"))
    if not args.value:
        return _out(args, {"key": args.key, "value": cfg.get(args.key), "config": config_path()},
                    cfg.get(args.key) or "(설정 없음)")
    path = os.path.abspath(os.path.expanduser(args.value))
    if args.key == "data":
        if os.path.exists(path) and not os.path.isdir(path):
            raise DevTrackError("폴더가 아닙니다: {}".format(path))
        Store(path).ensure()
    cfg[args.key] = path
    _save_config(cfg)
    _out(args, {"key": args.key, "value": path, "config": config_path()},
         "{} = {} (저장: {})".format(label, path, config_path()))
    sys.stdout.flush()
    if not args.json:
        if os.environ.get(env) and _real(os.environ[env]) != _real(path):
            _info("⚠ 이 셸의 ${}({}) 가 이 설정보다 먼저 쓰입니다 — 셸 설정(~/.bashrc 등)에서 그 줄을 지우거나 같은 경로로 "
                  "맞추세요".format(env, os.environ[env]))
        if args.key == "data":
            others = [o for o in other_data_dirs(path) if o["source"] != SRC_CONFIG]
            if others:
                _info("다른 기록 폴더에도 작업이 있습니다 — 필요하면 옮기세요:")
                for line in _other_lines(others):
                    _info(line)
        _info("이미 켜 둔 devtrack serve · watch · 대기 모드는 다시 켜야 새 위치를 씁니다")


def cmd_new(store, args):
    queued = args.queued
    t = store.create_task(
        args.title,
        instruction=_text(args.instruction, args.instruction_file) or "",
        type=args.type, agent=args.agent or args.author, repo=args.repo, branch=args.branch,
        repo_url=args.url, tags=args.tag, priority=args.priority,
        status="queued" if queued else "in_progress", created_by=USER if queued else None, plan=args.plan,
        related=args.related, model=args.model, effort=args.effort,
        review={"reviewer": args.review_by, "rounds": args.review_rounds, "model": args.review_model,
                "effort": args.review_effort, "doc": args.review_doc, "venue": args.review_venue}
        if args.review_by else None,
    )
    if not queued:  # AI 가 스스로 만들어 진행하는 작업 — 이 세션이 맡은 작업으로 적어 둔다
        _note_session(store, t["id"])
    _info("새 작업 생성: {} — {}".format(t["id"], t["title"]))
    _out(args, t, t["id"])


def _check_model(store, tid, args):
    """모델 · 강도가 지정된 작업을 다른 모델의 세션이 시작하지 않게 한다 (세션 모델을 모르면 알리기만)."""
    t = store.load(tid)
    want = models.spec_label(*models.task_spec(t))
    if not want or getattr(args, "any_model", False):
        return
    have = models.session_model()
    ok = models.spec_matches(t, *have)
    if ok is False:
        raise DevTrackError(
            "{0} 은(는) {1} 로 지정된 작업입니다 — 이 세션({2})에서는 시작하지 않습니다. 지정한 모델의 세션이나 "
            "devtrack watch 가 처리합니다. 이 세션에서 하려면 사용자가 /model · /effort 로 맞추거나 지정을 바꿔야 합니다 "
            "(devtrack model {0} …). 사용자가 그래도 이 세션에서 하라고 하면 --any-model".format(
                t["id"], want, models.spec_label(*have) or "모델 알 수 없음"))
    if ok is None:
        _info("참고: {} 은(는) {} 로 지정된 작업입니다 — 이 세션의 모델 · 강도를 확인하지 못했습니다. "
              "지정과 다르면 진행하지 마세요".format(t["id"], want))


def cmd_start(store, args):
    _check_model(store, args.task, args)
    t = store.update_task(args.task, status="in_progress", step=args.step, author=args.author,
                          claim=args.author, take=args.take)
    _out(args, t, "{} 진행 중".format(t["id"]))
    sys.stdout.flush()
    if not args.json:
        for line in _related_note_lines(store, t):
            _info(line)


def cmd_step(store, args):
    t = store.load(args.task)
    status = "in_progress" if t["status"] in ("queued", "changes_requested", "blocked") else None
    if t["status"] == "queued":
        _check_model(store, args.task, args)
    t = store.update_task(args.task, status=status, step=args.text, progress=args.progress, author=args.author,
                          claim=args.author if status else None, claim_queued_only=True)
    _out(args, t, "{} [{}%] {}".format(t["id"], t.get("progress", 0), args.text))


def cmd_status(store, args):
    if not (args.status or args.progress is not None or args.message):
        raise DevTrackError("변경할 상태, --progress, -m 중 하나 이상을 지정하세요")
    t = store.update_task(args.task, status=args.status, progress=args.progress, message=args.message,
                          author=args.author)
    _out(args, t, "{} {} {}%".format(t["id"], STATUSES[t["status"]], t.get("progress", 0)))


def cmd_model(store, args):
    """작업을 처리할 AI 모델 · 추론 강도 보기 / 정하기 / 해제."""
    if args.list:
        cat = models.catalog()
        lines = []
        for title, key in (("Claude (claude --model)", "claude"),
                           ("Codex (codex -m) — {}".format(
                               models.codex_cache_path() if cat["codex_source"] == "cache" else "기본 목록 (Codex 의 모델 목록 파일 없음)"),
                            "codex")):
            lines.append(title)
            wid = max([len(m["id"]) for m in cat[key]] or [0])
            wlabel = max([len(m["label"]) for m in cat[key]] or [0])
            lines += ["  {}  {}  강도: {}".format(m["id"].ljust(wid), m["label"].ljust(wlabel), ", ".join(m["efforts"]) or "-")
                      for m in cat[key]]
        lines.append("목록에 없는 모델도 이름을 그대로 적으면 됩니다 (예: devtrack model T-0001 <모델> --effort high)")
        _out(args, cat, "\n".join(lines))
        return
    if not args.task:
        raise DevTrackError("작업 ID 를 적어 주세요 (예: devtrack model T-0001 opus-5.5 --effort xhigh), 목록은 --list")
    author = USER if args.by_user else args.author
    if args.clear:
        if args.model or args.effort:
            raise DevTrackError("--clear 와 모델 · 강도를 함께 지정할 수 없습니다")
        t = store.set_model(args.task, None, None, author=author)
    elif args.model is None and args.effort is None:
        t = store.load(args.task)
    else:
        t = store.load(args.task)
        t = store.set_model(args.task, t.get("model") if args.model is None else args.model,
                            t.get("effort") if args.effort is None else args.effort, author=author)
    label = models.spec_label(*models.task_spec(t))
    _out(args, {"id": t["id"], "model": t.get("model"), "effort": t.get("effort")},
         "{} 모델: {}".format(t["id"], label or "지정 안 함 (어떤 모델이든)"))


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
    for st in store.paper_status.values():  # 논문 원고 · 그림이면 원고 폴더를 다시 만들었다 (.tex 가 아직 없으면 조용히)
        has_tex = any(f.lower().endswith(".tex") for f in st["files"])
        for line in _paper_lines(st, brief=True) if has_tex else []:
            _info("  " + line)
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


_PAPER_MISSING = (
    ("ext", "확장자를 빼야 할 그림", "같은 이름의 다른 형식만 있습니다 — \\includegraphics{fig_name} 처럼 확장자 없이 부르세요"),
    ("format", "LaTeX 가 바로 쓰지 못하는 형식의 그림", "SVG · GIF 등은 \\includegraphics 로 부를 수 없습니다 — PDF 나 PNG 로 만들어 같은 이름으로 올리세요"),
    ("path", "LaTeX 가 찾지 못하는 그림", "\\graphicspath{{figures/}} 를 넣거나 그림 폴더 이름을 맞추세요"),
    ("none", "그림 결과물이 없는 그림", "같은 파일 이름(확장자 무관)으로 그림을 결과물로 올리세요 — devtrack artifact <TID> figures/<이름>.png"),
)


def _paper_lines(st, brief=False):
    """원고 폴더 상태 → 출력 줄. brief: 결과물을 올린 뒤 한 줄 요약."""
    rel = os.path.relpath(st["dir"], os.path.dirname(os.path.dirname(os.path.dirname(st["dir"]))))
    tex = [f for f in st["files"] if f.lower().endswith(".tex")]
    if brief:
        miss = len(st["missing"]) + len(st.get("missing_bib") or [])
        return ["원고 폴더 갱신: {} — 원고 {}개 · 그림 {}/{}개{}".format(
            rel, len(tex), len(st["figures"]), len(st["refs"]),
            " · 확인할 것 {}개 (devtrack paper {})".format(miss, st["task"]) if miss else "")]
    lines = ["원고 폴더: {}".format(st["dir"])]
    if not st["files"]:
        lines.append("  아직 원고(.tex · .bib) 결과물이 없습니다 — 원고를 결과물로 올리면 만들어집니다")
        return lines
    lines.append("  원고: " + " · ".join(st["files"]))
    folders = sorted({os.path.dirname(f) for f in st["figures"]})
    lines.append("  그림: {}개 (원고가 부르는 그림 {}개{})".format(
        len(st["figures"]), len(st["refs"]), " · 폴더 " + ", ".join(d + "/" for d in folders) if folders else ""))
    if st.get("missing_bib"):
        lines.append("  참고문헌 파일 없음: {} — 원고가 \\bibliography 로 부릅니다. 그 이름의 .bib 를 결과물로 올리세요".format(
            ", ".join(st["missing_bib"])))
    if tex and not st.get("graphicspath") and st["refs"]:
        lines.append("  참고: \\graphicspath{{figures/}} 가 없습니다 — 그림은 figures/ 에 두고 확장자 없이 부르는 것을 권합니다")
    for key, label, hint in _PAPER_MISSING:
        items = [m for m in st["missing"] if m["reason"] == key]
        if not items:
            continue
        shown = ", ".join("{}{}".format(m["ref"], " → " + ", ".join(m["found"]) if m["found"] else "") for m in items[:6])
        lines.append("  {} {}개: {}{}".format(label, len(items), shown, " 외 {}개".format(len(items) - 6) if len(items) > 6 else ""))
        lines.append("    " + hint)
    return lines


def cmd_paper(store, args):
    st = store.rebuild_paper(args.task)
    _out(args, st, "\n".join(_paper_lines(st)))
    return 0


def cmd_request(store, args):
    commit = None
    if args.commit:
        commit = {"repo": args.repo, "branch": args.branch, "message": _text(args.message, args.message_file),
                  "files": args.files, "push": not args.no_push, "remote": args.remote}
    ap = store.request_approval(args.task, args.title, description=_text(args.desc, args.desc_file) or "",
                                kind=args.kind, artifact_ids=args.artifact, commit=commit, author=args.author,
                                choices=args.choices, choices_label=args.choices_label,
                                skip_review=getattr(args, "skip_review", False))
    excluded = (ap.get("action") or {}).get("excluded")
    if excluded:
        _info("빌드 결과물은 diff · 커밋에서 뺐습니다: " + gitops.excluded_text(excluded))
    _info("승인 요청 등록: {} {} — 웹에 알림으로 표시됩니다".format(normalize_task_id(args.task), ap["id"]))
    _note_reminder(store, args.task)
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


def cmd_send(store, args):
    e = store.send_agent_message(args.task, _text(args.message, args.message_file), author=args.author)
    _info("{} 에 AI 메시지 {} 를 보냈습니다 — 그 작업의 AI 가 받은 편지함에서 받습니다".format(
        normalize_task_id(args.task), e["id"]))
    _out(args, e, e["id"])


def cmd_review_request(store, args):
    r = store.review_request(args.task, reviewer=args.reviewer, rounds=args.rounds, model=args.model,
                             effort=args.effort, note=_text(args.message, None) or "", files=args.files,
                             author=args.author, same_session=args.same_session, doc=args.doc, venue=args.venue,
                             response=args.response)
    if r.get("kind") == "doc":
        lines = ["{}차 심사 요청 → {} ({}{}) · 최대 {}번".format(r["round"], r["reviewer"], r["task"],
                                                         " 새 심사 작업" if r["created"] else "", r["rounds"]),
                 "  원고: {}{}".format(", ".join(x for x in (r.get("file"), r.get("manuscript")) if x),
                                     " · 지난 심사 이후 바뀐 부분 {} (+{} −{})".format(
                                         r["since"], r["stat"]["additions"], r["stat"]["deletions"]) if r.get("since") else ""),
                 ]
        if r.get("response"):
            lines.append("  답변서: {}{}".format(r["response"], " (답한 항목 {})".format(" · ".join(r["answered"]))
                                                 if r.get("answered") else ""))
        if r.get("record"):
            lines.append("  심사 · 답변 기록: {} (논문 작업 {} 의 결과물 — 차례마다 새 버전)".format(
                r["record"], normalize_task_id(args.task)))
        fg = r.get("figures") or {}
        if fg.get("count"):
            chg = ", ".join("{} {}".format(label, len(fg[k])) for k, label in (("changed", "바뀜"), ("new", "새 그림"),
                                                                             ("removed", "빠짐")) if fg.get(k))
            lines.append("  그림: {}개 붙임{}".format(fg["count"], " ({})".format(chg) if chg else ""))
        if fg.get("missing"):
            lines.append("  찾지 못한 그림 {}개 (심사에 빠짐): {}".format(len(fg["missing"]), ", ".join(fg["missing"][:8])))
        lines.append("  판정은 {} 에 [AI 메시지] 로 옵니다 (심사위원 AI 가 켜져 있어야 함)".format(normalize_task_id(args.task)))
        _out(args, r, "\n".join(lines))
        return
    lines = ["{}차 리뷰 요청 → {} ({}{}) · 최대 {}번".format(r["round"], r["reviewer"], r["task"],
                                                     " 새 리뷰 작업" if r["created"] else "", r["rounds"]),
             "  변경: 파일 {files}개 +{additions} −{deletions}".format(**r["stat"])]
    if r["excluded"]:
        lines.append("  빌드 결과물 제외: " + gitops.excluded_text(r["excluded"]))
    runner = "codex" if agent_key(r["reviewer"]) == "codex" else "claude"
    lines.append("  결과는 {} 에 [AI 메시지] 로 옵니다 (리뷰어가 켜져 있어야 함: devtrack watch --{} · 또는 그 AI 의 대기 모드)".format(
        normalize_task_id(args.task), runner))
    if agent_key(r["reviewer"]) == agent_key(store.load(args.task).get("agent")):
        lines.append("  같은 AI 라도 리뷰는 이 대화가 아닌 다른 대화가 합니다 — devtrack watch --{} 가 새 대화로 처리합니다".format(runner))
    _out(args, r, "\n".join(lines))


def cmd_review_done(store, args):
    r = store.review_done(args.task, verdict=args.verdict, summary=args.message or "", author=args.author)
    t = store.load(args.task)
    from .store import JOURNAL_VERDICT_LABEL
    label = {"approve": "통과", "request_changes": "수정 필요"}.get(r["verdict"]) or JOURNAL_VERDICT_LABEL.get(r["verdict"], r["verdict"])
    _out(args, r, "{}차 결과 → {}: {}{}".format(
        r["round"], (t.get("review_of") or {}).get("task"), label,
        " (열린 항목 {})".format(", ".join(r["open"])) if r["open"] else ""))


def cmd_comment(store, args):
    e = store.add_user_comment(args.task, args.message, artifact=args.artifact)
    _out(args, e, e["id"])


def _comment_artifact_lines(it):
    """결과물 화면에서 보낸 메시지: 어느 결과물(버전)에 대한 것인지와, 고친 결과물을 같은 결과물의 새 버전으로 올리는 방법."""
    fid, tid = it["artifact"], it["task_id"]
    if it.get("artifact_missing"):
        return [_indent("→ 결과물 {} 은(는) 지워졌습니다".format(fid))]
    out = []
    if it.get("artifact_file"):
        out.append(_indent("→ 결과물 파일 (지금 v{}): {}".format(it.get("artifact_latest") or 1, it["artifact_file"])))
    elif it.get("artifact_url"):
        out.append(_indent("→ 결과물 주소: " + it["artifact_url"]))
    name = it.get("artifact_filename")
    if name and it.get("artifact_file"):
        how = ("devtrack review {} <고친 {}>".format(tid, name) if it.get("artifact_category") == "review" else
               "devtrack artifact {} <고친 {}> --replace".format(tid, name))
        out.append(_indent("  고친 결과물은 같은 파일 이름으로 올리면 {} 의 새 버전이 됩니다: {}".format(fid, how)))
    return out


def _sent_lines(it):
    """함께 보낸 파일 (GitHub 우편함) — AI 가 바로 열 수 있게 파일 경로와, Word · PDF 는 글자 뽑는 명령."""
    out = []
    for a in it.get("attachments") or []:
        name = a.get("filename") or ""
        how = ""
        if os.path.splitext(name)[1].lower() in (".docx", ".pdf"):
            how = " — 글자: devtrack text {}".format(shlex.quote(a["file"]))
        out.append(_indent("→ 보낸 파일 {} {}: {}{}".format(a["id"], name, a.get("file") or "(파일 없음)", how)))
    return out


def _format_inbox(items):
    out = []
    for it in items:
        if it["type"] == "new_task":
            who = "담당 미정 — 먼저 시작한 AI 가 맡음" if is_unassigned(it.get("agent")) else "담당 " + it["agent"]
            spec = models.spec_label(it.get("model"), it.get("effort"))
            if spec:
                who += " · 모델 " + spec
            rv = it.get("review")
            doc = bool(rv and rv.get("kind") == "doc")
            if rv:
                rspec = models.spec_label(rv.get("model"), rv.get("effort"))
                who += " · 끝나면 {}{} {} (최대 {}번)".format(rv["reviewer"], " " + rspec if rspec else "",
                                                        "심사" if doc else "리뷰", rv["rounds"])
            out.append("[새 작업] {} {} ({} · {})".format(it["task_id"], it["title"], it["task_type"], who))
            if it.get("instruction"):
                out.append(_indent(it["instruction"]))
            out.extend(_sent_lines(it))
            out.append(_indent("→ devtrack start {task_id}".format(**it)))
            if it.get("related"):
                out.append(_indent("→ 관련 작업 {}: 먼저 devtrack show <작업> --brief (인계 메모) 를 읽고, 리포트 · 파일은 필요한 것만"
                                   .format(", ".join(it["related"]))))
            if rv and doc:
                out.append(_indent("→ 원고를 다 쓰면 devtrack review-request {}{} 로 {} 에게 {}심사위원 관점의 심사를 받습니다. "
                                   "판정은 [AI 메시지] 로 오고, 고친 뒤에는 답변서와 함께 다시 요청 (--response)".format(
                                       it["task_id"], "" if rv.get("doc") else " --doc <원고 파일>", rv["reviewer"],
                                       rv["venue"] + " " if rv.get("venue") else "저널 ")))
            elif rv:
                out.append(_indent("→ 개발이 끝나면 커밋 승인 요청 전에 devtrack review-request {} 로 {} 의 리뷰를 받습니다 "
                                   "(결과는 [AI 메시지] 로 옴)".format(it["task_id"], rv["reviewer"])))
        elif it["type"] == "comment" and it.get("artifact"):
            ver = it.get("artifact_version")
            out.append("[메시지] {} {} — 결과물 {} '{}'{} 에 대해: {}".format(
                it["task_id"], it["id"], it["artifact"], it.get("artifact_title") or "",
                " (v{})".format(ver) if ver and ver > 1 else "", it["message"]))
            out.extend(_comment_artifact_lines(it))
            out.extend(_sent_lines(it))
        elif it["type"] == "comment":
            out.append("[메시지] {task_id} {id}: {message}".format(**it))
            out.extend(_sent_lines(it))
        elif it["type"] == "agent_message":
            lines = it["message"].splitlines() or [""]
            out.append("[AI 메시지] {} {} ({} 이(가) 보냄): {}".format(it["task_id"], it["id"], it.get("sender") or "AI",
                                                                    lines[0]))
            out.extend(_indent(x) for x in lines[1:])
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


def _session_me(args):
    """이 세션의 (모델, 강도) — 모델 지정이 다른 작업을 거르는 데 쓴다. --any-model 이거나 둘 다 모르면 None."""
    if getattr(args, "any_model", False):
        return None
    me = models.session_model()
    return me if (me[0] or me[1]) else None


def _split_by_model(items, me):
    """모델 · 강도 지정이 이 세션(me)과 다른 작업의 새 작업 · 메시지 · 결정을 따로 뺀다 → (처리할 것, 뺀 것).
    취소 · 삭제 · 완료 처리 알림은 늘 처리할 것에 둔다."""
    if not me:
        return items, []
    ok, held = [], []
    for it in items:
        if it["type"] not in NOTICE_TYPES and models.spec_matches(it, *me) is False:
            held.append(it)
        else:
            ok.append(it)
    return ok, held


def _held_lines(held, me):
    out, seen = [], []
    for it in held:
        if it["task_id"] in seen:
            continue
        seen.append(it["task_id"])
        n = sum(1 for x in held if x["task_id"] == it["task_id"])
        out.append("[다른 모델 지정] {} {} — {} 로 지정된 작업이라 이 세션({})에서는 처리하지 않습니다 (입력 {}건)".format(
            it["task_id"], it["title"], models.spec_label(it.get("model"), it.get("effort")),
            models.spec_label(*me) or "모델 알 수 없음", n))
    if out:
        out.append(_indent("지정한 모델의 세션이나 devtrack watch 가 처리합니다. 이 세션에서 하려면 사용자가 /model · /effort 로 "
                           "맞추거나 지정을 바꿔야 합니다 (devtrack model <작업> …)."))
    return out


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

    def __init__(self, store, kind, agent, task=None, seen=None, model=None, effort=None):
        self.store, self.kind, self.agent, self.task, self.seen = store, kind, agent or "", task, seen
        self.model, self.effort = model, effort
        self.started = None
        self.last = 0.0
        self.state, self.running = "waiting", None
        self.parent = os.getppid()
        self.orphan = False

    def _write(self):
        try:
            self.store.touch_listener(self.kind, self.agent, task=self.task, started_at=self.started,
                                      state=self.state, running=self.running, seen=self.seen,
                                      model=self.model, effort=self.effort)
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


def _wait_for_new(store, agent, seen_name, task=None, timeout=0, interval=3, on_tick=None, keep=None):
    """seen_name 기준으로 아직 보지 않은 입력이 생길 때까지 기다려 그 항목들을 돌려준다 (시간 초과면 None).
    keep: 받은 편지함 항목 중 기다릴 것만 남기는 함수 (예: 모델 지정이 다른 작업 빼기)."""
    deadline = time.time() + timeout if timeout else None
    last = object()
    while True:
        if on_tick:
            on_tick()
        token = store.change_token()
        if token != last:
            last = token
            items = store.inbox(agent=agent, task=task)
            if keep:
                items = keep(items)
            seen = store.inbox_seen(seen_name)
            new = [it for it in items if store.inbox_key(it) not in seen]
            if new:
                store.mark_inbox_seen(seen_name, items, merge=bool(task))
                return new
        if deadline and time.time() >= deadline:
            return None
        time.sleep(max(1, interval))


def _with_mailbox(store, tick, out):
    """기다리는 동안 GitHub 우편함도 확인한다 (설정이 없으면 하는 일 없음, 오류가 나도 대기는 계속)."""
    box = mailbox.Poller(store, user=USER, out=out)

    def run():
        if tick:
            tick()
        box.maybe()
    return run


def _inbox_wait(store, args):
    """아직 보지 않은 사용자 입력이 생길 때까지 기다린다 (백그라운드 실행용)."""
    if args.ack:
        raise DevTrackError("--wait 와 --ack 는 함께 쓸 수 없습니다 (처리한 뒤 devtrack inbox --ack)")
    agent, task = _inbox_scope(args)
    me = _session_me(args)
    scope = task or ("{} 에게 배정된 작업".format(agent) if agent else "모든 AI 의 작업")
    _info("새 사용자 입력을 기다리는 중 ({} — 웹의 새 작업 지시 · 메시지 · 승인 결정) — 중단: Ctrl+C".format(scope))
    _warn_other_data(store, args)
    if me:
        _info("이 세션: {} — 다른 모델 · 강도로 지정된 작업은 받지 않습니다".format(models.spec_label(*me)))
    def keep(items):  # 다른 모델의 작업 · 이 대화가 개발한 작업의 리뷰로는 깨어나지 않는다
        return _split_by_session(store, _split_by_model(items, me)[0])[0]
    with _Listening(store, "wait", agent, task=task, seen=args.author, model=me and me[0],
                    effort=me and me[1]) as listening:
        new = _wait_for_new(store, agent, args.author, task=task, timeout=args.timeout, interval=args.interval,
                            on_tick=_with_mailbox(store, listening.tick, _info), keep=keep)
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
    me = _session_me(args)
    items, held = _split_by_model(store.inbox(agent=agent, task=task), me)
    items, own = _split_by_session(store, items)
    held = held + own
    if args.ack:
        store.ack_inbox(items)
    store.mark_inbox_seen(args.author, items, merge=bool(task))
    if args.json:
        _out(args, items)
        return 0
    if not items:
        print("처리할 사용자 입력이 없습니다.")
        if agent:
            others = len(store.inbox(task=task)) - len(held)
            if others > 0:
                print("(다른 AI 에게 배정된 입력 {}건은 그 AI 가 처리합니다 — 모두 보기: devtrack inbox --all-agents)".format(
                    others))
    else:
        _print_inbox(items, hint=not args.ack)
    for line in _held_lines([x for x in held if x not in own], me) + _own_review_lines(own):
        print(line)
    return 0


# ---------------------------------------------------------------------------- 취소 · 삭제를 일하는 중인 AI 에 알리기
# 작업에 기록하는 명령 — 실행한 AI 세션을 그 작업에 적어 두고, 사용자가 취소 · 삭제한 작업이면 거절한다
TASK_WORK_CMDS = ("start", "step", "status", "link", "log", "plan", "check", "issue", "resolve", "artifact",
                  "review", "latex", "request", "apply", "done", "review-request", "review-done")
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
    """작업에 기록하기 전: 사용자가 취소 · 삭제했는데 아직 확인하지 않았으면 거절, 이 대화가 개발한 작업의 리뷰면 거절,
    아니면 이 세션의 작업으로 적어 둔다."""
    n = store.stop_notice(args.task)
    if n:
        raise DevTrackError(_stop_text(n))
    try:
        t = store.load(args.task)
    except DevTrackError:
        t = None
    if t and t.get("review_of"):
        if getattr(args, "same_session", False) and args.cmd == "start":
            store.allow_same_session_review(t["id"], author=args.author)
        else:
            dev = _own_review(store, t)
            if dev:
                raise DevTrackError(
                    "{} 은(는) 이 대화가 개발한 {} 의 리뷰입니다 — 리뷰는 개발한 대화와 다른 대화가 합니다. "
                    "실행기(devtrack watch --claude · --codex)가 새 대화로 처리하거나, 다른 대화에서 진행하세요 "
                    "(사용자가 이 대화에서 리뷰하라고 했으면 devtrack start {} --same-session)".format(t["id"], dev, t["id"]))
    _note_session(store, args.task)


def _own_review(store, task):
    """task(작업 또는 inbox 항목)가 이 대화가 개발한 작업의 리뷰면 그 개발 작업 ID, 아니면 None.
    같은 AI · 같은 모델끼리도 리뷰할 수 있게 하되, 자기가 짠 코드를 같은 대화에서 리뷰하지는 않게 한다."""
    of = task.get("review_of")
    if isinstance(of, dict):
        dev, same = of.get("task"), of.get("same_session")
    else:
        dev, same = of, task.get("review_same_session")
    if not dev or same:
        return None
    sid = _session_id()
    if not sid:
        return None
    return dev if normalize_task_id(dev) in store.session_tasks(sid) else None


def _split_by_session(store, items):
    """이 대화가 개발한 작업의 리뷰 작업 입력을 따로 뺀다 → (처리할 것, 뺀 것). 알림은 늘 처리할 것에 둔다."""
    if not _session_id():
        return items, []
    ok, held = [], []
    for it in items:
        if it["type"] not in NOTICE_TYPES and _own_review(store, it):
            held.append(it)
        else:
            ok.append(it)
    return ok, held


def _own_review_lines(held):
    out, seen = [], []
    for it in held:
        if it["task_id"] in seen:
            continue
        seen.append(it["task_id"])
        out.append("[다른 대화가 리뷰] {} {} — 이 대화가 개발한 {} 의 리뷰라 다른 대화(실행기 devtrack watch 등)가 처리합니다".format(
            it["task_id"], it["title"], it.get("review_of")))
    return out


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
    "~/.vscode-server/extensions/openai.chatgpt-*/bin/*/codex",  # VS Code 원격(SSH) 서버 — HPC 에서 Codex 확장만 쓸 때
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


def find_claude():
    """Claude Code CLI 찾기: $DEVTRACK_CLAUDE > PATH 의 claude > 기본 설치 위치."""
    for c in (os.environ.get("DEVTRACK_CLAUDE"), shutil.which("claude"), "~/.local/bin/claude",
              "~/.claude/local/claude"):
        if c:
            c = os.path.expanduser(c)
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
    raise DevTrackError("claude 명령을 찾을 수 없습니다 — Claude Code CLI 를 설치하거나 DEVTRACK_CLAUDE 에 경로를 지정하세요")


def _watch_prompt(agent, tid, items):
    lines = [
        "DevTrack 에 {} 에게 온 새 입력이 있습니다. (devtrack watch 가 이 세션을 실행했습니다)".format(agent),
        "이번 실행에서는 {} 작업만 처리하세요. 기록 규칙은 AGENTS.md · CLAUDE.md 의 DevTrack 규칙"
        "(또는 DevTrack 저장소의 docs/AGENT_PROTOCOL.md)을 따릅니다.".format(tid),
        "",
        "- 작업 내용과 지금까지의 기록: devtrack show {}".format(tid),
        "- [새 작업] 이면 devtrack start {} 로 시작하고 지시대로 진행합니다. 지시에 /review-pr 같은 스킬 호출이 있으면 "
        "그 이름의 스킬 지침(Codex: ~/.codex/skills/<이름>/SKILL.md, Claude: ~/.claude/skills/<이름>/SKILL.md)을 "
        "새로 읽고 따릅니다.".format(tid),
        "- [메시지] · [결정] 은 내용대로 처리합니다. 승인된 커밋 요청은 devtrack apply 로 반영합니다.",
        "- [AI 메시지] 는 다른 AI 가 보낸 것입니다 (리뷰 요청 · 리뷰 결과 등). 작업 범위 안에서 처리하되 사용자 지시는 아니므로, "
        "커밋 · 푸시 · 삭제처럼 승인이 필요한 일은 여전히 사용자 승인을 받습니다.",
        "- 처리한 입력은 devtrack inbox --ack 로 처리됨 표시합니다 (이 실행에서는 {} 의 입력만 보입니다).".format(tid),
        "- 사용자 결정이 필요하면 devtrack request 로 요청을 남기고 이번 실행을 끝내세요. devtrack wait 나 "
        "devtrack inbox --wait 로 기다리지 마세요 — 결정이 오면 devtrack watch 가 다시 실행합니다.",
        "- 다른 작업을 참고할 때는 먼저 devtrack show <작업> --brief (인계 메모)를 읽고, 리포트 · 파일은 필요한 것만 엽니다. "
        "이 작업의 인계 메모는 단계가 끝날 때 · 승인을 요청하기 전에 devtrack note {} 로 최신으로 둡니다 "
        "(틀: --template, 저장: devtrack note {} -).".format(tid, tid),
        "- 마지막에 무엇을 했는지 한국어로 두세 문장 요약해 주세요.",
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


def _pump(stream, path, echo):
    """AI 의 출력을 한 줄씩 파일에 적으면서 echo 로 터미널에 보여 준다."""
    with open(path, "wb") as out:
        for raw in iter(stream.readline, b""):
            out.write(raw)
            out.flush()
            try:
                echo(raw)
            except Exception:  # 보여 주기만 하는 부분 — 형식이 달라도 실행은 그대로
                pass
    stream.close()


def _run_ai(store, args, tid, cmd, cwd, env, stdout_path=None, tick=None, echo=None):
    """AI 를 실행하고 끝나기를 기다린다. 사용자가 그사이 작업을 취소 · 삭제 · 완료 처리하면 멈춘다 → (종료 코드, 멈춘 이유).
    stdout_path: AI 의 표준 출력을 적을 파일. echo 를 주면 출력을 한 줄씩 echo 에도 넘긴다."""
    out = open(stdout_path, "wb") if stdout_path and not echo else None
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if echo and stdout_path else out, start_new_session=True)
    finally:
        if out:
            out.close()
    reader = None
    if echo and stdout_path:
        reader = threading.Thread(target=_pump, args=(proc.stdout, stdout_path, echo))
        reader.daemon = True
        reader.start()
    try:
        while True:
            try:
                return proc.wait(timeout=max(1, args.interval)), None
            except subprocess.TimeoutExpired:
                pass
            if tick:
                tick()
            stopped = _stop_reason(store, tid)
            if stopped:
                _terminate(proc)
                return None, stopped
    except KeyboardInterrupt:
        _terminate(proc)
        raise
    finally:
        if reader:
            reader.join(10)


def _claude_result(path):
    """claude -p --output-format json 의 결과 → (요약, 세션 ID, 오류 여부). JSON 이 아니면 출력 그대로를 요약으로."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()
    except OSError:
        return "", None, False
    try:
        data = json.loads(text)
    except ValueError:
        return text, None, False
    if not isinstance(data, dict):
        return text, None, False
    return (data.get("result") or "").strip(), data.get("session_id"), bool(data.get("is_error"))


def _codex_thread(path):
    """codex exec --json 의 이벤트에서 대화 ID (thread.started 의 thread_id). 없으면 None."""
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b"thread_id" not in raw and b"session_id" not in raw:
                    continue
                try:
                    ev = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                tid = isinstance(ev, dict) and (ev.get("thread_id") or ev.get("session_id"))
                if isinstance(tid, str) and tid:
                    return tid
    except OSError:
        pass
    return None


def _codex_echo(raw):
    """codex exec --json 의 이벤트를 사람이 읽는 줄로: AI 의 말과 실행한 명령, 오류만 보여 준다."""
    line = raw.decode("utf-8", "replace").rstrip("\n")
    try:
        ev = json.loads(line)
    except ValueError:
        ev = None
    if not isinstance(ev, dict):
        if line.strip():
            print(_indent(line))
        return
    kind = ev.get("type")
    item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
    text = None
    if kind == "item.completed" and item.get("type") == "agent_message":
        text = (item.get("text") or "").strip()
    elif kind == "item.started" and item.get("type") == "command_execution":
        cmd = re.sub(r"^\S*/(?:ba|z)?sh -lc ", "", str(item.get("command") or ""))
        if len(cmd) > 1 and cmd[0] == cmd[-1] and cmd[0] in "'\"":
            cmd = cmd[1:-1].replace('\\"', '"') if cmd[0] == '"' else cmd[1:-1]
        text = "$ " + (cmd if len(cmd) <= 200 else cmd[:200] + "…")
    elif kind in ("error", "turn.failed"):
        err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
        text = "오류: {}".format(err.get("message") or line)
    if text:
        print(_indent(text))
        sys.stdout.flush()


# codex exec 에만 있고 exec resume 에는 없는 옵션 — -- 뒤에 이런 옵션을 적었으면 대화를 이어 가지 않는다
CODEX_EXEC_ONLY = ("-s", "--sandbox", "-C", "--cd", "--add-dir", "--oss", "--local-provider", "-p", "--profile",
                   "--profile-v2", "--color", "--full-auto")


def _has_flag(extra, *names):
    return any(a in names or any(a.startswith(n + "=") for n in names) for a in extra)


def _watch_run(store, args, agent, tid, items, runner, extra, default_cwd, tick=None):
    """runner: ("codex", 경로) · ("claude", 경로) · None(-- 뒤에 적은 명령). 작업에 모델 · 강도가 지정되어 있으면
    codex · claude 는 그 모델로 실행하고, 직접 적은 명령에는 DEVTRACK_TASK_MODEL · DEVTRACK_TASK_EFFORT 로 알려 준다."""
    t = store.load(tid)
    repo = (t.get("repo") or {}).get("path")
    cwd = repo if repo and os.path.isdir(repo) else default_cwd
    want_model, want_effort = models.task_spec(t)
    spec = models.spec_label(want_model, want_effort)
    kind, exe = runner or (None, None)
    prompt = _watch_prompt(agent, tid, items)
    tmpdir = tempfile.mkdtemp(prefix="devtrack-watch-")
    last_msg = os.path.join(tmpdir, "last-message.txt")
    # watch 를 실행한 쪽(예: Claude 대화)의 세션 ID 가 넘어가면 실행한 AI 의 작업이 그 세션 것으로 적힌다
    env = {k: v for k, v in os.environ.items()
           if k not in SESSION_ENV + ("DEVTRACK_MODEL", "DEVTRACK_EFFORT", "CLAUDE_EFFORT")}
    env.update({"DEVTRACK_AGENT": agent, "DEVTRACK_DATA": store.root, "DEVTRACK_TASK": tid, "DEVTRACK_WATCH": "1"})
    env["PATH"] = os.path.join(REPO_ROOT, "bin") + os.pathsep + env.get("PATH", "")
    if kind:  # 지정 모델로 실행한다 — 실행된 AI 의 devtrack 명령이 모델 확인에 쓴다
        if want_model:
            env["DEVTRACK_MODEL"] = want_model
        if want_effort:
            env["DEVTRACK_EFFORT"] = want_effort
    else:
        env["DEVTRACK_TASK_MODEL"] = want_model or ""
        env["DEVTRACK_TASK_EFFORT"] = want_effort or ""

    resume = None
    events = os.path.join(tmpdir, "events.jsonl")
    if kind == "codex":
        # 같은 작업의 다음 입력은 같은 대화로 이어 간다 (codex exec resume). resume 은 --sandbox · -C · --add-dir 를
        # 받지 않으므로 같은 설정을 -c 로 넘기고, 실행 폴더는 프로세스의 폴더(cwd)로 정한다
        sess = store.runner_session(agent, tid)
        if (sess and sess.get("cwd") == cwd and models.codex_transcript(sess["id"])
                and not _has_flag(extra, *CODEX_EXEC_ONLY)):
            resume = sess["id"]
        roots = [store.root] + ([store.publish_dir] if store.publish_dir else [])

        def codex_cmd(session):
            if session:
                # -c 의 writable_roots 는 config.toml 의 목록을 덮어쓰므로, 앞 차례에 쓰던 목록(config 의 폴더 + --add-dir)을 함께 넘긴다
                sandbox = models.codex_turn_context(session).get("sandbox_policy")
                prev = sandbox.get("writable_roots") if isinstance(sandbox, dict) else None
                keep = list(roots) + [r for r in prev or [] if isinstance(r, str) and r and r not in roots]
                c = [exe, "exec", "resume", "--json", "--skip-git-repo-check",
                     "-c", 'sandbox_mode="workspace-write"',
                     "-c", "sandbox_workspace_write.writable_roots=[{}]".format(", ".join(json.dumps(r) for r in keep))]
            else:
                c = [exe, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", cwd]
                for r in roots:
                    c += ["--add-dir", r]
            if not args.no_network:  # 대화창이 없어 허락을 물을 수 없으므로 gh · git push 에 필요한 인터넷을 미리 허용
                c += ["-c", "sandbox_workspace_write.network_access=true"]
            c += ["-o", last_msg] + extra
            if want_model:
                c += ["-m", want_model]
            if want_effort:
                c += ["-c", 'model_reasoning_effort="{}"'.format(want_effort)]
            return c + ([session] if session else []) + [prompt]
        session = resume
        cmd = codex_cmd(session)
    elif kind == "claude":
        # 같은 작업의 다음 입력은 같은 대화로 이어 간다 (대화 기록이 그대로 있고 실행 폴더가 같을 때)
        sess = store.runner_session(agent, tid)
        if sess and sess.get("cwd") == cwd and models.claude_transcript(sess["id"]):
            resume = sess["id"]

        def claude_cmd(session, cont):
            c = [exe, "-p", prompt, "--output-format", "json"]
            c += ["--resume", session] if cont else ["--session-id", session]
            if not _has_flag(extra, "--permission-mode", "--dangerously-skip-permissions"):
                c += ["--permission-mode", "auto"]  # 대화창이 없어 허락을 물을 수 없다
            c += extra
            if want_model:
                c += ["--model", want_model]
            if want_effort:
                c += ["--effort", want_effort]
            c += ["--add-dir", store.root] + ([store.publish_dir] if store.publish_dir else [])
            return c
        session = resume or str(uuid.uuid4())
        cmd = claude_cmd(session, bool(resume))
    else:
        cmd = extra + [prompt]

    _stamp("{} 실행 — {} {} (폴더: {}{}{})".format(agent, tid, t["title"], cwd, " · 모델 " + spec if spec else "",
                                               " · 이전 대화 이어 감" if resume else ""))
    print(_indent("\n".join(_format_inbox(items))))
    sys.stdout.flush()
    summary = ""
    try:
        try:
            out_path = last_msg if kind == "claude" else events if kind == "codex" else None
            echo = _codex_echo if kind == "codex" else None
            rc, stopped = _run_ai(store, args, tid, cmd, cwd, env, stdout_path=out_path, tick=tick, echo=echo)
            if kind == "codex":
                got = _codex_thread(events)
                if resume and rc not in (0, None) and not got:
                    # 이전 대화를 열지 못함 (기록이 지워졌거나 옮겨짐, resume 이 없는 옛 Codex) — 새 대화로 한 번 더
                    _watch_log(store, tid, "이전 {} 대화를 이어 가지 못해 새 대화로 실행합니다".format(agent),
                               kind="warning", author=agent)
                    _stamp("이전 대화를 이어 가지 못해 새 대화로 실행합니다 — {}".format(tid))
                    store.set_runner_session(agent, tid, None)
                    resume = None
                    cmd = codex_cmd(None)
                    rc, stopped = _run_ai(store, args, tid, cmd, cwd, env, stdout_path=events, tick=tick, echo=echo)
                    got = _codex_thread(events)
                if got:
                    store.set_runner_session(agent, tid, {"id": got, "cwd": cwd, "model": want_model,
                                                          "effort": want_effort})
                try:
                    with open(last_msg, encoding="utf-8", errors="replace") as fh:
                        summary = fh.read().strip()
                except OSError:
                    pass
            elif kind == "claude":
                summary, got, err = _claude_result(last_msg)
                if resume and rc not in (0, None) and not got:
                    # 이전 대화를 열지 못함 (기록이 지워졌거나 옮겨짐) — 새 대화로 한 번 더
                    _watch_log(store, tid, "이전 {} 대화를 이어 가지 못해 새 대화로 실행합니다".format(agent),
                               kind="warning", detail=summary[:2000] or None, author=agent)
                    _stamp("이전 대화를 이어 가지 못해 새 대화로 실행합니다 — {}".format(tid))
                    store.set_runner_session(agent, tid, None)
                    resume, session = None, str(uuid.uuid4())
                    cmd = claude_cmd(session, False)
                    rc, stopped = _run_ai(store, args, tid, cmd, cwd, env, stdout_path=last_msg, tick=tick)
                    summary, got, err = _claude_result(last_msg)
                if rc == 0 and err:
                    rc = 1
                if got or models.claude_transcript(session):
                    store.set_runner_session(agent, tid, {"id": got or session, "cwd": cwd, "model": want_model,
                                                          "effort": want_effort})
                if summary:
                    print(_indent(summary))
        except OSError as e:
            _watch_log(store, tid, "{} 을(를) 실행하지 못했습니다".format(agent), kind="error", detail=str(e),
                       author=agent)
            _stamp("실행 실패: {}".format(e))
            return None
    finally:
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
    if args.codex and args.claude:
        raise DevTrackError("--codex 와 --claude 는 함께 쓸 수 없습니다 — AI 마다 devtrack watch 를 하나씩 켜세요")
    agent = (args.agent or ("Codex" if args.codex else "Claude" if args.claude else args.author) or "").strip()
    if is_unassigned(agent):
        raise DevTrackError("--agent 에 AI 이름을 지정하세요 (예: --agent Codex)")
    extra = list(args.command or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    runner = None
    if args.codex:
        codex, ver = find_codex()
        runner = ("codex", codex)
        _info("Codex CLI: {} ({}) · 인터넷 {}".format(codex, ".".join(map(str, ver)) if ver else "버전 확인 안 됨",
                                                    "차단 (--no-network)" if args.no_network else "허용"))
    elif args.claude:
        claude = find_claude()
        runner = ("claude", claude)
        _info("Claude Code CLI: {} · 권한 모드 {}".format(
            claude, "-- 뒤에 지정한 대로" if _has_flag(extra, "--permission-mode", "--dangerously-skip-permissions")
            else "auto (바꾸려면 -- --permission-mode <모드>)"))
    if not runner and not extra:
        raise DevTrackError("실행할 AI 를 지정하세요: devtrack watch --claude   ·   devtrack watch --codex   또는   "
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
        agent, {"codex": "codex exec", "claude": "claude -p"}.get(runner and runner[0]) or extra[0]))
    _info("데이터: {} · 저장소 경로가 없는 작업의 실행 폴더: {} · 멈추기: Ctrl+C".format(store.root, default_cwd))
    _warn_other_data(store, args)
    if runner:
        _info("모델 · 추론 강도가 지정된 작업은 그 모델로 실행합니다{}".format(
            "" if runner[0] == "codex" and _has_flag(extra, *CODEX_EXEC_ONLY)
            else " · 같은 작업의 다음 입력은 같은 대화로 이어 갑니다"))
    else:
        _info("작업에 지정된 모델 · 강도는 DEVTRACK_TASK_MODEL · DEVTRACK_TASK_EFFORT 로 명령에 전달합니다 "
              "(명령이 그 값을 써야 지정 모델로 실행됩니다)")
    # 실행기가 작업마다 지정 모델로 띄우면 어떤 지정이든 받는다 ('*')
    listening = _Listening(store, "watch", agent, seen=seen_name, model="*" if runner else None).__enter__()
    box_tick = _with_mailbox(store, listening.tick, _stamp)
    if mailbox.load_config(store.root):
        _info("GitHub 우편함: {} — 기다리는 동안 확인합니다 (devtrack mailbox)".format(
            mailbox.load_config(store.root)["github"]))
    try:
        while True:
            new = _wait_for_new(store, agent, seen_name, timeout=args.timeout, interval=args.interval,
                                on_tick=box_tick)
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
                        _watch_run(store, args, agent, tid, pending, runner, extra, default_cwd, tick=listening.tick)
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
    _note_reminder(store, t["id"])


def _loop_state_label(loop):
    from .store import REVIEW_STATES
    label = REVIEW_STATES.get(loop.get("state"), loop.get("state") or "")
    return label.replace("리뷰", "심사") if loop.get("kind") == "doc" else label


NOTE_SKIP_TYPES = ("review", "docreview")  # 리뷰 작업은 검토 항목 · 리포트가 인계 역할을 한다
BRIEF_KINDS = ("markdown", "pdf", "html", "notebook", "latex", "docx")


def _note_meta(t, stale=None):
    n = t.get("note") or {}
    return "{} · {} · {:,}자{}".format((n.get("updated_at") or "")[:16].replace("T", " "),
                                     "사용자" if n.get("by") == USER else n.get("by") or "?", n.get("chars") or 0,
                                     " · 그 뒤 기록 {}건".format(stale) if stale else "")


def _print_note(store, t, indent="  "):
    text = store.read_note(t["id"])
    if not text:
        return False
    print("{}인계 메모 ({}):".format(indent, _note_meta(t, store.note_stale(t))))
    print(_indent(text.strip(), len(indent) + 2))
    return True


def _note_reminder(store, tid):
    """인계 메모가 없거나 오래됐으면 한 줄 알린다 (AI 가 다음 대화 · 다른 작업을 위해 남기게)."""
    try:
        t = store.load(tid)
    except DevTrackError:
        return
    if t.get("type") in NOTE_SKIP_TYPES:
        return
    stale = store.note_stale(t)
    if stale is None:
        _info("💡 인계 메모가 없습니다 — 남겨 두면 다음 대화 · 이 작업을 참고하는 다른 작업이 기록 · 리포트 · 파일을 다시 뒤지지 "
              "않습니다: devtrack note {0} --template 로 틀을 보고, 채운 글을 devtrack note {0} - (표준입력)로 저장".format(t["id"]))
    elif stale >= NOTE_STALE:
        _info("💡 인계 메모를 쓴 뒤 기록이 {}건 쌓였습니다 — 바뀐 결론 · 숫자 · 위치가 있으면 고쳐 두세요 "
              "(devtrack note {} 로 보고 고친 글을 devtrack note {} -)".format(stale, t["id"], t["id"]))


def _related_note_lines(store, t):
    """작업을 시작할 때: 관련 작업마다 인계 메모가 있는지와 읽는 명령."""
    out = []
    for rid in t.get("related") or []:
        try:
            r = store.load(rid)
        except DevTrackError:
            continue
        has = bool((r.get("note") or {}).get("updated_at"))
        out.append("관련 작업 {} {} — {}: devtrack show {} --brief".format(
            r["id"], r["title"], "인계 메모 있음 ({})".format(_note_meta(r)) if has else "인계 메모 없음 (요약 · 최근 기록)",
            r["id"]))
    if out:
        out.append("→ 관련 작업은 먼저 --brief 로 보고, 리포트 · 파일은 필요한 것만 여세요")
    return out


def _show_brief(store, t):
    """다른 작업 · 다음 대화가 이 작업을 참고할 때 먼저 읽는 짧은 보기: 상태 · 주요 결과물 · 인계 메모."""
    print("{}  {}".format(t["id"], t["title"]))
    print("  {} · {} · {}% · 에이전트 {}".format(TASK_TYPES.get(t["type"]), STATUSES.get(t["status"]),
                                             t.get("progress", 0), t.get("agent")))
    repo = t.get("repo") or {}
    if repo:
        print("  저장소: {}{}".format(repo.get("path") or repo.get("url"),
                                    " ({})".format(repo["branch"]) if repo.get("branch") else ""))
    if t.get("related"):
        print("  관련 작업: " + ", ".join(t["related"]))
    if t.get("current_step"):
        print("  현재 단계: " + t["current_step"])
    for a in t.get("approvals", []):
        if a["status"] == "pending":
            print("  승인 대기: {} {}".format(a["id"], a["title"]))
    opened = [i for i in t.get("issues", []) if i["status"] == "open"]
    if opened:
        print("  열린 문제: " + " · ".join("{} {}".format(i["id"], i["title"]) for i in opened[:5]))
    docs = [a for a in t.get("artifacts", []) if a.get("kind") in BRIEF_KINDS and a.get("category") != "source"]
    if docs:
        print("  주요 결과물 (리포트 · 문서):")
        for a in docs[-5:]:
            print("    {} [{}] {}".format(a["id"], a["kind"], a["title"]))
    print("  결과물 {}개 · 기록 {}건 — 전체: devtrack show {}".format(len(t.get("artifacts", [])), len(t.get("log", [])),
                                                                 t["id"]))
    if _print_note(store, t):
        return 0
    print("  인계 메모 없음")
    done = (t.get("completed") or {}).get("note")
    if done:
        print("  완료 메모: " + done)
    for e in t.get("log", [])[-3:]:
        first = (e.get("message") or "").strip().split("\n")[0]
        print("    {} [{}] {}".format(e["ts"][5:16].replace("T", " "), e["kind"], first[:160]))
    return 0


def cmd_note(store, args):
    """인계 메모 보기 · 쓰기 (파일 또는 - = 표준입력) · 틀 · 지우기."""
    t = store.load(args.task)
    if args.template:
        print(store.note_template(t["id"]))
        return 0
    if args.clear:
        store.set_note(t["id"], "", author=args.author)
        return _out(args, {"task": t["id"], "note": None}, "{} 인계 메모를 지웠습니다".format(t["id"]))
    if args.file is None:
        text = store.read_note(t["id"])
        if args.json:
            return _out(args, {"task": t["id"], "note": t.get("note"), "text": text})
        if not text:
            print("{} 인계 메모 없음 — 틀: devtrack note {} --template".format(t["id"], t["id"]))
            return 0
        _info("{} 인계 메모 ({})".format(t["id"], _note_meta(t, store.note_stale(t))))
        print(text.rstrip())
        return 0
    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(os.path.expanduser(args.file), encoding="utf-8") as fh:
            text = fh.read()
    if not text.strip():
        raise DevTrackError("메모가 비어 있습니다 — 지우려면 --clear")
    t2, prev_by = store.set_note(t["id"], text, author=args.author)
    n = t2["note"]
    _out(args, n, "{} 인계 메모 저장 ({:,}자, {}판)".format(t["id"], n["chars"], n["version"]))
    sys.stdout.flush()
    if prev_by == USER and args.author != USER:
        _info("⚠ 바로 전 메모는 사용자가 웹에서 고친 것이었습니다 — 그 내용을 살렸는지 확인하세요 (전 판: {})".format(
            store.note_path(t["id"], prev=True)))
    if n["chars"] > NOTE_SOFT:
        _info("메모가 깁니다 ({:,}자) — 결론 · 숫자 · 위치 · 명령만 남기고, 긴 설명은 리포트 결과물로 올린 뒤 그 ID 를 적으세요".format(
            n["chars"]))
    return 0


def cmd_show(store, args):
    t = store.load(args.task)
    if args.json:
        _out(args, t)
        return 0
    if args.brief:
        return _show_brief(store, t)
    repo = t.get("repo") or {}
    print("{}  {}".format(t["id"], t["title"]))
    print("  {} · {} · {}% · 에이전트 {}".format(TASK_TYPES.get(t["type"]), STATUSES.get(t["status"]),
                                             t.get("progress", 0), t.get("agent")))
    spec = models.spec_label(*models.task_spec(t))
    if spec:
        print("  모델: {} (이 모델의 세션 · devtrack watch 만 처리)".format(spec))
    loop = t.get("review_loop") or {}
    if loop.get("reviewer"):
        from .store import REVIEW_STATES
        rspec = models.spec_label(loop.get("model"), loop.get("effort"))
        doc = loop.get("kind") == "doc"
        print("  {}: {}{} · 최대 {}번 · {}{}".format("심사" if doc else "리뷰", loop["reviewer"],
                                                " (" + rspec + ")" if rspec else "", loop["rounds"],
                                                _loop_state_label(loop),
                                                (" · 심사 작업 " if doc else " · 리뷰 작업 ") + loop["task"]
                                                if loop.get("task") else ""))
        from .store import JOURNAL_VERDICT_LABEL
        if doc:
            print("    원고: {}{}".format(loop.get("doc") or "원고 파일 아직 안 정함 (--doc)",
                                       " · 투고처 " + loop["venue"] if loop.get("venue") else ""))
        for h in loop.get("history") or []:
            v = h.get("verdict")
            label = {"approve": "통과", "request_changes": "수정 필요"}.get(v) or JOURNAL_VERDICT_LABEL.get(v) or (
                "심사 중" if doc else "리뷰 중")
            print("    {}차 {} — {}{}".format(h["round"], (h.get("requested_at") or "")[5:16].replace("T", " "), label,
                                          " (열린 항목 {})".format(", ".join(h["open"])) if h.get("open") else ""))
    if t.get("review_of"):
        print("  리뷰 대상: {} {} ({}차)".format(t["review_of"]["task"], t["review_of"].get("title", ""),
                                            t.get("review_round", 1)))
    if repo:
        print("  저장소: {}{}".format(repo.get("path") or repo.get("url"),
                                    " ({})".format(repo["branch"]) if repo.get("branch") else ""))
    if t.get("related"):
        print("  관련 작업: {} (각 작업의 인계 메모: devtrack show <작업> --brief)".format(", ".join(t["related"])))
    if t.get("current_step"):
        print("  현재 단계: " + t["current_step"])
    _print_note(store, t)
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
            print("    {} [{}{}] {}".format(a["id"], a["kind"], ", 보낸 파일" if a.get("sent") else {
                "review": ", 리뷰", "source": ", 검토할 문서"}.get(a.get("category"), ""), a["title"]))
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
    _warn_other_data(store, args)
    serve(store, WEB_ROOT, host=args.host, port=args.port, open_browser=args.open, verbose=args.verbose)


def cmd_publish(store, args):
    dest = args.to or publish_location(store.root)[0]
    if not dest:
        raise DevTrackError("배포할 디렉토리를 --to 로 지정하거나 devtrack config publish <폴더> 로 정해 두세요 "
                            "($DEVTRACK_PUBLISH_DIR 도 됨)")
    path = store.publish(WEB_ROOT, dest)
    _out(args, {"published": path, "password": bool(store.auth())},
         "배포 완료: {}{}".format(path, " (비밀번호 보호)" if store.auth() else ""))


def _read_password(args):
    if args.stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise DevTrackError("비밀번호는 터미널에서 직접 입력하세요 (스크립트에서는 --stdin 으로 표준입력의 첫 줄을 씀)")
        import getpass
        pw = getpass.getpass("웹 비밀번호 ({}자 이상): ".format(access.MIN_LENGTH))
        if pw and getpass.getpass("한 번 더: ") != pw:
            raise DevTrackError("두 입력이 다릅니다 — 바꾸지 않았습니다")
    if not pw:
        raise DevTrackError("비밀번호를 입력하지 않았습니다 — 바꾸지 않았습니다")
    return pw


def cmd_password(store, args):
    dest = publish_location(store.root)[0]
    if args.off and args.status:
        raise DevTrackError("--off 와 --status 는 함께 쓸 수 없습니다")
    if args.status:
        a = store.auth()
        info = {"password": bool(a), "set_at": (a or {}).get("set_at"), "publish": dest}
        if not a:
            return _out(args, info, "웹 비밀번호 없음 — 공개 폴더의 웹은 주소를 아는 누구나 볼 수 있습니다 (정하기: devtrack password)")
        return _out(args, info, "웹 비밀번호 켜짐 (정한 때: {}){}".format(
            a.get("set_at") or "?", "" if dest else "\n  공개 폴더가 정해지지 않았습니다 — devtrack publish --to <폴더> 할 때 반영됩니다"))
    if args.off:
        had = store.clear_password()
        files = store.sync_access(dest)
        _out(args, {"password": False, "removed": files},
             "웹 비밀번호를 풀었습니다{}".format("" if had else " (원래 없었음)"))
        sys.stdout.flush()
        if had and not args.json:
            _info("⚠ 이제 공개 폴더의 웹은 주소를 아는 누구나 볼 수 있습니다")
        return
    try:
        had = bool(store.auth())
    except DevTrackError:
        had = True  # 망가진 설정 — 새로 정해서 고친다
    store.set_password(_read_password(args))
    files = store.sync_access(dest)
    _out(args, {"password": True, "files": files, "publish": dest},
         "웹 비밀번호를 {}습니다 (비밀번호 자체는 저장하지 않음)".format("바꾸었" if had else "정했"))
    sys.stdout.flush()
    if args.json:
        return
    if dest:
        _info("  공개 폴더 반영: {}".format(dest))
    else:
        _info("  공개 폴더가 정해지지 않았습니다 — devtrack publish --to <폴더> 할 때 반영됩니다")
    _info("  사이트를 열면 비밀번호를 묻습니다. 한 번 넣은 브라우저는 쿠키 · 사이트 데이터를 지울 때까지 다시 묻지 않습니다.")
    if had:
        _info("  전에 비밀번호를 넣은 브라우저도 새 비밀번호를 다시 넣어야 합니다.")
    _info("  기록(data/)을 막는 것은 웹 서버입니다: Apache(.htaccess) 와 devtrack serve. 다른 웹 서버에서는 화면에 경고가 나옵니다.")


def _repo_name(value):
    v = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", (value or "").strip()).strip("/")
    v = re.sub(r"\.git$", "", v)
    if not mailbox.REPO_RE.match(v):
        raise DevTrackError("저장소는 <계정>/<저장소> 로 적어 주세요 (예: hyungyukang/devtrack-inbox)")
    return v


def _read_token(args):
    if args.stdin:
        return sys.stdin.readline().strip(), "stdin"
    env = os.environ.get(mailbox.TOKEN_ENV, "").strip()
    if env:
        return env, "$" + mailbox.TOKEN_ENV
    saved, _ = mailbox.load_token()
    if not sys.stdin.isatty():
        if saved:
            return saved, "saved"
        raise DevTrackError("토큰은 터미널에서 직접 입력하세요 (스크립트에서는 --stdin 으로 표준입력의 첫 줄을 씀)")
    import getpass
    tok = getpass.getpass("GitHub 토큰 (Fine-grained, 이 저장소의 Issues 읽기 · 쓰기 — 파일 보내기는 Contents 읽기도){}: ".format(
        " — Enter 만 누르면 저장된 토큰" if saved else "")).strip()
    if not tok and saved:
        return saved, "saved"
    if not tok:
        raise DevTrackError("토큰을 입력하지 않았습니다 — 바꾸지 않았습니다")
    return tok, "typed"


def _mailbox_status(store, args):
    cfg = mailbox.load_config(store.root)
    if not cfg:
        return _out(args, {"mailbox": None}, "GitHub 우편함 꺼짐 — 켜기: devtrack mailbox <계정>/<저장소>")
    tok, src = mailbox.load_token()
    st = mailbox.load_state(store.root)
    threads = [(n, v["task"]) for n, v in st["issues"].items() if v.get("kind") == "thread" and not v.get("closed")]
    last = st.get("last_poll")
    info = {"mailbox": cfg, "token": src, "last_poll": last, "last_error": st.get("last_error"),
            "open_threads": [{"issue": int(n), "task": t} for n, t in threads]}
    lines = ["GitHub 우편함: {} (받는 계정: {})".format(cfg["github"], cfg.get("login") or "?"),
             "  토큰: {}".format(src or "없음 — devtrack mailbox {} 로 다시 넣으세요".format(cfg["github"])),
             "  확인 간격: {}초 · 마지막 확인: {}".format(
                 cfg.get("interval") or mailbox.DEFAULT_INTERVAL,
                 time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)) if last else "아직 없음"),
             "  새 작업의 기본 담당 AI: {}".format(cfg.get("default_agent") or "Claude")]
    if cfg.get("site") or st.get("site"):
        lines.append("  웹 주소 (답글의 링크): {}".format(cfg.get("site") or st.get("site")))
    if "files" in cfg:
        lines.append("  파일 보내기: {}".format("됨 (저장소에 올린 파일 링크를 받음)" if cfg["files"] else
                                            "토큰에 Contents: Read 가 없음 (설정할 때 확인 — 권한을 더한 뒤 다시 확인: devtrack mailbox {})".format(cfg["github"])))
    if st.get("last_error"):
        lines.append("  ⚠ 마지막 오류: {}".format(st["last_error"]))
    if threads:
        lines.append("  열린 작업 이슈: " + ", ".join("#{} → {}".format(n, t) for n, t in threads))
    lines.append("  확인은 devtrack watch · inbox --wait 가 기다리는 동안 합니다 (지금 바로: devtrack mailbox --check)")
    return _out(args, info, "\n".join(lines))


def cmd_mailbox(store, args):
    if sum(1 for x in (args.repo, args.off, args.check) if x) > 1:
        raise DevTrackError("저장소 · --off · --check 는 하나만 쓰세요")
    if args.off:
        had = bool(mailbox.load_config(store.root))
        mailbox.remove_config(store.root)
        forgot = mailbox.forget_token() if args.forget_token else False
        store.rebuild_index()
        store._sync_publish()
        return _out(args, {"mailbox": None, "token_removed": forgot},
                    "GitHub 우편함을 껐습니다{}{}".format("" if had else " (원래 꺼져 있었음)",
                                                    " · 저장한 토큰도 지웠습니다" if forgot else ""))
    if args.check:
        cfg = mailbox.load_config(store.root)
        if not cfg:
            raise DevTrackError("GitHub 우편함이 설정되지 않았습니다 — devtrack mailbox <계정>/<저장소>")
        r = mailbox.poll(store, cfg=cfg, user=USER, force=True) or {}
        for line in r.get("done", []):
            print(line)
        if r.get("error"):
            raise DevTrackError(r["error"])
        if not r.get("done"):
            print("새로 받은 것 없음 ({})".format(cfg["github"]))
        return 0
    if not args.repo:
        return _mailbox_status(store, args)
    repo = _repo_name(args.repo)
    token, src = _read_token(args)
    gh = mailbox.GitHub(token, repo)
    login = (gh.user() or {}).get("login")
    if not login:
        raise DevTrackError("토큰으로 GitHub 계정을 확인하지 못했습니다")
    try:
        info = gh.repo_info() or {}
    except mailbox.GitHubError as e:
        if e.status == 404:
            raise DevTrackError("{} 저장소가 없거나 이 토큰에 그 저장소 권한이 없습니다 — 토큰의 Repository access 에 이 저장소를 "
                                "넣었는지 확인하세요".format(repo))
        raise
    if not info.get("private") and not args.allow_public:
        raise DevTrackError("{} 은(는) 공개 저장소입니다 — 지시 내용이 누구에게나 보이므로 비공개 저장소를 쓰세요 "
                            "(그래도 쓰려면 --allow-public)".format(repo))
    if info.get("has_issues") is False:
        raise DevTrackError("{} 의 Issues 가 꺼져 있습니다 — 저장소 Settings → Features → Issues 를 켜세요".format(repo))
    gh.issues_since(mailbox.iso_now())  # 읽기 권한
    try:  # 쓰기 권한 (답글 · 닫기에 필요) — 라벨을 만들어 확인한다
        for name, color, desc in (("claude", "d97757", "DevTrack: 담당 AI Claude"),
                                  ("codex", "10a37f", "DevTrack: 담당 AI Codex")):
            gh.ensure_label(name, color, desc)
    except mailbox.GitHubError as e:
        if e.status in (401, 403):
            raise DevTrackError("토큰에 Issues 쓰기 권한이 없습니다 — Repository permissions 의 Issues 를 'Read and write' 로 "
                                "만든 토큰을 쓰세요")
        raise
    files = gh.can_read_files()  # 보낸 파일(저장소에 올린 파일 링크)을 받으려면 Contents: Read
    old = mailbox.load_config(store.root) or {}
    if src in ("typed", "stdin"):
        mailbox.save_token(token)
    cfg = {"version": 1, "github": repo, "login": login, "since": mailbox.iso_now(), "branch": info.get("default_branch") or "main",
           "interval": args.interval or old.get("interval") or mailbox.DEFAULT_INTERVAL,
           "default_agent": args.agent or old.get("default_agent") or "Claude"}
    site = args.site or old.get("site")
    if site:
        cfg["site"] = site
    if files is not None:
        cfg["files"] = files
    if old.get("github") != repo:
        mailbox.remove_config(store.root)  # 다른 저장소의 처리 기록은 버린다
    mailbox.save_config(store.root, cfg)
    store.rebuild_index()
    store._sync_publish()
    _out(args, {"mailbox": cfg, "token": "saved" if src in ("typed", "stdin") else src},
         "GitHub 우편함을 켰습니다: {} (받는 계정: {})".format(repo, login))
    sys.stdout.flush()
    if args.json:
        return 0
    if src in ("typed", "stdin"):
        _info("  토큰 저장: {} (나만 읽기)".format(mailbox.token_path()))
    _info("  지금부터 {} 이(가) 쓴 이슈 · 댓글만 받습니다 (이전 이슈는 받지 않음).".format(login))
    _info("  확인은 devtrack watch · inbox --wait 가 기다리는 동안 {}초마다 합니다 — 켜 두세요.".format(cfg["interval"]))
    if files is False:
        _info("  파일 보내기: 이 토큰은 저장소 파일을 읽지 못합니다 — 저장소에 올린 파일을 이슈로 보내려면 토큰의 Repository permissions 에 "
              "Contents: Read 를 더하세요 (토큰 값은 그대로라 다시 설정할 필요 없음)")
    _info("  정적 웹의 보내기 창에 'GitHub 이슈로 보내기' 가 나타납니다 (공개 폴더가 정해져 있으면 바로, 아니면 devtrack publish 뒤 —\n"
          "  DevTrack 을 업데이트한 뒤 처음이면 devtrack publish 를 한 번 실행해 웹 화면 파일도 새로 복사하세요).")
    return 0


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
                        help="데이터 디렉토리 (기본: $DEVTRACK_DATA > devtrack config data > <저장소>/data)")
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

    sp = add("config", cmd_config, "기록 폴더 · 웹 공개 폴더 확인 · 설정 (환경 변수와 달리 모든 프로세스에 적용)",
             epilog="예: devtrack config   ·   devtrack config data ~/devtrack-data   ·   "
                    "devtrack config publish ~/www/devtrack   ·   devtrack config data --unset")
    sp.add_argument("key", nargs="?", choices=sorted(CONFIG_KEYS), help="data (기록 폴더) · publish (웹 공개 폴더)")
    sp.add_argument("value", nargs="?", metavar="PATH", help="정할 폴더 (없으면 지금 값 출력)")
    sp.add_argument("--unset", action="store_true", help="설정 지우기")

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
    sp.add_argument("--model", help="이 작업을 처리할 AI 모델 (예: claude-opus-5-5, opus-5.5, gpt-6-sol) — "
                                    "devtrack watch 는 이 모델로 실행하고, 다른 모델의 대화는 받지 않음")
    sp.add_argument("--effort", help="추론 강도: " + ", ".join(models.EFFORTS) + " (xhigh = Extra high)")
    sp.add_argument("--queued", action="store_true", help="사용자 지시로 '대기' 상태 생성")
    sp.add_argument("--review-by", metavar="AI", help="개발이 끝나면 이 AI 에게 리뷰받기 (예: Codex) — 커밋 승인 요청 전에 "
                                                      "devtrack review-request 로 리뷰를 통과해야 함")
    sp.add_argument("--review-rounds", type=int, help="리뷰 최대 횟수 (기본 {})".format(REVIEW_ROUNDS_DEFAULT))
    sp.add_argument("--review-model", help="리뷰어 모델")
    sp.add_argument("--review-effort", help="리뷰어 추론 강도")
    sp.add_argument("--review-doc", metavar="FILE", help="심사할 원고 (논문 작업: main.tex · .docx · .pdf · .md — 저장소 기준 상대 경로 가능)")
    sp.add_argument("--review-venue", metavar="JOURNAL", help="투고처 (예: Journal of Fluid Mechanics) — 그 저널의 심사위원 관점으로 심사")

    sp = add("start", cmd_start, "대기 중인 작업을 진행 중으로 전환 (다른 AI 에게 배정된 작업은 --take 로만)")
    sp.add_argument("task")
    sp.add_argument("step", nargs="?", help="시작하며 기록할 현재 단계")
    sp.add_argument("--take", action="store_true", help="다른 AI 에게 배정된 작업이라도 내가 맡기 (담당 AI 변경)")
    sp.add_argument("--any-model", action="store_true",
                    help="작업에 지정된 모델 · 강도와 이 세션이 달라도 시작 (사용자가 그렇게 하라고 한 경우만)")
    sp.add_argument("--same-session", action="store_true",
                    help="이 대화가 개발한 작업의 리뷰라도 이 대화에서 시작 (사용자가 그렇게 하라고 한 경우만)")

    sp = add("model", cmd_model, "작업을 처리할 AI 모델 · 추론 강도 보기 / 정하기 (예: devtrack model T-0003 opus-5.5 --effort xhigh), "
                                 "고를 수 있는 모델은 --list")
    sp.add_argument("task", nargs="?")
    sp.add_argument("model", nargs="?", help="모델 (예: claude-opus-5-5, opus-5.5, sonnet, gpt-6-sol). 생략하면 강도만 바꾸거나 지금 지정을 보여 줌")
    sp.add_argument("--effort", help="추론 강도: " + ", ".join(models.EFFORTS) + " (모델마다 받는 값이 다름 — --list)")
    sp.add_argument("--list", action="store_true", help="고를 수 있는 모델 · 강도 목록 (Claude + 이 컴퓨터의 Codex 가 받아 둔 목록)")
    sp.add_argument("--clear", action="store_true", help="지정 해제 — 어떤 모델이든 처리")
    sp.add_argument("--by-user", action="store_true", help="사용자가 정한 것으로 기록 (웹에서 보낸 지정을 대신 기록할 때)")

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

    sp = add("paper", cmd_paper, "논문 작업의 원고 폴더(tasks/<T>/paper — 최신 .tex · .bib 와 원고가 부르는 그림) "
                                 "다시 만들기 · 빠진 그림 확인")
    sp.add_argument("task")

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
    sp.add_argument("--files", nargs="+", help="커밋할 파일 제한 (기본: 바뀐 코드 전체 — 빌드 결과물은 늘 뺌, 이름을 그대로 적으면 넣음)")
    sp.add_argument("--no-push", action="store_true", help="커밋만 하고 푸시하지 않음")
    sp.add_argument("--remote", default="origin")
    sp.add_argument("--skip-review", action="store_true",
                    help="리뷰받기로 한 작업이라도 리뷰 없이 커밋 승인 요청 (사용자가 그렇게 하라고 한 경우만)")

    sp = add("review-request", cmd_review_request,
             "개발 작업의 지금 변경(빌드 결과물 제외)을 다른 AI 에게 리뷰받기, 논문 작업은 원고를 저널 심사위원 관점으로 심사받기 — "
             "결과는 [AI 메시지] 로 옴")
    sp.add_argument("task", help="개발 작업 (저장소가 있는 작업)")
    sp.add_argument("--reviewer", help="리뷰할 AI (예: Codex, Claude). 작업에 정해 두었으면 생략")
    sp.add_argument("--model", help="리뷰어 모델 (예: gpt-6-astra, opus-5.5)")
    sp.add_argument("--effort", help="리뷰어 추론 강도")
    sp.add_argument("--rounds", type=int, help="리뷰 최대 횟수 (기본 {}, 최대 {}) — 늘리는 것은 사용자가 그렇게 하라고 한 경우만".format(
        REVIEW_ROUNDS_DEFAULT, REVIEW_ROUNDS_MAX))
    sp.add_argument("-m", "--message", help="리뷰어에게 남길 메모 (봐 줄 부분 · 고친 내용)")
    sp.add_argument("--files", nargs="+", help="리뷰할 파일 제한 (기본: 바뀐 코드 전체, 빌드 결과물 제외)")
    sp.add_argument("--doc", metavar="FILE", help="(논문 · 문서) 심사할 원고 — main.tex · .docx · .pdf · .md")
    sp.add_argument("--venue", metavar="JOURNAL", help="(논문 · 문서) 투고처 — 그 저널의 심사위원 관점으로 심사")
    sp.add_argument("--response", metavar="FILE", help="(논문 · 문서, 2차부터 필수) 답변서 — 지적마다 어떻게 고쳤는지 (response to reviewers)")
    sp.add_argument("--same-session", action="store_true",
                    help="개발한 대화가 리뷰해도 됨 (기본: 리뷰는 다른 대화가 함 — 사용자가 그렇게 하라고 한 경우만)")

    sp = add("review-done", cmd_review_done, "리뷰 작업의 이번 차례 결과(통과 / 수정 필요)를 개발 AI 에게 보내기")
    sp.add_argument("task", help="리뷰 작업 (review-request 로 만든 작업)")
    sp.add_argument("--verdict", help="코드: approve (통과) · changes (수정 필요). 논문 · 문서: accept · minor · major · reject. "
                                      "생략하면 devtrack review 로 남긴 verdict")
    sp.add_argument("-m", "--message", help="한두 줄 요약")

    sp = add("send", cmd_send, "다른 AI 가 맡은 작업에 메시지 보내기 (AI → AI, [AI 메시지])")
    sp.add_argument("task")
    sp.add_argument("message", nargs="?")
    sp.add_argument("--message-file", metavar="FILE")

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
    sp.add_argument("--artifact", metavar="F-ID",
                    help="이 메시지가 가리키는 결과물 (결과물 화면에서 보낸 수정 요청 · 추가 지시)")

    sp = add("inbox", cmd_inbox, "에이전트가 처리할 사용자 입력 확인 (새 지시, 메시지, 결정)")
    sp.add_argument("--ack", action="store_true", help="확인한 메시지/결정을 처리됨으로 표시")
    sp.add_argument("--wait", action="store_true",
                    help="아직 보지 않은 입력이 생길 때까지 기다렸다가 출력 (에이전트가 백그라운드로 실행해 두면 웹 지시에 바로 반응)")
    sp.add_argument("--timeout", type=int, default=0, help="--wait 최대 대기 초 (0 = 무제한, 시간 초과 시 exit 3)")
    sp.add_argument("--interval", type=int, default=3, help="--wait 확인 간격 초")
    sp.add_argument("--task", metavar="T-ID", help="이 작업의 입력만, 담당 AI 와 상관없이 (기본: $DEVTRACK_TASK)")
    sp.add_argument("--all-agents", action="store_true", help="다른 AI 에게 배정된 작업의 입력까지 모두 보기")
    sp.add_argument("--any-model", action="store_true",
                    help="모델 · 강도 지정이 이 세션과 다른 작업의 입력도 보기 (기본은 따로 알리고 처리하지 않음)")

    add("hook", cmd_hook, "AI 도구 실행 전 확인 (Claude Code · Codex 의 PreToolUse 훅) — 하던 작업을 사용자가 "
                          "취소 · 삭제했으면 멈추게 한다. 훅 입력(JSON)을 표준입력으로 받는다")

    sp = add("watch", cmd_watch, "AI 실행기 — 웹에서 온 입력(새 작업 · 메시지 · 결정)마다 AI 를 실행해 처리 (예: Claude · Codex)")
    sp.add_argument("--claude", action="store_true",
                    help="Claude Code 로 처리 (claude -p 실행, 담당 AI 이름 Claude) — 작업에 지정된 모델 · 강도로 실행하고, "
                         "같은 작업의 다음 입력은 같은 대화로 이어 감")
    sp.add_argument("--codex", action="store_true",
                    help="Codex 로 처리 (codex exec 실행, 담당 AI 이름 Codex) — 작업에 지정된 모델 · 강도로 실행하고, "
                         "같은 작업의 다음 입력은 같은 대화로 이어 감 (codex exec resume)")
    sp.add_argument("--agent", help="담당 AI 이름 — 이 AI 에게 배정된 작업만 처리 (기본: --codex 면 Codex, 아니면 $DEVTRACK_AGENT)")
    sp.add_argument("--cwd", metavar="DIR", help="저장소 경로가 없는 작업을 실행할 폴더 (기본: 현재 폴더)")
    sp.add_argument("--no-network", action="store_true",
                    help="--codex: 실행하는 명령의 인터넷 사용을 막음 (기본은 gh · git push 를 위해 허용)")
    sp.add_argument("--once", action="store_true", help="들어온 입력을 한 번 처리하고 끝내기")
    sp.add_argument("--timeout", type=int, default=0, help="새 입력을 기다리는 최대 초 (0 = 무제한, 시간 초과 시 exit 3)")
    sp.add_argument("--interval", type=int, default=3, help="확인 간격 초")
    sp.add_argument("command", nargs=argparse.REMAINDER, metavar="-- 명령",
                    help="실행할 AI 명령 (프롬프트를 마지막 인자로 붙여 실행). --claude · --codex 와 함께 쓰면 "
                         "claude -p · codex exec 에 더할 옵션")

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
    sp.add_argument("--brief", action="store_true",
                    help="짧게: 상태 · 주요 결과물 · 인계 메모 (다른 작업이 이 작업을 참고할 때 먼저 읽는 것)")

    sp = add("note", cmd_note, "인계 메모 보기 · 쓰기 — 이 작업에서 알아낸 것 (결론 · 숫자 · 위치 · 명령 · 결정 · 주의점)",
             epilog="예: devtrack note T-0040 (보기)   ·   devtrack note T-0040 --template   ·   "
                    "devtrack note T-0040 - <<'EOF' … EOF   ·   devtrack note T-0040 notes.md")
    sp.add_argument("task")
    sp.add_argument("file", nargs="?", help="메모 파일 (- = 표준입력). 없으면 지금 메모를 보여 줌")
    sp.add_argument("--template", action="store_true", help="채울 틀 보기 (목적 · 결론 · 숫자 · 위치 · 명령 · 결정 · 주의점 · 남은 일)")
    sp.add_argument("--clear", action="store_true", help="메모 지우기")

    sp = add("list", cmd_list, "작업 목록")
    sp.add_argument("--status", choices=list(STATUSES))
    sp.add_argument("--all", action="store_true", help="완료/취소 작업 포함")

    sp = add("serve", cmd_serve, "로컬 웹 서버 실행 (웹에서 승인/피드백/작업 지시 가능)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true", help="브라우저 열기")
    sp.add_argument("-v", "--verbose", action="store_true", help="요청 로그 출력")

    sp = add("publish", cmd_publish, "정적 웹사이트 배포 (예: 웹 서버의 공개 폴더)")
    sp.add_argument("--to", metavar="DIR", help="배포 디렉토리 (기본: $DEVTRACK_PUBLISH_DIR > devtrack config publish)")

    sp = add("password", cmd_password,
             "웹 비밀번호 정하기 · 바꾸기 — 비밀번호를 넣은 브라우저만 기록을 봄 (정적 웹: Apache .htaccess, devtrack serve)",
             epilog="예: devtrack password   ·   devtrack password --status   ·   devtrack password --off")
    sp.add_argument("--off", action="store_true", help="비밀번호 풀기 (공개 폴더의 웹을 다시 누구나 볼 수 있게)")
    sp.add_argument("--status", action="store_true", help="비밀번호가 걸려 있는지 보기")
    sp.add_argument("--stdin", action="store_true", help="비밀번호를 표준입력의 첫 줄에서 읽기 (묻지 않음)")

    sp = add("mailbox", cmd_mailbox,
             "GitHub 이슈 우편함 — 정적 웹(공개 폴더)에서 'GitHub 이슈로 보내기' 한 지시 · 메시지 · 결정을 받아 기록",
             epilog="예: devtrack mailbox hyungyukang/devtrack-inbox   ·   devtrack mailbox (상태)   ·   "
                    "devtrack mailbox --check   ·   devtrack mailbox --off")
    sp.add_argument("repo", nargs="?", help="우편함으로 쓸 비공개 저장소 (<계정>/<저장소>) — 토큰을 묻고 확인한 뒤 켬")
    sp.add_argument("--stdin", action="store_true", help="토큰을 표준입력의 첫 줄에서 읽기 (묻지 않음)")
    sp.add_argument("--interval", type=int, help="확인 간격 (초, 기본 {})".format(mailbox.DEFAULT_INTERVAL))
    sp.add_argument("--agent", help="담당 AI 를 적지 않은 이슈의 담당 (기본 Claude)")
    sp.add_argument("--site", help="정적 웹 주소 — 이슈 답글에 작업 링크를 붙임 (웹에서 보낸 이슈에서 저절로 알아냄)")
    sp.add_argument("--allow-public", action="store_true", help="공개 저장소도 허용 (지시 내용이 누구에게나 보임)")
    sp.add_argument("--check", action="store_true", help="지금 바로 한 번 확인")
    sp.add_argument("--off", action="store_true", help="우편함 끄기")
    sp.add_argument("--forget-token", action="store_true", help="--off 와 함께: 저장한 토큰도 지우기")

    sp = add("demo", cmd_demo, "예시 데이터 생성")
    sp.add_argument("--force", action="store_true", help="기존 데이터 디렉토리를 비우고 생성")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    args.data, args.data_from = data_location(getattr(args, "data", None))
    args.author = getattr(args, "author", None) or os.environ.get("DEVTRACK_AGENT") or "Claude"
    args.json = getattr(args, "json", False)
    publish_dir = publish_location(args.data)[0] if args.cmd not in ("publish", "demo") else None
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
