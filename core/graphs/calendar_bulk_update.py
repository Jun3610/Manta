"""
core/graphs/calendar_bulk_update.py
캘린더 일괄 수정 LangGraph 파이프라인 (SPEC 2.2절, 2.3절)

6단계 표준 뼈대:
  Parse → Filter → Preview → Approval → Execute → Summary

LLM 호출 횟수:
  - Parse 노드: 1회 (자연어 → 구조화 명령)
  - Summary 노드: 1회 (결과 요약)
  - Filter / Preview / Execute 는 순수 Python (LLM 호출 없음)

날짜/기간 원칙 (SPEC 2.4절):
  - LLM 은 상대 표현("이번 달")을 그대로 Parse 결과에 문자열로 남긴다.
  - 실제 날짜 변환은 Filter 노드에서 datetime.now() 기준으로 수행.
  - 이 구조로 "이번 달을 2024년으로 잘못 추론" 유형 오류가 구조적으로 재발 불가.

Approval (SPEC 2.3절):
  - 데이터를 수정하는 그래프이므로 Approval 노드 생략 불가.
  - 타임아웃/거부 시 Execute 건너뜀, "취소" 메시지로 Summary 대체.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, date, timedelta
from typing import Any, Optional

import discord
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from core.graphs.nodes.approval import request_approval
from infrastructure.metrics import record_llm_call_async
from services.fallback_formatter import format_bulk_execute_results, format_calendar_results
from services.calendar_service import CalendarService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 그래프 상태 정의
# ---------------------------------------------------------------------------

class BulkUpdateState(TypedDict, total=False):
    """
    LangGraph 파이프라인 전체에서 공유되는 상태.
    각 노드는 이 TypedDict 의 일부를 읽고 업데이트한다.
    """
    # 입력
    user_message: str           # 원본 사용자 메시지
    channel_id: str             # Discord 채널 ID
    discord_channel: Any        # discord.TextChannel (Approval 버튼 전송용)

    # Parse 노드 출력
    parsed_command: dict        # 구조화된 명령 {range, day_filter, rules, report_filter, ...}
    parse_error: Optional[str]  # Parse 실패 메시지

    # Filter 노드 출력
    target_events: list[dict]   # 수정 대상 이벤트 목록
    filter_error: Optional[str]

    # Preview 노드 출력
    preview_text: str           # 사용자에게 보여줄 미리보기 텍스트

    # Approval 노드 출력
    # None = 아직 결정 안 됨 (초기값), True = 승인, False = 거부/타임아웃
    approved: Optional[bool]

    # Execute 노드 출력
    succeeded: list[dict]       # 성공한 항목
    failed: list[dict]          # 실패한 항목

    # Summary 노드 출력 (최종 응답)
    final_response: str


# ---------------------------------------------------------------------------
# 노드 구현
# ---------------------------------------------------------------------------

async def parse_node(state: BulkUpdateState) -> BulkUpdateState:
    """
    [Parse] 자연어 → 구조화된 명령 (LLM 호출 1회).

    LLM 은 날짜를 직접 계산하지 않고 상대 표현을 그대로 반환한다.
    예: {"range": "이번달", "day_filter": "weekend",
         "rules": {"OP": ["06:00","15:00"], "CL": ["15:00","24:00"]},
         "report_filter": "weekday"}
    """
    from core.providers.anthropic_provider import get_provider
    provider = get_provider()
    llm = provider.get_chat_model(role="parse")
    model_name = llm.model

    system_msg = SystemMessage(content="""\
[CRITICAL LANGUAGE CONSTRAINT]
모든 응답은 무조건 한국어로만 해.

너는 사용자의 캘린더 수정 요청을 분석해 JSON으로 반환하는 파서야.

반환 형식 (JSON만 반환, 설명 없음):
{
  "range": "이번달" | "이번주" | "YYYY-MM-DD~YYYY-MM-DD" 등 원문 그대로,
  "day_filter": "weekend" | "weekday" | "friday" | "all" 등,
  "rules": {"태그명": ["시작시간", "종료시간"], ...} | null,
  "report_filter": "weekday" | "weekend" | null,
  "action": "modify_time" | "delete" | "report_only"
}

