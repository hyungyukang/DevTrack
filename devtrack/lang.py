"""작업 언어 — 작업 지시에 쓴 언어(한국어 · 영어)를 알아내고, 영어 작업이면 DevTrack 이 자동으로 남기는 기록을 영어로 바꾼다.

AI 가 쓰는 기록 · 결과물은 AI 가 작업 언어로 쓰고 (inbox · start · show 가 알려 줌, docs/AGENT_PROTOCOL.md),
여기서는 DevTrack 자신의 정해진 기록 문구(상태 변경 · 결과물 추가 · 승인 요청 …)만 바꾼다.
store.py 의 기록 문구를 바꾸면 EN_LOG 도 함께 고칠 것 (맞는 형식이 없으면 그대로 남는다).
"""

import re

LANGS = {"ko": "한국어", "en": "영어"}
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힣]")


_EN_WORD = re.compile(r"^[(\"'“‘\[]*[A-Za-z]+(?:'[A-Za-z]+)?[)\"'”’\],.;:!?]*$")


def detect(text):
    """글의 언어 — 낱말로 센다: 한글이 든 낱말 vs 영어 낱말 (파일 이름 · 코드 · 숫자 · T-0005 같은 것은 세지 않음).
    한국어 낱말이 영어 낱말의 절반을 넘으면 'ko', 아니면 'en' (낱말이 없으면 'ko')."""
    ko = en = 0
    for tok in (text or "").split():
        if _HANGUL.search(tok):
            ko += 1
        elif _EN_WORD.match(tok):
            en += 1
    if not ko and not en:
        return "ko"
    return "ko" if ko * 2 > en else "en"


def task_lang(t):
    """작업 언어 (예전 작업은 제목 · 지시로 알아낸다)."""
    lg = (t or {}).get("lang")
    return lg if lg in LANGS else detect("{} {}".format((t or {}).get("title", ""), (t or {}).get("instruction", "")))


def norm(lang):
    v = str(lang or "").strip().lower()
    v = {"한국어": "ko", "korean": "ko", "kr": "ko", "영어": "en", "english": "en"}.get(v, v)
    return v if v in LANGS else None


# ---------------------------------------------------------------------------- 자동 기록 문구 → 영어
_STATUS = {"대기": "Queued", "진행 중": "In progress", "승인 대기": "Awaiting approval", "수정 요청": "Changes requested",
           "막힘": "Blocked", "완료": "Done", "취소": "Cancelled"}
_DECISION = {"승인": "Approved", "거절": "Rejected", "수정 요청": "Changes requested"}
_VERDICT = {"통과": "pass", "수정 필요": "changes needed", "게재 가능": "accept", "소폭 수정": "minor revision",
            "대폭 수정": "major revision", "게재 불가": "reject"}
_ART = {"결과물 추가": "Artifact added", "리뷰 리포트 등록": "Review report added", "검토할 문서 등록": "Document to review added",
        "시각화 결과물 추가": "Visualization added", "결과물 갱신": "Artifact updated", "리뷰 리포트 갱신": "Review report updated",
        "검토할 문서 갱신": "Document to review updated", "시각화 결과물 갱신": "Visualization updated"}
_SEV = {"반드시 수정": "blocker", "수정 권장": "major", "개선 제안": "minor", "사소함": "nit", "질문": "question"}


def _withdrawn(m):
    return " (approval requests {} withdrawn)".format(m) if m else ""


def _verdict(v):
    """'통과' → 'pass', '대폭 수정 (major revision)' → 'major revision'"""
    if v in _VERDICT:
        return _VERDICT[v]
    m = re.match(r"^[^(]*\(([A-Za-z ]+)\)$", v)
    return m.group(1) if m else v


def _st(s):
    return _STATUS.get(s, s)


