import warnings
from langchain_core._api.deprecation import LangChainDeprecationWarning
warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)

import asyncio
import logging
import discord
from discord.ext import commands
import config

# 기본 로깅 설정
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("manta_bot")

# 서드파티 라이브러리의 불필요한 로그/경고 차단 (디버깅 화면 지저분함 방지)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

async def main():
    """
    Manta2 봇 실행을 위한 진입점 함수.
    인텐트를 설정하고, cogs/chat Cog를 로드한 뒤 봇을 실행합니다.
    """
    from infrastructure.database import init_all_dbs
    from tasks.calendar_sync_task import calendar_sync_task
    
    init_all_dbs()
    calendar_sync_task.start()
    
    intents = discord.Intents.default()
    intents.message_content = True  # 메시지 내용 읽기 권한 활성화

    bot = commands.Bot(command_prefix="!", intents=intents)

    # Cog 로드 프로세스 (예외 처리 포함)
    try:
        await bot.load_extension("cogs.chat")
        logger.info("Successfully loaded extension: cogs.chat")
    except Exception as e:
        logger.error(f"Failed to load extension cogs.chat: {e}", exc_info=True)
        return

    # 환경 변수 유효성 검사
    if not config.DISCORD_TOKEN:
        logger.error("DISCORD_BOT_TOKEN is not configured in .env file.")
        return

    # 봇 시작 (예외 처리 포함)
    try:
        async with bot:
            await bot.start(config.DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Error occurred while starting the bot: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot execution terminated by user.")
    except Exception as e:
        logger.error(f"Fatal error in main execution: {e}", exc_info=True)
