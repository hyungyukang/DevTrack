"""SVG 도식 점검 — AI 가 직접 그린 모식도의 흔한 실수와 화면에 넣으면 안 되는 요소를 찾는다 (표준 라이브러리만).

- 안전: <script> · <foreignObject> · on* 속성 · 외부 주소(href · url(...) · @import) · DOCTYPE 엔티티 → 오류.
  결과물로 올린 SVG 는 웹에서 새 탭으로 직접 열릴 수 있으므로 실행 코드가 있으면 안 된다 (Store.add_artifact 도 같은 검사로 거절).
- 모양: 캔버스 밖 글자, 상자를 넘는 글자, 겹친 글자, 어긋나게 겹친 상자, 상자 테두리에 걸친 글자, 너무 작은 글자 → 경고.
  글자 폭은 글꼴 없이 어림한다 (라틴 약 0.55em, 한글 · 한자 1em) — 정밀하지 않으므로 눈으로 보는 미리보기(PNG)와 함께 쓴다.
- 미리보기: 헤드리스 Chrome 이 있으면 PNG 로 그린다 (AI 가 그림을 열어 확인).
"""

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from . import DevTrackError

MIN_FONT = 12          # 이보다 작은 글자는 경고
TOL = 2.0              # 경계 여유 (px)
BIG_BOX = 3000.0       # 이보다 넓은 도형만 '상자'로 겹침을 본다 (아이콘을 이루는 작은 도형은 서로 겹쳐도 됨)

_FORBIDDEN = {"script", "foreignobject", "iframe", "object", "embed", "handler", "listener"}
_SKIP = {"defs", "marker", "clippath", "mask", "pattern", "symbol", "title", "desc", "metadata", "style",
         "lineargradient", "radialgradient", "filter"}
_DATA_IMAGE = re.compile(r"^\s*data:image/(png|jpe?g|gif|webp);", re.I)
_CSS_EXTERNAL = re.compile(r"@import|url\(\s*['\"]?\s*(?!#|data:image/(?:png|jpe?g|gif|webp);)", re.I)
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?", re.I)
_TF = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _short(s, n=30):
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------------------- 안전
def unsafe_parts(root):
    """위험 요소 목록 (비어 있으면 안전)."""
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if not name:
            continue
        if name in _FORBIDDEN:
            out.append("<{}> 요소".format(el.tag.rsplit("}", 1)[-1]))
        for key, val in el.attrib.items():
            k = _local(key)
            if k.startswith("on"):
                out.append("<{}> 의 {} 속성 (실행 코드)".format(name, k))
            elif k == "href" and val and not val.strip().startswith("#") and not _DATA_IMAGE.match(val):
                out.append("<{}> 의 외부 주소 {}=\"{}\"".format(name, k, _short(val, 50)))
            elif ("url(" in val or "@import" in val) and _CSS_EXTERNAL.search(val):
                out.append("<{}> 의 {} 에 외부 주소".format(name, k))
        if name == "style" and el.text and _CSS_EXTERNAL.search(el.text):
            out.append("<style> 의 외부 주소 · @import")
    return out


