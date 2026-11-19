"""승인된 변경사항의 commit/push 를 위한 git 헬퍼."""

import fnmatch
import hashlib
import os
import subprocess
import tempfile

from . import DevTrackError

# 커밋이 하나도 없는 저장소에서 diff 기준으로 쓰는 git 의 빈 트리 해시
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run(repo, args, check=True, env=None):
    proc = subprocess.run(
        ["git", "-C", repo] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
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


# ---------------------------------------------------------------------------- 바뀐 파일 · 빌드 결과물 제외
# diff · 커밋에는 바뀐 코드만 넣는다. 빌드 결과물은 .gitignore 에 없어도 뺀다 (어떤 파일을 뺐는지는 따로 알려 준다).
CMAKE_MARKER = "CMakeCache.txt"            # 이 파일이 있는 폴더 = CMake 빌드 폴더 (추적되는 파일이 없으면 통째로 뺌)
BUILD_DIR_NAMES = ("CMakeFiles", "__pycache__")  # 이 이름의 폴더 아래는 어디서든 뺌
BUILD_FILE_NAMES = ("CMakeCache.txt", "cmake_install.cmake", "CTestTestfile.cmake", "install_manifest.txt",
                    ".ninja_deps", ".ninja_log")
# 아래는 git 이 추적하지 않는 새 파일에만 (저장소에 올려 둔 파일은 일부러 둔 것으로 본다)
UNTRACKED_BUILD_DIRS = ("build", "_build", "builddir", "build-*", "build_*", "cmake-build-*")
UNTRACKED_BUILD_FILES = ("compile_commands.json", "build.ninja", "a.out")
UNTRACKED_BUILD_EXT = (".o", ".obj", ".a", ".lib", ".so", ".dylib", ".dll", ".exe", ".pyc", ".pyo", ".class",
                       ".gch", ".pch", ".mod", ".smod")  # .mod · .smod = Fortran 모듈 (go.mod 는 제외)
# 실행 파일 · 오브젝트 · 라이브러리의 첫 바이트 (ELF, Mach-O, 유니버설 바이너리, 정적 라이브러리, Windows PE)
BINARY_MAGIC = (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
                b"\xca\xfe\xba\xbe", b"!<arch>\n", b"MZ")


def _literal_env(index=None):
    env = dict(os.environ, GIT_LITERAL_PATHSPECS="1")  # 파일 이름의 * ? [ 를 패턴으로 보지 않는다
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


def _chunks(items, n=200):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def toplevel(repo, files=None):
    """저장소 맨 위 폴더와, 그 폴더 기준으로 고친 files (repo 가 하위 폴더여도 경로가 어긋나지 않게)."""
    rc, out, _ = _run(repo, ["rev-parse", "--show-toplevel"], check=False)
    top = out.strip() if rc == 0 and out.strip() else repo
    rel = os.path.relpath(os.path.realpath(repo), os.path.realpath(top))
    if files and rel not in (".", ""):
        files = [os.path.normpath(os.path.join(rel, f)) for f in files]
    return top, files


def changed_paths(repo, files=None):
    """HEAD 대비 바뀐 파일 [(저장소 맨 위 기준 경로, 상태)] — 스테이징 여부와 상관없이, .gitignore 는 지킨다.
    상태: 'head' (커밋된 파일) · 'added' (스테이징만 한 새 파일) · 'untracked' (git 이 모르는 새 파일)."""
    args = ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"]
    out = git(repo, *(args + (["--"] + list(files) if files else [])))
    found = []
    for entry in out.split("\0"):
        if len(entry) > 3:
            code = entry[:2]
            found.append((entry[3:], "untracked" if code == "??" else "added" if code[0] == "A" else "head"))
    return found


class _BuildFilter(object):
    def __init__(self, repo):
        self.repo = repo
        self._marker = {}
        self._tracked = {}

    def _has_tracked(self, d):
        """d 폴더 아래에 커밋된 파일이 있는지 (있으면 소스 폴더 — 통째로 빼지 않는다)."""
        if d not in self._tracked:
            base = _base(self.repo)
            if base == EMPTY_TREE:
                self._tracked[d] = False
            else:
                rc, out, _ = _run(self.repo, ["ls-tree", "-r", "--name-only", "-z", base, "--", d or "."],
                                  check=False, env=_literal_env())
                self._tracked[d] = rc != 0 or bool(out.strip("\0"))
        return self._tracked[d]

    def _cmake_any(self, path):
        """path 의 폴더나 그 위에 CMakeCache.txt 가 있는지 (소스 폴더 안에서 빌드한 경우 포함)."""
        d = os.path.dirname(path)
        while True:
            if os.path.isfile(os.path.join(self.repo, d, CMAKE_MARKER)):
                return True
            if not d:
                return False
            d = os.path.dirname(d)

    def _cmake_dir(self, path):
        """path 를 담은 CMake 빌드 폴더 (추적되는 파일이 없는 폴더만). 없으면 None."""
        d = os.path.dirname(path)
        while True:
            if d not in self._marker:
                self._marker[d] = (os.path.isfile(os.path.join(self.repo, d, CMAKE_MARKER))
                                   and not self._has_tracked(d))
            if self._marker[d]:
                return d
            if not d:
                return None
            d = os.path.dirname(d)

    def reason(self, path, tracked):
        """빌드 결과물이면 (묶을 이름, 이유), 아니면 None. tracked: HEAD 에 커밋된 파일인지."""
        parts = path.split("/")
        name = parts[-1]
        if not tracked:
            d = self._cmake_dir(path)
            if d:
                return d + "/", "CMake 빌드 폴더"
        for i, part in enumerate(parts[:-1]):
            if part in BUILD_DIR_NAMES:
                return "/".join(parts[:i + 1]) + "/", "{} 폴더".format(part)
        if name in BUILD_FILE_NAMES:
            return path, "CMake 가 만든 파일"
        if tracked:
            return None
        for i, part in enumerate(parts[:-1]):
            if any(fnmatch.fnmatchcase(part, pat) for pat in UNTRACKED_BUILD_DIRS):
                return "/".join(parts[:i + 1]) + "/", "빌드 폴더"
        if name in UNTRACKED_BUILD_FILES:
            return path, "빌드가 만든 파일"
        if name in ("Makefile", "build.ninja") and self._cmake_any(path):
            return path, "CMake 가 만든 파일"
        ext = os.path.splitext(name)[1].lower()
        if ext in UNTRACKED_BUILD_EXT and name != "go.mod":
            return "*" + ext, "컴파일 결과물"
        try:
            with open(os.path.join(self.repo, path), "rb") as fh:
                head = fh.read(8)
        except (IOError, OSError):
            return None
        if any(head.startswith(m) for m in BINARY_MAGIC):
            return path, "실행 파일 · 라이브러리"
        return None


def capture_changes(repo, files=None):
    """HEAD 대비 바뀐 코드 (스테이징 여부 무관 + 추적되지 않는 새 파일, 빌드 결과물은 뺌).
    → {"diff": 전체 diff, "paths": diff 에 넣은 파일, "excluded": [{"path": 폴더 · 파일 · *.확장자, "reason", "count"}]}
    files 에 파일 이름을 그대로 적은 것은 빌드 결과물처럼 보여도 넣는다. 경로는 저장소 맨 위 기준."""
    repo, files = toplevel(repo, files)
    explicit = set(os.path.normpath(f) for f in files or [])
    keep_tracked, keep_untracked, groups = [], [], {}
    flt = _BuildFilter(repo)
    for path, state in changed_paths(repo, files):
        why = None if path in explicit else flt.reason(path, state == "head")
        if why:
            groups[why] = groups.get(why, 0) + 1
        else:
            (keep_untracked if state == "untracked" else keep_tracked).append(path)
    if not groups:  # 뺄 것이 없으면 예전과 같은 한 번의 diff (같은 변경이면 같은 지문)
        out = git(repo, "diff", "--no-color", "--no-ext-diff", _base(repo), *(["--"] + list(files) if files else []))
    else:
        out = ""
        for chunk in _chunks(sorted(keep_tracked)):
            out += _run(repo, ["diff", "--no-color", "--no-ext-diff", _base(repo), "--"] + chunk,
                        env=_literal_env())[1]
    for name in sorted(keep_untracked):
        # --no-index 는 차이가 있으면 exit 1 을 반환하므로 check 하지 않는다
        _, extra, _ = _run(repo, ["diff", "--no-color", "--no-index", "--", "/dev/null", name], check=False)
        out += extra
    excluded = [{"path": g, "reason": why, "count": n}
                for (g, why), n in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0][0]))]
    return {"diff": out, "paths": sorted(keep_tracked + keep_untracked), "excluded": excluded}


