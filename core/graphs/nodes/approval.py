"""
core/graphs/nodes/approval.py
Discord 버튼 기반 승인 노드 — 공용 (SPEC 2.3절)

다건 수정/삭제/생성처럼 되돌리기 번거로운 작업은 Execute 전에
반드시 사용자 승인을 받는다. 이는 LLM 의 조건 해석이 미묘하게 틀렸을 때
사람이 잡아낼 수 있는 마지막 안전장치다.

구현 방식:
  - Preview 노드가 만든 요약과 함께 "✅ 실행" / "❌ 취소" 버튼을 Discord 채널에 전송.
  - asyncio.Event 로 버튼 클릭을 대기한다 (기본 타임아웃: 300초 = 5분).
  - 타임아웃/거부 시 Execute 를 건너뛰고 "취소되었습니다"로 Summary 대체.
  - 승인 대기 중인 세션은 _PENDING_APPROVALS 딕셔너리로 관리.

재사용:
  다른 LangGraph 그래프에서 Approval 노드가 필요하면 이 모듈의
  ApprovalState 와 run_approval_node() 를 그대로 가져다 쓴다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord

logger = logging.getLogger(__name__)

# 승인 대기 중인 세션 {message_id: asyncio.Event}
_PENDING_APPROVALS: dict[int, asyncio.Event] = {}
# 승인 결과 {message_id: bool}  True=승인, False=거부
_APPROVAL_RESULTS: dict[int, bool] = {}

APPROVAL_TIMEOUT_SEC = 300  # 5분


# ---------------------------------------------------------------------------
# Discord View (버튼 UI)
# ---------------------------------------------------------------------------

class ApprovalView(discord.ui.View):
    """✅ 실행 / ❌ 취소 버튼."""

    def __init__(self, message_id: int) -> None:
        super().__init__(timeout=APPROVAL_TIMEOUT_SEC)
        self._message_id = message_id

    @discord.ui.button(label="✅ 실행", style=discord.ButtonStyle.success, custom_id="approval_yes")
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        _APPROVAL_RESULTS[self._message_id] = True
        event = _PENDING_APPROVALS.get(self._message_id)
        if event:
            event.set()
        self.stop()
        await interaction.edit_original_response(content="✅ 실행을 승인했습니다. 처리 중...", view=None)

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.danger, custom_id="approval_no")
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        _APPROVAL_RESULTS[self._message_id] = False
        event = _PENDING_APPROVALS.get(self._message_id)
        if event:
            event.set()
        self.stop()
        await interaction.edit_original_response(content="❌ 취소되었습니다.", view=None)

    async def on_timeout(self) -> None:
        """타임아웃 시 거부로 처리."""
        _APPROVAL_RESULTS[self._message_id] = False
        event = _PENDING_APPROVALS.get(self._message_id)
        if event:
            event.set()


# ---------------------------------------------------------------------------
# 공개 인터페이스
# ---------------------------------------------------------------------------

async def request_approval(
    channel: discord.TextChannel,
    preview_text: str,
) -> bool:
    """
    Discord 채널에 Approval 버튼을 전송하고 사용자 응답을 기다린다.

    Args:
        channel:      버튼을 전송할 Discord 텍스트 채널.
        preview_text: Execute 전에 사용자에게 보여줄 Preview 요약 텍스트.

    Returns:
        True  → 사용자가 "✅ 실행" 클릭 (Execute 진행).
        False → "❌ 취소" 클릭 또는 타임아웃 (Execute 건너뜀).
    """
    event = asyncio.Event()

    try:
        view = ApprovalView(message_id=0)  # 임시 ID; 실제 message.id 로 교체
        message = await channel.send(
            content=(
                f"{preview_text}\n\n"
                f"⏳ 위 작업을 실행하시겠습니까? "
                f"({APPROVAL_TIMEOUT_SEC // 60}분 내에 응답하지 않으면 자동 취소)"
            ),
            view=view,
        )

        # message.id 로 View 와 Event 를 재연결
        view._message_id = message.id
        _PENDING_APPROVALS[message.id] = event

        # 버튼 클릭 또는 타임아웃까지 대기
        try:
            await asyncio.wait_for(event.wait(), timeout=APPROVAL_TIMEOUT_SEC + 5)
        except asyncio.TimeoutError:
            _APPROVAL_RESULTS[message.id] = False
            logger.warning("[Approval] 메시지 %d 승인 타임아웃.", message.id)

        approved = _APPROVAL_RESULTS.get(message.id, False)
        logger.info(
            "[Approval] 메시지 %d 결과: %s",
            message.id,
            "승인" if approved else "거부/타임아웃",
        )
        return approved

    except Exception as e:
        logger.error("[Approval] 승인 요청 중 오류: %s", e, exc_info=True)
        return False  # 오류 시 안전하게 거부 처리

    finally:
        # 사용한 임시 데이터 정리
        _PENDING_APPROVALS.pop(message.id if "message" in dir() else 0, None)
        _APPROVAL_RESULTS.pop(message.id if "message" in dir() else 0, None)
