"""슬라이드 · 포스터 설명(JSON)으로 PowerPoint(.pptx)를 만든다 — 표준 라이브러리만 (zipfile + XML 문자열).

HPC 처럼 pip 을 쓸 수 없는 곳에서도 /make-slides · /make-poster 스킬이 편집할 수 있는 pptx 를 만들 수 있게 한다.
- 레이아웃(제목 · 글머리표 · 그림 · 그림+글 · 그림 둘 · 표 · 핵심 문장 · 구역 · 자유 배치 · 포스터)은 여기서 계산하고,
  같은 계산으로 HTML 미리보기를 그려 헤드리스 Chrome 으로 PDF · PNG 를 만든다 (PowerPoint 와 줄바꿈이 조금 다를 수 있음).
- 글자가 칸을 넘칠지는 글꼴 없이 어림한다 (svgcheck.text_width) — 미리보기와 함께 본다.
- 외부 서비스는 쓰지 않는다. 그림은 사용자의 파일(PNG · JPEG · GIF · SVG)만 넣는다.
"""

import datetime as _dt
import html as _html
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib
from xml.sax.saxutils import escape as _x

from . import DevTrackError
from .svgcheck import text_width

IN = 914400          # EMU / inch
PT = 12700           # EMU / point
MM = 36000           # EMU / mm
SIZES = {
    "16:9": (12192000, 6858000), "4:3": (9144000, 6858000),
    "A0": (841 * MM, 1189 * MM), "A0-landscape": (1189 * MM, 841 * MM),
    "A1": (594 * MM, 841 * MM), "A1-landscape": (841 * MM, 594 * MM),
    "48x36in": (48 * IN, 36 * IN), "36x48in": (36 * IN, 48 * IN),
}
MAX_SIDE = 51206400  # PowerPoint 의 최대 슬라이드 크기 (56 in)
LAYOUTS = ("title", "section", "bullets", "figure", "figure-bullets", "two-figures", "table", "statement", "free", "poster")
IMAGE_EXT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".svg": "svg"}
THEME = {"accent": "#3a5ccc", "accent2": "#0f8b8d", "accent3": "#d9822b", "accent4": "#7b5cc4",
         "text": "#1f2937", "muted": "#6b7280", "background": "#ffffff", "light": "#f3f4f6",
         "font": "Arial", "font_ko": "맑은 고딕"}
PREVIEW_FONTS = "'{font}', Arial, 'Liberation Sans', 'Helvetica Neue', 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif"
_NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힣]")
_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*\s][^*]*?\*|\^\{[^}]*\}|_\{[^}]*\})")
SVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"


# ---------------------------------------------------------------------------- 예시
EXAMPLE_SLIDES = {
    "title": "A portable GPU pressure solver",
    "size": "16:9",
    "footer": "Kang & Lee · 2026",
    "numbers": True,
    "theme": {"accent": "#3a5ccc"},
    "slides": [
        {"layout": "title", "title": "A portable GPU pressure solver for incompressible flow",
         "subtitle": "H. Kang, J. Lee", "note": "Speaker notes: one line on why this matters."},
        {"layout": "bullets", "title": "Why a new solver?",
         "bullets": ["CPU solver limits **grid size** and turnaround",
                     ["Poisson solve takes 70 % of run time"], "Target: MI250X and A100 with one code"]},
        {"layout": "figure", "title": "Second-order convergence", "image": "figures/fig_convergence.png",
         "caption": "L2 error vs grid spacing h; observed order p = 2.01", "alt": "Log-log plot of L2 error falling with h"},
        {"layout": "figure-bullets", "title": "112.3× faster on 8 GCDs", "image": "figures/fig_speedup.png",
         "bullets": ["Mean of 5 runs, ±1.2 %", "CPU baseline: 64 cores"], "image_side": "left"},
        {"layout": "table", "title": "Timings", "table": {"header": ["Case", "CPU (s)", "GPU (s)", "Speedup"],
                                                          "rows": [["256³", "812", "7.23", "112.3×"]],
                                                          "highlight": [[1, 3]]}},
        {"layout": "statement", "title": "Take-home", "text": "One HIP code, second-order accurate, 112× faster",
         "sub": "Code and data: [[MISSING: availability statement]]"},
    ],
}
EXAMPLE_POSTER = {
    "title": "Near-wall velocity skewness: DNS and hot-wire",
    "size": "A0",
    "theme": {"accent": "#0f8b8d"},
    "slides": [{
        "layout": "poster",
        "title": "Near-wall velocity skewness in turbulent channel flow",
        "authors": "H. Kang, J. Lee",
        "affiliation": "[[MISSING: affiliations]]",
        "columns": [
            [{"heading": "Motivation", "bullets": ["Skewness S_{u} is sensitive to resolution", "Compare DNS with hot-wire"]},
             {"heading": "Methods", "bullets": ["DNS at Re_{τ} = 180, 256 × 128 × 256", "Single hot-wire, 1 mm"]}],
            [{"heading": "Results", "image": "figures/v1-overview.svg", "caption": "S_{u} at the wall: 0.98 vs 1.09"},
             {"heading": "Key numbers", "table": {"header": ["Quantity", "DNS", "Exp."], "rows": [["S_{u} (wall)", "0.98", "1.09"]]}}],
            [{"heading": "Conclusions", "bullets": ["Same sign change at y^{+} ≈ 15", "Difference 10.7 %"]},
             {"heading": "References", "text": "[[MISSING: references from the paper's .bib]]", "size": 0.8}],
        ],
    }],
}


# ---------------------------------------------------------------------------- 글자
def _runs(text):
    """간단한 글자 꾸밈: **굵게** · *기울임* · ^{위 첨자} · _{아래 첨자} → [(글, {속성})]."""
    text = str(text)
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], {}))
        tok = m.group(0)
        if tok.startswith("**"):
            out.append((tok[2:-2], {"b": True}))
        elif tok.startswith("^{"):
            out.append((tok[2:-1], {"sup": True}))
        elif tok.startswith("_{"):
            out.append((tok[2:-1], {"sub": True}))
        else:
            out.append((tok[1:-1], {"i": True}))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], {}))
    return [r for r in out if r[0]]


def plain(text):
    return "".join(t for t, _ in _runs(text))


def _color(c, default):
    c = str(c or default).strip().lstrip("#")
    if not re.match(r"^[0-9a-fA-F]{6}$", c):
        raise DevTrackError("색은 #rrggbb 형식이어야 합니다: {}".format(c))
    return c.upper()


def _para(text, size, level=0, bullet=False, bold=False, color=None, align="l", before=0.0, italic=False):
    return {"runs": _runs(text), "size": float(size), "level": level, "bullet": bullet, "bold": bold,
            "italic": italic, "color": color, "align": align, "before": before}


def _bullets(items, size, color, level=0, out=None):
    out = [] if out is None else out
    for it in items or []:
        if isinstance(it, (list, tuple)):
            _bullets(it, size * 0.85, color, level + 1, out)
        else:
            out.append(_para(it, size, level=level, bullet=True, color=color, before=size * 0.35))
    return out


def _lines_needed(p, width_pt):
    """문단이 몇 줄로 접히는지 어림 (한글 · 한자는 글자 단위, 나머지는 낱말 단위로 접음)."""
    indent = (p["level"] + 1) * 0.35 * p["size"] * 2 if p["bullet"] else 0
    width = max(1.0, width_pt - indent)
    lines, cur = 1, 0.0
    for tok in re.findall(r"[ᄀ-ᇿ㄰-㆏가-힣　-鿿]|\S+\s*|\s+", plain_runs(p["runs"])):
        w = text_width(tok, p["size"], p["bold"])
        if cur + w > width and cur > 0:
            lines += 1
            cur = text_width(tok.lstrip(), p["size"], p["bold"])
        else:
            cur += w
    return lines


def plain_runs(runs):
    return "".join(t for t, _ in runs)


def text_height_pt(paras, width_pt):
    return sum(_lines_needed(p, width_pt) * p["size"] * 1.2 + p["before"] for p in paras)


