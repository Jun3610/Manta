"""
services/fallback_formatter.py
LLM 호출 실패 시 규칙 기반 응답 생성 (SPEC 2.5절)

Summary 노드 등 최종 응답 생성 단계에서 LLM 호출이 실패한 경우
(429 Too Many Requests / timeout / 기타 API 오류),
원본 Tool/Execute 결과를 규칙 기반 텍스트로 정리해 사용자에게 반환한다.

빈 응답이나 에러 메시지만 보내지 않는다 (SPEC 2.5절 원칙).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_FOOTER = "\n(※ 자동 요약 생성에 실패해 원본 데이터를 표시합니다)"


# ---------------------------------------------------------------------------
# 범용 Tool 결과 포맷터
# ---------------------------------------------------------------------------

def format_tool_results(results: list[Any]) -> str:
    """
    AgentExecutor 의 Tool 호출 결과 목록을 사람이 읽기 좋은 텍스트로 변환한다.

    Args:
        results: Tool 실행 결과 목록. 각 항목은 str 또는 dict.

    Returns:
        Discord 전송 가능한 포맷 문자열.
    """
    if not results:
        return f"처리 결과가 없습니다.{_FOOTER}"

    lines: list[str] = ["도구 실행 결과:"]
    for idx, item in enumerate(results, start=1):
        if isinstance(item, dict):
            lines.append(f"\n[결과 {idx}]")
            for key, value in item.items():
                lines.append(f"  {key}: {value}")
        else:
            lines.append(f"\n[결과 {idx}] {item}")

    lines.append(_FOOTER)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 캘린더 특화 포맷터
# ---------------------------------------------------------------------------

def format_calendar_results(events: list[dict[str, Any]]) -> str:
    """
    캘린더 이벤트 목록을 포맷한다 (SPEC 2.5절 예시 출력 형태 준수).

    Args:
        events: 캘린더 이벤트 딕셔너리 목록.
                각 항목에 'title', 'date', 'time', 'uid' 등이 포함될 수 있음.

    Returns:
        Discord 전송 가능한 포맷 문자열.
    """
    if not events:
        return f"조회된 일정이 없습니다.{_FOOTER}"

    lines: list[str] = ["일정 조회 결과:"]

    # 태그/날짜 기준으로 그룹핑을 시도 (태그 키가 있을 경우)
    tagged: dict[str, list[dict]] = {}
    untagged: list[dict] = []

    for event in events:
        tag = event.get("tag") or event.get("calendar_name") or ""
        if tag:
            tagged.setdefault(tag, []).append(event)
        else:
            untagged.append(event)

    if tagged:
        for tag_name, tag_events in tagged.items():
            lines.append(f"\n{tag_name}:")
            for ev in tag_events:
                lines.append(_format_single_event(ev))
    else:
        lines.append("")
        for ev in untagged:
            lines.append(_format_single_event(ev))

    lines.append(_FOOTER)
    return "\n".join(lines)


def _format_single_event(event: dict[str, Any]) -> str:
    """단일 이벤트 딕셔너리를 한 줄 문자열로 변환."""
    date = event.get("date", "날짜 미상")
    title = event.get("title", "제목 없음")
    time_str = event.get("time", "")
    uid = event.get("uid", event.get("event_uid", ""))

    parts = [f"- {date}"]
    if time_str:
        parts.append(time_str)
    parts.append(title)
    if uid:
        parts.append(f"[{uid[:8]}...]")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# LangGraph Execute 결과 포맷터 (bulk 수정 결과)
# ---------------------------------------------------------------------------

def format_bulk_execute_results(
    succeeded: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> str:
    """
    LangGraph Execute 노드의 bulk 수정 결과를 포맷한다.

    Args:
        succeeded: 성공한 항목 목록.
        failed:    실패한 항목 목록.

    Returns:
        Discord 전송 가능한 포맷 문자열.
    """
    total = len(succeeded) + len(failed)
    lines: list[str] = [f"일괄 수정 결과: {total}건 중 {len(succeeded)}건 성공"]

    if succeeded:
        lines.append("\n✅ 성공:")
        for item in succeeded:
            lines.append(f"  - {item.get('date', '')} {item.get('title', '')}")

    if failed:
        lines.append("\n❌ 실패:")
        for item in failed:
            reason = item.get("reason", "알 수 없는 오류")
            lines.append(f"  - {item.get('date', '')} {item.get('title', '')} ({reason})")

    lines.append(_FOOTER)
    return "\n".join(lines)
