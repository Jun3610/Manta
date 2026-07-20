"""
core/graphs/calendar_rollback.py
마지막 일괄 수정을 원래 상태로 되돌리는 LangGraph 파이프라인.

5단계 구조:
  Load → Preview → Approval → Execute → Summary

- Load    : rollback_store에서 스냅샷을 읽어온다.
- Preview : 복원할 이벤트 목록을 사용자에게 보여준다.
- Approval: Discord 버튼으로 사용자 승인 대기 (데이터 수정 그래프 필수).
- Execute : 원본 시간으로 복원한다.
- Summary : 결과를 요약해 반환한다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

from core.graphs.nodes.approval import request_approval
from infrastructure.metrics import record_llm_call_async
from infrastructure.rollback_store import load_snapshot, clear_snapshot
from services.calendar_service import CalendarService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 그래프 상태 정의
# ---------------------------------------------------------------------------

class RollbackState(TypedDict, total=False):
    """롤백 파이프라인 공유 상태."""
    # 입력
    user_message: str
    channel_id: str
    discord_channel: Any

    # Load 노드 출력
    snapshot_events: list[dict]   # 복원할 이벤트(원본 상태)
    saved_at: str                 # 스냅샷 저장 시각
    load_error: Optional[str]

    # Preview 노드 출력
    preview_text: str

    # Approval 노드 출력
    approved: Optional[bool]

    # Execute 노드 출력
    succeeded: list[dict]
    failed: list[dict]

    # Summary 노드 출력
    final_response: str


# ---------------------------------------------------------------------------
# 노드 구현
# ---------------------------------------------------------------------------

async def load_node(state: RollbackState) -> RollbackState:
    """
    [Load] rollback_store에서 마지막 스냅샷을 로드한다 (LLM 호출 없음).
    """
    data = load_snapshot()
    if data is None:
        return {
            **state,
            "snapshot_events": [],
            "load_error": "되돌릴 이전 작업이 없습니다. 일괄 수정을 먼저 실행해주세요.",
        }

    events = data.get("events", [])
    saved_at = data.get("saved_at", "")
    logger.info("[Rollback.Load] 스냅샷 로드: %d건 (저장 시각: %s)", len(events), saved_at)
    return {**state, "snapshot_events": events, "saved_at": saved_at}


async def preview_node(state: RollbackState) -> RollbackState:
    """
    [Preview] 복원할 이벤트 목록을 사용자에게 표시 (LLM 호출 없음).
    """
    if state.get("load_error"):
        return {**state, "preview_text": f"❌ {state['load_error']}"}

    events = state.get("snapshot_events", [])
    saved_at = state.get("saved_at", "")

    if not events:
        return {
            **state,
            "preview_text": "ℹ️ 복원할 일정이 없습니다.",
            "approved": False,
        }

    lines = [
        f"↩️ 다음 {len(events)}건을 원래 상태로 복원합니다:",
        f"  (스냅샷 시각: {saved_at})",
        "",
    ]
    for ev in events[:20]:
        date_str = ev.get("date", "")
        title = ev.get("title", "")
        time_str = ev.get("time", "")
        dur = ev.get("duration_min", 0)
        # 복원 후 종료 시간 계산
        try:
            from datetime import datetime, timedelta
            start_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
            end_dt = start_dt + timedelta(minutes=int(dur))
            end_str = end_dt.strftime("%H:%M")
            time_info = f"{time_str}~{end_str}"
        except Exception:
            time_info = time_str

        lines.append(f"  - {date_str} {title} → {time_info}")

    if len(events) > 20:
        lines.append(f"  ... 외 {len(events) - 20}건")

    logger.info("[Rollback.Preview] 생성 완료 (%d건).", len(events))
    return {**state, "preview_text": "\n".join(lines)}


async def approval_node(state: RollbackState) -> RollbackState:
    """
    [Approval] Discord 버튼으로 사용자 승인 대기.

    - load 오류 또는 빈 결과 → 자동 거부.
    - discord_channel 없음 → 자동 승인 (CLI/테스트 환경).
    """
    if state.get("approved") is False:
        return state
    if state.get("load_error"):
        return {**state, "approved": False}

    channel = state.get("discord_channel")
    preview_text = state.get("preview_text", "")

    if channel is None:
        logger.warning("[Rollback.Approval] discord_channel 없음 → 자동 승인 (테스트 모드).")
        return {**state, "approved": True}

    approved = await request_approval(channel, preview_text)
    return {**state, "approved": approved}


async def execute_node(state: RollbackState) -> RollbackState:
    """
    [Execute] 원본 스냅샷 상태로 각 이벤트를 복원한다 (LLM 호출 없음).

    - 복원 성공 후 스냅샷 파일을 삭제 (중복 롤백 방지).
    """
    if not state.get("approved"):
        logger.info("[Rollback.Execute] 미승인 → 건너뜀.")
        return {**state, "succeeded": [], "failed": []}

    events = state.get("snapshot_events", [])
    service = CalendarService()
    succeeded: list[dict] = []
    failed: list[dict] = []

    for ev in events:
        uid = ev.get("uid", "")
        date_str = ev.get("date", "")
        time_str = ev.get("time", "")      # "HH:MM"
        dur = ev.get("duration_min", 0)

        if not uid or not date_str or not time_str:
            ev_copy = dict(ev)
            ev_copy["reason"] = "uid / date / time 정보 부족"
            failed.append(ev_copy)
            continue

        try:
            from datetime import datetime, timedelta
            start_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
            end_dt = start_dt + timedelta(minutes=int(dur))
            # CalendarService.modify_event로 원본 시간 복원
            ok = service.modify_event(uid, new_start_dt=start_dt, new_end_dt=end_dt)
            if ok:
                succeeded.append(ev)
                logger.debug(
                    "[Rollback.Execute] 성공: %s %s → %s~%s",
                    date_str, ev.get("title"), time_str,
                    end_dt.strftime("%H:%M"),
                )
            else:
                ev_copy = dict(ev)
                ev_copy["reason"] = "modify_event 반환값 False"
                failed.append(ev_copy)
                logger.warning(
                    "[Rollback.Execute] 실패: %s %s",
                    date_str, ev.get("title"),
                )
        except Exception as e:
            ev_copy = dict(ev)
            ev_copy["reason"] = str(e)
            failed.append(ev_copy)
            logger.error(
                "[Rollback.Execute] 예외: %s %s — %s",
                date_str, ev.get("title"), e,
            )

    logger.info("[Rollback.Execute] 완료. 성공 %d / 실패 %d", len(succeeded), len(failed))

    # 성공 항목이 있으면 스냅샷 삭제 (중복 롤백 방지)
    if succeeded:
        clear_snapshot()

    return {**state, "succeeded": succeeded, "failed": failed}


async def summary_node(state: RollbackState) -> RollbackState:
    """
    [Summary] 롤백 결과를 사람이 읽기 좋은 형태로 정리 (LLM 호출 1회, 실패 시 fallback).
    """
    if state.get("load_error"):
        return {**state, "final_response": f"❌ {state['load_error']}"}
    if not state.get("approved"):
        return {**state, "final_response": "↩️ 롤백이 취소되었습니다. 변경된 내용이 그대로 유지됩니다."}

    succeeded = state.get("succeeded", [])
    failed = state.get("failed", [])

    # LLM 요약 시도
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
        f"다음 롤백(되돌리기) 결과를 한국어로 친절하게 요약해줘 (2~3문장):\n"
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
        logger.error("[Rollback.Summary] LLM 호출 실패 → fallback: %s", e)
        await record_llm_call_async(
            role="summary", model=model_name,
            channel_id=state.get("channel_id", ""),
            latency_ms=latency_ms, status="error", error_type=str(type(e).__name__),
        )
        fallback = (
            f"↩️ 롤백 완료: 성공 {len(succeeded)}건 / 실패 {len(failed)}건\n"
        )
        if failed:
            fallback += "실패 목록:\n"
            for ev in failed:
                fallback += f"  - {ev.get('date')} {ev.get('title')} ({ev.get('reason', '')})\n"
        return {**state, "final_response": fallback}


# ---------------------------------------------------------------------------
# 라우팅 함수
# ---------------------------------------------------------------------------

def _after_load(state: RollbackState) -> str:
    if state.get("load_error"):
        return "summary"
    if not state.get("snapshot_events"):
        return "summary"
    return "preview"


def _should_execute(state: RollbackState) -> str:
    if state.get("approved"):
        return "execute"
    return "summary"


# ---------------------------------------------------------------------------
# 그래프 조립
# ---------------------------------------------------------------------------

def build_calendar_rollback_graph():
    """calendar_rollback LangGraph를 조립하고 컴파일된 그래프를 반환한다."""
    graph = StateGraph(RollbackState)

    graph.add_node("load", load_node)
    graph.add_node("preview", preview_node)
    graph.add_node("approval", approval_node)
    graph.add_node("execute", execute_node)
    graph.add_node("summary", summary_node)

    graph.set_entry_point("load")
    graph.add_conditional_edges("load", _after_load, {"preview": "preview", "summary": "summary"})
    graph.add_edge("preview", "approval")
    graph.add_conditional_edges("approval", _should_execute, {"execute": "execute", "summary": "summary"})
    graph.add_edge("execute", "summary")
    graph.add_edge("summary", END)

    return graph.compile()


_COMPILED_GRAPH = None


def get_graph():
    """컴파일된 롤백 그래프 싱글톤을 반환한다."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_calendar_rollback_graph()
        logger.info("[CalendarRollback] 그래프 컴파일 완료.")
    return _COMPILED_GRAPH


async def run(
    user_message: str,
    channel_id: str,
    discord_channel: Optional[Any] = None,
) -> str:
    """
    calendar_rollback 그래프를 실행하고 최종 응답 문자열을 반환한다.

    Args:
        user_message:    사용자 원문 메시지.
        channel_id:      Discord 채널 ID (metrics 기록용).
        discord_channel: discord.TextChannel (Approval 버튼 전송용).

    Returns:
        최종 응답 텍스트.
    """
    graph = get_graph()
    initial_state: RollbackState = {
        "user_message": user_message,
        "channel_id": channel_id,
        "discord_channel": discord_channel,
        "snapshot_events": [],
        "saved_at": "",
        "preview_text": "",
        "approved": None,
        "succeeded": [],
        "failed": [],
        "final_response": "",
    }

    try:
        result = await graph.ainvoke(initial_state)
        return result.get("final_response", "롤백 처리가 완료되었습니다.")
    except Exception as e:
        logger.error("[CalendarRollback] 그래프 실행 오류: %s", e, exc_info=True)
        return f"❌ 롤백 처리 중 오류가 발생했습니다: {e}"
