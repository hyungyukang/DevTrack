"""오래 걸리는 실행 감시 — devtrack run · runs.

AI 가 빌드 · 시뮬레이션 · 학습처럼 오래 걸리는 명령을 실행할 때, DevTrack 이 그 명령을 떼어 낸(백그라운드) 실행기로 돌리며
정해진 간격(--every 분)마다 진행을 확인해 작업의 진행 기록에 남기고, 새 출력도 CPU 사용도 파일 변화도 없이 --stall 분이
지나면 '멈춤 의심' 문제를 연다 (다시 움직이면 저절로 닫음). 끝나거나 멈추면 담당 AI 의 받은 편지함에 [실행 알림] 을 남겨
inbox --wait · watch 를 깨우고, devtrack run 을 기다리던 AI 에게는 그 결과를 출력한다.

세 가지 방식:
  명령      devtrack run T -- make -j8     DevTrack 이 시작 (출력은 --log 또는 기록 폴더의 .agent/runs/<T>/<R>.log).
                                          작업이 취소 · 삭제되거나 중지 요청(웹 · runs --stop)이 오면 멈춘다.
  --pid     이미 실행 중인 프로세스를 따라감 (이 컴퓨터, 출력 파일은 --log)
  --slurm   Slurm 작업을 따라감 (squeue · sacct, 출력 파일은 --log 또는 scontrol 의 StdOut)
따라가기만 하는 실행은 작업이 취소돼도 따라가기만 그만두고, 명시적인 중지 요청에만 멈춘다 (Slurm 은 scancel).

실행 기록은 작업의 runs 목록 (Store.add_run · run_update), 기록 문구는 작업 언어로 여기서 쓴다 (TEXT).
"""

import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time

from . import DevTrackError, lang as _lang

ACTIVE = ("starting", "running", "stalled")
FINAL = ("done", "failed", "stopped", "lost")
RUN_STATES = {"starting": "시작 중", "running": "실행 중", "stalled": "멈춤 의심", "done": "끝남", "failed": "실패",
              "stopped": "중지됨", "lost": "감시 끊김"}
EVERY_DEFAULT = 10.0   # 분: 진행 확인 간격
STALL_DEFAULT = 15.0   # 분: 이만큼 아무 움직임이 없으면 멈춤 의심
TAIL_LINES = 20        # 실행 기록에 남기는 마지막 출력 줄 수 (웹에 보임)
LINE_MAX = 300
RUNNER = "DevTrack"    # 실행기가 남기는 기록의 작성자
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "devtrack")
SLURM_FAIL = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE")
STOP_GRACE = 10        # 초: SIGTERM 뒤 SIGKILL 까지

TEXT = {
    "ko": {
        "start": "실행 시작 {label}: {what} — {every}마다 확인, {stall} 동안 움직임이 없으면 멈춤 의심",
        "start_nostall": "실행 시작 {label}: {what} — {every}마다 확인 (출력 파일을 몰라 멈춤 감지는 하지 않음 — --log 로 지정)",
        "follow_pid": "PID {pid} 따라가기 ({cmd})",
        "follow_slurm": "Slurm 작업 {job} 따라가기",
        "start_fail": "실행 시작 실패 {label}: {err}",
        "check": "진행 확인 {label} · {elapsed}: {parts}",
        "out_lines": "새 출력 {n}줄", "out_some": "새 출력 있음", "out_none": "새 출력 없음 ({since}째)",
        "files": "파일 변화 {n}건",
        "cpu": "CPU 약 {cores}코어", "cpu_idle": "CPU 쉼",
        "progress": "진행 {pct}%", "eta": "남은 시간 약 {eta}",
        "queue": "Slurm 대기열 ({reason}, {since}째)", "slurm": "Slurm {state}{nodes}",
        "stall": "실행 멈춤 의심 {label}: {dur} 동안 {what} 없음",
        "w_out": "새 출력", "w_cpu": "CPU 사용", "w_files": "파일 변화", "w_join": " · ",
        "resume": "다시 움직임 ({label})",
        "done": "실행 끝남 {label}: {how} · {dur}{lines}",
        "failed": "실행 실패 {label}: {how} · {dur}{lines}",
        "stopped": "실행 중지 {label}: {why} · {dur}",
        "follow_end": "따라가기 끝 {label}: {why} — 감시만 그만둠 (프로세스 · Slurm 작업은 그대로)",
        "lost": "실행 감시 끊김 {label}: 실행기(PID {runner})가 없어졌습니다 — {state}",
        "lost_alive": "프로세스 {pid} 는 아직 실행 중 (진행 기록은 멈춤)", "lost_gone": "프로세스도 끝남 (결과는 로그로 확인)",
        "lost_unknown": "결과는 로그로 확인",
        "exit": "종료 코드 {code}", "signal": "신호 {sig} 로 끝남", "exit_unknown": "끝남 (종료 코드는 알 수 없음)",
        "slurm_end": "Slurm {state}{code}", "slurm_unknown": "Slurm 대기열에서 빠짐 (결과는 sacct 로 확인)",
        "lines": " · 출력 {n}줄",
        "why_user": "{who} 의 중지 요청", "why_cancel": "작업이 취소됨", "why_completed": "사용자가 작업을 완료로 처리함",
        "why_signal": "실행기가 중단됨", "why_slurm": "Slurm 에서 취소됨",
        "stop_req": "실행 중지 요청: {label}",
        "last": "마지막 출력:", "log": "전체 로그: {path}",
    },
    "en": {
        "start": "Run started {label}: {what} — checking every {every}, stall alert after {stall} without activity",
        "start_nostall": "Run started {label}: {what} — checking every {every} (no output file, so no stall alert — "
                         "give one with --log)",
        "follow_pid": "following PID {pid} ({cmd})",
        "follow_slurm": "following Slurm job {job}",
        "start_fail": "Run failed to start {label}: {err}",
        "check": "Progress check {label} · {elapsed}: {parts}",
        "out_lines": "{n} new output lines", "out_some": "new output", "out_none": "no new output (for {since})",
        "files": "{n} file changes",
        "cpu": "CPU ≈{cores} cores", "cpu_idle": "CPU idle",
        "progress": "{pct}% done", "eta": "≈{eta} left",
        "queue": "Slurm queue ({reason}, for {since})", "slurm": "Slurm {state}{nodes}",
        "stall": "Run may be stuck {label}: no {what} for {dur}",
        "w_out": "output", "w_cpu": "CPU use", "w_files": "file changes", "w_join": ", ",
        "resume": "Moving again ({label})",
        "done": "Run finished {label}: {how} · {dur}{lines}",
        "failed": "Run failed {label}: {how} · {dur}{lines}",
        "stopped": "Run stopped {label}: {why} · {dur}",
        "follow_end": "Stopped following {label}: {why} — the process or Slurm job keeps running",
        "lost": "Lost track of run {label}: the runner (PID {runner}) is gone — {state}",
        "lost_alive": "process {pid} is still running (progress is no longer recorded)",
        "lost_gone": "the process has ended too (check the log)", "lost_unknown": "check the log",
        "exit": "exit code {code}", "signal": "ended by signal {sig}", "exit_unknown": "ended (exit code unknown)",
        "slurm_end": "Slurm {state}{code}", "slurm_unknown": "left the Slurm queue (check sacct)",
        "lines": " · {n} output lines",
        "why_user": "stop requested by {who}", "why_cancel": "the task was cancelled",
        "why_completed": "the user marked the task done", "why_signal": "the runner was interrupted",
        "why_slurm": "cancelled in Slurm",
        "stop_req": "Stop requested: {label}",
        "last": "Last output:", "log": "Full log: {path}",
    },
}


