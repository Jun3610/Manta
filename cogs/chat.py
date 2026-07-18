"""
cogs/chat.py
Discord 메시지 이벤트 처리 Cog (SPEC 1장 규칙 1, 2.1절)

설계 원칙:
  - on_message 리스너는 이 Cog 단 하나. @bot.event 또는 다른 파일에서의
    중복 등록은 프로젝트 전체에서 금지 (SPEC 1장 규칙 1).
  - 비즈니스 로직 없음. Discord I/O 와 에이전트/그래프 사이의 어댑터 역할만.
  - 모든 메시지는 core.router.route() 를 거쳐 경로가 결정됨 (SPEC 2.1절).
"""
import logging
import discord
from discord.ext import commands

from core.agent import MantaAgent
from core.router import route

logger = logging.getLogger(__name__)


class ChatCog(commands.Cog):
    """
    Discord 메시지 이벤트를 처리하는 어댑터(Adapter) Cog.
    비즈니스 로직과 Discord 의존성을 철저히 분리하며,
    on_message 리스너를 단 하나만 등록한다.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.agent = MantaAgent()
        self.processed_message_ids: set[int] = set()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.bot.user, self.bot.user.id)
        logger.info("Manta Bot (ChatCog) is ready.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        logger.info(
            "[on_message] 수신: %s → %s (멘션됨: %s)",
            message.author, message.content, self.bot.user in message.mentions,
        )

        # 1. 봇 자신의 메시지 무시
        if message.author == self.bot.user:
            return

        # 2. 중복 메시지 ID 무시 (안전장치, SPEC 5.3절)
        if message.id in self.processed_message_ids:
            return
        self.processed_message_ids.add(message.id)

        # 비동기 예외 처리 (REVIEW_RULES.md 준수)
        try:
            is_mentioned = self.bot.user in message.mentions
            clean_content = message.content

            if is_mentioned:
                clean_content = clean_content.replace(f"<@{self.bot.user.id}>", "")
                clean_content = clean_content.replace(f"<@!{self.bot.user.id}>", "")
                clean_content = clean_content.strip()

            if not clean_content:
                if is_mentioned:
                    await message.channel.send("네, 말씀하세요!")
                return

            # 3. Router 로 경로 결정 (SPEC 2.1절)
            channel_id = str(message.channel.id)
            decision = route(clean_content, channel_id)

            async with message.channel.typing():
                if decision["path"] == "graph":
                    response = await self._handle_graph(
                        graph_name=decision["graph_name"],
                        user_message=clean_content,
                        channel_id=channel_id,
                        discord_channel=message.channel,
                    )
                else:
                    # AgentExecutor 경로 (단순 조회/단건 작업)
                    response = await self.agent.chat(channel_id, clean_content)

                # Discord 메시지 길이 제한 안전장치 (SPEC — Discord Output Safety)
                if len(response) > 1800:
                    response = response[:1800] + "\n...(생략)"

                await message.channel.send(response)

        except Exception as e:
            logger.error("[ChatCog] 메시지 처리 중 오류: %s", e, exc_info=True)
            try:
                await message.channel.send(
                    "죄송합니다. 메시지를 처리하는 도중 오류가 발생했습니다."
                )
            except Exception as reply_err:
                logger.error("[ChatCog] 에러 메시지 발송 실패: %s", reply_err, exc_info=True)

    async def _handle_graph(
        self,
        graph_name: str,
        user_message: str,
        channel_id: str,
        discord_channel: discord.TextChannel,
    ) -> str:
        """
        Router 가 graph 경로로 분류한 요청을 적절한 LangGraph 파이프라인으로 위임한다.

        현재 지원 그래프:
          - "calendar_bulk_update": 캘린더 일괄 수정 (SPEC 2.2절)

        신규 그래프 추가 시: 이 메서드에 elif 분기 추가만 하면 됨.
        """
        if graph_name == "calendar_bulk_update":
            from core.graphs.calendar_bulk_update import run as run_bulk_update
            return await run_bulk_update(
                user_message=user_message,
                channel_id=channel_id,
                discord_channel=discord_channel,
            )

        # 알 수 없는 그래프 → AgentExecutor 폴백
        logger.warning(
            "[ChatCog] 알 수 없는 graph_name '%s' → AgentExecutor 폴백.", graph_name
        )
        return await self.agent.chat(channel_id, user_message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))