EN_LOG = [
    (r"사용자가 새 작업을 지시했습니다", lambda g: "The user created this task"),
    (r"(.+) 이\(가\) (.+) 에게 맡긴 작업입니다", lambda g: "{} handed this task to {}".format(g[0], g[1])),
    (r"작업을 시작했습니다", lambda g: "Started the task"),
    (r"작업 계획 수립 \((\d+)단계\)", lambda g: "Plan set ({} steps)".format(g[0])),
    (r"계획 단계 추가 \((\d+)단계\)", lambda g: "Plan steps added ({} steps)".format(g[0])),
    (r"관련 작업 연결: (.*)", lambda g: "Linked related tasks: {}".format(g[0])),
    (r"관련 작업 연결 해제: (.*)", lambda g: "Unlinked related tasks: {}".format(g[0])),
    (r"관련 작업 (\S+) 이\(가\) 삭제되어 연결을 해제했습니다", lambda g: "Related task {} was deleted and unlinked".format(g[0])),
    (r"상태 변경: (.+) → (.+)", lambda g: "Status: {} → {}".format(_st(g[0]), _st(g[1]))),
    (r"모델 지정 해제 — 어떤 모델이든 처리", lambda g: "Model cleared — any model"),
    (r"모델 지정: (.*)", lambda g: "Model set: {}".format(g[0])),
    (r"(.+) 이\(가\) 작업을 맡았습니다", lambda g: "{} took the task".format(g[0])),
    (r"담당 AI 변경: (.+) → (.+)", lambda g: "Assigned AI: {} → {}".format(g[0], g[1])),
    (r"작업 취소(?:: (.*?))?(?: \(승인 요청 (.+) 철회\))?",
     lambda g: "Task cancelled" + (": " + g[0] if g[0] else "") + _withdrawn(g[1])),
    (r"(사용자가 완료로 처리|완료 처리)(?:: (.*?))?(?: \(승인 요청 (.+) 철회\))?",
     lambda g: ("Marked done by the user" if g[0].startswith("사용자") else "Marked done") + (": " + g[1] if g[1] else "")
     + _withdrawn(g[2])),
    (r"(사용자가 )?인계 메모를 (고쳤습니다|남겼습니다) \(([\d,]+)자\)",
     lambda g: "{} the handoff note ({} chars)".format(("The user " if g[0] else "") + ("updated" if g[1] == "고쳤습니다"
                                                                                       else "wrote"), g[2]).capitalize()),
    (r"인계 메모를 지웠습니다", lambda g: "Cleared the handoff note"),
    (r"보낸 파일 새 버전 \(v(\d+)\): (.*)", lambda g: "Sent file, new version (v{}): {}".format(g[0], g[1])),
    (r"보낸 파일: (.*)", lambda g: "Sent file: {}".format(g[0])),
    (r"(\d+)차 리뷰 요청 → (.*?) \((.*?)\) · 파일 (\d+)개 (.*)",
     lambda g: "Review round {} requested → {} ({}) · {} file{} {}".format(g[0], g[1], g[2], g[3], "" if g[3] == "1" else "s",
                                                                         g[4])),
    (r"(\d+)차 심사 요청 → (.*?) \((.*?)\) · 원고 (.*)",
     lambda g: "Journal review round {} requested → {} ({}) · manuscript {}".format(*g)),
    (r"(\d+)차 원고 그림 (\d+)개 \(새로 붙임 (\d+) · 바뀜 (\d+)\)",
     lambda g: "Round {} manuscript figures: {} (new {} · changed {})".format(*g)),
    (r"개발한 대화가 리뷰해도 됨 \(사용자 허락\)", lambda g: "The developing conversation may review (allowed by the user)"),
    (r"(\d+)차 (리뷰|심사) 결과: (.+) \(열린 항목 (\d+)개\) → (\S+)",
     lambda g: "Round {} {} result: {} ({} open) → {}".format(g[0], "journal review" if g[1] == "심사" else "review",
                                                                _verdict(g[2]), g[3], g[4])),
    (r"단계 완료 취소: (.*)", lambda g: "Step undone: {}".format(g[0])),
    (r"단계 완료: (.*)", lambda g: "Step done: {}".format(g[0])),
    (r"문제 발견: (.*)", lambda g: "Issue: {}".format(g[0])),
    (r"문제 해결: (.*)", lambda g: "Issue resolved: {}".format(g[0])),
    (r"(결과물 갱신|리뷰 리포트 갱신|검토할 문서 갱신|시각화 결과물 갱신) \(v(\d+)\): (.*)",
     lambda g: "{} (v{}): {}".format(_ART[g[0]], g[1], g[2])),
    (r"(결과물 추가|리뷰 리포트 등록|검토할 문서 등록|시각화 결과물 추가): (.*)", lambda g: "{}: {}".format(_ART[g[0]], g[1])),
    (r"LaTeX 컴파일 성공: (\S+) \((\S+)쪽(?:, 경고 (\d+))?\)",
     lambda g: "LaTeX build succeeded: {} ({} pages{})".format(g[0], g[1], ", {} warnings".format(g[2]) if g[2] else "")),
    (r"LaTeX 컴파일 실패: (.*)", lambda g: "LaTeX build failed: {}".format(g[0])),
    (r"검토 결과 기록: 항목 (\d+)개(?: — (.*?))?(?: \(상태 변경: (.*)\))?",
     lambda g: "Review findings recorded: {} items{}{}".format(
         g[0], " — " + re.sub("|".join(map(re.escape, _SEV)), lambda m: _SEV[m.group(0)], g[1]) if g[1] else "",
         " (status changes: {})".format(g[2]) if g[2] else "")),
    (r"승인 요청 철회: (.*)", lambda g: "Approval request withdrawn: {}".format(g[0])),
    (r"승인 요청: (.*)", lambda g: "Approval requested: {}".format(g[0])),
    (r"(승인|거절|수정 요청): (.*?)(?: \(선택: (.*)\))?",
     lambda g: "{}: {}{}".format(_DECISION[g[0]], g[1], " (selected: {})".format(g[2]) if g[2] else "")),
    (r"커밋 완료, 푸시 실패: (.*) \((\w+)\)", lambda g: "Committed, push failed: {} ({})".format(g[0], g[1])),
    (r"커밋( 및 푸시)? 완료: (.*) \((\w+)\)",
     lambda g: "Committed{}: {} ({})".format(" and pushed" if g[0] else "", g[1], g[2])),
]
_EN_LOG = [(re.compile("^" + p + "$", re.S), f) for p, f in EN_LOG]
_EN_DETAIL = [("- 정의되지 않은 인용: ", "- Undefined citations: "), ("- 정의되지 않은 참조: ", "- Undefined references: "),
              ("빌드 결과물 제외: ", "Build outputs left out: ")]


def en_log(message):
    """DevTrack 의 자동 기록 문구를 영어로 (맞는 형식이 없으면 그대로)."""
    if not message or not _HANGUL.search(message):
        return message
    for rx, f in _EN_LOG:
        m = rx.match(message)
        if m:
            return f(m.groups())
    return message


def en_detail(detail):
    if not detail or not _HANGUL.search(detail):
        return detail
    for ko, en in _EN_DETAIL:
        detail = detail.replace(ko, en)
    return re.sub(r"- Overfull box (\d+)개", r"- Overfull boxes: \1", detail)
