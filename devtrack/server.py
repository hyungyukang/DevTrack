"""로컬 웹 서버: 정적 웹 화면 + data 디렉토리 + 쓰기용 API.

정적 호스팅(예: Frontier www)에서는 이 서버 없이 화면이 '읽기 전용' 모드로 동작하고,
이 서버로 열면 웹에서 바로 승인/피드백/작업 지시를 저장할 수 있다.

API
  GET  /api/health
  POST /api/tasks                               {title, instruction, type, agent, repo, branch, priority}
  POST /api/tasks/<T>/comments                  {message}
  POST /api/tasks/<T>/approvals/<A>/decision    {decision: approve|changes|reject, comment}
"""

import http.server
import json
import os
import posixpath
import re
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

from . import DevTrackError, __version__
from .store import USER

TEXT_TYPES = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".tex": "text/plain", ".bib": "text/plain",
    ".diff": "text/plain", ".patch": "text/plain", ".log": "text/plain", ".txt": "text/plain",
    ".csv": "text/plain", ".tsv": "text/plain", ".py": "text/plain", ".f90": "text/plain",
    ".c": "text/plain", ".cpp": "text/plain", ".h": "text/plain", ".java": "text/plain",
    ".jl": "text/plain", ".r": "text/plain", ".sh": "text/plain", ".yaml": "text/plain",
    ".yml": "text/plain", ".toml": "text/plain", ".ipynb": "application/json",
    ".json": "application/json", ".js": "text/javascript", ".css": "text/css", ".html": "text/html",
    ".svg": "image/svg+xml",
}

MAX_BODY = 1 << 20


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(store, web_root, verbose=False):
    data_root = store.root
    write_lock = threading.Lock()

    class Handler(http.server.SimpleHTTPRequestHandler):
        server_version = "DevTrack/" + __version__

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=web_root, **kw)

        # ------------------------------------------------------------ 정적 파일
        def translate_path(self, path):
            p = urllib.parse.urlsplit(path).path
            p = posixpath.normpath(urllib.parse.unquote(p))
            if p == "/data" or p.startswith("/data/"):
                base, rel = data_root, p[len("/data"):]
            else:
                base, rel = web_root, p
            parts = [x for x in rel.split("/") if x and x not in (".", "..") and not x.startswith(".")]
            return os.path.join(base, *parts)

        def list_directory(self, path):
            self.send_error(404, "Not Found")
            return None

        def guess_type(self, path):
            ext = os.path.splitext(path)[1].lower()
            if ext in TEXT_TYPES:
                t = TEXT_TYPES[ext]
                return t + "; charset=utf-8" if t.startswith("text/") or t.endswith("json") else t
            return super().guess_type(path)

        def end_headers(self):
            if self.path.startswith(("/data/", "/api/")):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def log_message(self, fmt, *args):
            if verbose:
                sys.stderr.write("[devtrack] %s - %s\n" % (self.address_string(), fmt % args))

        # ------------------------------------------------------------ API
        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/health":
                return self._json({"ok": True, "mode": "live", "version": __version__, "data": data_root})
            if path.startswith("/api/"):
                return self._json({"error": "not found"}, 404)
            return super().do_GET()

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path.rstrip("/")
            try:
                body = self._body()
                with write_lock:
                    if path == "/api/tasks":
                        related = body.get("related") or []
                        if isinstance(related, str):
                            related = [related]
                        t = store.create_task(
                            (body.get("title") or "").strip() or _need("title"),
                            instruction=body.get("instruction") or "",
                            type=body.get("type") or "code",
                            agent=(body.get("agent") or "Claude").strip(),
                            repo=(body.get("repo") or "").strip() or None,
                            branch=(body.get("branch") or "").strip() or None,
                            priority=body.get("priority") or "normal",
                            status="queued", created_by=USER, related=[r for r in related if r],
                        )
                        return self._json({"ok": True, "task": {"id": t["id"]}}, 201)
                    m = re.match(r"^/api/tasks/(T-\d+)/comments$", path)
                    if m:
                        e = store.add_user_comment(m.group(1), body.get("message") or "")
                        return self._json({"ok": True, "entry": e}, 201)
                    m = re.match(r"^/api/tasks/(T-\d+)/approvals/(A-\d+)/decision$", path)
                    if m:
                        ap = store.decide(m.group(1), m.group(2), body.get("decision") or "",
                                          comment=body.get("comment") or "", by=USER)
                        return self._json({"ok": True, "approval": ap})
                return self._json({"error": "not found"}, 404)
            except DevTrackError as e:
                return self._json({"error": str(e)}, 400)
            except ValueError:
                return self._json({"error": "잘못된 요청 본문 (JSON)"}, 400)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise DevTrackError("요청 본문이 너무 큽니다")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError
            return data

        def _json(self, obj, status=200):
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _need(field):
    raise DevTrackError("'{}' 항목이 필요합니다".format(field))


def serve(store, web_root, host="127.0.0.1", port=8765, open_browser=False, verbose=False):
    store.ensure()
    httpd = _Server((host, port), make_handler(store, web_root, verbose=verbose))
    url = "http://{}:{}/".format("localhost" if host in ("127.0.0.1", "0.0.0.0", "") else host, port)
    sys.stderr.write("DevTrack 서버 실행 중: {}\n  데이터: {}\n  종료: Ctrl+C\n".format(url, store.root))
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write("  ⚠ 인증 기능이 없으므로 신뢰할 수 있는 네트워크에서만 외부 바인딩하세요.\n")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
