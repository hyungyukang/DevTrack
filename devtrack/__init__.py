"""DevTrack — AI 에이전트 개발 진행상황 기록 · 확인 · 승인 도구.

Python 3.8+ 표준 라이브러리만 사용한다 (HPC 같은 공유 서버에서도 추가 설치 불필요).
"""

__version__ = "0.1.0"


class DevTrackError(Exception):
    """사용자에게 그대로 보여줄 수 있는 오류."""