def texts(en):
    return TEXT["en" if en else "ko"]


def poll_seconds():
    """실행기가 상태를 보는 간격 (초). 시험에서는 DEVTRACK_RUN_POLL 로 줄인다."""
    try:
        return max(0.05, float(os.environ.get("DEVTRACK_RUN_POLL") or 5))
    except ValueError:
        return 5.0


def label(run):
    return "{} '{}'".format(run["id"], run["name"]) if run.get("name") else run["id"]


def short(text, n=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def fmt_dur(sec, en=False):
    sec = max(0, int(round(sec or 0)))
    if sec < 60:
        return "{} s".format(sec) if en else "{}초".format(sec)
    m = int(round(sec / 60.0))
    if m < 60:
        return "{} min".format(m) if en else "{}분".format(m)
    h, m = divmod(m, 60)
    if en:
        return "{} h {} min".format(h, m) if m else "{} h".format(h)
    return "{}시간 {}분".format(h, m) if m else "{}시간".format(h)


def find_run(task, rid):
    rid = normalize_run_id(rid)
    for r in task.get("runs") or []:
        if r.get("id") == rid:
            return r
    raise DevTrackError("{} 에 실행 {} 이(가) 없습니다".format(task.get("id"), rid))


def normalize_run_id(rid):
    s = str(rid or "").strip().upper()
    if re.match(r"^\d+$", s):
        s = "R-" + s
    if not re.match(r"^R-\d+$", s):
        raise DevTrackError("실행 ID 형식이 아닙니다: {} (예: R-1)".format(rid))
    return s


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except OSError:
        return True
    return True


# ---------------------------------------------------------------------------- 출력 · 파일 · CPU 보기
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")


def clean_lines(text):
    """진행 막대(\\r 로 덮어쓴 줄)는 마지막 모습만, 색 코드 · 제어 문자는 뺀다."""
    out = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if "\r" in line:
            line = line.rsplit("\r", 1)[1]
        line = _ANSI.sub("", line)
        line = "".join(ch for ch in line if ch == "\t" or ch >= " ").rstrip()
        out.append(line if len(line) <= LINE_MAX else line[:LINE_MAX - 1] + "…")
    return out


def read_tail(path, n=TAIL_LINES, max_bytes=65536):
    """파일 끝의 n 줄 (정리한 줄)과, 진행률을 찾을 끝부분 글."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read(max_bytes)
    except (OSError, TypeError):
        return [], ""
    text = data.decode("utf-8", "replace")
    if size > max_bytes and "\n" in text:
        text = text.split("\n", 1)[1]
    lines = clean_lines(text)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:], "\n".join(lines)


class Growth(object):
    """출력 파일이 늘었는지와 새 줄 수 (새로 붙은 부분만 세어 큰 파일도 가볍게)."""

    def __init__(self, path, from_start=False):
        self.path, self.offset, self.lines = path, 0, 0
        if not from_start:
            try:
                self.offset = os.path.getsize(path)
            except OSError:
                pass

    def update(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return False
        if size < self.offset:  # 잘리거나 새 파일로 바뀜
            self.offset = 0
        if size == self.offset:
            return False
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                left = size - self.offset
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.lines += chunk.count(b"\n")
                    left -= len(chunk)
        except OSError:
            return False
        self.offset = size
        return True


def path_stamp(path):
    """파일은 (크기, 수정 시각), 폴더는 (항목 수, 가장 최근 수정 시각 — 바로 아래만). 없으면 None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isdir(path):
        return (st.st_size, st.st_mtime)
    newest, count = st.st_mtime, 0
    try:
        for e in os.scandir(path):
            count += 1
            try:
                newest = max(newest, e.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return (count, newest)


def _cpu_seconds(text):
    """ps 의 time 값 ([일-][시:]분:초[.xx]) → 초."""
    text = text.strip()
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    sec = 0.0
    for part in text.split(":"):
        sec = sec * 60 + float(part)
    return days * 86400 + sec


def tree_cpu(pid):
    """pid 와 그 자손 프로세스가 지금까지 쓴 CPU 시간(초). ps 가 없거나 그 프로세스가 없으면 None."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,time="], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    kids, cpu = {}, {}
    for line in out.splitlines():
        f = line.split()
        if len(f) < 3:
            continue
        try:
            p, pp, c = int(f[0]), int(f[1]), _cpu_seconds(f[2])
        except ValueError:
            continue
        cpu[p] = c
        kids.setdefault(pp, []).append(p)
    if int(pid) not in cpu:
        return None
    total, todo, seen = 0.0, [int(pid)], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        total += cpu.get(p, 0.0)
        todo.extend(kids.get(p, []))
    return total


def find_progress(text, pattern):
    """진행률(%) — 정규식의 마지막 일치에서: 묶음 하나면 그 값(%), 둘이면 앞/뒤 (예: '(\\d+)/(\\d+)')."""
    try:
        rx = re.compile(pattern)
    except (re.error, TypeError):
        return None
    last = None
    for m in rx.finditer(text or ""):
        last = m
    if not last:
        return None
    nums = []
    for g in (last.groups() or (last.group(0),)):
        if g is None:
            continue
        try:
            nums.append(float(g.replace(",", "")))
        except ValueError:
            pass
    if len(nums) >= 2 and nums[1] > 0:
        pct = 100.0 * nums[0] / nums[1]
    elif nums:
        pct = nums[0]
    else:
        return None
    return max(0.0, min(100.0, pct))


# ---------------------------------------------------------------------------- Slurm
def _cmd(argv, timeout=30):
    try:
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def slurm_state(job):
    """{"state", "reason", "nodes", "name"} — 대기열에 없으면 None, squeue 를 쓰지 못하면 'unknown'."""
    r = _cmd(["squeue", "-h", "-j", str(job), "-o", "%T|%r|%N|%j"])
    if r is None:
        return "unknown"
    if r.returncode != 0:
        return None if "invalid job id" in (r.stderr or "").lower() else "unknown"
    lines = [x for x in (r.stdout or "").splitlines() if x.strip()]
    if not lines:
        return None
    f = (lines[0].split("|") + ["", "", "", ""])[:4]
    return {"state": f[0].strip(), "reason": f[1].strip(), "nodes": f[2].strip(), "name": f[3].strip()}


def slurm_final(job):
    """(상태, 종료 코드) — sacct 를 쓰지 못하면 (None, None)."""
    r = _cmd(["sacct", "-n", "-X", "-P", "-j", str(job), "-o", "State,ExitCode"])
    if r is None or r.returncode != 0:
        return None, None
    for line in (r.stdout or "").splitlines():
        f = line.strip().split("|")
        if f and f[0].strip():
            code = f[1].split(":")[0] if len(f) > 1 and f[1] else None
            return f[0].split()[0], code
    return None, None


def slurm_stdout(job):
    r = _cmd(["scontrol", "show", "job", "-o", str(job)])
    if r is None or r.returncode != 0:
        return None
    m = re.search(r"\bStdOut=(\S+)", r.stdout or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------- 시작
def prepare(command=None, pid=None, job=None, name=None, every=None, stall=None, progress=None, log=None,
            watch=None, cwd=None):
    """devtrack run 의 인자를 실행 기록(Store.add_run 에 넘길 값)으로. 잘못된 값은 여기서 거절한다."""
    every = EVERY_DEFAULT if every is None else float(every)
    stall = STALL_DEFAULT if stall is None else float(stall)
    if every <= 0 or stall <= 0:
        raise DevTrackError("--every · --stall 은 0보다 커야 합니다 (분)")
    if sum(1 for x in (command, pid, job) if x) != 1:
        raise DevTrackError("실행할 명령(-- 명령) · --pid · --slurm 중 하나를 주세요")
    if progress:
        try:
            re.compile(progress)
        except re.error as e:
            raise DevTrackError("--progress 정규식 오류: {}".format(e))
    cwd = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    if not os.path.isdir(cwd):
        raise DevTrackError("실행할 폴더가 없습니다: {}".format(cwd))
    info = {"every": every * 60, "stall": stall * 60, "progress": progress or None, "cwd": cwd,
            "host": socket.gethostname(),
            "watch": [os.path.abspath(os.path.join(cwd, os.path.expanduser(p))) for p in (watch or [])]}
    if log:
        info["log_file"] = os.path.abspath(os.path.join(cwd, os.path.expanduser(log)))
    if command:
        if len(command) == 1:  # 한 덩어리로 준 명령은 셸로 (파이프 · && · 리다이렉트)
            info.update(mode="process", shell=command[0], command=command[0])
        else:
            exe = command[0]
            if os.sep not in exe and not shutil.which(exe):
                raise DevTrackError("명령을 찾을 수 없습니다: {}".format(exe))
            if os.sep in exe and not os.path.exists(os.path.join(cwd, exe)):
                raise DevTrackError("실행 파일이 없습니다: {}".format(exe))
            info.update(mode="process", argv=list(command), command=" ".join(shlex.quote(c) for c in command))
        info["name"] = name or short(info["command"], 40)
    elif pid:
        if not pid_alive(pid):
            raise DevTrackError("PID {} 프로세스가 없습니다 (이 컴퓨터에서 따라갈 수 있는 프로세스만)".format(pid))
        r = _cmd(["ps", "-o", "command=", "-p", str(pid)])
        cmdline = (r.stdout or "").strip() if r is not None and r.returncode == 0 else ""
        info.update(mode="pid", pid=int(pid), command=cmdline or "PID {}".format(pid))
        info["name"] = name or short(os.path.basename(cmdline.split()[0]) if cmdline else "PID {}".format(pid), 40)
    else:
        if not shutil.which("squeue"):
            raise DevTrackError("squeue 를 찾을 수 없습니다 — Slurm 이 있는 컴퓨터(로그인 노드)에서 실행하세요")
        st = slurm_state(job)
        if st is None:
            raise DevTrackError("Slurm 작업 {} 이(가) 대기열에 없습니다 (이미 끝났으면 sacct -j {} 로 확인)".format(job, job))
        info.update(mode="slurm", job=str(job), command="Slurm job {}".format(job))
        if isinstance(st, dict):
            info["slurm"] = st
        info["name"] = name or (st.get("name") if isinstance(st, dict) and st.get("name") else "Slurm {}".format(job))
        if not info.get("log_file"):
            out = slurm_stdout(job)
            if out:
                info["log_file"] = out
    return info


def start_message(run, en):
    W = texts(en)
    if run["mode"] == "pid":
        what = W["follow_pid"].format(pid=run["pid"], cmd=short(run.get("command"), 60))
    elif run["mode"] == "slurm":
        what = W["follow_slurm"].format(job=run["job"])
    else:
        what = short(run.get("command"), 120)
    can_stall = run["mode"] != "slurm" or run.get("log_file") or run.get("watch")
    return W["start" if can_stall else "start_nostall"].format(
        label=label(run), what=what, every=fmt_dur(run["every"], en), stall=fmt_dur(run["stall"], en))


def spawn(store, tid, rid, wait_start=20):
    """실행기를 떼어 내어(새 세션) 띄운다 — 띄운 쪽(AI 의 셸 · 대화)이 끝나도 실행과 확인은 계속된다."""
    t = store.load(tid)
    run = find_run(t, rid)
    run_dir = os.path.join(store.root, ".agent", "runs", t["id"])
    os.makedirs(run_dir, exist_ok=True)
    runner_log = os.path.join(run_dir, rid + ".runner.log")
    env = dict(os.environ, DEVTRACK_DATA=store.root)
    if store.publish_dir:
        env["DEVTRACK_PUBLISH_DIR"] = store.publish_dir
    with open(runner_log, "ab") as out:
        p = subprocess.Popen([sys.executable, BIN, "run", t["id"], "--runner", rid], stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                             cwd=run.get("cwd") or store.root, env=env)
    deadline = time.time() + wait_start
    while time.time() < deadline:
        run = find_run(store.load(tid), rid)
        if run["status"] != "starting":
            return run
        if p.poll() is not None:
            break
        time.sleep(0.1)
    if p.poll() is not None:
        run = find_run(store.load(tid), rid)
        if run["status"] != "starting":
            return run
        tail = read_tail(runner_log, 6)[0]
        en = _lang.task_lang(t) == "en"
        store.run_update(tid, rid, fields={"status": "failed", "ended_at": _now()},
                         log={"message": texts(en)["start_fail"].format(label=label(run), err="runner exited"),
                              "kind": "error", "detail": "\n".join(tail), "event": "failed"})
        raise DevTrackError("실행기를 시작하지 못했습니다{}".format(": " + tail[-1] if tail else ""))
    return run


def _now():
    from .store import now
    return now()


def who_name(who, en=False):
    from .store import USER
    if not who or who == USER:
        return "the user" if en else "사용자"
    return who


def run_age(run):
    """실행을 시작한 뒤 지난 초 (모르면 0)."""
    import datetime as _dt
    try:
        started = _dt.datetime.fromisoformat(run.get("started_at"))
        return max(0.0, (_dt.datetime.now(started.tzinfo) - started).total_seconds())
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------- 실행기 (감시 루프)
class Monitor(object):
    """한 실행을 끝날 때까지 지켜보며 기록한다 (떼어 낸 실행기 또는 --fg)."""

    def __init__(self, store, tid, rid):
        self.store, self.tid, self.rid = store, tid, normalize_run_id(rid)
        t = store.load(tid)
        self.tid = t["id"]
        self.en = _lang.task_lang(t) == "en"
        self.W = texts(self.en)
        r = find_run(t, self.rid)
        self.run = r
        self.label = label(r)
        self.mode = r["mode"]
        self.every, self.stall = float(r["every"]), float(r["stall"])
        self.poll = poll_seconds()
        self.sample = min(30.0, max(self.poll, self.every / 4.0, 0.05))
        self.log_path = r.get("log_file")
        self.growth = None
        self.watch = [[p, path_stamp(p)] for p in r.get("watch") or []]
        self.pid, self.child = r.get("pid"), None
        self.stop_reason, self.stop_by, self.signal = None, None, None
        self.t0 = time.time()
        self.last_activity = self.last_output = self.t0
        self.cpu, self.cpu_known, self.cpu_used, self.cpu_used_at_check = None, False, 0.0, 0.0
        self.lines_at_check, self.files_since = 0, 0
        self.out_since_check = False
        self.last_check = self.t0
        self.stalled_since = None
        self.slurm = r.get("slurm") if isinstance(r.get("slurm"), dict) else None
        self.running_since = None if self.mode == "slurm" else self.t0
        self.queued_since = self.t0
        self.progress, self.progress_first, self.eta = None, None, None

    # -------------------------------------------------------------- 시작
    def begin(self):
        runner = {"pid": os.getpid(), "host": socket.gethostname()}
        if self.mode == "process":
            if not self.log_path:
                self.log_path = os.path.join(self.store.root, ".agent", "runs", self.tid, self.rid + ".log")
            env = dict(os.environ, DEVTRACK_TASK=self.tid, DEVTRACK_RUN=self.rid)
            env.setdefault("PYTHONUNBUFFERED", "1")  # 파이썬 프로그램의 출력이 버퍼에 묶여 '멈춤' 으로 보이지 않게
            argv = self.run.get("argv") or [shutil.which("bash") or "/bin/sh", "-c", self.run["shell"]]
            try:
                d = os.path.dirname(self.log_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                with open(self.log_path, "wb") as out:
                    self.child = subprocess.Popen(argv, cwd=self.run.get("cwd"), stdin=subprocess.DEVNULL, stdout=out,
                                                  stderr=subprocess.STDOUT, start_new_session=True, env=env)
            except OSError as e:
                self.store.run_update(self.tid, self.rid, fields={"status": "failed", "ended_at": _now(),
                                                                  "runner": runner},
                                      log={"message": self.W["start_fail"].format(label=self.label, err=e),
                                           "kind": "error", "event": "failed"})
                return False
            self.pid = self.child.pid
            self.growth = Growth(self.log_path, from_start=True)
            self.store.run_update(self.tid, self.rid, fields={"status": "running", "pid": self.pid, "runner": runner,
                                                              "log_file": self.log_path, "checked_at": _now()})
        else:
            if self.log_path:
                self.growth = Growth(self.log_path)
            self.store.run_update(self.tid, self.rid, fields={"status": "running", "runner": runner,
                                                              "log_file": self.log_path, "checked_at": _now()})
        return True

    # -------------------------------------------------------------- 상태 보기
    def _ended(self, sample, now_t):
        if self.mode == "process":
            rc = self.child.poll()
            return None if rc is None else {"rc": rc}
        if self.mode == "pid":
            return None if pid_alive(self.pid) else {"rc": None}
        if not sample:
            return None
        st = slurm_state(self.run["job"])
        if st == "unknown":
            return None
        if st is None:
            state, code = slurm_final(self.run["job"])
            return {"slurm": state, "code": code}
        self.slurm = st
        if st["state"] == "RUNNING" and self.running_since is None:
            self.running_since = now_t
            self.last_activity = now_t
        return None

    def _activity(self, now_t, sample):
        if self.growth is None and self.log_path and self.mode == "slurm":
            self.growth = Growth(self.log_path)
        if self.growth is not None and self.growth.update():
            self.last_activity = self.last_output = now_t
            self.out_since_check = True
        if not sample:
            return
        for w in self.watch:
            st = path_stamp(w[0])
            if st is not None and st != w[1]:
                self.files_since += 1
                self.last_activity = now_t
                w[1] = st
        if self.mode in ("process", "pid") and self.pid:
            c = tree_cpu(self.pid)
            if c is not None:
                self.cpu_known = True
                if self.cpu is not None:
                    d = c - self.cpu
                    if d >= 0.5:
                        self.last_activity = now_t
                    if d > 0:
                        self.cpu_used += d
                self.cpu = c

    def _check_stop(self):
        try:
            t = self.store.load(self.tid)
        except DevTrackError:
            self.stop_reason = "deleted"
            return
        try:
            r = find_run(t, self.rid)
        except DevTrackError:
            self.stop_reason = "deleted"
            return
        req = r.get("stop_requested")
        if req:
            self.stop_reason, self.stop_by = "user", req.get("by")
        elif t.get("status") == "cancelled":
            self.stop_reason = "cancel"
        elif t.get("status") == "done" and (t.get("completed") or {}).get("by_user"):
            self.stop_reason = "completed"

    # -------------------------------------------------------------- 멈추기 · 끝내기
    def _wait_gone(self, alive, grace=STOP_GRACE):
        end = time.time() + grace
        while time.time() < end:
            if not alive():
                return True
            time.sleep(0.2)
        return not alive()

    def _stop(self):
        if self.mode == "process":  # DevTrack 이 시작한 명령: 어떤 이유든 멈춘다
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.child.pid, sig)
                except OSError:
                    pass
                try:
                    self.child.wait(timeout=STOP_GRACE)
                    break
                except subprocess.TimeoutExpired:
                    continue
            return {"rc": self.child.wait(), "stopped": True}
        follow_only = self.stop_reason != "user"  # 따라가기만 한 실행은 명시적인 중지 요청에만 멈춘다
        if self.mode == "pid" and not follow_only:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(self.pid, sig)
                except OSError:
                    pass
                if self._wait_gone(lambda: pid_alive(self.pid)):
                    break
        if self.mode == "slurm" and not follow_only:
            _cmd(["scancel", str(self.run["job"])])
        return {"rc": None, "stopped": True, "follow_only": follow_only}

    def _tail(self):
        if not self.log_path:
            return [], ""
        return read_tail(self.log_path)

    def _finish(self, end):
        W, en = self.W, self.en
        if self.stop_reason == "deleted":
            return 1  # 작업이 지워졌으므로 기록하지 않는다
        dur = time.time() - self.t0
        if self.growth is not None:
            self.growth.update()
        tail, text = self._tail()
        lines = self.growth.lines if self.growth is not None else None
        if self.run.get("progress"):
            p = find_progress(text, self.run["progress"])
            if p is not None:
                self.progress = p
        lines_txt = W["lines"].format(n="{:,}".format(lines)) if lines else ""
        code = None
        if end.get("stopped"):
            why = {"user": W["why_user"].format(who=who_name(self.stop_by, self.en)), "cancel": W["why_cancel"],
                   "completed": W["why_completed"], "signal": W["why_signal"]}.get(self.stop_reason, self.stop_reason)
            # 작업이 취소 · 완료 처리돼 멈춘 것은 AI 가 [취소] · [완료 처리] 로 이미 안다
            status, kind = "stopped", "warning"
            event = None if self.stop_reason in ("cancel", "completed") else "stopped"
            msg = (W["follow_end"].format(label=self.label, why=why) if end.get("follow_only")
                   else W["stopped"].format(label=self.label, why=why, dur=fmt_dur(dur, en)))
            code = end.get("rc")
        elif "slurm" in end:
            state, c = end.get("slurm"), end.get("code")
            code = int(c) if c and c.lstrip("-").isdigit() else None
            if state:
                self.slurm = dict(self.slurm or {}, state=state, reason="")
            if state is None:
                status, how = "done", W["slurm_unknown"]
            else:
                how = W["slurm_end"].format(state=state, code=" ({})".format(W["exit"].format(code=code))
                                            if code is not None else "")
                status = ("failed" if state in SLURM_FAIL else "stopped" if state.startswith("CANCEL")
                          else "done" if state == "COMPLETED" else "failed")
            kind, event = {"done": ("success", "done"), "failed": ("error", "failed"),
                           "stopped": ("warning", "stopped")}[status]
            msg = (W["stopped"].format(label=self.label, why=W["why_slurm"], dur=fmt_dur(dur, en)) if status == "stopped"
                   else W[status].format(label=self.label, how=how, dur=fmt_dur(dur, en), lines=lines_txt))
        else:
            rc = end.get("rc")
            code = rc
            if rc is None:
                status, how = "done", W["exit_unknown"]
            elif rc == 0:
                status, how = "done", W["exit"].format(code=0)
            elif rc < 0:
                status, how = "failed", W["signal"].format(sig=-rc)
            else:
                status, how = "failed", W["exit"].format(code=rc)
            kind, event = ("success", "done") if status == "done" else ("error", "failed")
            msg = W[status].format(label=self.label, how=how, dur=fmt_dur(dur, en), lines=lines_txt)
        detail = self._detail(tail, 8)
        fields = {"status": status, "ended_at": _now(), "checked_at": _now(), "exit_code": code, "duration": round(dur),
                  "tail": tail, "lines": lines, "eta": None, "progress": self.progress, "slurm": self.slurm}
        self.store.run_update(self.tid, self.rid, fields=fields,
                              log={"message": msg, "kind": kind, "detail": detail, "event": event},
                              resolve_issue=msg)
        return 0 if status == "done" else 1

    def _detail(self, tail, n):
        parts = []
        if tail:
            parts.append(self.W["last"] + "\n" + "\n".join(tail[-n:]))
        if self.log_path:
            parts.append(self.W["log"].format(path=self.log_path))
        return "\n\n".join(parts) or None

    # -------------------------------------------------------------- 멈춤 의심 · 진행 확인
    def _stall_check(self, now_t):
        if self.running_since is None:
            return
        if self.growth is None and not self.watch and not self.cpu_known:
            return  # 볼 것이 없음
        if self.stalled_since is None and now_t - self.last_activity >= self.stall:
            self.stalled_since = now_t
            tail, _ = self._tail()
            what = [self.W[k] for k, on in (("w_out", self.growth is not None), ("w_cpu", self.cpu_known),
                                            ("w_files", bool(self.watch))) if on]
            if self.en and len(what) > 1:
                what = what[:-1] + ["or " + what[-1]]
            title = self.W["stall"].format(label=self.label, dur=fmt_dur(now_t - self.last_activity, self.en),
                                           what=self.W["w_join"].join(what))
            self.store.run_update(self.tid, self.rid, fields={"status": "stalled", "stalled_at": _now(), "tail": tail},
                                  issue={"title": title, "detail": self._detail(tail, 8), "severity": "medium",
                                         "event": "stalled"}, bump="stalls")
            return
        if self.stalled_since is not None and self.last_activity > self.stalled_since:
            self.stalled_since = None
            self.store.run_update(self.tid, self.rid, fields={"status": "running"},
                                  resolve_issue=self.W["resume"].format(label=self.label))

    def _report(self, now_t):
        W, en = self.W, self.en
        window = max(1.0, now_t - self.last_check)
        parts = []
        if self.growth is not None:
            n = self.growth.lines - self.lines_at_check
            if n > 0:
                parts.append(W["out_lines"].format(n="{:,}".format(n)))
            elif self.out_since_check:
                parts.append(W["out_some"])
            else:
                parts.append(W["out_none"].format(since=fmt_dur(now_t - self.last_output, en)))
        if self.watch and self.files_since:
            parts.append(W["files"].format(n=self.files_since))
        cores = None
        if self.cpu_known:
            used = self.cpu_used - self.cpu_used_at_check
            cores = round(used / window, 1)
            parts.append(W["cpu"].format(cores=cores) if used >= 0.5 else W["cpu_idle"])
        if self.mode == "slurm" and self.slurm:
            if self.slurm.get("state") == "PENDING":
                parts.append(W["queue"].format(reason=self.slurm.get("reason") or "?",
                                               since=fmt_dur(now_t - self.queued_since, en)))
            else:
                nodes = self.slurm.get("nodes")
                parts.append(W["slurm"].format(state=self.slurm.get("state"), nodes=" ({})".format(nodes) if nodes else ""))
        tail, text = self._tail()
        if self.run.get("progress"):
            p = find_progress(text, self.run["progress"])
            if p is not None:
                self.progress = p
                if self.progress_first is None or p < self.progress_first[1]:
                    self.progress_first = (now_t, p)
                elif p > self.progress_first[1] and p < 100:
                    rate = (p - self.progress_first[1]) / max(1.0, now_t - self.progress_first[0])
                    self.eta = (100 - p) / rate if rate > 0 else None
                parts.append(W["progress"].format(pct=int(p) if p == int(p) else round(p, 1)))
                if self.eta:
                    parts.append(W["eta"].format(eta=fmt_dur(self.eta, en)))
        msg = W["check"].format(label=self.label, elapsed=fmt_dur(now_t - self.t0, en), parts=" · ".join(parts) or "—")
        fields = {"checked_at": _now(), "tail": tail, "lines": self.growth.lines if self.growth is not None else None,
                  "progress": self.progress, "eta": round(self.eta) if self.eta else None, "cpu_cores": cores,
                  "slurm": self.slurm}
        self.store.run_update(self.tid, self.rid, fields=fields,
                              log={"message": msg, "kind": "run", "detail": "\n".join(tail[-3:]) or None},
                              bump="checks")
        self.last_check = now_t
        self.lines_at_check = self.growth.lines if self.growth is not None else 0
        self.cpu_used_at_check = self.cpu_used
        self.files_since = 0
        self.out_since_check = False

    # -------------------------------------------------------------- 루프
    def _on_signal(self, signum, frame):
        self.signal = signum

    def loop(self):
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(s, self._on_signal)
            except (ValueError, OSError):
                pass
        if not self.begin():
            return 127
        last_sample = last_task = 0.0
        while True:
            now_t = time.time()
            sample = now_t - last_sample >= self.sample
            if sample:
                last_sample = now_t
            end = self._ended(sample, now_t)
            self._activity(now_t, sample)
            if now_t - last_task >= 2 * self.poll:
                last_task = now_t
                self._check_stop()
            if self.signal and not self.stop_reason:
                self.stop_reason = "signal"
            if self.stop_reason and end is None:
                end = self._stop()
            if end is not None:
                if self.stop_reason and "stopped" not in end:
                    end["stopped"] = True
                return self._finish(end)
            try:  # 기록 폴더에 잠깐 쓰지 못해도(공유 파일시스템 등) 감시는 계속한다 — 다음 차례에 다시
                self._stall_check(now_t)
                if now_t - self.last_check >= self.every:
                    self._report(now_t)
            except OSError as e:
                sys.stderr.write("[devtrack run] 기록 실패 (다음 확인 때 다시): {}\n".format(e))
                self.last_check = now_t
            time.sleep(self.poll)


def monitor(store, tid, rid):
    """실행기 본체. 예상하지 못한 오류로 감시가 끝나면 '감시 끊김' 으로 남긴다 (명령은 그대로 — 결과는 로그로)."""
    import traceback
    try:
        return Monitor(store, tid, rid).loop()
    except Exception as e:  # noqa — 실행기가 조용히 죽어 '실행 중' 으로 남지 않게
        traceback.print_exc()
        try:
            t = store.load(tid)
            r = find_run(t, rid)
            if r.get("status") in ACTIVE:
                W = texts(_lang.task_lang(t) == "en")
                store.run_update(t["id"], r["id"], fields={"status": "lost", "ended_at": _now()},
                                 log={"message": W["lost"].format(label=label(r), runner=os.getpid(),
                                                                  state=W["lost_unknown"]),
                                      "kind": "warning", "detail": "{}: {}".format(type(e).__name__, e), "event": "lost"})
        except Exception:  # noqa
            pass
        return 1


# ---------------------------------------------------------------------------- 기다리기 · 중지 · 끊긴 감시
def check_lost(store, tid=None):
    """이 컴퓨터에서 실행기가 없어진 실행을 '감시 끊김' 으로 (실행기가 죽었거나 컴퓨터가 다시 켜짐). 고친 수."""
    host, n = socket.gethostname(), 0
    tasks = [store.load(tid)] if tid else store.all_tasks()
    for t in tasks:
        for r in t.get("runs") or []:
            rn = r.get("runner") or {}
            if r.get("status") not in ACTIVE or rn.get("host") != host or not rn.get("pid") or pid_alive(rn["pid"]):
                continue
            # 실행기가 막 끝을 기록하고 끝났을 수 있으니 다시 읽어 본다
            fresh = find_run(store.load(t["id"]), r["id"])
            if fresh.get("status") not in ACTIVE:
                continue
            en = _lang.task_lang(t) == "en"
            W = texts(en)
            pid = r.get("pid")
            if r.get("mode") in ("process", "pid") and pid:
                state = W["lost_alive" if pid_alive(pid) else "lost_gone"].format(pid=pid)
            else:
                state = W["lost_unknown"]
            store.run_update(t["id"], r["id"], fields={"status": "lost", "ended_at": _now()},
                             log={"message": W["lost"].format(label=label(r), runner=rn["pid"], state=state),
                                  "kind": "warning", "event": "lost"}, resolve_issue=W["lost"].format(
                                      label=label(r), runner=rn["pid"], state=state))
            n += 1
    return n


def wait(store, tid, rid, timeout=None, out=print):
    """실행이 끝나거나 새로 멈춤 의심이 될 때까지 기다린다 → 종료 코드 (0 끝남 · 1 실패 · 중지 · 끊김 · 4 멈춤 의심 · 3 시간 초과).
    알린 [실행 알림] 은 처리 표시한다 (받은 편지함에 같은 알림이 다시 뜨지 않게)."""
    rid = normalize_run_id(rid)
    t = store.load(tid)
    tid = t["id"]
    run = find_run(t, rid)
    stalls0 = run.get("stalls", 0)  # 지금 멈춤 의심이면 그다음 멈춤 · 끝을 기다린다
    t0, last_lost = time.time(), 0.0
    step = min(3.0, max(0.1, poll_seconds()))
    while run.get("status") not in FINAL and run.get("stalls", 0) <= stalls0:
        if time.time() - last_lost >= 30:
            last_lost = time.time()
            check_lost(store, tid)
        if timeout and time.time() - t0 >= timeout:
            out("{} 은(는) 아직 {} — 기다리기만 그만둡니다 (실행은 계속). 다시: devtrack runs {} {} --wait".format(
                label(run), RUN_STATES.get(run.get("status"), run.get("status")), tid, rid))
            return 3
        time.sleep(step)
        try:
            run = find_run(store.load(tid), rid)
        except DevTrackError as e:
            out(str(e))
            return 1
    store.ack_run_events(tid, rid)
    for line in summary_lines(tid, run):
        out(line)
    st = run.get("status")
    return 0 if st == "done" else 4 if st == "stalled" else 1


def summary_lines(tid, run, tail=10):
    """AI 에게 보여 줄 실행 요약 (CLI 출력 — 한국어)."""
    st = run.get("status")
    out = ["{} {} — {}".format(tid, label(run), RUN_STATES.get(st, st))]
    bits = []
    if run.get("exit_code") is not None:
        bits.append("종료 코드 {}".format(run["exit_code"]))
    if run.get("duration") is not None:
        bits.append(fmt_dur(run["duration"]))
    elif run.get("started_at"):
        bits.append("시작 {}".format(run["started_at"][11:16]))
    if run.get("lines"):
        bits.append("출력 {:,}줄".format(run["lines"]))
    if run.get("progress") is not None:
        bits.append("진행 {}%".format(int(run["progress"])))
    if run.get("eta"):
        bits.append("남은 시간 약 {}".format(fmt_dur(run["eta"])))
    if run.get("slurm"):
        s = run["slurm"]
        bits.append("Slurm {}{}".format(s.get("state"), " ({})".format(s.get("reason")) if s.get("state") == "PENDING" else ""))
    if bits:
        out.append("  " + " · ".join(bits))
    out.append("  명령: {}".format(short(run.get("command"), 160)))
    lines = (run.get("tail") or [])[-tail:]
    if lines:
        out.append("  마지막 출력:")
        out.extend("    " + x for x in lines)
    if run.get("log_file"):
        out.append("  전체 로그: {}".format(run["log_file"]))
    rid = run["id"]
    if st == "stalled":
        out.append("→ 실행은 계속되고 있습니다. 로그에서 막힌 곳을 찾으세요: devtrack runs {} {} --log 80".format(tid, rid))
        out.append("  계속 기다리기: devtrack runs {} {} --wait (백그라운드로) · 멈추기: devtrack runs {} {} --stop".format(
            tid, rid, tid, rid))
    elif st in ACTIVE:
        out.append("→ 기다리기: devtrack runs {} {} --wait (백그라운드로) · 멈추기: devtrack runs {} {} --stop".format(
            tid, rid, tid, rid))
    return out


def stop(store, tid, rid, author, out=print):
    """중지 요청 — 실행기가 멈춘다 (명령은 종료, --pid 는 SIGTERM, --slurm 은 scancel). 실행기가 없으면 여기서 직접."""
    rid = normalize_run_id(rid)
    run = store.request_run_stop(tid, rid, author=author)
    tid = store.load(tid)["id"]
    rn = run.get("runner") or {}
    here = rn.get("host") == socket.gethostname()
    if here and rn.get("pid") and pid_alive(rn["pid"]):
        deadline = time.time() + STOP_GRACE + 10
        while time.time() < deadline:
            run = find_run(store.load(tid), rid)
            if run.get("status") not in ACTIVE:
                break
            time.sleep(0.3)
        store.ack_run_events(tid, rid)  # 멈추게 한 쪽이 알고 있으므로 [실행 알림] 은 보내지 않는다
        out("{} {} — {}".format(tid, label(run), RUN_STATES.get(run["status"], run["status"])))
        return 0 if run.get("status") not in ACTIVE else 1
    if not here and rn.get("host"):
        out("{} 의 실행기는 다른 컴퓨터({})에 있습니다 — 그 실행기가 다음 확인 때 멈춥니다".format(label(run), rn["host"]))
        return 0
    # 실행기가 없음: 이 컴퓨터의 프로세스면 직접 멈추고 기록
    pid = run.get("pid")
    if run.get("mode") == "process" and pid and pid_alive(pid):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(int(pid), sig)
            except OSError:
                pass
            end = time.time() + STOP_GRACE
            while time.time() < end and pid_alive(pid):
                time.sleep(0.2)
            if not pid_alive(pid):
                break
    elif run.get("mode") == "pid" and pid and pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
    elif run.get("mode") == "slurm":
        _cmd(["scancel", str(run.get("job"))])
    t = store.load(tid)
    W = texts(_lang.task_lang(t) == "en")
    store.run_update(tid, rid, fields={"status": "stopped", "ended_at": _now()},
                     log={"message": W["stopped"].format(label=label(run), why=W["why_user"].format(
                         who=who_name(author, W is TEXT["en"])),
                                                         dur=fmt_dur(run_age(run), W is TEXT["en"])),
                          "kind": "warning"}, resolve_issue=W["why_user"].format(who=who_name(author, W is TEXT["en"])))
    out("{} {} — 중지됨 (실행기가 없어 직접 멈춤)".format(tid, label(run)))
    return 0