def _parse(path):
    """(root, 오류 목록). 읽을 수 없거나 XML 이 깨졌으면 root 는 None."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, ["읽을 수 없음: {}".format(e)]
    if b"<!ENTITY" in raw:
        return None, ["DOCTYPE 엔티티 정의 (허용하지 않음)"]
    try:
        return ET.fromstring(raw), []
    except ET.ParseError as e:
        return None, ["SVG(XML) 형식 오류: {}".format(e)]


def unsafe_file(path):
    """결과물로 올릴 SVG 의 문제 목록 (비어 있으면 올려도 됨)."""
    root, errors = _parse(path)
    return errors if root is None else unsafe_parts(root)


# ---------------------------------------------------------------------------- 모양
def _char_em(ch):
    o = ord(ch)
    if ch == " ":
        return 0.28
    if (0x1100 <= o <= 0x11FF or 0x2190 <= o <= 0x21FF or 0x2460 <= o <= 0x24FF or 0x2600 <= o <= 0x27BF
            or 0x3000 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFF60):
        return 1.0
    if ch in "iljtfrI.,;:'|!()[]`\"":
        return 0.3
    if ch in "mwMW@%":
        return 0.85
    if ch.isupper() or ch.isdigit():
        return 0.64
    return 0.53


def text_width(s, size, bold=False):
    """글자 폭 어림 (px)."""
    return sum(_char_em(c) for c in s) * size * (1.07 if bold else 1.0)


def _num(val, default=None, size=16.0):
    if val is None:
        return default
    m = _NUM.match(str(val).strip())
    if not m:
        return default
    x = float(m.group(0))
    rest = str(val).strip()[m.end():].strip().lower()
    if rest.startswith("em"):
        return x * size
    if rest.startswith("%"):
        return default
    return x


def _style(el):
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _own(el, name):
    st = _style(el)
    return st[name] if name in st else el.get(name)


def _transform(tf, cur):
    """(sx, sy, tx, ty) 에 transform 을 더한다. 회전 · 기울이기는 None (그 아래 도형은 모양 검사를 건너뜀)."""
    sx, sy, tx, ty = cur
    for name, args in _TF.findall(tf or ""):
        a = [float(x) for x in _NUM.findall(args)]
        name = name.lower()
        if name == "translate":
            tx, ty = tx + sx * (a[0] if a else 0), ty + sy * (a[1] if len(a) > 1 else 0)
        elif name == "scale":
            s1 = a[0] if a else 1.0
            sx, sy = sx * s1, sy * (a[1] if len(a) > 1 else s1)
        elif name == "matrix" and len(a) == 6 and abs(a[1]) < 1e-9 and abs(a[2]) < 1e-9:
            tx, ty = tx + sx * a[4], ty + sy * a[5]
            sx, sy = sx * a[0], sy * a[3]
        elif name == "rotate" and a and abs(a[0]) < 1e-9:
            continue
        else:
            return None
    return (sx, sy, tx, ty)


def _box(tf, x0, y0, x1, y1):
    sx, sy, tx, ty = tf
    xs, ys = sorted((tx + sx * x0, tx + sx * x1)), sorted((ty + sy * y0, ty + sy * y1))
    return (xs[0], ys[0], xs[1], ys[1])


def _first(val):
    m = _NUM.search(val or "")
    return float(m.group(0)) if m else None


def _invisible(el, inh):
    fill = (_own(el, "fill") or inh.get("fill") or "").strip().lower()
    stroke = (_own(el, "stroke") or inh.get("stroke") or "").strip().lower()
    return fill in ("none", "transparent") and stroke in ("", "none", "transparent")


def _text_lines(el, inh, tf):
    """<text> 의 줄: [{x0, y0, x1, y1, text, size}] (캔버스 좌표)."""
    size, anchor = inh["size"], inh.get("text-anchor", "start")
    bold = str(inh.get("font-weight", "")).lower() in ("bold", "bolder", "600", "700", "800", "900")
    base = inh.get("dominant-baseline", "")
    x, y = _first(el.get("x")) or 0.0, _first(el.get("y")) or 0.0
    lines, cur = [], {"x": x, "y": y, "text": el.text or "", "size": size, "anchor": anchor, "bold": bold}

    def flush():
        if cur["text"].strip():
            lines.append(dict(cur))

    for ts in el:
        if _local(ts.tag) not in ("tspan", "textpath", "a"):
            continue
        tsize = _num(_own(ts, "font-size"), size, size=size)
        tanchor = _own(ts, "text-anchor") or cur["anchor"]
        tbold = bold or str(_own(ts, "font-weight") or "").lower() in ("bold", "bolder", "600", "700", "800", "900")
        dy = _num(ts.get("dy"), 0.0, size=tsize)
        # 위치 없이 조금만 올리고 내린 tspan 은 같은 줄의 아래 · 위 첨자 (줄을 바꾸지 않고, 작은 글자 검사도 하지 않음)
        inline = ts.get("x") is None and ts.get("y") is None and abs(dy) <= 0.6 * cur["size"]
        if not inline:
            flush()
            nx = _first(ts.get("x"))
            ny = _first(ts.get("y"))
            cur = {"x": x if nx is None else nx, "y": (cur["y"] if ny is None else ny) + dy,
                   "text": ts.text or "", "size": tsize, "anchor": tanchor, "bold": tbold}
        else:
            cur["text"] += ts.text or ""
            cur["bold"] = cur["bold"] or tbold
        cur["text"] += ts.tail or ""
    flush()
    out = []
    for ln in lines:
        txt, s = ln["text"].strip(), ln["size"]
        w = text_width(txt, s, ln["bold"])
        lx = ln["x"] - (w / 2 if ln["anchor"] == "middle" else w if ln["anchor"] == "end" else 0)
        if base in ("middle", "central"):
            top, bottom = ln["y"] - 0.5 * s, ln["y"] + 0.5 * s
        elif base in ("hanging", "text-before-edge"):
            top, bottom = ln["y"], ln["y"] + s
        else:
            top, bottom = ln["y"] - 0.78 * s, ln["y"] + 0.22 * s
        x0, y0, x1, y1 = _box(tf, lx, top, lx + w, bottom)
        out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": txt, "size": s * abs(tf[1]),
                    "ax": tf[2] + tf[0] * ln["x"]})
    return out


def _collect(root):
    shapes, texts, skipped = [], [], [0]

    def walk(el, inh, tf):
        name = _local(el.tag)
        if not name or name in _SKIP:
            return
        if (_own(el, "display") or "") == "none" or (_own(el, "visibility") or "") == "hidden":
            return
        if el.get("transform"):
            tf = _transform(el.get("transform"), tf) if tf else None
        inh = dict(inh)
        fs = _own(el, "font-size")
        if fs is not None:
            inh["size"] = _num(fs, inh["size"], size=inh["size"])
        for k in ("font-weight", "text-anchor", "dominant-baseline", "fill", "stroke"):
            v = _own(el, k)
            if v is not None:
                inh[k] = v
        if name in ("rect", "circle", "ellipse"):
            if tf is None:
                skipped[0] += 1
            elif not _invisible(el, inh):
                if name == "rect":
                    x, y = _num(el.get("x"), 0.0), _num(el.get("y"), 0.0)
                    w, h = _num(el.get("width"), 0.0), _num(el.get("height"), 0.0)
                    b = _box(tf, x, y, x + w, y + h) if w > 0 and h > 0 else None
                else:
                    cx, cy = _num(el.get("cx"), 0.0), _num(el.get("cy"), 0.0)
                    rx = _num(el.get("r") if name == "circle" else el.get("rx"), 0.0)
                    ry = _num(el.get("r") if name == "circle" else el.get("ry"), 0.0)
                    b = _box(tf, cx - rx, cy - ry, cx + rx, cy + ry) if rx > 0 and ry > 0 else None
                if b:
                    shapes.append(b)
            return
        if name == "text":
            if tf is None:
                skipped[0] += 1
            else:
                texts.append(_text_lines(el, inh, tf))
            return
        for ch in el:
            walk(ch, inh, tf)

    walk(root, {"size": 16.0}, (1.0, 1.0, 0.0, 0.0))
    return shapes, texts, skipped[0]


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _contains(outer, inner, tol=TOL):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and inner[2] <= outer[2] + tol
            and inner[3] <= outer[3] + tol)


def _canvas(root):
    vb = [float(x) for x in _NUM.findall(root.get("viewBox") or "")]
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        return tuple(vb)
    w, h = _num(root.get("width")), _num(root.get("height"))
    if w and h:
        return (0.0, 0.0, w, h)
    return None


def check(path):
    """SVG 하나를 점검한다 → {"file", "errors", "warnings", "shapes", "texts", "skipped", "size"}."""
    res = {"file": path, "errors": [], "warnings": [], "shapes": 0, "texts": 0, "skipped": 0, "size": None}
    root, errors = _parse(path)
    if root is None:
        res["errors"] = errors
        return res
    if _local(root.tag) != "svg":
        res["errors"].append("SVG 가 아닙니다 (최상위 요소: <{}>)".format(_local(root.tag)))
        return res
    if not root.tag.startswith("{http://www.w3.org/2000/svg}"):
        res["errors"].append('xmlns="http://www.w3.org/2000/svg" 가 없습니다 — 그림(<img>)으로 넣으면 그려지지 않습니다')
    res["errors"] += unsafe_parts(root)
    canvas = _canvas(root)
    if not canvas:
        res["errors"].append("viewBox (또는 width · height) 가 없습니다 — 크기를 정할 수 없습니다")
        return res
    res["size"] = (canvas[2], canvas[3])
    cbox = (canvas[0], canvas[1], canvas[0] + canvas[2], canvas[1] + canvas[3])
    shapes, texts, res["skipped"] = _collect(root)
    boxes = [b for b in shapes if _area(b) < 0.9 * _area(cbox)]  # 바탕 사각형은 뺀다
    big = [b for b in boxes if _area(b) >= BIG_BOX]
    res["shapes"], res["texts"] = len(boxes), len(texts)
    warn = res["warnings"].append
    small = set()
    lines = [(i, ln) for i, t in enumerate(texts) for ln in t]
    for i, ln in lines:
        b = (ln["x0"], ln["y0"], ln["x1"], ln["y1"])
        if ln["size"] < MIN_FONT and ln["text"] not in small:
            small.add(ln["text"])
            warn('글자가 너무 작습니다 ({:.0f}px < {}px): "{}"'.format(ln["size"], MIN_FONT, _short(ln["text"])))
        if not _contains(cbox, b):
            warn('글자가 캔버스 밖으로 나갑니다: "{}"'.format(_short(ln["text"])))
        ax = min(max(ln["ax"], b[0] + 1), b[2] - 1)
        ay = (b[1] + b[3]) / 2
        holders = [s for s in boxes if s[0] <= ax <= s[2] and s[1] <= ay <= s[3]]
        if holders:
            c = min(holders, key=_area)
            if not _contains(c, b):
                if b[2] - b[0] > c[2] - c[0] + TOL:
                    warn('글자가 상자를 넘칩니다: "{}" (상자 폭 {:.0f}, 글자 폭 약 {:.0f})'.format(
                        _short(ln["text"]), c[2] - c[0], b[2] - b[0]))
                else:
                    warn('글자가 상자 가장자리를 넘습니다: "{}" (상자 {:.0f},{:.0f}–{:.0f},{:.0f})'.format(
                        _short(ln["text"]), c[0], c[1], c[2], c[3]))
        else:
            for s in big:
                if _inter(s, b) > 0.05 * _area(b) and not _contains(s, b):
                    warn('글자가 상자 테두리에 걸칩니다: "{}"'.format(_short(ln["text"])))
                    break
    for n, (i, a) in enumerate(lines):
        ba = (a["x0"], a["y0"], a["x1"], a["y1"])
        for j, b in lines[n + 1:]:
            if i == j:
                continue
            bb = (b["x0"], b["y0"], b["x1"], b["y1"])
            if _inter(ba, bb) > 0.15 * min(_area(ba), _area(bb)):
                warn('글자가 겹칩니다: "{}" ↔ "{}"'.format(_short(a["text"], 20), _short(b["text"], 20)))
    for n, a in enumerate(big):
        for b in big[n + 1:]:
            if _inter(a, b) > 4 and not _contains(a, b) and not _contains(b, a):
                warn("상자가 어긋나게 겹칩니다: ({:.0f},{:.0f} {:.0f}×{:.0f}) ↔ ({:.0f},{:.0f} {:.0f}×{:.0f})".format(
                    a[0], a[1], a[2] - a[0], a[3] - a[1], b[0], b[1], b[2] - b[0], b[3] - b[1]))
    return res


# ---------------------------------------------------------------------------- 미리보기
def render_png(svg_path, png_path, size, timeout=60):
    """헤드리스 Chrome 으로 SVG 를 PNG 로 그린다 (그림 <img> 로 넣었을 때와 같은 모습)."""
    from .pdf import find_browser, _file_url
    browser = find_browser()
    if not browser:
        raise DevTrackError("미리보기에 쓸 Chrome/Chromium 을 찾지 못했습니다 (DEVTRACK_CHROME=<브라우저 실행 파일>)")
    w, h = max(1, int(round(size[0]))), max(1, int(round(size[1])))
    tmp = tempfile.mkdtemp(prefix="devtrack-svg-")
    try:
        page = os.path.join(tmp, "view.html")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write('<!doctype html><html><head><meta charset="utf-8"><style>html,body{{margin:0;background:#fff}}'
                     'img{{display:block;width:{w}px;height:{h}px}}</style></head><body><img src="{src}"></body></html>'
                     .format(w=w, h=h, src=_file_url(svg_path).replace('"', "%22")))
        out = os.path.join(tmp, "out.png")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--hide-scrollbars", "--force-device-scale-factor=1",
               "--user-data-dir=" + os.path.join(tmp, "profile"), "--window-size={},{}".format(w, h),
               "--virtual-time-budget=3000", "--screenshot=" + out, _file_url(page)]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise DevTrackError("미리보기 시간 초과 ({}초)".format(timeout))
        except OSError as e:
            raise DevTrackError("브라우저를 실행하지 못했습니다 ({}): {}".format(browser, e))
        try:
            with open(out, "rb") as fh:
                ok = fh.read(8) == b"\x89PNG\r\n\x1a\n"
        except OSError:
            ok = False
        if not ok:
            raise DevTrackError("미리보기를 만들지 못했습니다 (브라우저: {})".format(browser))
        os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
        shutil.move(out, png_path)
        return png_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
