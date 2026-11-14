"""Word(.docx) → Markdown (Python 표준 라이브러리만).

문서 리뷰에서 Word 원본을 웹에 미리 보여 주고, pip 설치 없이 AI 가 글자를 읽게 하는 용도이다.
제목 · 문단 · 굵게/기울임 · 위/아래 첨자 · 목록 · 표 · 각주/미주를 옮기고, 그림 · 개체는 자리 표시만,
수식은 글자만 남긴다. 머리글/바닥글 · 메모 · 변경 내용 추적의 삭제분은 넣지 않는다 (삽입분은 넣는다).
"""

import re
import zipfile
import xml.etree.ElementTree as ET

from . import DevTrackError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"

MAX_XML = 64 * 1024 * 1024  # 압축을 푼 XML 한 파일의 상한 (zip 폭탄 방지)

# 문단 안에서 그대로 따라 들어가는 감싸기 요소 (하이퍼링크, 변경 추적 삽입, 콘텐츠 컨트롤 등)
_WRAPPERS = {W + "hyperlink", W + "ins", W + "smartTag", W + "customXml", W + "fldSimple", W + "sdtContent",
             W + "dir", W + "bdo", W + "moveTo"}
_SKIP = {W + "del", W + "moveFrom", W + "pPr", W + "rPr", W + "sdtPr", W + "sdtEndPr", W + "proofErr",
         W + "bookmarkStart", W + "bookmarkEnd", W + "commentRangeStart", W + "commentRangeEnd"}


def _read_xml(zf, name):
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML:
        raise DevTrackError("Word 문서의 {} 가 너무 큽니다 ({} 바이트)".format(name, info.file_size))
    data = zf.read(info)
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:  # Word 는 DTD 를 쓰지 않는다 — 엔티티 확장 공격 차단
        raise DevTrackError("Word 문서에 허용되지 않는 XML 선언이 있습니다: {}".format(name))
    return ET.fromstring(data)


def _val(el, tag):
    """<w:tag w:val="..."/> 의 값 (없으면 None)."""
    x = el.find(tag) if el is not None else None
    return None if x is None else x.get(W + "val")


def _on(rpr, tag):
    """굵게 · 기울임 같은 켜고 끄는 속성: 요소가 있고 val 이 0/false 가 아니면 켜짐."""
    x = rpr.find(tag) if rpr is not None else None
    return x is not None and x.get(W + "val", "true").lower() not in ("0", "false", "off", "none")


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~])")


def _esc(text):
    return _MD_SPECIAL.sub(r"\\\1", text)


