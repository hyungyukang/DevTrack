# -*- coding: utf-8 -*-
"""작업별 AI 모델 · 추론 강도(effort) 지정.

작업에 model(예: claude-opus-5-5) · effort(예: xhigh)를 적어 두면
- devtrack watch 가 그 작업을 실행할 때마다 그 모델 · 강도로 AI 를 띄우고,
- 이미 켜 둔 대화(대기 모드)는 자기 모델 · 강도와 맞지 않는 작업을 받지 않는다.

대화의 모델 · 강도는 Claude Code · Codex 가 남기는 세션 기록에서 읽는다. 읽지 못하면 '알 수 없음'으로 보고
막지 않는다 (작업 지정은 inbox 에 함께 보여 주므로 AI 가 스스로 확인한다).
"""

import glob
import json
import os
import re

from . import DevTrackError

# 웹 화면의 선택 목록 (목록에 없는 모델은 이름을 직접 적는다)
CLAUDE_MODELS = (
    ("claude-fable-5-2", "Fable 5.2"),
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-sonnet-5-5", "Sonnet 5.5"),
    ("claude-haiku-5-5", "Haiku 5.5"),
)
CLAUDE_FAMILIES = ("fable", "opus", "sonnet", "haiku")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")  # claude --effort
# Codex 모델 — 평소에는 Codex 가 받아 두는 ~/.codex/models_cache.json 에서 읽고 (codex_models), 읽지 못할 때만 이 목록을 쓴다
_CODEX_ALL = ("low", "medium", "high", "xhigh", "max", "ultra")
_CODEX_NO_ULTRA = ("low", "medium", "high", "xhigh", "max")
CODEX_MODELS = (
    ("gpt-6.1-sol", "GPT-6.1-Sol", _CODEX_ALL),
    ("gpt-6-astra", "GPT-6-Astra", _CODEX_ALL),
    ("gpt-6.1-terra", "GPT-6.1 Terra", _CODEX_ALL),
    ("gpt-6-sol", "GPT-6-Sol", _CODEX_ALL),
    ("gpt-6-luna", "GPT-6-Luna", _CODEX_NO_ULTRA),
    ("gpt-5.6-sol", "GPT-5.6-Sol", _CODEX_ALL),
    ("gpt-5.6-terra", "GPT-5.6-Terra", _CODEX_ALL),
    ("gpt-5.6-luna", "GPT-5.6-Luna", _CODEX_NO_ULTRA),
)
# Claude Code: low – max,  Codex: low – ultra (예전 Codex 모델은 minimal 도 받는다)
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
EFFORT_LABEL = {"minimal": "Minimal", "low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high",
                "max": "Max", "ultra": "Ultra"}
_EFFORT_ALIASES = {"extrahigh": "xhigh", "veryhigh": "xhigh", "maximum": "max", "med": "medium", "min": "minimal"}
_NONE = ("", "-", "none", "default", "any", "*")


def normalize_model(name):
    """모델 이름을 비교할 수 있는 모양으로: 'Opus 5.5' · 'opus-5.5' → claude-opus-5-5, 'opus' 는 그대로 (최신 Opus 별칭).
    Codex 목록에 있는 모델은 표시 이름(GPT-6.1 Terra)으로 적어도 모델 ID(gpt-6.1-terra)로 바꾼다.
    그 밖의 이름은 앞뒤 공백만 다듬어 그대로 둔다. 지정 안 함이면 None."""
    s = (name or "").strip()
    if s.lower() in _NONE:
        return None
    low = s.lower()
    m = re.match(r"^(?:claude[\s_-]*)?(fable|opus|sonnet|haiku)(?:[\s_-]*(\d+)(?:[\s._-]+(\d+))?)?(.*)$", low)
    if m and (m.group(2) or not m.group(4)):
        fam, major, minor, rest = m.groups()
        if not major:
            return fam
        return "claude-{}-{}{}{}".format(fam, major, "-" + minor if minor else "", rest)
    key = _squash(s)
    for mid, label, _ in codex_models()[0]:  # 'GPT-6.1 Terra' · 'GPT-6-Sol' → Codex 의 모델 ID
        if key in (_squash(mid), _squash(label)):
            return mid
    if re.search(r"\s", s):
        raise DevTrackError("모델 이름에 공백을 넣을 수 없습니다: {}".format(s))
    return s


def normalize_effort(value):
    s = re.sub(r"[\s_-]+", "", (value or "").strip().lower())
    if s in _NONE:
        return None
    s = _EFFORT_ALIASES.get(s, s)
    if s not in EFFORTS and s not in known_efforts():
        raise DevTrackError("추론 강도는 {} 중 하나여야 합니다: {}".format(", ".join(known_efforts()), value))
    return s


def _squash(value):
    return re.sub(r"[\s_-]+", "", (value or "").lower())