# ---------------------------------------------------------------------------- 그림
def image_size(path):
    """그림의 (가로, 세로) 픽셀 — PNG · JPEG · GIF · SVG(viewBox)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(64 * 1024)
    except OSError as e:
        raise DevTrackError("그림을 읽을 수 없습니다: {} ({})".format(path, e))
    if ext == ".png" and head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    if ext == ".gif" and head[:4] == b"GIF8":
        return struct.unpack("<HH", head[6:10])
    if ext in (".jpg", ".jpeg") and head[:2] == b"\xff\xd8":
        with open(path, "rb") as fh:
            data = fh.read()
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + seg
        raise DevTrackError("JPEG 크기를 읽지 못했습니다: {}".format(path))
    if ext == ".svg":
        from .svgcheck import _parse, _canvas
        root, errors = _parse(path)
        c = _canvas(root) if root is not None else None
        if not c:
            raise DevTrackError("SVG 크기(viewBox)를 읽지 못했습니다: {}".format(path))
        return c[2], c[3]
    raise DevTrackError("넣을 수 없는 그림 형식입니다 (PNG · JPEG · GIF · SVG): {}".format(path))


def _fit(box, size):
    x, y, w, h = box
    iw, ih = size
    s = min(w / float(iw), h / float(ih))
    fw, fh = int(iw * s), int(ih * s)
    return (x + (w - fw) // 2, y + (h - fh) // 2, fw, fh)


# ---------------------------------------------------------------------------- 레이아웃
class _Deck(object):
    def __init__(self, spec, base):
        self.spec, self.base = spec, base
        size = spec.get("size", "16:9")
        if isinstance(size, dict):
            self.W, self.H = int(float(size["width_mm"]) * MM), int(float(size["height_mm"]) * MM)
        elif size in SIZES:
            self.W, self.H = SIZES[size]
        else:
            raise DevTrackError("size 는 {} 또는 {{\"width_mm\", \"height_mm\"}} 입니다: {}".format(
                " · ".join(SIZES), size))
        if not (0 < self.W <= MAX_SIDE and 0 < self.H <= MAX_SIDE):
            raise DevTrackError("슬라이드 한 변은 PowerPoint 한도 1422 mm(56 in) 이하여야 합니다")
        th = dict(THEME, **(spec.get("theme") or {}))
        self.theme = {k: (_color(v, THEME[k]) if k not in ("font", "font_ko") else v) for k, v in th.items()}
        self.s = min(self.W, self.H) / 6858000.0 if min(self.W, self.H) < 20 * IN else min(self.W, self.H) / float(841 * MM)
        self.slide_scale = self.H / 6858000.0
        self.warnings = []

    def path(self, rel):
        p = os.path.expanduser(str(rel))
        p = p if os.path.isabs(p) else os.path.join(self.base, p)
        if not os.path.isfile(p):
            raise DevTrackError("그림 파일이 없습니다: {}".format(rel))
        if os.path.splitext(p)[1].lower() not in IMAGE_EXT:
            raise DevTrackError("넣을 수 없는 그림 형식입니다 (PNG · JPEG · GIF · SVG): {}".format(rel))
        return p

    # -- 요소 만들기
    def text(self, box, paras, anchor="t", fill=None, line=None, title=False, name=None, inset=None):
        ins = int(0.08 * IN * min(self.slide_scale, 2.0)) if inset is None else inset
        return {"kind": "text", "box": tuple(int(v) for v in box), "paras": paras, "anchor": anchor, "fill": fill,
                "line": line, "title": title, "name": name, "inset": ins}

    def image(self, box, rel, alt="", top=False):
        p = self.path(rel)
        w, h = image_size(p)
        fit = _fit(tuple(int(v) for v in box), (w, h))
        if top:
            fit = (fit[0], int(box[1]), fit[2], fit[3])
        return {"kind": "image", "box": fit, "path": p, "alt": alt or "", "px": (w, h)}

    def rect(self, box, fill=None, line=None, shape="rect", line_w=1.0):
        return {"kind": "shape", "box": tuple(int(v) for v in box), "fill": fill, "line": line, "shape": shape,
                "line_w": line_w}

    def line(self, x1, y1, x2, y2, color=None, w=1.5, arrow=False):
        return {"kind": "line", "pts": tuple(int(v) for v in (x1, y1, x2, y2)), "color": color or self.theme["muted"],
                "w": w, "arrow": arrow}

    def table(self, box, spec, size):
        header = [str(c) for c in spec.get("header") or []]
        rows = [[str(c) for c in r] for r in spec.get("rows") or []]
        if not header and not rows:
            raise DevTrackError("표에 header 나 rows 가 없습니다")
        ncol = max([len(header)] + [len(r) for r in rows])
        return {"kind": "table", "box": tuple(int(v) for v in box), "header": header, "rows": rows, "ncol": ncol,
                "size": size, "highlight": [tuple(h) for h in spec.get("highlight") or []]}

    # -- 공통 틀
    def frame(self, sl, n, total):
        W, H, s, th = self.W, self.H, self.slide_scale, self.theme
        m = 0.5 * IN * s
        els = []
        if sl.get("title") and sl.get("layout") not in ("title", "section", "statement", "poster"):
            els.append(self.text((m, 0.35 * IN * s, W - 2 * m, 0.95 * IN * s),
                                 [_para(sl["title"], sl.get("title_size", 30), bold=True, color=th["text"])],
                                 anchor="b", title=True))
            els.append(self.rect((m, 1.33 * IN * s, 1.1 * IN * s, 0.06 * IN * s), fill=th["accent"]))
        foot = self.spec.get("footer")
        if foot and sl.get("layout") not in ("title", "poster"):
            els.append(self.text((m, H - 0.45 * IN * s, W * 0.6, 0.35 * IN * s),
                                 [_para(foot, 12, color=th["muted"])], anchor="ctr"))
        if self.spec.get("numbers") and sl.get("layout") not in ("title", "poster"):
            els.append(self.text((W - m - 1.0 * IN * s, H - 0.45 * IN * s, 1.0 * IN * s, 0.35 * IN * s),
                                 [_para("{}".format(n), 12, color=th["muted"], align="r")], anchor="ctr"))
        return els

    def body_box(self, sl):
        W, H, s = self.W, self.H, self.slide_scale
        m = 0.5 * IN * s
        top = 1.55 * IN * s if sl.get("title") else 0.5 * IN * s
        bottom = H - (0.6 * IN * s if (self.spec.get("footer") or self.spec.get("numbers")) else 0.4 * IN * s)
        return (m, top, W - 2 * m, bottom - top)

    def figure(self, box, item, size=14, top=False):
        """그림 + 바로 아래 설명 (설명 두 줄 자리를 빼고 그림을 맞춘다)."""
        x, y, w, h = box
        cap = [_para(item["caption"], size, color=self.theme["muted"], align="ctr")] if item.get("caption") else []
        gap = size * 0.3 * PT
        cap_h = text_height_pt(cap, w / float(PT)) * PT * 1.05 + gap if cap else 0
        img = self.image((x, y, w, h - cap_h), item["image"] if "image" in item else item["path"], item.get("alt"), top=top)
        els = [img]
        if cap:
            ix, iy, iw, ih = img["box"]
            els.append(self.text((x, iy + ih + gap, w, cap_h - gap), cap, anchor="t", inset=0))
        return els

    def build(self, sl, n, total):
        lay = sl.get("layout", "bullets")
        if lay not in LAYOUTS:
            raise DevTrackError("슬라이드 {}: layout 은 {} 중 하나입니다 ({})".format(n, " · ".join(LAYOUTS), lay))
        th, s, W, H = self.theme, self.slide_scale, self.W, self.H
        els = self.frame(sl, n, total)
        if lay == "title":
            m = 0.9 * IN * s
            els.append(self.rect((0, 0, W, 0.18 * IN * s), fill=th["accent"]))
            els.append(self.text((m, H * 0.28, W - 2 * m, H * 0.3),
                                 [_para(sl.get("title", ""), sl.get("title_size", 40), bold=True, color=th["text"])],
                                 anchor="b", title=True))
            sub = [_para(x, 20 if i == 0 else 16, color=th["text"] if i == 0 else th["muted"], before=6 if i else 0)
                   for i, x in enumerate([sl.get("subtitle")] + list(sl.get("lines") or [])) if x]
            if sub:
                els.append(self.text((m, H * 0.6, W - 2 * m, H * 0.3), sub, anchor="t"))
        elif lay == "section":
            els.append(self.rect((0, 0, 0.35 * IN * s, H), fill=th["accent"]))
            els.append(self.text((1.0 * IN * s, H * 0.3, W - 2 * IN * s, H * 0.4),
                                 [_para(sl.get("title", ""), 40, bold=True, color=th["text"])]
                                 + ([_para(sl["subtitle"], 20, color=th["muted"], before=8)] if sl.get("subtitle") else []),
                                 anchor="ctr", title=True))
        elif lay == "bullets":
            els.append(self.text(self.body_box(sl), _bullets(sl.get("bullets"), sl.get("size", 24), th["text"])))
        elif lay == "figure":
            els += self.figure(self.body_box(sl), sl)
        elif lay == "figure-bullets":
            x, y, w, h = self.body_box(sl)
            fw = w * float(sl.get("image_width", 0.55))
            gap = 0.3 * IN * s
            left = sl.get("image_side", "left") == "left"
            fig_box = (x, y, fw - gap / 2, h) if left else (x + w - fw + gap / 2, y, fw - gap / 2, h)
            txt_box = (x + fw + gap / 2, y, w - fw - gap / 2, h) if left else (x, y, w - fw - gap / 2, h)
            els += self.figure(fig_box, sl)
            els.append(self.text(txt_box, _bullets(sl.get("bullets"), sl.get("size", 22), th["text"]), anchor="ctr"))
        elif lay == "two-figures":
            imgs = sl.get("images") or []
            if len(imgs) != 2:
                raise DevTrackError("슬라이드 {}: two-figures 는 images 두 개가 필요합니다".format(n))
            x, y, w, h = self.body_box(sl)
            gap = 0.3 * IN * s
            half = (w - gap) / 2
            els += self.figure((x, y, half, h), imgs[0])
            els += self.figure((x + half + gap, y, half, h), imgs[1])
        elif lay == "table":
            x, y, w, h = self.body_box(sl)
            tb = sl.get("table") or {}
            nrow = len(tb.get("rows") or []) + (1 if tb.get("header") else 0)
            size = sl.get("size", 18)
            th_h = min(h, nrow * size * 2.0 * PT)
            box = (x, y, w, th_h)
            if sl.get("caption"):
                els.append(self.text((x, y + th_h + 0.1 * IN * s, w, 0.6 * IN * s),
                                     [_para(sl["caption"], 14, color=th["muted"])]))
            els.append(self.table(box, tb, size))
        elif lay == "statement":
            m = 1.0 * IN * s
            paras = [_para(sl.get("text", ""), sl.get("size", 34), bold=True, color=th["accent"], align="ctr")]
            if sl.get("sub"):
                paras.append(_para(sl["sub"], 18, color=th["muted"], align="ctr", before=14))
            if sl.get("title"):
                els.append(self.text((m, 0.5 * IN * s, W - 2 * m, 0.7 * IN * s),
                                     [_para(sl["title"], 22, bold=True, color=th["muted"], align="ctr")], title=True))
            els.append(self.text((m, H * 0.25, W - 2 * m, H * 0.5), paras, anchor="ctr"))
        elif lay == "free":
            els += self.free(sl.get("items") or [], n)
        elif lay == "poster":
            els += self.poster(sl, n)
        if sl.get("note") is not None and not isinstance(sl.get("note"), str):
            raise DevTrackError("슬라이드 {}: note 는 글이어야 합니다".format(n))
        return els

    def pct(self, it, key, total):
        v = float(it.get(key, 0))
        return v / 100.0 * total

    def free(self, items, n):
        th = self.theme
        out = []
        for it in items:
            kind = it.get("type", "text")
            box = (self.pct(it, "x", self.W), self.pct(it, "y", self.H), self.pct(it, "w", self.W), self.pct(it, "h", self.H))
            if kind == "text":
                size = it.get("size", 18)
                lines = it["text"] if isinstance(it.get("text"), list) else [it.get("text", "")]
                paras = [_para(t, size, bold=it.get("bold", False), color=_color(it.get("color"), th["text"]),
                               align={"left": "l", "center": "ctr", "right": "r"}.get(it.get("align", "left"), "l"))
                         for t in lines]
                if it.get("bullets"):
                    paras = _bullets(it["bullets"], size, _color(it.get("color"), th["text"]))
                out.append(self.text(box, paras, anchor={"top": "t", "middle": "ctr", "bottom": "b"}.get(
                    it.get("valign", "top"), "t"), fill=it.get("fill") and _color(it["fill"], "ffffff"),
                    line=it.get("line") and _color(it["line"], "000000")))
            elif kind == "image":
                out.append(self.image(box, it["path"], it.get("alt")))
            elif kind in ("rect", "round", "ellipse"):
                out.append(self.rect(box, fill=it.get("fill") and _color(it["fill"], "ffffff"),
                                     line=it.get("line") and _color(it["line"], "000000"),
                                     shape={"rect": "rect", "round": "roundRect", "ellipse": "ellipse"}[kind]))
            elif kind in ("line", "arrow"):
                out.append(self.line(box[0], box[1], self.pct(it, "x2", self.W), self.pct(it, "y2", self.H),
                                     color=it.get("color") and _color(it["color"], "000000"), arrow=kind == "arrow"))
            elif kind == "table":
                out.append(self.table(box, it, it.get("size", 16)))
            else:
                raise DevTrackError("슬라이드 {}: 자유 배치 항목 type 은 text · image · rect · round · ellipse · line · arrow · "
                                    "table 입니다 ({})".format(n, kind))
        return out

    def poster(self, sl, n):
        """포스터: 위쪽 제목 띠 + 단(column)마다 블록(제목 · 글 · 글머리표 · 그림 · 표)을 위에서 아래로."""
        th, W, H = self.theme, self.W, self.H
        f = self.s  # A0 짧은 변 기준 배율
        self.text_size = float(sl.get("text_size", 30))      # 본문 (A0 기준 pt)
        self.heading_size = float(sl.get("heading_size", 48))  # 블록 제목
        m = 25 * MM * f
        out = []
        band_h = sl.get("band_mm", 150) * MM * f
        out.append(self.rect((0, 0, W, band_h), fill=th["accent"]))
        title = [_para(sl.get("title", ""), 84 * f, bold=True, color="FFFFFF")]
        if sl.get("authors"):
            title.append(_para(sl["authors"], 40 * f, color="FFFFFF", before=14 * f))
        if sl.get("affiliation"):
            title.append(_para(sl["affiliation"], 30 * f, color="FFFFFF", before=6 * f))
        logo_w = 0
        for i, lg in enumerate(sl.get("logos") or []):
            lw = band_h * 0.9
            out.append(self.image((W - m - (i + 1) * lw - i * m * 0.5, band_h * 0.05, lw, band_h * 0.9), lg))
            logo_w += lw + m * 0.5
        out.append(self.text((m, band_h * 0.08, W - 2 * m - logo_w, band_h * 0.84), title, anchor="ctr", title=True))
        cols = sl.get("columns") or []
        if not cols:
            raise DevTrackError("슬라이드 {}: poster 에 columns 가 없습니다".format(n))
        gap = 18 * MM * f
        top = band_h + gap
        bottom = H - m
        cw = (W - 2 * m - gap * (len(cols) - 1)) / float(len(cols))
        self.poster_fill = []
        for ci, col in enumerate(cols):
            x = m + ci * (cw + gap)
            avail = bottom - top - gap * (len(col) - 1)
            fixed = [b.get("h") for b in col]
            want = [float(h) / 100.0 * (bottom - top) if h else self._block_height(b, cw, f) for b, h in zip(col, fixed)]
            extra = avail - sum(want)
            # 블록은 내용 높이 그대로 (남으면 단 아래가 비고, 점검이 알린다). 넘치면 줄여 맞추고 알린다
            if extra < 0:
                k = (avail - sum(w for w, h in zip(want, fixed) if h)) / max(1.0, sum(w for w, h in zip(want, fixed) if not h))
                want = [w if h else w * k for w, h in zip(want, fixed)]
            self.poster_fill.append((ci + 1, (sum(want) + gap * (len(col) - 1)) / (bottom - top), extra < 0))
            y = top
            for b, bh in zip(col, want):
                out += self._block((x, y, cw, bh), b, f, n)
                y += bh + gap
        return out

    def _heading(self, b, f, width):
        """블록 제목 문단과 높이 (접히는 줄 수만큼)."""
        if not b.get("heading"):
            return [], 0
        p = [_para(b["heading"], self.heading_size * f, bold=True, color=self.theme["accent"])]
        return p, text_height_pt(p, width / float(PT)) * PT * 1.05 + 0.25 * self.heading_size * f * PT

    def _block_paras(self, b, f):
        size = self.text_size * f * float(b.get("size", 1.0))
        paras = []
        if b.get("text"):
            for t in (b["text"] if isinstance(b["text"], list) else [b["text"]]):
                paras.append(_para(t, size, color=self.theme["text"], before=size * 0.3))
        if b.get("bullets"):
            paras += _bullets(b["bullets"], size, self.theme["text"])
        return paras

    def _block_height(self, b, cw, f):
        inner = cw - 2 * 10 * MM * f
        head = self._heading(b, f, inner)[1]
        body = text_height_pt(self._block_paras(b, f), inner / PT) * PT * 1.1
        if b.get("image"):
            iw, ih = image_size(self.path(b["image"]))
            cap = [_para(b["caption"], self.text_size * 0.87 * f)] if b.get("caption") else []
            body += inner * ih / float(iw) + (text_height_pt(cap, inner / float(PT)) * PT * 1.05 + 0.3 * self.text_size * f * PT
                                              if cap else 0)
        if b.get("table"):
            rows = len(b["table"].get("rows") or []) + (1 if b["table"].get("header") else 0)
            body += rows * self.text_size * 0.87 * f * 2.0 * PT
        return head + body + 2 * 10 * MM * f

    def _block(self, box, b, f, n):
        th = self.theme
        x, y, w, h = box
        pad = 10 * MM * f
        out = [self.rect(box, fill=th["light"], shape="roundRect")]
        cy = y + pad
        inner_w = w - 2 * pad
        hp, hh = self._heading(b, f, inner_w)
        if hp:
            out.append(self.text((x + pad, cy, inner_w, hh), hp, anchor="t", inset=0))
            cy += hh
        paras = self._block_paras(b, f)
        if paras:
            th_pt = text_height_pt(paras, inner_w / PT) * PT * 1.1
            out.append(self.text((x + pad, cy, inner_w, th_pt), paras, inset=0))
            cy += th_pt
        rest = y + h - pad - cy
        if b.get("table"):
            rows = len(b["table"].get("rows") or []) + (1 if b["table"].get("header") else 0)
            tbh = min(rest, rows * self.text_size * 0.87 * f * 2.0 * PT)
            out.append(self.table((x + pad, cy, inner_w, tbh), b["table"], self.text_size * 0.87 * f))
            cy += tbh
            rest = y + h - pad - cy
        if b.get("image") and rest > 0:
            out += self.figure((x + pad, cy, inner_w, rest), b, size=self.text_size * 0.87 * f, top=True)
        return out


# ---------------------------------------------------------------------------- 점검
def _check(deck, slides):
    warns = []
    for n, (sl, els) in enumerate(slides, 1):
        for e in els:
            if e["kind"] == "text":
                x, y, w, h = e["box"]
                need = text_height_pt(e["paras"], (w - 2 * e["inset"]) / float(PT)) * PT
                if need > (h - e["inset"]) * 1.03 and plain_runs(sum((p["runs"] for p in e["paras"]), [])).strip():
                    first = plain_runs(e["paras"][0]["runs"])[:30]
                    warns.append("슬라이드 {}: 글자가 칸을 넘칠 수 있습니다 — \"{}\" (필요 {:.0f}% 정도)".format(
                        n, first, 100.0 * need / max(1, h - e["inset"])))
                small = [p["size"] for p in e["paras"] if p["size"] < (12 if deck.H < 20 * IN else 24)]
                if small:
                    warns.append("슬라이드 {}: 글자가 작습니다 ({:.0f}pt) — \"{}\"".format(
                        n, min(small), plain_runs(e["paras"][0]["runs"])[:30]))
            elif e["kind"] == "image" and not e["path"].lower().endswith(".svg"):
                px_needed = e["box"][2] / float(IN) * 96
                if e["px"][0] < px_needed * 0.6:
                    warns.append("슬라이드 {}: 그림 해상도가 낮습니다 — {} ({}px 를 {:.0f}px 넓이로 늘림)".format(
                        n, os.path.basename(e["path"]), e["px"][0], px_needed))
        for col, ratio, over in getattr(deck, "poster_fill", []) if sl.get("layout") == "poster" else []:
            if over:
                warns.append("슬라이드 {}: 포스터 {}단의 내용이 넘칩니다 — 줄이거나 다른 단으로 옮기세요".format(n, col))
            elif ratio < 0.75:
                warns.append("슬라이드 {}: 포스터 {}단의 아래쪽이 {:.0f}% 비어 있습니다 — 단 사이 내용을 맞추세요".format(
                    n, col, 100 * (1 - ratio)))
        bullets = sum(1 for e in els if e["kind"] == "text" for p in e["paras"] if p["bullet"])
        if sl.get("layout") in ("bullets", "figure-bullets") and bullets > 6:
            warns.append("슬라이드 {}: 글머리표가 {}개 — 6개 이하로 나누는 것이 좋습니다".format(n, bullets))
    return warns


# ---------------------------------------------------------------------------- XML
def _rpr(attrs, p, color, theme):
    a = ['lang="{}"'.format("ko-KR" if attrs.get("ko") else "en-US"), 'sz="{}"'.format(int(round(p["size"] * 100)))]
    if p["bold"] or attrs.get("b"):
        a.append('b="1"')
    if p.get("italic") or attrs.get("i"):
        a.append('i="1"')
    if attrs.get("sup"):
        a.append('baseline="30000"')
    if attrs.get("sub"):
        a.append('baseline="-25000"')
    a.append('dirty="0"')
    # 글꼴을 글자마다 적어 둔다 — 테마 글꼴(+mn-lt)을 풀지 못하는 렌더러가 명조로 바꾸지 않게
    return ('<a:rPr {}><a:solidFill><a:srgbClr val="{}"/></a:solidFill><a:latin typeface="{f}"/><a:ea typeface="{k}"/>'
            '<a:cs typeface="{f}"/></a:rPr>').format(" ".join(a), color, f=_x(theme["font"], {'"': "&quot;"}),
                                                    k=_x(theme["font_ko"], {'"': "&quot;"}))


def _paras_xml(paras, theme):
    out = []
    for p in paras:
        color = p["color"] or theme["text"]
        ppr = ['algn="{}"'.format(p["align"])]
        inner = []
        if p["before"]:
            inner.append('<a:spcBef><a:spcPts val="{}"/></a:spcBef>'.format(int(p["before"] * 100)))
        if p["bullet"]:
            mar = int((p["level"] + 1) * 0.35 * p["size"] * 2 * PT)
            ppr += ['marL="{}"'.format(mar), 'indent="{}"'.format(-int(0.35 * p["size"] * 2 * PT)), 'lvl="{}"'.format(
                min(p["level"], 8))]
            inner.append('<a:buClr><a:srgbClr val="{}"/></a:buClr><a:buFont typeface="Arial"/>'
                         '<a:buChar char="{}"/>'.format(theme["accent"], "•" if p["level"] == 0 else "–"))
        else:
            ppr += ['marL="0"', 'indent="0"']
            inner.append("<a:buNone/>")
        runs = []
        for t, attrs in p["runs"] or [("", {})]:
            if _HANGUL.search(t):
                attrs = dict(attrs, ko=True)
            runs.append("<a:r>{}<a:t>{}</a:t></a:r>".format(_rpr(attrs, p, color, theme), _x(t)))
        out.append("<a:p><a:pPr {}>{}</a:pPr>{}</a:p>".format(" ".join(ppr), "".join(inner), "".join(runs)))
    return "".join(out)


def _xfrm(box, tag="a:xfrm", flip=""):
    x, y, w, h = box
    return '<{tag}{flip}><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></{tag}>'.format(
        tag=tag, flip=flip, x=int(x), y=int(y), w=max(1, int(w)), h=max(1, int(h)))


def _fill(c):
    return '<a:solidFill><a:srgbClr val="{}"/></a:solidFill>'.format(c) if c else "<a:noFill/>"


def _ln(c, w=1.0):
    return '<a:ln w="{}"><a:solidFill><a:srgbClr val="{}"/></a:solidFill></a:ln>'.format(int(w * PT), c) if c \
        else "<a:ln><a:noFill/></a:ln>"


def _sp_text(e, sid, theme):
    if e["title"]:
        nv = ('<p:nvSpPr><p:cNvPr id="{}" name="Title {}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
              '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>').format(sid, sid)
    else:
        nv = ('<p:nvSpPr><p:cNvPr id="{}" name="TextBox {}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>').format(sid, sid)
    ins = e["inset"]
    return ('<p:sp>{nv}<p:spPr>{xfrm}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>{fill}{ln}</p:spPr>'
            '<p:txBody><a:bodyPr wrap="square" lIns="{i}" tIns="{i}" rIns="{i}" bIns="{i}" anchor="{anchor}" rtlCol="0">'
            '<a:normAutofit/></a:bodyPr><a:lstStyle/>{paras}</p:txBody></p:sp>').format(
        nv=nv, xfrm=_xfrm(e["box"]), fill=_fill(e["fill"]), ln=_ln(e["line"]) if e["line"] else "<a:ln><a:noFill/></a:ln>",
        i=int(ins), anchor=e["anchor"], paras=_paras_xml(e["paras"], theme))


def _sp_shape(e, sid):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Shape {sid}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>{xfrm}'
            '<a:prstGeom prst="{shape}"><a:avLst/></a:prstGeom>{fill}{ln}</p:spPr>'
            '<p:txBody><a:bodyPr rtlCol="0" anchor="ctr"/><a:lstStyle/><a:p><a:endParaRPr lang="en-US" dirty="0"/></a:p>'
            '</p:txBody></p:sp>').format(sid=sid, xfrm=_xfrm(e["box"]), shape=e["shape"], fill=_fill(e["fill"]),
                                         ln=_ln(e["line"], e["line_w"]))


def _cxn(e, sid):
    x1, y1, x2, y2 = e["pts"]
    flip = (' flipH="1"' if x2 < x1 else "") + (' flipV="1"' if y2 < y1 else "")
    box = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
    tail = '<a:tailEnd type="triangle" w="med" len="med"/>' if e["arrow"] else ""
    return ('<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{sid}" name="Connector {sid}"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
            '<p:spPr>{xfrm}<a:prstGeom prst="straightConnector1"><a:avLst/></a:prstGeom>'
            '<a:ln w="{w}"><a:solidFill><a:srgbClr val="{c}"/></a:solidFill>{tail}</a:ln></p:spPr></p:cxnSp>').format(
        sid=sid, xfrm=_xfrm(box, flip=flip), w=int(e["w"] * PT), c=e["color"], tail=tail)


def _pic(e, sid, rid, svg_rid=None):
    ext = ('<a:extLst><a:ext uri="{}"><asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
           'r:embed="{}"/></a:ext></a:extLst>'.format(SVG_EXT_URI, svg_rid)) if svg_rid else ""
    return ('<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="Picture {sid}" descr="{alt}"/><p:cNvPicPr>'
            '<a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="{rid}">{ext}</a:blip>'
            '<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>{xfrm}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '</p:spPr></p:pic>').format(sid=sid, alt=_x(e["alt"], {'"': "&quot;"}), rid=rid, ext=ext, xfrm=_xfrm(e["box"]))


def _tbl(e, sid, theme):
    x, y, w, h = e["box"]
    ncol = e["ncol"]
    rows = ([e["header"]] if e["header"] else []) + e["rows"]
    rh = int(h / max(1, len(rows)))
    cw = int(w / ncol)
    size = e["size"]
    out = []
    for ri, row in enumerate(rows):
        head = bool(e["header"]) and ri == 0
        cells = []
        for ci in range(ncol):
            txt = row[ci] if ci < len(row) else ""
            hl = (ri, ci) in e["highlight"]
            color = "FFFFFF" if head else (theme["accent"] if hl else theme["text"])
            p = _para(txt, size, bold=head or hl, color=color, align="l" if ci == 0 else "ctr")
            fill = theme["accent"] if head else (theme["light"] if ri % 2 == 0 else "FFFFFF")
            cells.append('<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{}</a:txBody><a:tcPr marL="{m}" marR="{m}" anchor="ctr">'
                         '<a:lnB w="{lw}"><a:solidFill><a:srgbClr val="{lc}"/></a:solidFill></a:lnB>{fill}</a:tcPr>'
                         '</a:tc>'.format(_paras_xml([p], theme), m=int(0.08 * IN), lw=int(0.75 * PT), lc="D1D5DB",
                                          fill=_fill(fill)))
        out.append('<a:tr h="{}">{}</a:tr>'.format(rh, "".join(cells)))
    return ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="Table {sid}"/><p:cNvGraphicFramePr>'
            '<a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>{xfrm}'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl>'
            '<a:tblPr firstRow="{fr}" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{rows}</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>').format(sid=sid, xfrm=_xfrm(e["box"], tag="p:xfrm"), fr=1 if e["header"] else 0,
                                        grid="".join('<a:gridCol w="{}"/>'.format(cw) for _ in range(ncol)),
                                        rows="".join(out))


_TREE_HEAD = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm>'
              '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def _theme_xml(th, name):
    acc = [th["accent"], th["accent2"], th["accent3"], th["accent4"], "4B5563", "9CA3AF"]
    font = ('<a:latin typeface="{f}"/><a:ea typeface=""/><a:cs typeface=""/><a:font script="Hang" typeface="{k}"/>'
            .format(f=_x(th["font"]), k=_x(th["font_ko"])))
    return (_XML + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="{name}"><a:themeElements>'
            '<a:clrScheme name="DevTrack"><a:dk1><a:srgbClr val="{text}"/></a:dk1><a:lt1><a:srgbClr val="{bg}"/></a:lt1>'
            '<a:dk2><a:srgbClr val="374151"/></a:dk2><a:lt2><a:srgbClr val="{light}"/></a:lt2>{acc}'
            '<a:hlink><a:srgbClr val="{a1}"/></a:hlink><a:folHlink><a:srgbClr val="{a4}"/></a:folHlink></a:clrScheme>'
            '<a:fontScheme name="DevTrack"><a:majorFont>{font}</a:majorFont><a:minorFont>{font}</a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="DevTrack"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"><a:tint val="50000"/></a:schemeClr></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"><a:shade val="80000"/></a:schemeClr></a:solidFill></a:fillStyleLst>'
            '<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
            '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
            '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/></a:schemeClr></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"><a:shade val="90000"/></a:schemeClr></a:solidFill></a:bgFillStyleLst>'
            '</a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>').format(
        name=name, text=th["text"], bg=th["background"], light=th["light"], a1=acc[0], a4=acc[3], font=font,
        acc="".join('<a:accent{i}><a:srgbClr val="{c}"/></a:accent{i}>'.format(i=i + 1, c=c) for i, c in enumerate(acc)))


def _master_xml(W, H):
    title = ('<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
             '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr>{xfrm}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
             '</p:spPr><p:txBody><a:bodyPr vert="horz" lIns="91440" tIns="45720" rIns="91440" bIns="45720" rtlCol="0" '
             'anchor="b"><a:normAutofit/></a:bodyPr><a:lstStyle/><a:p><a:endParaRPr lang="en-US" dirty="0"/></a:p></p:txBody>'
             '</p:sp>').format(xfrm=_xfrm((int(W * 0.04), int(H * 0.05), int(W * 0.92), int(H * 0.14))))
    lvl = ('<a:lvl1pPr algn="l" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:defRPr sz="{sz}" kern="1200"{b}>'
           '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+{f}-lt"/><a:ea typeface="+{f}-ea"/>'
           '<a:cs typeface="+{f}-cs"/></a:defRPr></a:lvl1pPr>')
    return (_XML + '<p:sldMaster {ns}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            '<p:spTree>{head}{title}</p:spTree></p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
            'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            '<p:txStyles><p:titleStyle>{t}</p:titleStyle><p:bodyStyle>{b}</p:bodyStyle>'
            '<p:otherStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:otherStyle></p:txStyles></p:sldMaster>').format(
        ns=_NS, head=_TREE_HEAD, title=title, t=lvl.format(sz=3200, b=' b="1"', f="mj"), b=lvl.format(sz=2000, b="", f="mn"))


def _layout_xml():
    return (_XML + '<p:sldLayout {ns} type="titleOnly" preserve="1"><p:cSld name="Title Only"><p:spTree>{head}'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>'
            '<a:p><a:endParaRPr lang="en-US" dirty="0"/></a:p></p:txBody></p:sp></p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>').format(ns=_NS, head=_TREE_HEAD)


def _notes_master_xml():
    return (_XML + '<p:notesMaster {ns}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            '<p:spTree>{head}'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" '
            'noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr>'
            '<a:xfrm><a:off x="381000" y="685800"/><a:ext cx="6096000" cy="3429000"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln w="12700"><a:solidFill>'
            '<a:prstClr val="black"/></a:solidFill></a:ln></p:spPr></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="685800" '
            'y="4343400"/><a:ext cx="5486400" cy="4114800"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '</p:spPr><p:txBody><a:bodyPr vert="horz" lIns="91440" tIns="45720" rIns="91440" bIns="45720" rtlCol="0"/>'
            '<a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp></p:spTree></p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
            'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:notesStyle><a:lvl1pPr marL="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" '
            'hangingPunct="1"><a:defRPr sz="1200" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
            '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr>'
            '</p:notesStyle></p:notesMaster>').format(ns=_NS, head=_TREE_HEAD)


def _notes_xml(text):
    paras = "".join('<a:p><a:r><a:rPr lang="{}" dirty="0"/><a:t>{}</a:t></a:r></a:p>'.format(
        "ko-KR" if _HANGUL.search(ln) else "en-US", _x(ln)) for ln in text.split("\n")) or "<a:p/>"
    return (_XML + '<p:notes {ns}><p:cSld><p:spTree>{head}'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" '
            'noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/>'
            '<a:lstStyle/>{paras}</p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
            '</p:notes>').format(ns=_NS, head=_TREE_HEAD, paras=paras)


def _rels(items):
    return (_XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
        '<Relationship Id="{}" Type="{}" Target="{}"/>'.format(i, "http://schemas.openxmlformats.org/officeDocument/2006/"
                                                                  "relationships/" + t if "/" not in t else t, _x(tg))
        for i, t, tg in items) + "</Relationships>")


# ---------------------------------------------------------------------------- 정보
POSTER_MIN_MM = 500  # 한 장짜리이고 긴 변이 이보다 크면 포스터 (A2 594 mm 이상, 16:9 슬라이드는 339 mm)


def pptx_info(path):
    """pptx 의 슬라이드 수 · 크기(mm) · 포스터인지 (한 장짜리 큰 판 — 웹이 발표 자료와 포스터를 나눈다). 읽지 못하면 None."""
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("ppt/presentation.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return None
    ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    sz, ids = root.find(ns + "sldSz"), root.find(ns + "sldIdLst")
    if sz is None:
        return None
    w_mm, h_mm = int(round(int(sz.get("cx", 0)) / float(MM))), int(round(int(sz.get("cy", 0)) / float(MM)))
    n = len(ids) if ids is not None else 0
    return {"slides": n, "w_mm": w_mm, "h_mm": h_mm, "poster": n == 1 and max(w_mm, h_mm) >= POSTER_MIN_MM}


# ---------------------------------------------------------------------------- 만들기
def load_spec(path):
    try:
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except OSError as e:
        raise DevTrackError("설명 파일을 읽을 수 없습니다: {} ({})".format(path, e))
    except ValueError as e:
        raise DevTrackError("설명 파일이 JSON 이 아닙니다: {} ({})".format(path, e))
    if not isinstance(spec, dict) or not isinstance(spec.get("slides"), list) or not spec["slides"]:
        raise DevTrackError("설명 JSON 에 slides 목록이 필요합니다 (devtrack pptx --example 참고)")
    return spec


def layout(spec, base):
    deck = _Deck(spec, base)
    total = len(spec["slides"])
    slides = [(sl, deck.build(sl, n, total)) for n, sl in enumerate(spec["slides"], 1)]
    deck.warnings = _check(deck, slides)
    return deck, slides


def _blank_png(path):
    """1×1 투명 PNG (대체 그림을 만들 수 없을 때)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(raw)
    return path