def capture_diff(repo, files=None):
    """HEAD 대비 바뀐 코드의 diff (빌드 결과물 제외 — capture_changes)."""
    return capture_changes(repo, files)["diff"]


def excluded_text(excluded, limit=6):
    """'build/ (CMake 빌드 폴더) 1,234개 · *.o (컴파일 결과물) 12개' 처럼."""
    parts = ["{} ({}){}".format(e["path"], e["reason"], " {:,}개".format(e["count"]) if e["count"] > 1 else "")
             for e in excluded[:limit]]
    if len(excluded) > limit:
        parts.append("외 {}곳".format(len(excluded) - limit))
    return " · ".join(parts)


def _addable(repo, paths, env=None):
    """git add 에 넘길 수 있는 경로 — 작업 폴더에도 index 에도 없는 경로(git rm · git mv 로 삭제를 이미 스테이징한 파일)는
    add 하면 'pathspec did not match' 로 실패하므로 뺀다 (삭제는 이미 스테이징되어 있음)."""
    gone = [p for p in paths if not os.path.lexists(os.path.join(repo, p))]
    in_index = set()
    for chunk in _chunks(gone):
        out = _run(repo, ["ls-files", "-z", "--"] + chunk, env=env or _literal_env())[1]
        in_index.update(x for x in out.split("\0") if x)
    skip = set(gone) - in_index
    return [p for p in paths if p not in skip]


