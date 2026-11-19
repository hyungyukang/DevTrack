# -*- coding: utf-8 -*-
"""논문 · 문서 원고 읽기 — 심사 반복(Store.review_request 의 문서 모드)에서 차례마다 원고를 떠 두고,
차례 사이에 바뀐 부분을 diff 로 만든다.

- LaTeX: 본문(.tex)과 \\input · \\include 로 불러오는 .tex, \\bibliography · \\addbibresource 의 .bib (원고 폴더 안의 파일만).
  같은 이름의 PDF 가 있으면 함께 붙인다.
- Word(.docx): 글자를 Markdown 으로 (devtrack/docx.py), 원본 .docx 도 붙인다.
- PDF: pdftotext 가 있으면 글자를 뽑아 diff 에 쓰고, 없으면 PDF 만 붙인다.
- Markdown · 텍스트: 그대로.
"""

import difflib
import hashlib
import os
import re
import shutil
import subprocess

from . import DevTrackError

TEXT_EXT = (".tex", ".md", ".markdown", ".txt", ".rst", ".org", ".bib")
MAX_FILES = 300
MAX_BYTES = 8 * 1024 * 1024
_INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
_BIB_RE = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _tex_files(main):
    """본문 .tex 에서 시작해 불러오는 파일을 차례로 모은다 → {원고 폴더 기준 이름: 글} (불러온 순서)."""
    base = os.path.dirname(main)
    files, size = {}, [0]

    def add(path):
        path = os.path.normpath(path)
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        if rel in files or rel.startswith("../") or not os.path.isfile(path) or len(files) >= MAX_FILES:
            return None
        text = _read(path)
        size[0] += len(text)
        if size[0] > MAX_BYTES:
            raise DevTrackError("원고가 너무 큽니다 ({}MB 넘음): {}".format(MAX_BYTES // (1024 * 1024), main))
        files[rel] = text
        return text

    def visit(path):
        text = add(path)
        if text is None:
            return
        body = "\n".join(_COMMENT_RE.sub("", line) for line in text.splitlines())
        for m in _BIB_RE.finditer(body):
            for name in m.group(1).split(","):
                name = name.strip()
                if name:
                    add(os.path.join(base, name if name.endswith(".bib") else name + ".bib"))
        for m in _INPUT_RE.finditer(body):
            name = m.group(1).strip()
            if not name:
                continue
            cand = os.path.join(base, name)  # \input 경로는 본문(컴파일하는 곳) 기준
            visit(cand if os.path.splitext(cand)[1] else cand + ".tex")

    visit(main)
    return files


def pdf_text(path, timeout=120):
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", "-enc", "UTF-8", path, "-"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, check=True, timeout=timeout).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return out.decode("utf-8", "replace")


def snapshot(path):
    """원고 → {"source": 원본 경로, "kind": latex · word · pdf · text, "main": 본문 이름, "files": {이름: 글}, "pdf": PDF 경로 또는 None}."""
    src = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(src):
        raise DevTrackError("원고 파일을 찾을 수 없습니다: {}".format(path))
    name = os.path.basename(src)
    ext = os.path.splitext(name)[1].lower()
    pdf = None
    if ext == ".tex":
        kind, files = "latex", _tex_files(src)
        cand = os.path.splitext(src)[0] + ".pdf"
        pdf = cand if os.path.isfile(cand) else None
    elif ext == ".docx":
        from .docx import docx_to_markdown
        kind, files = "word", {os.path.splitext(name)[0] + ".md": docx_to_markdown(src)}
    elif ext == ".pdf":
        text = pdf_text(src)
        kind, files, pdf = "pdf", ({os.path.splitext(name)[0] + ".txt": text} if text else {}), src
    elif ext in TEXT_EXT:
        kind, files = "text", {name: _read(src)}
    elif ext == ".doc":
        raise DevTrackError("옛 Word 형식(.doc)은 읽지 못합니다 — .docx 나 PDF 로 저장해 주세요")
    else:
        raise DevTrackError("원고는 LaTeX(.tex) · Word(.docx) · PDF · Markdown · 텍스트 파일이어야 합니다: {}".format(name))
    return {"source": src, "kind": kind, "main": name, "files": files, "pdf": pdf}


def combined_text(snap):
    """원고 원문 한 파일로 (LaTeX 는 파일마다 머리줄을 붙여 이어 붙인다)."""
    if snap["kind"] != "latex" or len(snap["files"]) == 1:
        return next(iter(snap["files"].values()), "")
    parts = []
    for name, text in snap["files"].items():
        parts.append("%%%%%%%%%%%%%%%%%%%% {} %%%%%%%%%%%%%%%%%%%%\n{}".format(name, text.rstrip("\n")))
    return "\n\n".join(parts) + "\n"


def digest(snap):
    """원고가 바뀌었는지 비교하는 지문 (글이 없는 PDF 는 파일 내용으로)."""
    h = hashlib.sha256()
    for name in sorted(snap["files"]):
        h.update(name.encode("utf-8") + b"\0" + snap["files"][name].encode("utf-8", "replace") + b"\0")
    if not snap["files"] and snap.get("pdf"):
        with open(snap["pdf"], "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


def diff(old_files, new_files):
    """두 차례 원고의 바뀐 부분 (git diff 모양 — 웹의 diff 보기와 diffstat 이 그대로 읽는다)."""
    out = []
    for name in sorted(set(old_files) | set(new_files)):
        a, b = old_files.get(name), new_files.get(name)
        if a == b:
            continue
        lines = list(difflib.unified_diff((a or "").splitlines(True), (b or "").splitlines(True),
                                          "/dev/null" if a is None else "a/" + name,
                                          "/dev/null" if b is None else "b/" + name, n=3))
        if not lines:
            continue
        out.append("diff --git a/{0} b/{0}\n".format(name))
        for line in lines:
            out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)
