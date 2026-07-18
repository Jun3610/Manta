"""Discord 연결 초기화, 백그라운드 작업 시작과 전역 오류 처리."""
import asyncio
import os
import signal

import discord

import manta_daemon.state as state
from manta_daemon.config import SYSTEM_CHANNEL_ID, SCHEDULE_CHANNEL_ID, WORK_STATION_ROOT
from manta_daemon.integrations.calendar_ops import cal_db_init, cal_db_full_sync, user_db_init
from manta_daemon.integrations.gmail import _init_gmail, _gmail_poll_loop
from manta_daemon.tasks.system_status import _system_embed_task
from manta_daemon.tasks.schedule import _status_topic_task
from manta_daemon.tasks.daily_report import _start_daily_report_when_ready
from manta_daemon.tasks.weekly_report import start_weekly_report
from manta_daemon.utils.errors import report_error_to_discord

# ── on_ready ─────────────────────────────────────────────────────────────────

@state.bot.event
async def on_ready():
    print("==========================================")
    print("Manta 가드 시스템 연동 완료.")
    print(f"작업 루트: {WORK_STATION_ROOT}")
    print(f"메모리: {len(state.conversation_history)}턴 로드됨")
    print("==========================================")

    # 종료 시그널 → 대화 자동 저장 후 종료
    loop = asyncio.get_running_loop()

    def _shutdown():
        state._save_memory()
        asyncio.create_task(state.bot.close())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    await state.bot.change_presence(activity=discord.Game(name="🐟 만타 대기 중"))

    # 로컬 캘린더 DB 초기화 + 풀싱크 (백그라운드, 재연결 시 중복 실행 방지)
    cal_db_init()
    if not state._cal_db_syncing:
        state._cal_db_syncing = True

        async def _run_cal_sync():
            try:
                await asyncio.get_running_loop().run_in_executor(None, cal_db_full_sync)
            finally:
                state._cal_db_syncing = False

        asyncio.ensure_future(_run_cal_sync())

    # 유저 프로파일 DB 초기화
    user_db_init()

    # system 채널 임베드 태스크 시작 (재연결 시 중복 방지)
    system_channel = state.bot.get_channel(SYSTEM_CHANNEL_ID)
    if system_channel:
        if state._system_embed_task_ref is None or state._system_embed_task_ref.done():
            state._system_embed_task_ref = asyncio.create_task(_system_embed_task(system_channel))

    # schedule 채널 토픽 갱신 태스크 시작 (재연결 시 중복 방지)
    schedule_channel = state.bot.get_channel(SCHEDULE_CHANNEL_ID)
    if schedule_channel:
        if state._status_topic_task_ref is None or state._status_topic_task_ref.done():
            state._status_topic_task_ref = asyncio.create_task(_status_topic_task(schedule_channel))

    # 매일 자정 자동 일정 보고 시작 (재연결 시 중복 방지)
    if state._daily_report_task_ref is None or state._daily_report_task_ref.done():
        asyncio.create_task(_start_daily_report_when_ready())

    if state._weekly_report_task_ref is None or state._weekly_report_task_ref.done():
        state._weekly_report_task_ref = start_weekly_report()

    # 재시작 완료 알림
    _restart_flag = "/tmp/.manta_restart_channel"
    if os.path.exists(_restart_flag):
        try:
            with open(_restart_flag) as _f:
                _ch_id = int(_f.read().strip())
            os.remove(_restart_flag)
            _restart_ch = state.bot.get_channel(_ch_id) or await state.bot.fetch_channel(_ch_id)
            if _restart_ch:
                await _restart_ch.send("✅ 만타 재시작 완료!")
        except Exception as e:
            print(f"[재시작 알림] 실패: {e}")

    # Gmail 초기화 + 폴링 시작
    if state._gmail_service is None:
        await asyncio.get_running_loop().run_in_executor(None, _init_gmail)
    if state._gmail_service and (state._gmail_task_ref is None or state._gmail_task_ref.done()):
        state._gmail_task_ref = asyncio.create_task(_gmail_poll_loop())

    # asyncio 태스크 예외 → Discord 에러 리포트
    def _asyncio_exception_handler(loop, context):
        exc = context.get("exception")
        if exc and not isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
            msg = context.get("message", "")
            asyncio.ensure_future(report_error_to_discord(exc, msg))

    loop.set_exception_handler(_asyncio_exception_handler)


# ── on_error ─────────────────────────────────────────────────────────────────

@state.bot.event
async def on_error(event: str, *args, **kwargs):
    import sys
    exc_type, exc_val, _ = sys.exc_info()
    if exc_val and not isinstance(exc_val, (asyncio.CancelledError, KeyboardInterrupt)):
        asyncio.ensure_future(report_error_to_discord(exc_val, f"event:{event}"))