def snapshot_tree(repo, paths):
    """HEAD 에 paths 의 지금 내용을 얹은 git 트리 ID — 리뷰 차례 사이의 diff 와 '리뷰한 코드 그대로인지' 확인에 쓴다.
    임시 index 를 쓰므로 사용자의 index(스테이징)는 건드리지 않는다."""
    repo = toplevel(repo)[0]
    fd, index = tempfile.mkstemp(prefix="devtrack-index-")
    os.close(fd)
    os.remove(index)
    try:
        env = _literal_env(index)
        base = _base(repo)
        _run(repo, ["read-tree", base] if base != EMPTY_TREE else ["read-tree", "--empty"], env=env)
        for chunk in _chunks(_addable(repo, paths, env)):
            _run(repo, ["add", "-A", "--"] + chunk, env=env)
        return _run(repo, ["write-tree"], env=env)[1].strip()
    finally:
        if os.path.exists(index):
            os.remove(index)


def diff_trees(repo, old, new):
    return _run(toplevel(repo)[0], ["diff", "--no-color", "--no-ext-diff", old, new])[1]


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


def commit_and_push(repo, message, files=None, branch=None, push=True, remote="origin", paths=None):
    """변경사항을 커밋하고(선택) 푸시한다.

    paths: 커밋할 파일 (capture_changes 의 paths — 빌드 결과물을 뺀 목록). 이 파일만 스테이징해서 커밋하고,
    그 밖에 스테이징되어 있던 파일은 스테이징만 풀어 둔다 (파일 내용은 그대로).
    커밋은 성공했지만 푸시가 실패한 경우 예외 대신 ``push_error`` 를 채워 반환한다.
    """
    repo = require_repo(repo)
    cur = current_branch(repo)
    if branch and cur != branch:
        raise DevTrackError(
            "현재 브랜치({})가 승인된 브랜치({})와 다릅니다. 브랜치를 맞춘 뒤 다시 실행하세요.".format(cur, branch)
        )
    if paths is not None:
        repo = toplevel(repo)[0]
        if not paths:
            raise DevTrackError("커밋할 변경 사항이 없습니다.")
        for chunk in _chunks(_addable(repo, paths)):
            _run(repo, ["add", "-A", "--"] + chunk, env=_literal_env())
        staged = [p for p in git(repo, "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0") if p]
        extra = sorted(set(staged) - set(paths))
        for chunk in _chunks(extra):
            _run(repo, ["reset", "-q", "--"] + chunk, env=_literal_env())
        if not set(staged) & set(paths):
            raise DevTrackError("커밋할 변경 사항이 없습니다.")
        commit_args = ["commit", "-m", message]
    else:
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