주의:
- 날짜 범위를 직접 계산하지 마라. "이번달", "다음주" 등 원문 그대로 반환.
- 연도를 추정하지 마라.
- 태그(OP, CL 등)가 있으면 rules 에 매핑해 반환.
""")
    human_msg = HumanMessage(content=state["user_message"])

    start_ms = int(time.monotonic() * 1000)
    try:
        response = await llm.ainvoke([system_msg, human_msg])
        latency_ms = int(time.monotonic() * 1000) - start_ms

        # JSON 파싱
        import json, re
        raw = response.content
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError(f"JSON 추출 실패. LLM 응답: {raw}")

        parsed = json.loads(json_match.group())
        logger.info("[Parse] 구조화 결과: %s", parsed)

        await record_llm_call_async(
            role="parse", model=model_name,
            channel_id=state.get("channel_id", ""),
            latency_ms=latency_ms, status="success",
        )
        return {**state, "parsed_command": parsed}

    except Exception as e:
        latency_ms = int(time.monotonic() * 1000) - start_ms
        logger.error("[Parse] 실패: %s", e, exc_info=True)
        await record_llm_call_async(
            role="parse", model=model_name,
            channel_id=state.get("channel_id", ""),
            latency_ms=latency_ms, status="error", error_type=str(type(e).__name__),
        )
        return {**state, "parse_error": f"요청 분석에 실패했습니다: {e}"}


async def filter_node(state: BulkUpdateState) -> BulkUpdateState:
    """
    [Filter] Service 레이어 순수 Python — 대상 이벤트 추출 (LLM 호출 없음).

    datetime.now() 기준으로 상대 날짜 범위를 절대 날짜로 환산 (SPEC 2.4절).
    """
    if state.get("parse_error"):
        return state

    cmd = state.get("parsed_command", {})

    try:
        service = CalendarService()
        # range 문자열을 실제 날짜 범위로 환산 (Python 처리 — LLM 추론 금지)
        start_date, end_date = _resolve_date_range(cmd.get("range", "이번달"))
        day_filter = cmd.get("day_filter", "all")

        events = await service.get_events_in_range(start_date, end_date)
        filtered = _apply_day_filter(events, day_filter)

        # rules에 명시된 일정 제목만 필터링
        rules = cmd.get("rules", {})
        if rules:
            rule_filtered = []
            for ev in filtered:
                title_nospace = ev.get("title", "").replace(" ", "").lower()
                for rule_key in rules.keys():
                    rule_key_nospace = rule_key.replace(" ", "").lower()
                    # rule_key가 title에 포함되거나 반대인 경우 매칭 (예: 'OP출근' in 'OP 출근')
                    if rule_key_nospace in title_nospace or title_nospace in rule_key_nospace:
                        ev["matched_rule_key"] = rule_key
                        rule_filtered.append(ev)
                        break
            filtered = rule_filtered

        logger.info(
            "[Filter] 날짜범위 %s~%s, 필터 '%s' → 대상 %d건",
            start_date, end_date, day_filter, len(filtered),
        )
        return {**state, "target_events": filtered}

    except Exception as e:
        logger.error("[Filter] 실패: %s", e, exc_info=True)
        return {**state, "filter_error": f"대상 이벤트 필터링 실패: {e}"}


async def preview_node(state: BulkUpdateState) -> BulkUpdateState:
    """
    [Preview] Filter 결과를 사용자가 확인할 수 있는 형태로 표시 (LLM 호출 없음).
    """
    if state.get("parse_error"):
        return {**state, "preview_text": f"❌ 분석 오류: {state['parse_error']}"}
    if state.get("filter_error"):
        return {**state, "preview_text": f"❌ 필터 오류: {state['filter_error']}"}

    events = state.get("target_events", [])
    cmd = state.get("parsed_command", {})

    if not events:
        preview = "ℹ️ 조건에 해당하는 일정이 없습니다. 변경할 내용이 없습니다."
        return {**state, "preview_text": preview, "approved": False}

    lines = [f"📋 다음 {len(events)}건이 변경됩니다:"]
    rules = cmd.get("rules", {})
    for ev in events[:20]:  # 최대 20건 미리보기
        tag = ev.get("tag", "")
        matched_key = ev.get("matched_rule_key", "")
        rule_info = ""
        if rules and matched_key in rules:
            times = rules[matched_key]
            rule_info = f" → {times[0]}~{times[1]}"
        lines.append(f"  - {ev.get('date', '')} {ev.get('title', '')} [{tag}]{rule_info}")

    if len(events) > 20:
        lines.append(f"  ... 외 {len(events) - 20}건")

    preview = "\n".join(lines)
    logger.info("[Preview] 생성 완료 (%d건).", len(events))
    return {**state, "preview_text": preview}


async def approval_node(state: BulkUpdateState) -> BulkUpdateState:
    """
    [Approval] Discord 버튼으로 사용자 승인 대기 (SPEC 2.3절).

    - Preview 오류/빈 결과인 경우 자동 거부.
    - discord_channel 이 없으면 (CLI 테스트 등) 자동 승인.
    """
    # preview_node가 명시적으로 approved=False를 설정한 경우만 건너뜀
    # (초기값 None은 통과시킴 — 초기값 False와 구분하기 위해 None 사용)
    if state.get("approved") is False:
        return state

    # parse/filter 오류 → 자동 거부
    if state.get("parse_error") or state.get("filter_error"):
        return {**state, "approved": False}

    channel = state.get("discord_channel")
    preview_text = state.get("preview_text", "")

    if channel is None:
        # CLI/테스트 환경: Discord 없이 자동 승인
        logger.warning("[Approval] discord_channel 없음 → 자동 승인 (테스트 모드).")
        return {**state, "approved": True}

    approved = await request_approval(channel, preview_text)
    return {**state, "approved": approved}


async def execute_node(state: BulkUpdateState) -> BulkUpdateState:
    """
    [Execute] Service 레이어 순수 Python — 실제 수정 수행 (LLM 호출 없음).

    승인된 경우에만 실행. 각 항목 성공/실패를 개별 기록 (부분 실패 허용).
    """
    if not state.get("approved"):
        logger.info("[Execute] 미승인 → 건너뜀.")
        return {**state, "succeeded": [], "failed": []}

    events = state.get("target_events", [])
    cmd = state.get("parsed_command", {})
    rules = cmd.get("rules", {})

    service = CalendarService()
    succeeded: list[dict] = []
    failed: list[dict] = []

    for ev in events:
        matched_key = ev.get("matched_rule_key", "")
        uid = ev.get("uid", ev.get("event_uid", ""))

        try:
            if rules and matched_key in rules:
                start_time_str, end_time_str = rules[matched_key]
                await service.update_event_time(uid, start_time_str, end_time_str)
            succeeded.append(ev)
            logger.debug("[Execute] 성공: %s %s", ev.get("date"), ev.get("title"))

        except Exception as e:
            ev_copy = dict(ev)
            ev_copy["reason"] = str(e)
            failed.append(ev_copy)
            logger.warning("[Execute] 실패: %s %s — %s", ev.get("date"), ev.get("title"), e)

    logger.info("[Execute] 완료. 성공 %d / 실패 %d", len(succeeded), len(failed))
    return {**state, "succeeded": succeeded, "failed": failed}


async def summary_node(state: BulkUpdateState) -> BulkUpdateState:
    """
    [Summary] 결과를 사람이 읽기 좋은 형태로 정리 (LLM 호출 1회, 실패 시 fallback).
    """
    # 오류 또는 미승인 케이스
    if state.get("parse_error"):
        return {**state, "final_response": f"❌ {state['parse_error']}"}
    if state.get("filter_error"):
        return {**state, "final_response": f"❌ {state['filter_error']}"}
    if not state.get("approved"):
        return {**state, "final_response": "취소되었습니다. 변경이 이루어지지 않았습니다."}

    succeeded = state.get("succeeded", [])
    failed = state.get("failed", [])

    # LLM 으로 자연스러운 요약 생성 시도
    from core.providers.anthropic_provider import get_provider
    provider = get_provider()
    llm = provider.get_chat_model(role="summary")
    model_name = llm.model

    succeeded_list = [f"{e.get('date')} {e.get('title')}" for e in succeeded[:5]]
    failed_list = [
        f"{e.get('date')} {e.get('title')} ({e.get('reason', '')})"
        for e in failed[:3]
    ]
    summary_prompt = (
        f"다음 일괄 수정 결과를 한국어로 친절하게 요약해줘 (2~4문장):\n"
        f"성공: {len(succeeded)}건, 실패: {len(failed)}건\n"
        f"성공 목록: {succeeded_list}\n"
        f"실패 목록: {failed_list}"
    )


    start_ms = int(time.monotonic() * 1000)
    try:
        response = await llm.ainvoke([HumanMessage(content=summary_prompt)])
        latency_ms = int(time.monotonic() * 1000) - start_ms

        await record_llm_call_async(
            role="summary", model=model_name,
            channel_id=state.get("channel_id", ""),
            latency_ms=latency_ms, status="success",
        )
        return {**state, "final_response": response.content}

    except Exception as e:
        latency_ms = int(time.monotonic() * 1000) - start_ms
        logger.error("[Summary] LLM 호출 실패 → fallback 사용: %s", e)
        await record_llm_call_async(
            role="summary", model=model_name,
            channel_id=state.get("channel_id", ""),
            latency_ms=latency_ms, status="error", error_type=str(type(e).__name__),
        )
        # Fallback (SPEC 2.5절)
        fallback = format_bulk_execute_results(succeeded, failed)
        return {**state, "final_response": fallback}


# ---------------------------------------------------------------------------
# 라우팅 함수 (조건부 엣지)
# ---------------------------------------------------------------------------

def _should_execute(state: BulkUpdateState) -> str:
    """Approval 결과에 따라 다음 노드를 결정."""
    if state.get("approved"):
        return "execute"
    return "summary"  # 미승인이면 Execute 건너뜀


def _after_parse(state: BulkUpdateState) -> str:
    """Parse 오류 시 Summary 로 바로 이동."""
    if state.get("parse_error"):
        return "summary"
    return "filter"


def _after_filter(state: BulkUpdateState) -> str:
    """Filter 오류 또는 결과 없음 시 Summary 로 바로 이동."""
    if state.get("filter_error"):
        return "summary"
    if not state.get("target_events"):
        return "summary"
    return "preview"


# ---------------------------------------------------------------------------
# 그래프 조립
# ---------------------------------------------------------------------------

def build_calendar_bulk_update_graph():
    """
    calendar_bulk_update LangGraph 를 조립하고 컴파일된 그래프를 반환한다.
    """
    graph = StateGraph(BulkUpdateState)

    # 노드 등록
    graph.add_node("parse", parse_node)
    graph.add_node("filter", filter_node)
    graph.add_node("preview", preview_node)
    graph.add_node("approval", approval_node)
    graph.add_node("execute", execute_node)
    graph.add_node("summary", summary_node)

    # 엣지 설정
    graph.set_entry_point("parse")
    graph.add_conditional_edges("parse", _after_parse, {"filter": "filter", "summary": "summary"})
    graph.add_conditional_edges("filter", _after_filter, {"preview": "preview", "summary": "summary"})
    graph.add_edge("preview", "approval")
    graph.add_conditional_edges("approval", _should_execute, {"execute": "execute", "summary": "summary"})
    graph.add_edge("execute", "summary")
    graph.add_edge("summary", END)

    return graph.compile()


# 모듈 수준 싱글톤 (매 요청마다 재조립 방지)
_COMPILED_GRAPH = None


def get_graph():
    """컴파일된 그래프 싱글톤을 반환한다."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_calendar_bulk_update_graph()
        logger.info("[CalendarBulkUpdate] 그래프 컴파일 완료.")
    return _COMPILED_GRAPH


