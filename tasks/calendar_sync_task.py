import asyncio
import logging
from services.calendar_service import CalendarService

logger = logging.getLogger(__name__)

class CalendarSyncTask:
    """
    백그라운드에서 Mac 캘린더 데이터를 로컬 SQLite 캐시로 동기화하는 태스크.
    아이폰, Mac 등에서 직접 추가/변경된 일정을 Manta가 인지할 수 있도록 주기적으로 실행됩니다.
    """
    def __init__(self, interval_seconds: int = 3600):
        self.interval_seconds = interval_seconds
        self.service = CalendarService()
        self.is_running = False
        self.task = None

    async def _sync_loop(self):
        logger.info(f"Calendar Sync Task 시작됨 (주기: {self.interval_seconds}초)")
        while self.is_running:
            try:
                # 비동기 루프 내에서 블로킹 함수(osascript 등) 실행을 위해
                # asyncio.to_thread로 감싸서 실행합니다.
                logger.info("Calendar Sync Task: 동기화 시작...")
                await asyncio.to_thread(self.service.sync_all_from_provider)
            except Exception as e:
                logger.error(f"Calendar Sync Task 에러: {e}")
            
            await asyncio.sleep(self.interval_seconds)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.task = asyncio.create_task(self._sync_loop())

    def stop(self):
        if self.is_running:
            self.is_running = False
            if self.task:
                self.task.cancel()
            logger.info("Calendar Sync Task 종료됨")

# 싱글톤 인스턴스 (cog 등에서 start/stop 호출 용이)
calendar_sync_task = CalendarSyncTask()
