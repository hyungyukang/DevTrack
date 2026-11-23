"""로컬 웹 서버: 정적 웹 화면 + data 디렉토리 + 쓰기용 API.

정적 호스팅(예: 웹 서버의 공개 폴더)에서는 이 서버 없이 화면이 '읽기 전용' 모드로 동작하고,
이 서버로 열면 웹에서 바로 승인/피드백/작업 지시를 저장할 수 있다.

API
  GET  /api/health
  POST /api/tasks                               {title, instruction, type, agent, repo, branch, priority}
  POST /api/tasks/<T>/comments                  {message, artifact?: F-ID}  (artifact = 결과물 화면에서 보낸 수정 요청 · 추가 지시)
  POST /api/tasks/<T>/approvals/<A>/decision    {decision: approve|changes|reject, comment, selected?: [ID]}
  POST /api/tasks/<T>/cancel                     {reason}
  POST /api/tasks/<T>/complete                   {note}   끝나지 않은 작업을 사용자가 완료로 처리
  POST /api/tasks/<T>/delete                     {confirm: "<작업 ID>"}  (ID 가 일치해야 삭제)

웹 비밀번호(devtrack password)가 있으면 data/ · api/ 는 비밀번호를 넣은 브라우저의 쿠키가 있어야 열린다 (access.py).
/auth.json 은 비밀번호가 있는지와, 화면이 비밀번호를 물을 때 쓰는 값(salt · 반복 횟수)을 준다.
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

from . import DevTrackError, __version__, access
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

        # ------------------------------------------------------------ 웹 비밀번호
        def _authorized(self):
            try:
                auth = store.auth()
            except DevTrackError:
                return False  # 망가진 설정 — 풀린 채로 두지 않고 막는다 (devtrack password 로 다시 정하면 열림)
            return not auth or access.cookie_ok(self.headers.get("Cookie"), auth)

        def send_head(self):  # GET · HEAD 의 정적 파일 — data/ 는 비밀번호를 넣은 브라우저만
            p = urllib.parse.urlsplit(self.path).path
            if (p == "/data" or p.startswith("/data/")) and not self._authorized():
                self.send_error(403, "Password required")
                return None
            return super().send_head()

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
            else:  # 웹 화면 파일: 매번 변경 여부를 확인 (업데이트 후 예전 화면이 남지 않게, 바뀌지 않았으면 304)
                self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def log_message(self, fmt, *args):
            if verbose:
                sys.stderr.write("[devtrack] %s - %s\n" % (self.address_string(), fmt % args))

        # ------------------------------------------------------------ API
        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/auth.json":
                try:
                    auth = store.auth()
                except DevTrackError as e:
                    return self._json({"error": str(e)}, 500)
                return self._json(access.site_info(auth))
            if path == "/api/health":
                ok = self._authorized()
                return self._json(dict({"ok": True, "mode": "live", "version": __version__, "locked": not ok},
                                       **({"data": data_root} if ok else {})))
            if path.startswith("/api/") and not self._authorized():
                return self._json({"error": "비밀번호가 필요합니다 — 화면을 새로고침해 비밀번호를 넣으세요"}, 403)
            if path == "/api/agents":
                # 지금 웹 입력을 기다리는 AI (devtrack inbox --wait · devtrack watch) — 대기 중인 작업 화면에 표시
                return self._json({"listeners": store.listeners()})
            if path.startswith("/api/"):
                return self._json({"error": "not found"}, 404)
            return super().do_GET()

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path.rstrip("/")
            if not self._authorized():
                return self._json({"error": "비밀번호가 필요합니다 — 화면을 새로고침해 비밀번호를 넣으세요"}, 403)
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
                            model=(body.get("model") or "").strip() or None,
                            effort=(body.get("effort") or "").strip() or None,
                            review={"reviewer": (body.get("review_by") or "").strip(),
                                    "rounds": body.get("review_rounds") or None,
                                    "model": (body.get("review_model") or "").strip() or None,
                                    "effort": (body.get("review_effort") or "").strip() or None,
                                    "doc": (body.get("review_doc") or "").strip() or None,
                                    "venue": (body.get("review_venue") or "").strip() or None}
                            if (body.get("review_by") or "").strip() else None,
                        )
                        return self._json({"ok": True, "task": {"id": t["id"]}}, 201)
                    m = re.match(r"^/api/tasks/(T-\d+)/comments$", path)
                    if m:
                        e = store.add_user_comment(m.group(1), body.get("message") or "",
                                                   artifact=(body.get("artifact") or "").strip() or None)
                        return self._json({"ok": True, "entry": e}, 201)
                    m = re.match(r"^/api/tasks/(T-\d+)/note$", path)
                    if m:  # 인계 메모를 사용자가 고침 (빈 글이면 지움)
                        t, _ = store.set_note(m.group(1), body.get("text") or "", author=USER)
                        return self._json({"ok": True, "note": t.get("note")})
                    m = re.match(r"^/api/tasks/(T-\d+)/model$", path)
                    if m:
                        t = store.set_model(m.group(1), (body.get("model") or "").strip() or None,
                                            (body.get("effort") or "").strip() or None, author=USER)
                        return self._json({"ok": True, "task": {"id": t["id"], "model": t.get("model"),
                                                                "effort": t.get("effort")}})
                    m = re.match(r"^/api/tasks/(T-\d+)/cancel$", path)
                    if m:
                        t = store.cancel_task(m.group(1), reason=body.get("reason") or "", author=USER)
                        return self._json({"ok": True, "task": {"id": t["id"], "status": t["status"]}})
                    m = re.match(r"^/api/tasks/(T-\d+)/complete$", path)
                    if m:
                        t = store.complete_task(m.group(1), note=body.get("note") or "", author=USER)
                        return self._json({"ok": True, "task": {"id": t["id"], "status": t["status"]}})
                    m = re.match(r"^/api/tasks/(T-\d+)/delete$", path)
                    if m:
                        if str(body.get("confirm") or "").strip().upper() != m.group(1):
                            raise DevTrackError("확인용 작업 ID 가 일치하지 않아 삭제하지 않았습니다")
                        r = store.delete_task(m.group(1), author=USER)
                        return self._json({"ok": True, "deleted": r})
                    m = re.match(r"^/api/tasks/(T-\d+)/approvals/(A-\d+)/decision$", path)
                    if m:
                        selected = body.get("selected")
                        if selected is not None and not isinstance(selected, list):
                            raise DevTrackError("selected 는 항목 ID 목록이어야 합니다")
                        ap = store.decide(m.group(1), m.group(2), body.get("decision") or "",
                                          comment=body.get("comment") or "", by=USER, selected=selected)
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
    try:
        locked = bool(store.auth())
    except DevTrackError as e:
        locked = True
        sys.stderr.write("  ⚠ {} — 고칠 때까지 기록을 열지 않습니다.\n".format(e))
    if locked:
        sys.stderr.write("  웹 비밀번호: 켜짐 (devtrack password)\n")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write("  ⚠ 웹 비밀번호가 없으므로 신뢰할 수 있는 네트워크에서만 외부 바인딩하세요 (devtrack password).\n")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
