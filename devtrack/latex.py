"""LaTeX 컴파일과 로그 분석.

latexmk 가 있으면 latexmk 를, 없으면 엔진(pdflatex 등) + bibtex 를 직접 돌린다.
결과는 Store.record_build() 로 기록한다 (성공: PDF 첨부, 실패: 오류 + 로그 첨부).
"""

import os
import re
import shutil
import subprocess
import time

from . import DevTrackError

ENGINES = {"pdf": ("pdflatex", "-pdf"), "xe": ("xelatex", "-xelatex"), "lua": ("lualatex", "-lualatex")}

_FILE_LINE_ERR = re.compile(r"^(.*\.(?:tex|sty|cls|bib|bbl|ltx)):(\d+): (.*)$")
_CITE = re.compile(r"Citation [`'](.+?)' (?:on page \d+ )?undefined")
_REF = re.compile(r"Reference [`'](.+?)' (?:on page \d+ )?undefined")
_PAGES = re.compile(r"Output written on .+? \((\d+) pages?")


def _run(cmd, cwd, timeout):
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        return -1, out + "\n[devtrack] 시간 초과 ({}초)".format(timeout)


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def parse_log(text):
    """LaTeX 로그에서 오류, 경고, 쪽수를 뽑는다."""
    errors, cites, refs, other = [], [], [], []
    overfull = underfull = 0
    pages = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _FILE_LINE_ERR.match(line)
        if m:
            errors.append({"file": os.path.basename(m.group(1)), "line": int(m.group(2)), "message": m.group(3).strip()})
            continue
        if line.startswith("! "):
            ln = None
            for nxt in lines[i + 1:i + 10]:
                mm = re.match(r"^l\.(\d+)", nxt)
                if mm:
                    ln = int(mm.group(1))
                    break
            errors.append({"file": None, "line": ln, "message": line[2:].strip()})
            continue
        m = _CITE.search(line)
        if m:
            cites.append(m.group(1))
            continue
        m = _REF.search(line)
        if m:
            refs.append(m.group(1))
            continue
        if line.startswith(("Overfull \\hbox", "Overfull \\vbox")):
            overfull += 1
        elif line.startswith(("Underfull \\hbox", "Underfull \\vbox")):
            underfull += 1
        elif ("LaTeX Warning:" in line or "Package natbib Warning" in line) and "There were undefined" not in line:
            other.append(line.strip())  # 'There were undefined ...' 는 위 개별 경고의 요약이라 제외
        m = _PAGES.search(line)
        if m:
            pages = int(m.group(1))
    seen, uniq_err = set(), []
    for e in errors:
        key = (e["message"], e["line"])
        if key not in seen:
            seen.add(key)
            uniq_err.append(e)
    dedup = lambda xs: list(dict.fromkeys(xs))
    return {
        "errors": uniq_err[:10],
        "pages": pages,
        "warnings": {
            "undefined_citations": dedup(cites)[:30],
            "undefined_references": dedup(refs)[:30],
            "overfull": overfull,
            "underfull": underfull,
            "other": dedup(other)[:10],
        },
    }


def warning_count(w):
    w = w or {}
    return (len(w.get("undefined_citations") or []) + len(w.get("undefined_references") or [])
            + len(w.get("other") or []) + int(w.get("overfull") or 0))


def _needs_bibtex(aux_path):
    try:
        return "\\bibdata" in _read(aux_path)
    except OSError:
        return False


def compile_tex(tex_path, engine="pdf", timeout=600):
    tex = os.path.abspath(os.path.expanduser(tex_path))
    if not os.path.isfile(tex):
        raise DevTrackError("LaTeX 파일을 찾을 수 없습니다: {}".format(tex_path))
    if engine not in ENGINES:
        raise DevTrackError("엔진은 {} 중 하나여야 합니다".format(", ".join(ENGINES)))
    cwd, base = os.path.split(tex)
    stem = os.path.splitext(base)[0]
    prog, mkflag = ENGINES[engine]
    flags = ["-interaction=nonstopmode", "-halt-on-error", "-file-line-error"]
    start = time.time()
    outputs = []
    if shutil.which("latexmk"):
        used = "latexmk {}".format(mkflag)
        rc, out = _run(["latexmk", mkflag] + flags + [base], cwd, timeout)
        outputs.append(out)
    elif shutil.which(prog):
        used = prog
        rc, out = _run([prog] + flags + [base], cwd, timeout)
        outputs.append(out)
        if rc == 0:
            if _needs_bibtex(os.path.join(cwd, stem + ".aux")) and shutil.which("bibtex"):
                _, o = _run(["bibtex", stem], cwd, timeout)
                outputs.append(o)
                used += " + bibtex"
            for _ in range(2):  # 참조/인용 확정
                rc, out = _run([prog] + flags + [base], cwd, timeout)
                outputs.append(out)
                if rc != 0:
                    break
    else:
        raise DevTrackError(
            "LaTeX(latexmk 또는 {})을 찾을 수 없습니다. TeX 를 설치(module load 등)하거나, "
            "다른 곳에서 컴파일한 PDF 는 --pdf 로 기록하세요.".format(prog))
    duration = round(time.time() - start, 1)
    pdf = os.path.join(cwd, stem + ".pdf")
    fresh = os.path.exists(pdf) and os.path.getmtime(pdf) >= start - 1
    log_path = os.path.join(cwd, stem + ".log")
    log_text = _read(log_path) if os.path.exists(log_path) else "\n".join(outputs)
    info = parse_log(log_text)
    ok = rc == 0 and fresh
    if not ok and not info["errors"]:
        tail = [l for l in "\n".join(outputs).splitlines() if l.strip()][-3:]
        info["errors"].append({"file": None, "line": None,
                               "message": "컴파일 실패 (exit {}){}".format(rc, ": " + " / ".join(tail) if tail else "")})
    result = {"status": "ok" if ok else "failed", "pdf": pdf if ok else None, "log_text": log_text,
              "engine": used, "duration": duration}
    result.update(info)
    return result


def from_existing(tex_path, pdf_path):
    """다른 곳에서 컴파일한 PDF 를 기록용 결과로 만든다 (같은 이름의 .log 가 있으면 분석)."""
    pdf = os.path.abspath(os.path.expanduser(pdf_path))
    if not os.path.isfile(pdf):
        raise DevTrackError("PDF 를 찾을 수 없습니다: {}".format(pdf_path))
    stem = os.path.splitext(os.path.basename(tex_path))[0]
    log_text = ""
    for d in (os.path.dirname(os.path.abspath(os.path.expanduser(tex_path))), os.path.dirname(pdf)):
        p = os.path.join(d, stem + ".log")
        if os.path.exists(p):
            log_text = _read(p)
            break
    info = parse_log(log_text) if log_text else {"errors": [], "pages": None, "warnings": {}}
    result = {"status": "ok", "pdf": pdf, "log_text": log_text, "engine": "external", "duration": None}
    result.update(info)
    result["errors"] = []
    return result