def known_efforts():
    """받을 수 있는 강도 이름: EFFORTS + Codex 목록에만 있는 새 이름."""
    extra = []
    for _, _, levels in codex_models()[0]:
        extra.extend(e for e in levels if e not in EFFORTS and e not in extra)
    return EFFORTS + tuple(extra)


def effort_label(effort):
    return EFFORT_LABEL.get(effort) or (effort or "").capitalize()


def _is_claude_model(model):
    m = (model or "").lower()
    return m in CLAUDE_FAMILIES or m.startswith("claude-")


def allowed_efforts(agent=None, model=None):
    """이 모델 · 담당 AI 가 받는 추론 강도 (web/js/util.js 의 effortsFor 와 같은 규칙).
    Claude 모델 · Claude 담당은 claude --effort 의 값, 목록에 있는 Codex 모델은 그 모델이 받는 값,
    모델을 모르는 Codex 담당은 Codex 목록의 값 + minimal, 그 밖에는 모든 값."""
    m = (model or "").lower()
    if m:
        if _is_claude_model(m):
            return CLAUDE_EFFORTS
        for mid, _, levels in codex_models()[0]:
            if m == mid.lower() and levels:
                return levels
    a = (agent or "").strip().lower()
    if a == "claude":
        return CLAUDE_EFFORTS
    if a == "codex":
        union = ["minimal"]
        for _, _, levels in codex_models()[0]:
            union.extend(e for e in levels if e not in union)
        return tuple(e for e in known_efforts() if e in union)
    return known_efforts()


def check_effort(agent, model, effort):
    """고른 강도를 그 모델 · 담당 AI 가 받지 않으면 오류 (실행기가 AI 를 띄우다 실패하지 않게)."""
    if not effort:
        return
    allowed = allowed_efforts(agent, model)
    if effort not in allowed:
        who = model_label(model) if model else (agent or "")
        raise DevTrackError("{} 은(는) 추론 강도 {} 를 받지 않습니다 — 고를 수 있는 강도: {}".format(
            who, effort_label(effort), ", ".join(allowed)))


def model_label(model):
    for mid, label in CLAUDE_MODELS:
        if model == mid:
            return label
    low = (model or "").lower()
    if low and not _is_claude_model(low):
        for mid, label, _ in codex_models()[0]:
            if low == mid.lower():
                return label
    return model or ""


def spec_label(model, effort):
    """'Opus 5.5 · Extra high' 처럼 사람이 읽는 표시 (둘 다 없으면 빈 문자열)."""
    parts = [model_label(model)] if model else []
    if effort:
        parts.append(effort_label(effort))
    return " · ".join(parts)


def task_spec(task):
    return (task.get("model") or None, task.get("effort") or None)


def model_matches(want, have):
    """지정한 모델(want)과 세션 모델(have)이 맞는지 — True / False, 세션 모델을 모르면 None.
    'claude-opus-5-5' 는 'claude-opus-5-5[1m]' · 날짜가 붙은 이름도 맞고, 별칭 'opus' 는 Opus 의 어느 버전이든 맞다."""
    if not want:
        return True
    if not have:
        return None
    try:
        want, have = normalize_model(want), normalize_model(have)
    except DevTrackError:
        return False
    if not have:
        return None
    want, have = want.lower(), have.lower()
    if want == have:
        return True
    if have.startswith(want) and have[len(want):len(want) + 1] in ("-", "["):
        return True
    if want in CLAUDE_FAMILIES:
        return re.match(r"^claude-{}(-|\[|$)".format(want), have) is not None
    return False


def spec_matches(task, have_model, have_effort):
    """작업의 지정과 이 세션이 맞는지: True(맞음 · 지정 없음) · False(다름) · None(지정은 있는데 세션 값을 모름)."""
    want_m, want_e = task_spec(task)
    results = []
    if want_m:
        results.append(model_matches(want_m, have_model))
    if want_e:
        try:
            have = normalize_effort(have_effort) if have_effort else None
        except DevTrackError:
            have = None
        results.append(None if not have else have == want_e)
    if False in results:
        return False
    if None in results:
        return None
    return True


# ---------------------------------------------------------------------------- 고를 수 있는 모델 목록
_codex_memo = {}


def codex_cache_path():
    base = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    return os.path.join(base, "models_cache.json")