def _svg_fallback(path, box_px, tmp, warnings):
    """SVG 그림은 PowerPoint 2016 이후에만 그대로 보이므로 PNG 대체 그림을 함께 넣는다 — 같은 이름 .png > Chrome 으로
    그린 PNG > (Chrome 이 없으면) 투명 그림 + 경고."""
    same = os.path.splitext(path)[0] + ".png"
    if os.path.isfile(same):
        return same
    from .svgcheck import render_png
    w, h = image_size(path)
    k = max(1.0, 2.0 * box_px / float(w))
    out = os.path.join(tmp, "fallback-{}.png".format(abs(hash(path))))
    try:
        return render_png(path, out, (w * k, h * k))
    except DevTrackError:
        msg = ("SVG 그림 {} 은 PowerPoint 2016 이후 · Microsoft 365 에서만 보입니다 (대체 PNG 를 만들 Chrome 이 없음 — "
               "같은 이름의 .png 를 옆에 두면 그것을 씀)".format(os.path.basename(path)))
        if msg not in warnings:
            warnings.append(msg)
        return _blank_png(out)


def write_pptx(spec, out_path, base, svg_fallback=True):
    """pptx 를 쓴다. svg_fallback=False 면 SVG 의 PNG 대체 그림을 그리지 않는다 (Chrome 없이 빠르게 — 데모용)."""
    deck, slides = layout(spec, base)
    W, H, th = deck.W, deck.H, deck.theme
    tmp = tempfile.mkdtemp(prefix="devtrack-pptx-")
    try:
        files = {}
        media, media_ix = {}, [0]

        def add_media(path):
            if path not in media:
                media_ix[0] += 1
                ext = os.path.splitext(path)[1].lower().lstrip(".")
                ext = "jpeg" if ext == "jpg" else ext
                media[path] = "media/image{}.{}".format(media_ix[0], ext)
                with open(path, "rb") as fh:
                    files["ppt/" + media[path]] = fh.read()
            return media[path]

        slide_parts = []
        for n, (sl, els) in enumerate(slides, 1):
            rels = [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]
            shapes, sid = [], 2
            for e in els:
                if e["kind"] == "text":
                    shapes.append(_sp_text(e, sid, th))
                elif e["kind"] == "shape":
                    shapes.append(_sp_shape(e, sid))
                elif e["kind"] == "line":
                    shapes.append(_cxn(e, sid))
                elif e["kind"] == "table":
                    shapes.append(_tbl(e, sid, th))
                elif e["kind"] == "image":
                    svg_rid = None
                    src = e["path"]
                    if src.lower().endswith(".svg"):
                        fb = (_svg_fallback(src, e["box"][2] / float(IN) * 96, tmp, deck.warnings) if svg_fallback
                              else _blank_png(os.path.join(tmp, "blank-{}.png".format(len(media)))))
                        svg_rid = "rId{}".format(len(rels) + 2)
                        rid = "rId{}".format(len(rels) + 1)
                        rels.append((rid, "image", "../" + add_media(fb)))
                        rels.append((svg_rid, "image", "../" + add_media(src)))
                    else:
                        rid = "rId{}".format(len(rels) + 1)
                        rels.append((rid, "image", "../" + add_media(src)))
                    shapes.append(_pic(e, sid, rid, svg_rid))
                sid += 1
            note = sl.get("note")
            if note:
                rels.append(("rId{}".format(len(rels) + 1), "notesSlide", "../notesSlides/notesSlide{}.xml".format(n)))
                files["ppt/notesSlides/notesSlide{}.xml".format(n)] = _notes_xml(note)
                files["ppt/notesSlides/_rels/notesSlide{}.xml.rels".format(n)] = _rels([
                    ("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"),
                    ("rId2", "slide", "../slides/slide{}.xml".format(n))])
            files["ppt/slides/slide{}.xml".format(n)] = (
                _XML + '<p:sld {}><p:cSld><p:spTree>{}{}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
                       '</p:sld>'.format(_NS, _TREE_HEAD, "".join(shapes)))
            files["ppt/slides/_rels/slide{}.xml.rels".format(n)] = _rels(rels)
            slide_parts.append((n, bool(note)))

        n_slides = len(slides)
        pres_rels = [("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
                     ("rId2", "notesMaster", "notesMasters/notesMaster1.xml"),
                     ("rId3", "presProps", "presProps.xml"), ("rId4", "viewProps", "viewProps.xml"),
                     ("rId5", "theme", "theme/theme1.xml"), ("rId6", "tableStyles", "tableStyles.xml")]
        pres_rels += [("rId{}".format(10 + n), "slide", "slides/slide{}.xml".format(n)) for n in range(1, n_slides + 1)]
        files["ppt/presentation.xml"] = (
            _XML + '<p:presentation {ns} saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
                   '</p:sldMasterIdLst><p:notesMasterIdLst><p:notesMasterId r:id="rId2"/></p:notesMasterIdLst><p:sldIdLst>{ids}'
                   '</p:sldIdLst><p:sldSz cx="{W}" cy="{H}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>').format(
            ns=_NS, W=W, H=H, ids="".join('<p:sldId id="{}" r:id="rId{}"/>'.format(255 + n, 10 + n)
                                         for n in range(1, n_slides + 1)))
        files["ppt/_rels/presentation.xml.rels"] = _rels(pres_rels)
        files["ppt/slideMasters/slideMaster1.xml"] = _master_xml(W, H)
        files["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels([
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")])
        files["ppt/slideLayouts/slideLayout1.xml"] = _layout_xml()
        files["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels([
            ("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")])
        files["ppt/notesMasters/notesMaster1.xml"] = _notes_master_xml()
        files["ppt/notesMasters/_rels/notesMaster1.xml.rels"] = _rels([("rId1", "theme", "../theme/theme2.xml")])
        files["ppt/theme/theme1.xml"] = _theme_xml(th, "DevTrack")
        files["ppt/theme/theme2.xml"] = _theme_xml(th, "DevTrack Notes")
        files["ppt/presProps.xml"] = _XML + '<p:presentationPr {}/>'.format(_NS)
        files["ppt/viewProps.xml"] = (_XML + '<p:viewPr {}><p:normalViewPr><p:restoredLeft sz="15620"/>'
                                             '<p:restoredTop sz="94660"/></p:normalViewPr><p:gridSpacing cx="76200" '
                                             'cy="76200"/></p:viewPr>'.format(_NS))
        files["ppt/tableStyles.xml"] = (_XML + '<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                                               'def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>')
        now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        files["docProps/core.xml"] = (
            _XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                   'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                   '<dc:title>{t}</dc:title><dc:creator>DevTrack</dc:creator>'
                   '<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                   '<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>').format(
            t=_x(plain(spec.get("title") or "")), now=now)
        files["docProps/app.xml"] = (
            _XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
                   'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>DevTrack'
                   '</Application><Slides>{}</Slides><Notes>{}</Notes></Properties>').format(
            n_slides, sum(1 for _, has in slide_parts if has))
        exts = sorted(set(os.path.splitext(p)[1].lstrip(".") for p in media.values()))
        ctype = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml"}
        over = [("/ppt/presentation.xml", "presentationml.presentation.main+xml"),
                ("/ppt/slideMasters/slideMaster1.xml", "presentationml.slideMaster+xml"),
                ("/ppt/slideLayouts/slideLayout1.xml", "presentationml.slideLayout+xml"),
                ("/ppt/notesMasters/notesMaster1.xml", "presentationml.notesMaster+xml"),
                ("/ppt/theme/theme1.xml", "theme+xml"), ("/ppt/theme/theme2.xml", "theme+xml"),
                ("/ppt/presProps.xml", "presentationml.presProps+xml"),
                ("/ppt/viewProps.xml", "presentationml.viewProps+xml"),
                ("/ppt/tableStyles.xml", "presentationml.tableStyles+xml"),
                ("/docProps/app.xml", "extended-properties+xml")]
        over += [("/ppt/slides/slide{}.xml".format(n), "presentationml.slide+xml") for n in range(1, n_slides + 1)]
        over += [("/ppt/notesSlides/notesSlide{}.xml".format(n), "presentationml.notesSlide+xml")
                 for n, has in slide_parts if has]
        files["[Content_Types].xml"] = (
            _XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
            + "".join('<Default Extension="{}" ContentType="{}"/>'.format(e, ctype[e]) for e in exts)
            + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            + "".join('<Override PartName="{}" ContentType="application/vnd.openxmlformats-officedocument.{}"/>'.format(p, t)
                      for p, t in over) + "</Types>")
        files["_rels/.rels"] = _rels([
            ("rId1", "officeDocument", "ppt/presentation.xml"),
            ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
             "docProps/core.xml"),
            ("rId3", "extended-properties", "docProps/app.xml")])
        out_path = os.path.abspath(out_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        part = out_path + ".part"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", files.pop("[Content_Types].xml"))
            z.writestr("_rels/.rels", files.pop("_rels/.rels"))
            for name in sorted(files):
                data = files[name]
                z.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
        os.replace(part, out_path)
        return deck, slides
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------- 미리보기 (HTML → Chrome)
def _css_color(c):
    return "#" + c if c else "transparent"


def _html_paras(paras):
    out = []
    for p in paras:
        style = "margin:{b:.2f}pt 0 0 0;font-size:{s:.2f}pt;line-height:1.2;text-align:{a};color:#{c};{w}{i}".format(
            b=p["before"], s=p["size"], a={"l": "left", "ctr": "center", "r": "right"}[p["align"]],
            c=p["color"] or "1F2937", w="font-weight:bold;" if p["bold"] else "", i="font-style:italic;" if p.get("italic") else "")
        runs = []
        for t, a in p["runs"]:
            s = _html.escape(t)
            if a.get("b"):
                s = "<b>{}</b>".format(s)
            if a.get("i"):
                s = "<i>{}</i>".format(s)
            if a.get("sup"):
                s = "<sup>{}</sup>".format(s)
            if a.get("sub"):
                s = "<sub>{}</sub>".format(s)
            runs.append(s)
        if p["bullet"]:
            ind = 0.35 * p["size"] * 2
            style += "padding-left:{:.2f}pt;text-indent:-{:.2f}pt;".format((p["level"] + 1) * ind, ind)
            runs.insert(0, '<span style="display:inline-block;width:{:.2f}pt;text-indent:0">{}</span>'.format(
                ind, "•" if p["level"] == 0 else "–"))
        out.append('<p style="{}">{}</p>'.format(style, "".join(runs) or "&nbsp;"))
    return "".join(out)


def _emu_in(v):
    return "{:.4f}in".format(v / float(IN))


def _slide_html(deck, els):
    parts = []
    lines = []
    for e in els:
        if e["kind"] == "line":
            lines.append(e)
            continue
        x, y, w, h = e["box"]
        pos = "left:{};top:{};width:{};height:{};".format(_emu_in(x), _emu_in(y), _emu_in(w), _emu_in(h))
        if e["kind"] == "text":
            just = {"t": "flex-start", "ctr": "center", "b": "flex-end"}[e["anchor"]]
            parts.append('<div class="el" style="{pos}display:flex;flex-direction:column;justify-content:{j};'
                         'padding:{p};background:{bg};{bd}">{body}</div>'.format(
                             pos=pos, j=just, p=_emu_in(e["inset"]), bg=_css_color(e["fill"]),
                             bd="border:1px solid #{};".format(e["line"]) if e["line"] else "", body=_html_paras(e["paras"])))
        elif e["kind"] == "shape":
            rad = {"roundRect": "border-radius:{};".format(_emu_in(min(w, h) * 0.08)), "ellipse": "border-radius:50%;"}.get(
                e["shape"], "")
            parts.append('<div class="el" style="{}background:{};{}{}"></div>'.format(
                pos, _css_color(e["fill"]), "border:{:.2f}pt solid #{};".format(e["line_w"], e["line"]) if e["line"] else "",
                rad))
        elif e["kind"] == "image":
            from .pdf import _file_url
            parts.append('<img class="el" style="{}" src="{}" alt="">'.format(pos, _html.escape(_file_url(e["path"]))))
        elif e["kind"] == "table":
            rows = ([e["header"]] if e["header"] else []) + e["rows"]
            th = deck.theme
            trs = []
            for ri, row in enumerate(rows):
                head = bool(e["header"]) and ri == 0
                tds = []
                for ci in range(e["ncol"]):
                    hl = (ri, ci) in e["highlight"]
                    bg = th["accent"] if head else (th["light"] if ri % 2 == 0 else "FFFFFF")
                    color = "FFFFFF" if head else (th["accent"] if hl else th["text"])
                    tds.append('<td style="background:#{};color:#{};font-weight:{};text-align:{}">{}</td>'.format(
                        bg, color, "bold" if head or hl else "normal", "left" if ci == 0 else "center",
                        _html_paras([_para(row[ci] if ci < len(row) else "", e["size"], color=color, bold=head or hl,
                                           align="l" if ci == 0 else "ctr")])))
                trs.append("<tr>{}</tr>".format("".join(tds)))
            parts.append('<table class="el" style="{}">{}</table>'.format(pos, "".join(trs)))
    if lines:
        svg = ['<svg class="el" style="left:0;top:0;width:{w};height:{h}" viewBox="0 0 {W} {H}" '
               'xmlns="http://www.w3.org/2000/svg"><defs>'.format(w=_emu_in(deck.W), h=_emu_in(deck.H), W=deck.W, H=deck.H)]
        for c in sorted(set(e["color"] for e in lines if e["arrow"])):
            svg.append('<marker id="ah{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" '
                       'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#{c}"/></marker>'.format(c=c))
        svg.append("</defs>")
        for e in lines:
            x1, y1, x2, y2 = e["pts"]
            svg.append('<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#{}" stroke-width="{}"{}/>'.format(
                x1, y1, x2, y2, e["color"], e["w"] * PT, ' marker-end="url(#ah{})"'.format(e["color"]) if e["arrow"] else ""))
        svg.append("</svg>")
        parts.append("".join(svg))
    return '<section class="slide">{}</section>'.format("".join(parts))


def preview_html(deck, slides):
    font = PREVIEW_FONTS.format(font=deck.theme["font"].replace("'", ""))
    return ('<!doctype html><html><head><meta charset="utf-8"><style>@page{{size:{w} {h};margin:0}}'
            'html,body{{margin:0;padding:0;background:#fff}}'
            '.slide{{position:relative;width:{w};height:{h};overflow:hidden;background:#{bg};font-family:{font};'
            'break-after:page;page-break-after:always}}.slide:last-child{{break-after:auto;page-break-after:auto}}'
            '.el{{position:absolute;box-sizing:border-box;margin:0}}p{{margin:0}}img.el{{object-fit:contain}}'
            'table.el{{border-collapse:collapse;table-layout:fixed}}td{{border-bottom:1px solid #d1d5db;padding:0 0.08in}}'
            '</style></head><body>{body}</body></html>').format(
        w=_emu_in(deck.W), h=_emu_in(deck.H), bg=deck.theme["background"], font=font,
        body="".join(_slide_html(deck, els) for _, els in slides))


def _browser():
    from .pdf import find_browser
    b = find_browser()
    if not b:
        raise DevTrackError("미리보기에 쓸 Chrome/Chromium 을 찾지 못했습니다 (DEVTRACK_CHROME=<브라우저 실행 파일>)")
    return b


def _run_chrome(args, out, timeout=180):
    try:
        subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise DevTrackError("미리보기 시간 초과 ({}초)".format(timeout))
    except OSError as e:
        raise DevTrackError("브라우저를 실행하지 못했습니다: {}".format(e))
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise DevTrackError("미리보기를 만들지 못했습니다")


def preview_pdf(deck, slides, pdf_path, timeout=180):
    """레이아웃을 HTML 로 그려 슬라이드 크기 그대로 PDF 로 인쇄한다 (PowerPoint 와 줄바꿈이 조금 다를 수 있음)."""
    from .pdf import _file_url
    browser = _browser()
    tmp = tempfile.mkdtemp(prefix="devtrack-pptx-pdf-")
    try:
        page = os.path.join(tmp, "deck.html")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(preview_html(deck, slides))
        out = os.path.join(tmp, "out.pdf")
        _run_chrome([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                     "--disable-extensions", "--allow-file-access-from-files", "--user-data-dir=" + os.path.join(tmp, "p"),
                     "--no-pdf-header-footer", "--print-to-pdf-no-header", "--virtual-time-budget=5000",
                     "--print-to-pdf=" + out, _file_url(page)], out, timeout)
        os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
        shutil.move(out, pdf_path)
        return pdf_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def preview_png(deck, slides, out_dir, max_px=1600, timeout=120):
    """슬라이드마다 PNG (긴 변이 max_px 이하가 되게 줄여서)."""
    from .pdf import _file_url
    browser = _browser()
    os.makedirs(out_dir, exist_ok=True)
    wpx, hpx = deck.W / float(IN) * 96, deck.H / float(IN) * 96
    k = min(1.0, max_px / max(wpx, hpx))
    W, H = int(round(wpx * k)), int(round(hpx * k))
    tmp = tempfile.mkdtemp(prefix="devtrack-pptx-png-")
    outs = []
    try:
        for n, (sl, els) in enumerate(slides, 1):
            page = os.path.join(tmp, "s{}.html".format(n))
            html = preview_html(deck, [(sl, els)]).replace("<body>", '<body style="zoom:{:.5f}">'.format(k), 1)
            with open(page, "w", encoding="utf-8") as fh:
                fh.write(html)
            out = os.path.join(out_dir, "slide-{:02d}.png".format(n))
            if os.path.exists(out):
                os.unlink(out)
            _run_chrome([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                         "--disable-extensions", "--allow-file-access-from-files", "--hide-scrollbars",
                         "--force-device-scale-factor=1", "--user-data-dir=" + os.path.join(tmp, "p"),
                         "--window-size={},{}".format(W, H), "--virtual-time-budget=3000", "--screenshot=" + out,
                         _file_url(page)], out, timeout)
            outs.append(out)
        return outs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
