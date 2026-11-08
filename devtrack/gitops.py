"""승인된 변경사항의 commit/push 를 위한 git 헬퍼."""

import hashlib
import os
import subprocess

from . import DevTrackError

# 커밋이 하나도 없는 저장소에서 diff 기준으로 쓰는 git 의 빈 트리 해시
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run(repo, args, check=True):
    proc = subprocess.run(
        ["git", "-C", repo] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise DevTrackError(
            "git {} 실패 (exit {}):\n{}".format(" ".join(args), proc.returncode, (err or out).strip())
        )
    return proc.returncode, out, err


def git(repo, *args):
    return _run(repo, args)[1]


def is_repo(path):
    if not path or not os.path.isdir(path):
        return False
    rc, _, _ = _run(path, ["rev-parse", "--git-dir"], check=False)
    return rc == 0


def require_repo(path):
    if not is_repo(path):
        raise DevTrackError("git 저장소가 아닙니다: {}".format(path))
    return os.path.abspath(path)


def current_branch(repo):
    rc, out, _ = _run(repo, ["symbolic-ref", "--short", "HEAD"], check=False)
    return out.strip() if rc == 0 else None


def remote_url(repo, remote="origin"):
    rc, out, _ = _run(repo, ["remote", "get-url", remote], check=False)
    return out.strip() if rc == 0 else None


def _base(repo):
    rc, _, _ = _run(repo, ["rev-parse", "--verify", "-q", "HEAD"], check=False)
    return "HEAD" if rc == 0 else EMPTY_TREE


def capture_diff(repo, files=None):
    """HEAD 대비 작업 트리의 전체 변경사항(스테이징 여부 무관 + 추적되지 않는 새 파일)."""
    paths = ["--"] + list(files) if files else []
    out = git(repo, "diff", "--no-color", "--no-ext-diff", _base(repo), *paths)
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", *paths)
    for name in sorted(n for n in untracked.split("\0") if n):
        # --no-index 는 차이가 있으면 exit 1 을 반환하므로 check 하지 않는다
        _, extra, _ = _run(repo, ["diff", "--no-color", "--no-index", "--", "/dev/null", name], check=False)
        out += extra
    return out


def fingerprint(diff_text):
    return hashlib.sha256(diff_text.encode("utf-8", "replace")).hexdigest()[:16]


def diffstat(diff_text):
    files = adds = dels = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    return {"files": files, "additions": adds, "deletions": dels}


def commit_and_push(repo, message, files=None, branch=None, push=True, remote="origin"):
    """변경사항을 커밋하고(선택) 푸시한다.

    커밋은 성공했지만 푸시가 실패한 경우 예외 대신 ``push_error`` 를 채워 반환한다.
    """
    repo = require_repo(repo)
    cur = current_branch(repo)
    if branch and cur != branch:
        raise DevTrackError(
            "현재 브랜치({})가 승인된 브랜치({})와 다릅니다. 브랜치를 맞춘 뒤 다시 실행하세요.".format(cur, branch)
        )
    if files:
        git(repo, "add", "-A", "--", *files)
    else:
        git(repo, "add", "-A")
    staged = git(repo, "diff", "--cached", "--name-only").strip()
    if not staged:
        raise DevTrackError("커밋할 변경 사항이 없습니다.")
    commit_args = ["commit", "-m", message]
    if files:
        commit_args += ["--"] + list(files)
    log = [git(repo, *commit_args).strip()]
    sha = git(repo, "rev-parse", "HEAD").strip()
    result = {"commit": sha, "branch": cur, "pushed": False, "push_error": None}
    if push:
        rc, out, err = _run(repo, ["push", "-u", remote, cur], check=False)
        log.append((out + err).strip())
        if rc == 0:
            result["pushed"] = True
        else:
            result["push_error"] = (err or out).strip() or "git push 실패 (exit {})".format(rc)
    result["output"] = "\n".join(x for x in log if x)
    return result