def codex_models():
    """Codex 로 고를 수 있는 모델 ((ID, 표시 이름, (강도, …)), …) 과 출처 'cache' · 'builtin'.
    Codex 가 받아 두는 models_cache.json 에서 목록에 보이는 모델만 Codex 의 순서대로 읽는다 (파일이 바뀔 때만 다시 읽음).
    파일이 없거나 형식이 바뀌어 읽지 못하면 CODEX_MODELS."""
    path = codex_cache_path()
    try:
        st = os.stat(path)
    except OSError:
        return CODEX_MODELS, "builtin"
    key = (path, st.st_mtime, st.st_size)
    if _codex_memo.get("key") != key:
        found = []
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            items = [m for m in (data.get("models") or []) if isinstance(m, dict)]
            items.sort(key=lambda m: m.get("priority") if isinstance(m.get("priority"), int) else 9999)
            for m in items:
                slug = m.get("slug")
                if not isinstance(slug, str) or not _safe_id(slug) or m.get("visibility", "list") != "list":
                    continue
                levels = []
                for lv in m.get("supported_reasoning_levels") or []:
                    e = lv.get("effort") if isinstance(lv, dict) else None
                    if isinstance(e, str) and re.match(r"^[a-z]{1,16}$", e) and e not in levels:
                        levels.append(e)
                label = m.get("display_name")
                found.append((slug, label.strip() if isinstance(label, str) and label.strip() else slug, tuple(levels)))
        except (OSError, ValueError, AttributeError, TypeError):
            found = []
        _codex_memo.update(key=key, models=tuple(found))
    models = _codex_memo["models"]
    return (models, "cache") if models else (CODEX_MODELS, "builtin")


def catalog():
    """웹의 모델 선택 목록 (index.json 의 models)."""
    codex, source = codex_models()
    return {
        "claude": [{"id": mid, "label": label, "efforts": list(CLAUDE_EFFORTS)} for mid, label in CLAUDE_MODELS],
        "codex": [{"id": mid, "label": label, "efforts": list(levels)} for mid, label, levels in codex],
        "codex_source": source,
    }


# ---------------------------------------------------------------------------- 지금 세션의 모델 · 강도
def session_model():
    """이 명령을 실행한 AI 세션의 (모델, 강도). 모르는 값은 None.
    DEVTRACK_MODEL · DEVTRACK_EFFORT(devtrack watch 가 넣어 줌) > Claude Code · Codex 의 세션 기록 > CLAUDE_EFFORT."""
    model = (os.environ.get("DEVTRACK_MODEL") or "").strip() or None
    effort = (os.environ.get("DEVTRACK_EFFORT") or "").strip() or None
    if model and effort:
        return model, effort
    found = (None, None)
    try:
        if os.environ.get("CLAUDE_CODE_SESSION_ID"):
            found = claude_session_model(os.environ["CLAUDE_CODE_SESSION_ID"])
            if not found[1] and os.environ.get("CLAUDE_EFFORT"):
                found = (found[0], os.environ["CLAUDE_EFFORT"])
        elif os.environ.get("CODEX_THREAD_ID"):
            found = codex_session_model(os.environ["CODEX_THREAD_ID"])
    except Exception:  # 보조 기능 — 기록 형식이 바뀌어도 명령은 그대로 진행
        found = (None, None)
    return model or found[0], effort or found[1]


def _tail_lines(path, size=512 * 1024):
    """파일 끝부분의 줄을 뒤에서부터 (긴 대화 기록도 끝만 읽는다)."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        fh.seek(max(0, end - size))
        data = fh.read()
    lines = data.split(b"\n")
    if end > size:
        lines = lines[1:]  # 잘린 첫 줄
    return reversed(lines)


def _safe_id(value):
    return bool(value) and re.match(r"^[A-Za-z0-9._-]+$", value) is not None


def claude_transcript(session_id):
    """Claude Code 세션 기록 파일 (~/.claude/projects/<폴더>/<세션>.jsonl). 없으면 None."""
    if not _safe_id(session_id):
        return None
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    hits = glob.glob(os.path.join(base, "projects", "*", session_id + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def claude_session_model(session_id):
    path = claude_transcript(session_id)
    if not path:
        return None, None
    for raw in _tail_lines(path):
        if b'"assistant"' not in raw or b'"model"' not in raw:
            continue
        try:
            d = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "assistant":
            continue
        model = (d.get("message") or {}).get("model")
        if not model or model.startswith("<"):  # <synthetic> 등 실제 모델이 아닌 기록
            continue
        return model, d.get("effort")
    return None, None


def codex_transcript(thread_id):
    """Codex 대화 기록 파일 (~/.codex/sessions/<연>/<월>/<일>/rollout-…-<대화>.jsonl). 없으면 None."""
    if not _safe_id(thread_id):
        return None
    base = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    hits = glob.glob(os.path.join(base, "sessions", "*", "*", "*", "rollout-*{}.jsonl".format(thread_id)))
    return max(hits, key=os.path.getmtime) if hits else None


def codex_turn_context(thread_id, size=4 * 1024 * 1024):
    """Codex 대화의 마지막 차례 설정 (모델 · 강도 · 폴더 · 샌드박스). 없으면 {}."""
    path = codex_transcript(thread_id)
    if not path:
        return {}
    for raw in _tail_lines(path, size):
        if b'"turn_context"' not in raw:
            continue
        try:
            d = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("type") == "turn_context" and isinstance(d.get("payload"), dict):
            return d["payload"]
    return {}


def codex_session_model(thread_id):
    p = codex_turn_context(thread_id, 512 * 1024)
    return p.get("model"), p.get("effort")