async def run(
    user_message: str,
    channel_id: str,
    discord_channel: Optional[Any] = None,
) -> str:
    """
    calendar_bulk_update 그래프를 실행하고 최종 응답 문자열을 반환한다.

    Args:
        user_message:    사용자 원문 메시지.
        channel_id:      Discord 채널 ID (metrics 기록용).
        discord_channel: discord.TextChannel (Approval 버튼 전송용; None 이면 자동 승인).

    Returns:
        최종 응답 텍스트 (Discord 로 전송할 내용).
    """
    graph = get_graph()
    initial_state: BulkUpdateState = {
        "user_message": user_message,
        "channel_id": channel_id,
        "discord_channel": discord_channel,
        "parsed_command": {},
        "target_events": [],
        "preview_text": "",
        "approved": None,  # None = 아직 결정 전 (False와 구분)
        "succeeded": [],
        "failed": [],
        "final_response": "",
    }

    try:
        result = await graph.ainvoke(initial_state)
        return result.get("final_response", "처리가 완료되었습니다.")
    except Exception as e:
        logger.error("[CalendarBulkUpdate] 그래프 실행 오류: %s", e, exc_info=True)
        return f"❌ 일괄 수정 처리 중 오류가 발생했습니다: {e}"


# ---------------------------------------------------------------------------
# 내부 헬퍼 (순수 Python — LLM 호출 없음)
# ---------------------------------------------------------------------------

