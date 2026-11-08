"""Markdown 리포트 → PDF (헤드리스 Chrome/Chromium 으로 인쇄).

웹 화면과 같은 렌더러(web/vendor 의 marked · DOMPurify · highlight.js)로 HTML 을 만든 뒤
브라우저의 "PDF 로 인쇄" 기능을 쓴다. 한글은 시스템 글꼴(Apple SD Gothic Neo, Noto Sans CJK KR,
맑은 고딕 등)로 표시되어 PDF 에 포함된다. Python 표준 라이브러리만 사용한다.

브라우저 위치는 자동으로 찾고, 못 찾으면 환경 변수 DEVTRACK_CHROME 으로 지정한다.
"""

import json
import os
import shutil
import subprocess
import tempfile
from urllib.parse import quote

from . import DevTrackError

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

_MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "msedge", "chrome")

CSS = """
@page { size: A4; margin: 15mm 14mm 16mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: -apple-system, "Apple SD Gothic Neo", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic",
       "Nanum Gothic", "NanumGothic", sans-serif; font-size: 10.5pt; line-height: 1.62; color: #1b1b1b;
       word-break: keep-all; overflow-wrap: break-word; margin: 0; }
h1 { font-size: 17pt; line-height: 1.35; margin: 0 0 12px; padding-bottom: 6px; border-bottom: 2px solid #333; }
h2 { font-size: 13.5pt; margin: 1.5em 0 .55em; padding-bottom: 3px; border-bottom: 1px solid #ccc; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 1.25em 0 .45em; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 1em 0 .4em; break-after: avoid; }
p, ul, ol, table, pre, blockquote { margin: 0 0 .75em; }
ul, ol { padding-left: 1.4em; }
li { margin: .15em 0; }
li > p { margin: 0 0 .3em; }
table { border-collapse: collapse; width: 100%; font-size: 9.3pt; line-height: 1.45; }
tr { break-inside: avoid; }
th, td { border: 1px solid #bdbdbd; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #f0f2f5; font-weight: 650; white-space: nowrap; }
code { font-family: Menlo, "D2Coding", "SF Mono", Consolas, "DejaVu Sans Mono", monospace; font-size: .86em;
       background: #f3f4f6; padding: .05em .3em; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #dcdfe4; border-radius: 5px; padding: 8px 10px; font-size: 8.6pt;
      line-height: 1.45; white-space: pre-wrap; word-break: break-all; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit; }
blockquote { border-left: 4px solid #4c6ef5; background: #f3f5ff; padding: 6px 12px; border-radius: 0 4px 4px 0; }
blockquote p { margin: 0; }
a { color: #1d4ed8; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ddd; margin: 1.2em 0; }
img { max-width: 100%; }
.hljs-comment, .hljs-quote { color: #6a737d; font-style: italic; }
.hljs-keyword, .hljs-selector-tag, .hljs-built_in { color: #c4302b; }
.hljs-string, .hljs-attr, .hljs-addition { color: #1a7f37; }
.hljs-number, .hljs-literal, .hljs-type { color: #0550ae; }
.hljs-title, .hljs-section, .hljs-function { color: #6639ba; }
.hljs-deletion { color: #b31d28; background: #ffeef0; }
"""

HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<style>{css}</style>
{scripts}
</head><body><article id="doc"></article>
<script>
(function () {{
  var md = {md};
  var el = document.getElementById('doc');
  if (window.marked && window.DOMPurify) {{
    el.innerHTML = DOMPurify.sanitize(marked.parse(md, {{ gfm: true, breaks: false }}));
    if (window.hljs) document.querySelectorAll('pre code').forEach(function (c) {{
      try {{ hljs.highlightElement(c); }} catch (e) {{}}
    }});
  }} else {{
    var pre = document.createElement('pre'); pre.textContent = md; el.appendChild(pre);
  }}
}})();
</script></body></html>
"""


def find_browser():
    env = os.environ.get("DEVTRACK_CHROME")
    if env:
        return env if os.path.exists(env) or shutil.which(env) else None
    for p in _MAC_APPS:
        if os.path.exists(p):
            return p
    for c in _COMMANDS:
        p = shutil.which(c)
        if p:
            return p
    return None


def _file_url(path):
    return "file://" + quote(os.path.abspath(path))


def markdown_to_pdf(md_path, pdf_path=None, title=None, timeout=120):
    """md_path 를 PDF 로 만든다. 만든 PDF 경로를 돌려준다 (기본: 같은 폴더, 같은 이름 .pdf)."""
    md_path = os.path.abspath(os.path.expanduser(md_path))
    pdf_path = os.path.abspath(os.path.expanduser(pdf_path or os.path.splitext(md_path)[0] + ".pdf"))
    browser = find_browser()
    if not browser:
        raise DevTrackError("PDF 변환에 쓸 Chrome/Chromium 을 찾지 못했습니다 "
                            "(설치하거나 DEVTRACK_CHROME=<브라우저 실행 파일> 로 지정하세요)")
    with open(md_path, encoding="utf-8") as fh:
        text = fh.read()
    if title is None:
        first = next((ln for ln in text.splitlines() if ln.startswith("# ")), "")
        title = first[2:].strip() or os.path.basename(md_path)
    vendor = os.path.join(WEB_ROOT, "vendor")
    scripts = "\n".join('<script src="{}"></script>'.format(_file_url(os.path.join(vendor, name)))
                        for name in ("marked.min.js", "purify.min.js", "highlight.min.js", "hljs-latex.min.js",
                                     "hljs-fortran.min.js", "hljs-julia.min.js", "hljs-matlab.min.js")
                        if os.path.exists(os.path.join(vendor, name)))
    md_js = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028") \
        .replace("\u2029", "\\u2029")
    esc_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    tmp = tempfile.mkdtemp(prefix="devtrack-pdf-")
    try:
        html_path = os.path.join(tmp, "report.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(HTML.format(title=esc_title, css=CSS, scripts=scripts, md=md_js))
        out_tmp = os.path.join(tmp, "out.pdf")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--user-data-dir=" + os.path.join(tmp, "profile"),
               "--no-pdf-header-footer", "--print-to-pdf-no-header", "--virtual-time-budget=10000",
               "--print-to-pdf=" + out_tmp, _file_url(html_path)]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise DevTrackError("PDF 변환 시간 초과 ({}초)".format(timeout))
        except OSError as e:
            raise DevTrackError("브라우저를 실행하지 못했습니다 ({}): {}".format(browser, e))
        try:
            with open(out_tmp, "rb") as fh:
                ok = fh.read(5) == b"%PDF-"
        except OSError:
            ok = False
        if not ok:
            raise DevTrackError("PDF 를 만들지 못했습니다 (브라우저: {})".format(browser))
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        shutil.move(out_tmp, pdf_path)
        return pdf_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
