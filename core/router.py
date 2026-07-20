"""
core/router.py
요청 복잡도 기반 라우팅 (SPEC 2.1절)

Cog 에서 메시지를 받으면 바로 AgentExecutor 나 특정 그래프로 보내지 않고
반드시 이 Router 를 거친다.

분류 기준 (우선순위 순):
  1. 규칙 기반 키워드 매칭 — "일괄", "전부", "~별로 다르게" 등 → graph 후보
  2. 대상 도구가 LangGraph 전용 도구 키워드와 일치하면 → graph
  3. 애매하면 기본값 → agent (AgentExecutor)

분류 결과와 근거는 반드시 로그에 남긴다 (SPEC 2.1절 — 추적 가능성).
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from typing_extensions import TypedDict, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 타입 정의
# ---------------------------------------------------------------------------

class RouteDecision(TypedDict):
    """라우터 결정 결과."""
    path: Literal["agent", "graph"]
    graph_name: Optional[str]   # path == "graph" 일 때만 유효
    reason: str                 # 로그/추적용 근거 설명


# ---------------------------------------------------------------------------
# 규칙 기반 매칭 설정
# ---------------------------------------------------------------------------

# 0순위: 롤백/되돌리기 전용 패턴 (bulk_update보다 먼저 검사)
_ROLLBACK_KEYWORD_PATTERNS: list[re.Pattern] = [
    re.compile(r"(되돌려|되돌려줘|되돌려\s*주세요)"),
    re.compile(r"롤백"),
    re.compile(r"undo", re.IGNORECASE),
    re.compile(r"(취소해줘|취소\s*해줘|취소\s*주세요)"),  # "취소해줘" 단독 (일반 취소와 구분됨)
    re.compile(r"원래대로"),
]

# 1순위: 자연어 키워드 패턴 → graph 후보
# "일괄", "전부", "모두", "~별로", "조건부", "다르게", "바꿔줘" + 다건 암시
_GRAPH_KEYWORD_PATTERNS: list[re.Pattern] = [
    re.compile(r"일괄"),
    re.compile(r"전부\s*(바꿔|변경|수정|삭제)"),
    re.compile(r"모두\s*(바꿔|변경|수정|삭제)"),
    re.compile(r".+별로\s+다르"),          # "OP/CL별로 다르게" 패턴
    re.compile(r"조건부"),
    re.compile(r"(금|토|일|평일|주말).*(전체|모두|전부)"),
    re.compile(r"이번\s*(달|주).*(전부|모두|일괄)"),
    re.compile(r"(바꿔|변경|수정).*(\d+건|여러|다수)"),
]

# 2순위: LangGraph 전용 도구 키워드 (SPEC 4장 A절 테이블)
_GRAPH_TOOL_KEYWORDS: list[str] = [
    "bulk_update",
    "일괄_수정",
]

# graph_name 매핑 (키워드 → 그래프 모듈명)
_GRAPH_NAME_MAP: dict[str, str] = {
    "calendar_bulk": "calendar_bulk_update",
    "calendar_rollback": "calendar_rollback",
}


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------

def route(user_message: str, channel_id: str = "") -> RouteDecision:
    """
    사용자 메시지를 분석해 적절한 처리 경로를 반환한다.

    Args:
        user_message: 멘션 제거 후의 사용자 입력 원문.
        channel_id:   Discord 채널 ID (로깅용).

    Returns:
        RouteDecision: {"path": "agent"|"graph", "graph_name": str|None, "reason": str}
    """
    # 0순위: 롤백/되돌리기 키워드 (bulk_update보다 먼저 검사)
    for pattern in _ROLLBACK_KEYWORD_PATTERNS:
        if pattern.search(user_message):
            decision: RouteDecision = {
                "path": "graph",
                "graph_name": "calendar_rollback",
                "reason": f"롤백 키워드 패턴 매칭: '{pattern.pattern}'",
            }
            _log_decision(decision, user_message, channel_id)
            return decision

    # 1순위: 자연어 키워드 패턴 매칭
    for pattern in _GRAPH_KEYWORD_PATTERNS:
        if pattern.search(user_message):
            decision = {
                "path": "graph",
                "graph_name": "calendar_bulk_update",
                "reason": f"키워드 패턴 매칭: '{pattern.pattern}'",
            }
            _log_decision(decision, user_message, channel_id)
            return decision

    # 2순위: 도구 키워드 매칭
    lower_msg = user_message.lower()
    for keyword in _GRAPH_TOOL_KEYWORDS:
        if keyword in lower_msg:
            decision = {
                "path": "graph",
                "graph_name": "calendar_bulk_update",
                "reason": f"LangGraph 전용 도구 키워드 매칭: '{keyword}'",
            }
            _log_decision(decision, user_message, channel_id)
            return decision

    # 3순위: 기본값 → agent
    decision = {
        "path": "agent",
        "graph_name": None,
        "reason": "키워드 매칭 없음 → AgentExecutor 기본 경로",
    }
    _log_decision(decision, user_message, channel_id)
    return decision


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _log_decision(
    decision: RouteDecision,
    user_message: str,
    channel_id: str,
) -> None:
    """라우팅 결정을 로그에 남긴다 (SPEC 2.1절 — 추적 가능성)."""
    preview = user_message[:60] + "..." if len(user_message) > 60 else user_message
    logger.info(
        "[Router] channel=%s | path=%s | graph=%s | reason=%s | msg='%s'",
        channel_id or "-",
        decision["path"],
        decision.get("graph_name") or "-",
        decision["reason"],
        preview,
    )