def _resolve_date_range(range_str: str) -> tuple[date, date]:
    """
    상대 날짜 표현을 datetime.now() 기준 절대 날짜 범위로 환산 (SPEC 2.4절).

    Args:
        range_str: "이번달", "이번주", "다음달", "YYYY-MM-DD~YYYY-MM-DD" 등.

    Returns:
        (start_date, end_date) 튜플.
    """
    today = datetime.now().date()

    if "이번달" in range_str or "이번 달" in range_str:
        start = today.replace(day=1)
        # 다음 달 첫날 - 1일
        if today.month == 12:
            end = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(today.year, today.month + 1, 1) - timedelta(days=1)
        return start, end

    if "다음달" in range_str or "다음 달" in range_str:
        if today.month == 12:
            start = date(today.year + 1, 1, 1)
            end = date(today.year + 1, 2, 1) - timedelta(days=1)
        else:
            start = date(today.year, today.month + 1, 1)
            if today.month + 1 == 12:
                end = date(today.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(today.year, today.month + 2, 1) - timedelta(days=1)
        return start, end

    if "이번주" in range_str or "이번 주" in range_str:
        start = today - timedelta(days=today.weekday())  # 월요일
        end = start + timedelta(days=6)  # 일요일
        return start, end

    if "다음주" in range_str or "다음 주" in range_str:
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        end = start + timedelta(days=6)
        return start, end

    # "N월" 또는 "N월 지금까지" 등 특정 월 표현 (예: "7월", "7월 동안")
    month_match = re.search(r"(\d{1,2})월", range_str)
    if month_match:
        month = int(month_match.group(1))
        year = today.year
        # 해당 월이 이미 지난 경우 다음 해로 처리 (예: 현재 8월인데 "7월" → 올해 7월 과거)
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)
        # "지금까지" 포함 시 오늘까지만
        if "지금까지" in range_str or "현재까지" in range_str:
            end = min(end, today)
        logger.info("[_resolve_date_range] '%s' → %s~%s", range_str, start, end)
        return start, end

    # YYYY-MM-DD~YYYY-MM-DD 형식 시도
    match = re.search(r"(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})", range_str)
    if match:
        return (
            date.fromisoformat(match.group(1)),
            date.fromisoformat(match.group(2)),
        )

    # 파싱 불가 → 이번 달 기본값
    logger.warning("[_resolve_date_range] 알 수 없는 range '%s' → 이번달 기본값 사용.", range_str)
    return _resolve_date_range("이번달")


