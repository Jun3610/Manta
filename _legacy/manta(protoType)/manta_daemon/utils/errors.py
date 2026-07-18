"""
utils/errors.py — report_error_to_discord, ErrorFixView, OpenAI 쿼터 에러
"""
import asyncio
import discord

from manta_daemon.config import MY_DISCORD_UID, SYSTEM_CHANNEL_ID
import manta_daemon.state as state


# ==================== [ OpenAI 쿼터 에러 ] ====================

async def _notify_openai_quota():
    """OpenAI 토큰 소진 시 system 채널에 알림"""
    if state._openai_quota_alerted:
        return
    state._openai_quota_alerted = True
    try:
        ch = state.bot.get_channel(SYSTEM_CHANNEL_ID)
        if ch:
            await ch.send(
                "⚠️ **OpenAI 토큰 소진!**\n"
                "API 사용량이 한도에 도달했어요. GPT 기능이 일시 중단됩니다.\n"
                "OpenAI 대시보드에서 한도를 확인하거나 충전해주세요."
            )
    except Exception:
        pass


def _check_openai_quota_error(e: Exception) -> bool:
    """OpenAI 쿼터/한도 오류인지 확인"""
    msg = str(e).lower()
    return any(k in msg for k in ["insufficient_quota", "quota", "rate limit", "429", "billing"])


# ==================== [ ErrorFixView ] ====================

class ErrorFixView(discord.ui.View):
    """에러 발생 시 직원에게 수정 맡길지 컨펌하는 버튼 UI"""

    def __init__(self, channel, task_description: str):
        super().__init__(timeout=300)
        self.channel = channel
        self.task_description = task_description

    @discord.ui.button(label="✏️ 직원한테 수정 맡기기", style=discord.ButtonStyle.danger)
    async def fix_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=interaction.message.content, view=self)
        # 순환 임포트 방지를 위해 로컬 임포트
        from manta_daemon.ui.views import delegate_write
        await delegate_write(self.channel, self.task_description)

    @discord.ui.button(label="무시", style=discord.ButtonStyle.secondary)
    async def ignore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=interaction.message.content + "\n~~(무시됨)~~", view=self
        )


# ==================== [ report_error_to_discord ] ====================

async def report_error_to_discord(error: Exception, context: str = ""):
    """에러 발생 시 system 채널에 수정 컨펌 요청"""
    import traceback
    tb = traceback.format_exc()
    err_key = f"{type(error).__name__}:{str(error)[:80]}"
    if err_key in state._last_reported_errors:
        return
    state._last_reported_errors.add(err_key)
    if len(state._last_reported_errors) > 50:
        state._last_reported_errors.clear()

    system_ch = state.bot.get_channel(SYSTEM_CHANNEL_ID)
    if not system_ch:
        return

    ctx_text = f"\n📍 발생 위치: `{context}`" if context else ""
    task_description = (
        f"manta_daemon.py에서 다음 오류가 발생했어. 코드를 분석해서 원인을 찾고 수정해줘.\n\n"
        f"오류 종류: {type(error).__name__}\n"
        f"오류 메시지: {str(error)}\n\n"
        f"트레이스백:\n{tb[-1500:]}"
        + (f"\n\n발생 위치 힌트: {context}" if context else "")
    )
    msg = (
        f"🚨 **봇 오류 발생**{ctx_text}\n"
        f"```\n{type(error).__name__}: {str(error)[:200]}\n```\n"
        f"직원한테 수정 맡길까요?"
    )
    view = ErrorFixView(system_ch, task_description)
    await system_ch.send(msg, view=view)