class _Doc:
    def __init__(self, zf):
        self.zf = zf
        self.styles = {}       # styleId → (이름 소문자, 개요 수준 또는 None, 스타일의 numPr)
        self.numfmt = {}       # (numId, ilvl) → numFmt
        self.notes = []        # (번호, 종류, 본문) — 본문 순서대로
        self.note_ids = {}     # (종류, id) → 번호
        self.note_src = {"footnote": {}, "endnote": {}}
        self._load_styles()
        self._load_numbering()
        for kind in ("footnote", "endnote"):
            root = _read_xml(zf, "word/{}s.xml".format(kind))
            if root is None:
                continue
            for n in root.findall(W + kind):
                if n.get(W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                self.note_src[kind][n.get(W + "id")] = n

    # ------------------------------------------------------------ 스타일 · 번호 매기기
    def _load_styles(self):
        root = _read_xml(self.zf, "word/styles.xml")
        if root is None:
            return
        for st in root.findall(W + "style"):
            sid = st.get(W + "styleId")
            name = (_val(st, W + "name") or sid or "").lower()
            ppr = st.find(W + "pPr")
            lvl = _val(ppr, W + "outlineLvl")
            num = ppr.find(W + "numPr") if ppr is not None else None
            self.styles[sid] = (name, int(lvl) if lvl and lvl.isdigit() else None, num)

    def _load_numbering(self):
        root = _read_xml(self.zf, "word/numbering.xml")
        if root is None:
            return
        abstract = {}
        for a in root.findall(W + "abstractNum"):
            abstract[a.get(W + "abstractNumId")] = {
                lv.get(W + "ilvl"): _val(lv, W + "numFmt") or "decimal" for lv in a.findall(W + "lvl")}
        for n in root.findall(W + "num"):
            fmts = abstract.get(_val(n, W + "abstractNumId"), {})
            for ilvl, fmt in fmts.items():
                self.numfmt[(n.get(W + "numId"), ilvl)] = fmt

    def heading_level(self, ppr):
        """제목 수준: 0 = 문서 제목(Title), 1~9 = 제목 1~9, None = 본문."""
        sid = _val(ppr, W + "pStyle")
        name, lvl, _ = self.styles.get(sid, ((sid or "").lower(), None, None))
        own = _val(ppr, W + "outlineLvl")
        if name in ("title", "제목"):
            return 0
        m = re.match(r"^(?:heading|제목)\s*(\d)$", name)
        if m:
            return int(m.group(1))
        if own and own.isdigit() and int(own) < 9:
            return int(own) + 1
        if lvl is not None and lvl < 9:
            return lvl + 1
        return None

    def list_info(self, ppr):
        """목록 문단이면 (수준, 글머리표 여부), 아니면 None."""
        num = ppr.find(W + "numPr") if ppr is not None else None
        if num is None:
            sid = _val(ppr, W + "pStyle")
            num = self.styles.get(sid, (None, None, None))[2]
        if num is None:
            return None
        num_id = _val(num, W + "numId")
        if not num_id or num_id == "0":  # numId 0 = 번호 매기기 해제
            return None
        ilvl = _val(num, W + "ilvl") or "0"
        fmt = self.numfmt.get((num_id, ilvl), "decimal")
        return int(ilvl) if ilvl.isdigit() else 0, fmt in ("bullet", "none")

    # ------------------------------------------------------------ 글자 (run)
    def segments(self, el, out):
        """문단(또는 감싸기 요소) 안의 글자를 (텍스트, 서식) 조각으로 모은다. 서식: (굵게, 기울임, 위첨자, 아래첨자)."""
        for ch in el:
            tag = ch.tag
            if tag in _SKIP:
                continue
            if tag == W + "r":
                self._run(ch, out)
            elif tag in _WRAPPERS or tag == W + "sdt":
                self.segments(ch, out)
            elif tag in (M + "oMath", M + "oMathPara"):
                text = "".join(x.text or "" for x in ch.iter(M + "t"))
                if text.strip():
                    out.append((text, (False, True, False, False)))

    def _run(self, r, out):
        rpr = r.find(W + "rPr")
        va = _val(rpr, W + "vertAlign")
        style = (_on(rpr, W + "b"), _on(rpr, W + "i"), va == "superscript", va == "subscript")
        plain = (False, False, False, False)
        for ch in r:
            tag = ch.tag
            if tag == W + "t":
                out.append((ch.text or "", style))
            elif tag == W + "tab":
                out.append((" ", style))
            elif tag in (W + "br", W + "cr"):
                if ch.get(W + "type") != "page":
                    out.append(("\n", plain))
            elif tag == W + "noBreakHyphen":
                out.append(("-", style))
            elif tag in (W + "footnoteReference", W + "endnoteReference"):
                kind = "footnote" if tag == W + "footnoteReference" else "endnote"
                out.append(("\0note:{}:{}".format(kind, ch.get(W + "id")), plain))
            elif tag == W + "drawing":
                pr = next(ch.iter(WP + "docPr"), None)
                alt = ((pr.get("descr") or pr.get("title") or "") if pr is not None else "").strip()
                out.append(("\0fig:" + alt, plain))
            elif tag == W + "pict":
                out.append(("\0fig:", plain))
            elif tag == W + "object":
                out.append(("\0obj", plain))
            elif tag in (M + "oMath", M + "oMathPara"):
                out.append(("".join(x.text or "" for x in ch.iter(M + "t")), (False, True, False, False)))

    def note_mark(self, kind, nid):
        key = (kind, nid)
        if key not in self.note_ids:
            src = self.note_src[kind].get(nid)
            num = len(self.note_ids) + 1
            self.note_ids[key] = num
            body = ""
            if src is not None:
                body = " ".join(filter(None, (self.inline(p) for p in src.iter(W + "p"))))
            self.notes.append((num, kind, body))
        return self.note_ids[key]

    def inline(self, p, br="<br>"):
        """문단 하나 → Markdown 한 줄 (같은 서식의 이웃 조각은 합친다)."""
        segs = []
        self.segments(p, segs)
        parts = []
        cur_text, cur_style = [], None
        plain = (False, False, False, False)

        def flush():
            if cur_text:
                parts.append(_styled("".join(cur_text), cur_style))
                del cur_text[:]

        for text, style in segs:
            if text.startswith("\0"):
                flush()
                if text.startswith("\0note:"):
                    _, kind, nid = text.split(":", 2)
                    parts.append("<sup>[{}]</sup>".format(self.note_mark(kind, nid)))
                elif text.startswith("\0fig:"):
                    alt = text[5:]
                    parts.append("*[그림{}]*".format(": " + _esc(alt) if alt else ""))
                else:
                    parts.append("*[개체]*")
                cur_style = None
                continue
            if text == "\n" and style == plain:
                flush()
                parts.append(br)
                cur_style = None
                continue
            if style != cur_style:
                flush()
                cur_style = style
            cur_text.append(text)
        flush()
        return "".join(parts).strip()

    # ------------------------------------------------------------ 블록
    def table(self, tbl):
        rows = []
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                tcpr = tc.find(W + "tcPr")
                vmerge = tcpr.find(W + "vMerge") if tcpr is not None else None
                span = _val(tcpr, W + "gridSpan")
                if vmerge is not None and vmerge.get(W + "val", "continue") == "continue":
                    text = ""  # 위 칸과 합쳐진 칸
                else:
                    paras = [self.inline(p) for p in tc.iter(W + "p")]
                    text = "<br>".join(x for x in paras if x)
                cells.append(text.replace("\n", " "))
                cells.extend([""] * ((int(span) if span and span.isdigit() else 1) - 1))
            if cells:
                rows.append(cells)
        if not rows or not any(any(c for c in r) for r in rows):
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        line = lambda r: "| " + " | ".join(c or " " for c in r) + " |"  # noqa: E731
        return "\n".join([line(rows[0]), "|" + "---|" * width] + [line(r) for r in rows[1:]])

    def blocks(self, parent, out, state):
        for el in parent:
            tag = el.tag
            if tag == W + "p":
                self.paragraph(el, out, state)
            elif tag == W + "tbl":
                text = self.table(el)
                if text:
                    out.append(("block", text))
                state["list"] = -1
            elif tag in (W + "sdt", W + "sdtContent", W + "customXml"):
                inner = el.find(W + "sdtContent") if tag == W + "sdt" else el
                if inner is not None:
                    self.blocks(inner, out, state)

    def paragraph(self, p, out, state):
        ppr = p.find(W + "pPr")
        text = self.inline(p)
        if not text:
            return
        level = self.heading_level(ppr)
        if level is not None:
            state["list"] = -1
            out.append(("block", "#" * min(level + 1, 6) + " " + text.replace("<br>", " ")))
            return
        li = self.list_info(ppr)
        if li:
            depth = min(li[0], state["list"] + 1)  # 위 수준 없이 깊어지면 코드 블록이 되므로 한 단계씩만
            state["list"] = depth
            out.append(("item", "    " * depth + ("- " if li[1] else "1. ") + text))
            return
        state["list"] = -1
        if text.startswith(("#", "-", "+", "1.")) or re.match(r"^\d+[.)]\s", text):
            text = "\\" + text  # 문단 첫 글자가 Markdown 문법으로 읽히지 않게
        out.append(("block", text))


def _styled(text, style):
    if not text:
        return ""
    if text.isspace():
        return " "
    bold, italic, sup, sub = style or (False, False, False, False)
    body = _esc(text.strip())
    if sup:
        body = "<sup>{}</sup>".format(body)
    elif sub:
        body = "<sub>{}</sub>".format(body)
    if bold and italic:
        body = "***{}***".format(body)
    elif bold:
        body = "**{}**".format(body)
    elif italic:
        body = "*{}*".format(body)
    lead = " " if text[:1].isspace() else ""
    trail = " " if text[-1:].isspace() else ""
    return lead + body + trail


def docx_to_markdown(path):
    """Word 문서(.docx) 경로 → Markdown 문자열."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise DevTrackError("Word(.docx) 문서를 열 수 없습니다 ({}): {}".format(path, e))
    with zf:
        root = _read_xml(zf, "word/document.xml")
        if root is None:
            raise DevTrackError("Word 문서가 아닙니다 (word/document.xml 없음): {}".format(path))
        doc = _Doc(zf)
        body = root.find(W + "body")
        out = []
        doc.blocks(body if body is not None else root, out, {"list": -1})
    lines = []
    prev = None
    for kind, text in out:
        if lines and not (kind == "item" and prev == "item"):
            lines.append("")
        lines.append(text)
        prev = kind
    if doc.notes:
        lines += ["", "---", ""]
        for num, kind, body in doc.notes:
            lines.append("<sup>[{}]</sup> {}{}  ".format(num, "(미주) " if kind == "endnote" else "", body))
    return "\n".join(lines).strip() + "\n"