def _apply_day_filter(events: list[dict], day_filter: str) -> list[dict]:
    """
    요일 필터를 적용해 대상 이벤트만 반환 (순수 Python).

    day_filter 값:
      "weekend"                    — 토/일
      "weekday"                    — 월~금
      "friday"                     — 금요일만
      "saturday"                   — 토요일만
      "sunday"                     — 일요일만
      "friday,saturday,sunday"     — 쉼표 구분 복합 지정 (LLM이 반환하는 형태)
      "금,토,일"                    — 한국어 단일 글자
      "all"                        — 필터 없음
    """
    if day_filter == "all" or not day_filter:
        return events

    _WEEKDAY_MAP = {
        # 영어
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
        # 한국어 단일 글자
        "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
    }

    # 쉼표/슬래시 구분 복합 지정 처리 (예: "friday,saturday,sunday" / "금,토,일")
    tokens = [t.strip().lower() for t in re.split(r"[,/]", day_filter)]

    # 특수 그룹 처리
    target_weekdays: set[int] = set()
    for token in tokens:
        if token in ("weekend", "주말"):
            target_weekdays |= {5, 6}
        elif token in ("weekday", "평일"):
            target_weekdays |= {0, 1, 2, 3, 4}
        elif token in _WEEKDAY_MAP:
            target_weekdays.add(_WEEKDAY_MAP[token])
        else:
            # 알 수 없는 토큰은 경고 후 무시
            logger.warning("[_apply_day_filter] 알 수 없는 day_filter 토큰: '%s'", token)

    if not target_weekdays:
        return events  # 파싱 실패 시 전체 반환

    filtered = []
    for ev in events:
        date_str = ev.get("date", "")
        try:
            ev_date = date.fromisoformat(date_str)
            if ev_date.weekday() in target_weekdays:
                filtered.append(ev)
        except (ValueError, TypeError):
            logger.warning("[_apply_day_filter] 날짜 파싱 실패: '%s', 건너뜀.", date_str)

    return filtered
