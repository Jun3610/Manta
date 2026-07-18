"""LLM tool call을 실제 로컬·외부 기능으로 디스패치한다."""
import asyncio
import json
import os
import re
import signal
import subprocess
from datetime import datetime, timedelta

import discord

import manta_daemon.state as state
from manta_daemon import voice
from manta_daemon.config import (
    MY_DISCORD_UID, MANTA_CHANNEL_ID, LMS_CHANNEL_ID,
    SYSTEM_CHANNEL_ID, SCHEDULE_CHANNEL_ID, HEALTH_CHANNEL_ID,
    WORK_STATION_ROOT, LMS_BASE, LMS_ID, LMS_PW,
    DISCORD_BOT_TOKEN, SITE_NAME_MAP,
    MAX_BUTTONS_PER_PAGE, MAX_WORKSPACE_BUTTONS, MAX_COURSE_BUTTONS,
)

# ── 유틸 ────────────────────────────────────────────────────────────────────
from manta_daemon.utils.helpers import (
    log_activity, send_long, send_as_file,
    _bring_discord_to_front, _offer_entertainment,
)
from manta_daemon.utils.errors import (
    report_error_to_discord, _check_openai_quota_error, _notify_openai_quota,
)

# ── UI Views ─────────────────────────────────────────────────────────────────
from manta_daemon.ui.views import (
    ConfirmView, RepoSelectView, WorkspaceSelectView,
    NotionDeleteView, LMSCourseSelectView,
    get_workspace_folders, delegate_write,
)

# ── GPT + Notion + LMS + Calendar + Weather ─────────────────────────────────
from manta_daemon.integrations.gpt import tools, analyze_and_save_profile
from manta_daemon.prompts import build_system_prompt
from manta_daemon.integrations.notion import (
    create_notion_page, read_notion_page, update_notion_page,
    list_notion_subpages, delete_notion_page, append_to_notion_page,
)
from manta_daemon.integrations.lms import (
    lms_get_courses, lms_get_homework, lms_get_all_homework,
)
from manta_daemon.integrations.calendar_ops import (
    cal_db_init, cal_db_full_sync,
    user_db_init, load_user_profile_summary,
    get_apple_calendar, add_apple_calendar_event,
    modify_apple_calendar_event, delete_apple_calendar_event,
    delete_all_calendar_events_on_date,
)
from manta_daemon.integrations.weather import get_weather
from manta_daemon.integrations.health import handle_health_message
from manta_daemon.integrations.gmail import (
    _init_gmail, _gmail_poll_loop,
    _gmail_fetch_inbox_sync, _gmail_sender_name,
)

# ── 파일 / Git / 시스템 / 웹 ─────────────────────────────────────────────────
from manta_daemon.tools.file_ops import (
    find_local_file_for_discord, read_local_file, read_pdf,
    analyze_and_suggest_code, write_local_file, list_folder_contents,
)
from manta_daemon.tools.git_ops import (
    _find_git_repos, get_git_status_by_path, get_git_status,
)
from manta_daemon.tools.system_ops import (
    open_mac_app, quit_mac_app, get_notion_app_context,
    read_mac_mail, get_system_status, run_terminal_command, run_python_code,
)
from manta_daemon.tools.web_ops import web_confirm_gate, scrap_lms_website

# ── 태스크 ────────────────────────────────────────────────────────────────────
from manta_daemon.tasks.system_status import _system_embed_task
from manta_daemon.tasks.schedule import _status_topic_task
from manta_daemon.tasks.daily_report import _start_daily_report_when_ready, get_daily_briefing
from manta_daemon.tasks.timers import (
    _timer_task, _pomodoro_task,
    list_background_tasks, cancel_background_task,
)

# ── 커맨드 ────────────────────────────────────────────────────────────────────
from manta_daemon.commands.restart import _cmd_restart, _cmd_shutdown
from manta_daemon.commands.vacation import _cmd_vacation, _cmd_vacation_end
from manta_daemon.commands.bridge import _handle_claude_bridge_oneshot



# ── on_message ───────────────────────────────────────────────────────────────


async def execute_tool_calls(message, tool_calls, user_cmd: str) -> None:
    # ── Tool 실행 ──
    _LONG_TOOLS = {"scrap_lms_website", "read_notion_page", "update_notion_page", "lms_get_all_homework"}
    called_tool_names = {tc.function.name for tc in tool_calls}
    if called_tool_names & _LONG_TOOLS:
        await _offer_entertainment(message.channel)

    tool_results_summary = []
    _executed_funcs = set()

    for tool_call in tool_calls:
        func_name = tool_call.function.name
        if func_name in _executed_funcs:
            continue
        _executed_funcs.add(func_name)
        try:
            func_args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            func_args = {}

        tool_result_str = ""

        if func_name == "open_mac_app":
            tool_result_str = open_mac_app(func_args.get("app_name", ""))
            await send_long(message.channel, tool_result_str)

        elif func_name == "quit_mac_app":
            tool_result_str = quit_mac_app(func_args.get("app_name", ""))
            await send_long(message.channel, tool_result_str)

        elif func_name == "get_notion_app_context":
            tool_result_str = get_notion_app_context(func_args.get("target_app_name", "Notion"))
            await send_long(message.channel, tool_result_str)

        elif func_name == "read_mac_mail":
            tool_result_str = read_mac_mail(func_args.get("count", 5))
            await send_long(message.channel, tool_result_str)

        elif func_name == "send_file_to_discord":
            res = find_local_file_for_discord(func_args.get("hint", ""))
            if "error" in res:
                await send_long(message.channel, res["error"])
                tool_result_str = res["error"]
            elif "candidates" in res:
                cands = res["candidates"]

                class _FilePickView(discord.ui.View):
                    def __init__(self_v):
                        super().__init__(timeout=60)
                        for i, c in enumerate(cands):
                            label = f"{c['name'][:40]} ({c['mtime_str']})"
                            btn = discord.ui.Button(
                                label=label, style=discord.ButtonStyle.secondary,
                                custom_id=str(i), row=i // 3
                            )

                            async def _cb(intr, idx=i, path=c["path"], name=c["name"], size=c["size_kb"]):
                                try:
                                    df = discord.File(path, filename=name)
                                    await intr.response.send_message(
                                        content=f"📎 **{name}** ({size}KB)",
                                        file=df
                                    )
                                except Exception as e2:
                                    await intr.response.send_message(f"❌ 전송 실패: {e2}")

                            btn.callback = _cb
                            self_v.add_item(btn)

                names = "\n".join(
                    f"- **{c['name']}** ({c['mtime_str']}, {c['size_kb']}KB)" for c in cands
                )
                await message.channel.send(
                    f"🔍 비슷한 파일이 {len(cands)}개 있어요. 어떤 걸 보낼까요?\n{names}",
                    view=_FilePickView()
                )
                tool_result_str = f"파일 후보 {len(cands)}개 표시"
            else:
                fpath    = res["path"]
                fname    = res["name"]
                size_kb  = res["size_kb"]
                try:
                    df = discord.File(fpath, filename=fname)
                    await message.channel.send(
                        content=f"📎 **{fname}** ({size_kb}KB) — 찾았어요!",
                        file=df
                    )
                    tool_result_str = f"파일 전송 완료: {fname} ({fpath})"
                except Exception as e:
                    err = f"❌ 파일 전송 실패: {e}"
                    await send_long(message.channel, err)
                    tool_result_str = err

        elif func_name == "read_local_file":
            res = read_local_file(func_args.get("target_hint", ""))
            if isinstance(res, dict):
                name       = res["name"]
                content    = res["content"]
                line_count = len(content.splitlines())
                preview    = f"📂 **`{name}`** ({line_count}줄)\n\n"
                body       = res["numbered_content"]
                full_msg   = preview + f"```\n{body[:1500]}\n```"
                if len(body) > 1500:
                    full_msg += "\n*(파일이 길어서 앞부분만 표시했어요. 전체 내용은 파일로 첨부해요!)*"
                    await message.channel.send(full_msg[:1900])
                    await send_as_file(message.channel, body, f"{name}_full.txt")
                else:
                    await send_long(message.channel, full_msg)
                tool_result_str = f"파일 로드 완료: {name} ({line_count}줄)"
            else:
                await send_long(message.channel, res)
                tool_result_str = res

        elif func_name == "read_pdf":
            res = read_pdf(
                func_args.get("path_or_hint", ""),
                func_args.get("pages", ""),
            )
            if "error" in res:
                tool_result_str = res["error"]
                await send_long(message.channel, tool_result_str)
            else:
                name       = res["name"]
                total      = res["total_pages"]
                loaded     = res["loaded_pages"]
                content    = res["content"]
                page_label = (
                    f"{loaded[0]}~{loaded[-1]}p" if len(loaded) > 1
                    else f"{loaded[0]}p"
                )
                preview = content[:1200]
                header  = f"📖 **`{name}`** ({page_label} / 전체 {total}p)\n\n"
                if len(content) > 1200:
                    await message.channel.send(header + f"```\n{preview}\n```\n*(앞부분만 표시, 전체 파일 첨부)*")
                    await send_as_file(message.channel, content, f"{name}_text.txt")
                else:
                    await send_long(message.channel, header + f"```\n{preview}\n```")
                tool_result_str = f"PDF 로드 완료: {name} ({page_label}, 전체 {total}p)"

        elif func_name == "analyze_and_suggest_code":
            res = analyze_and_suggest_code(
                func_args.get("target_hint", ""),
                func_args.get("question", "")
            )
            if isinstance(res, dict):
                analysis = res.get("analysis", "")
                name     = res.get("name", "")
                header   = f"📋 **`{name}` 분석**\n\n"
                await send_long(message.channel, header + analysis)
                tool_result_str = f"분석 완료: {name} | {analysis[:150]}"
            else:
                await send_long(message.channel, res)
                tool_result_str = res

        elif func_name == "scrap_lms_website":
            raw_url   = func_args.get("url") or f"{LMS_BASE}/ilos/main/main_form.acl"
            raw_lower = raw_url.strip().lower()
            target_url = SITE_NAME_MAP.get(raw_lower, raw_url)
            if not target_url.startswith("http"):
                target_url = "https://" + target_url
            tool_result_str = await web_confirm_gate(
                message.channel,
                "웹 페이지 스크래핑",
                target_url,
                lambda u=target_url: scrap_lms_website(u)
            )
            if tool_result_str == "__CONFIRM_PENDING__":
                tool_result_str = "사용자 컨펌 대기 중"
            elif tool_result_str and tool_result_str != "사용자 컨펌 대기 중":
                await send_long(message.channel, tool_result_str)

        elif func_name == "create_notion_page":
            tool_result_str = create_notion_page(
                func_args.get("title", ""),
                func_args.get("content", "")
            )
            await send_long(message.channel, tool_result_str)

        elif func_name == "read_notion_page":
            res = read_notion_page(func_args.get("page_id", ""))
            if isinstance(res, dict) and res.get("type") == "file":
                await message.channel.send(content=res["message"], file=res["file_object"])
                tool_result_str = f"노션 페이지 파일 전송 완료: {state.current_context.get('name', '')}"
            else:
                await send_long(message.channel, str(res))
                tool_result_str = str(res)[:500]

        elif func_name == "update_notion_page":
            tool_result_str = update_notion_page(
                func_args.get("page_id", ""),
                func_args.get("new_title"),
                func_args.get("new_content")
            )
            await send_long(message.channel, tool_result_str)

        elif func_name == "list_notion_subpages":
            pages, error = list_notion_subpages()
            if error and not pages:
                await send_long(message.channel, error)
                tool_result_str = error
            elif not pages:
                await message.channel.send("📭 하위 페이지가 없어요.")
                tool_result_str = "페이지 없음"
            else:
                total_pages_n = (len(pages) - 1) // MAX_BUTTONS_PER_PAGE + 1
                view = NotionDeleteView(pages)
                page_info = f" — 1/{total_pages_n}페이지" if total_pages_n > 1 else ""
                sent_msg = await message.channel.send(
                    content=f"📋 **노션 하위 페이지 목록** ({len(pages)}개){page_info}",
                    view=view
                )
                view.origin_message = sent_msg
                pages_summary = "\n".join([f"- {p['title']} (id: {p['id']})" for p in pages])
                tool_result_str = f"목록 조회 완료 ({len(pages)}개):\n{pages_summary}"

        elif func_name == "get_daily_briefing":
            _briefing_date = func_args.get("date_str", "")
            lms_url = f"{LMS_BASE}/ilos/mp/todo_list.acl"
            tool_result_str = await web_confirm_gate(
                message.channel, "브리핑 (캘린더+LMS)", lms_url,
                lambda: get_daily_briefing(_briefing_date)
            )
            if tool_result_str not in ("__CONFIRM_PENDING__", "__CANCELLED__"):
                await send_long(message.channel, tool_result_str)

        elif func_name == "get_system_status":
            tool_result_str = get_system_status()
            await send_long(message.channel, tool_result_str)

        elif func_name == "run_terminal_command":
            cmd       = func_args.get("command", "")
            cmd_lower = cmd.strip().lower()
            _needs_confirm  = False
            _confirm_label  = ""
            if re.search(r"\brm\b", cmd_lower):
                _needs_confirm = True
                _confirm_label = "🗑️ 파일/폴더 삭제"
            elif re.search(r"\bgit\s+(commit|push|merge|reset|rebase|branch\s+-[dD]|tag\s+-d)\b", cmd_lower):
                _needs_confirm = True
                _confirm_label = "🌿 git 쓰기 작업"
            if _needs_confirm:
                confirm_view = ConfirmView()
                await message.channel.send(
                    f"⚠️ **{_confirm_label} 컨펌 요청**\n```\n{cmd}\n```\n실행할까요?",
                    view=confirm_view
                )
                await confirm_view.wait()
                if confirm_view.confirmed:
                    tool_result_str = run_terminal_command(cmd)
                    await send_long(message.channel, f"💻 `{cmd}`\n{tool_result_str}")
                else:
                    tool_result_str = "사용자가 취소했어요."
                    await message.channel.send("❌ 취소됐어요.")
            else:
                tool_result_str = run_terminal_command(cmd)
                await send_long(message.channel, f"💻 `{cmd}`\n{tool_result_str}")

        elif func_name == "run_python_code":
            tool_result_str = run_python_code(func_args.get("code", ""))
            await send_long(message.channel, f"🐍 코드 실행 결과\n{tool_result_str}")

        elif func_name == "delegate_write":
            await delegate_write(message.channel, func_args.get("task_description", ""))
            tool_result_str = "LLM 선택 UI 표시됨 — 유저가 선택하면 실행됩니다."

        elif func_name == "write_local_file":
            tool_result_str = write_local_file(
                func_args.get("relative_path", ""),
                func_args.get("content", "")
            )
            await send_long(message.channel, tool_result_str)

        elif func_name == "get_git_status":
            folder_hint = func_args.get("folder_hint", "")
            if not folder_hint:
                repos = _find_git_repos(WORK_STATION_ROOT)
                if not repos:
                    tool_result_str = "❌ work-station 안에 git 저장소가 없어요."
                elif len(repos) == 1:
                    tool_result_str = get_git_status_by_path(repos[0])
                else:
                    view = RepoSelectView(repos)
                    sent = await message.channel.send("📂 어떤 레포 볼까요?", view=view)
                    await view._done.wait()
                    if view.selected_path:
                        tool_result_str = get_git_status_by_path(view.selected_path)
                        await sent.edit(
                            content=f"📁 `{os.path.basename(view.selected_path)}` 상태",
                            view=None
                        )
                    else:
                        tool_result_str = "취소됨"
            else:
                tool_result_str = get_git_status(folder_hint)
            await send_long(message.channel, tool_result_str)

        elif func_name == "get_apple_calendar":
            tool_result_str = get_apple_calendar(
                days=func_args.get("days", 1),
                start_date=func_args.get("start_date", ""),
                end_date=func_args.get("end_date", ""),
                keyword=func_args.get("keyword", ""),
            )
            await send_long(message.channel, tool_result_str)
            state.current_context["calendar_data"] = tool_result_str
            state.conversation_history.append({
                "role": "assistant",
                "content": f"[캘린더 조회 결과]\n{tool_result_str}"
            })

        elif func_name == "modify_apple_calendar_event":
            tool_result_str = modify_apple_calendar_event(
                title_keyword=func_args.get("title_keyword", ""),
                new_title=func_args.get("new_title", ""),
                new_date=func_args.get("new_date", ""),
                new_time=func_args.get("new_time", ""),
                new_duration_min=func_args.get("new_duration_min", 0),
                search_date=func_args.get("search_date", ""),
            )
            await send_long(message.channel, tool_result_str)

        elif func_name == "delete_all_calendar_events_on_date":
            ds = func_args.get("date_str", "")
            from datetime import date as _date
            label = ds if ds else str(_date.today())
            confirm_view = ConfirmView()
            await message.channel.send(
                f"🗑️ **{label} 일정 전체 삭제 확인**\n정말 다 지울까요?",
                view=confirm_view
            )
            await confirm_view.wait()
            if confirm_view.confirmed:
                tool_result_str = await asyncio.get_running_loop().run_in_executor(
                    None, delete_all_calendar_events_on_date, ds
                )
            else:
                tool_result_str = "❌ 삭제 취소했어요."
            await send_long(message.channel, tool_result_str)

        elif func_name == "delete_apple_calendar_event":
            kw = func_args.get("title_keyword", "")
            ds = func_args.get("date_str", "")
            confirm_view = ConfirmView()
            confirm_msg = (
                f"🗑️ **캘린더 일정 삭제 확인**\n키워드: `{kw}`"
                + (f"\n날짜: `{ds}`" if ds else "")
                + "\n삭제할까요?"
            )
            await message.channel.send(confirm_msg, view=confirm_view)
            await confirm_view.wait()
            if confirm_view.confirmed:
                tool_result_str = delete_apple_calendar_event(kw, ds)
            else:
                tool_result_str = "❌ 삭제 취소했어요."
            await send_long(message.channel, tool_result_str)

        elif func_name == "add_apple_calendar_event":
            tool_result_str = add_apple_calendar_event(
                title=func_args.get("title", ""),
                date_str=func_args.get("date_str", ""),
                end_date=func_args.get("end_date", ""),
                time_str=func_args.get("time_str", "09:00"),
                duration_min=func_args.get("duration_min", 60),
                calendar_name=func_args.get("calendar_name", ""),
                notes=func_args.get("notes", ""),
                important=func_args.get("important", False),
            )
            await send_long(message.channel, tool_result_str)

        elif func_name == "get_weather":
            tool_result_str = get_weather(func_args.get("city", "부산"))
            await send_long(message.channel, tool_result_str)

        elif func_name == "list_background_tasks":
            tool_result_str = list_background_tasks()
            await send_long(message.channel, tool_result_str)

        elif func_name == "cancel_background_task":
            tool_result_str = cancel_background_task(func_args.get("name", ""))
            await send_long(message.channel, tool_result_str)

        elif func_name == "get_gmail_inbox":
            if not state._gmail_service:
                tool_result_str = "❌ Gmail이 연결되지 않았어요. `credentials.json`을 프로젝트 루트에 넣고 재시작해주세요."
                await send_long(message.channel, tool_result_str)
            else:
                n = min(int(func_args.get("count", 10)), 20)
                loop = asyncio.get_running_loop()
                items = await loop.run_in_executor(None, lambda: _gmail_fetch_inbox_sync(n))
                if not items:
                    tool_result_str = "📭 받은 편지함이 비어있거나 조회에 실패했어요."
                else:
                    lines = []
                    for i, it in enumerate(items, 1):
                        unread_mark = "🔵 " if it["unread"] else "   "
                        sender_disp = _gmail_sender_name(it["from"])
                        subj = it["subject"][:40] + ("…" if len(it["subject"]) > 40 else "")
                        lines.append(f"{unread_mark}**{i}.** {sender_disp} — {subj}")
                    tool_result_str = f"📬 **받은 편지함 최근 {len(items)}개**\n" + "\n".join(lines)
                await send_long(message.channel, tool_result_str)

        elif func_name == "append_to_notion_page":
            tool_result_str = append_to_notion_page(
                func_args.get("page_id", ""),
                func_args.get("content", "")
            )
            await send_long(message.channel, tool_result_str)

        elif func_name == "list_folder_contents":
            tool_result_str = list_folder_contents(func_args.get("folder_hint", ""))
            await send_long(message.channel, tool_result_str)

        elif func_name == "lms_get_all_homework":
            lms_url = f"{LMS_BASE}/ilos/mp/todo_list.acl"
            tool_result_str = await web_confirm_gate(
                message.channel,
                "LMS 미완료 과제·강의 조회",
                lms_url,
                lambda: lms_get_all_homework()
            )
            if tool_result_str == "__CONFIRM_PENDING__":
                tool_result_str = "사용자 컨펌 대기 중"
            elif tool_result_str and tool_result_str != "사용자 컨펌 대기 중":
                await send_long(message.channel, tool_result_str)
                _bring_discord_to_front()

        elif func_name == "lms_get_course_homework":
            cname   = func_args.get("course_name", "")
            _courses, _ = lms_get_courses()
            _kjkey  = ""
            _ABBR   = {
                "컴개론": "컴퓨터과학개론", "경분": "경제성분석", "기경": "기술경영",
                "인공":   "인간공학",        "확분": "확률및분포", "경과": "경영과학"
            }
            cname_norm = _ABBR.get(cname, cname)
            if _courses:
                for c in _courses:
                    if cname_norm in c["name"] or c["name"] in cname_norm:
                        _kjkey = c["kjkey"]
                        cname  = c["name"]
                        break
                if not _kjkey:
                    for c in _courses:
                        if (any(ch in c["name"] for ch in cname_norm if len(ch) > 1) or
                                any(c["name"][i:i+2] in cname_norm for i in range(len(c["name"]) - 1))):
                            _kjkey = c["kjkey"]
                            cname  = c["name"]
                            break
            if not _kjkey:
                tool_result_str = f"❌ '{cname}' 과목을 찾지 못했어요. 정확한 과목명을 알려줘요."
                await send_long(message.channel, tool_result_str)
            else:
                lms_url    = f"{LMS_BASE}/ilos/st/course/submain_form.acl"
                _cname_cap = cname
                tool_result_str = await web_confirm_gate(
                    message.channel,
                    f"LMS {_cname_cap} 과제 전체 조회",
                    lms_url,
                    lambda kjk=_kjkey, cn=_cname_cap: lms_get_homework(kjk, cn)
                )
                if tool_result_str == "__CONFIRM_PENDING__":
                    tool_result_str = "사용자 컨펌 대기 중"
                elif tool_result_str and tool_result_str != "사용자 컨펌 대기 중":
                    await send_long(message.channel, tool_result_str)
                    _bring_discord_to_front()

        else:
            tool_result_str = f"❌ 정의되지 않은 도구: {func_name}"
            await send_long(message.channel, tool_result_str)

        tool_results_summary.append(f"[{func_name}]")

    # 오래 걸리는 작업 완료 → Discord 최전면 + presence 원래대로
    if called_tool_names & (_LONG_TOOLS | {"lms_get_all_homework"}):
        _bring_discord_to_front()
        await state.bot.change_presence(activity=discord.Game(name="🐟 만타 대기 중"))

    state.conversation_history.append({
        "role": "assistant",
        "content": f"도구 실행: {', '.join(tool_results_summary)}"
    })

    # 5턴마다 자동 저장
    if len(state.conversation_history) % 10 == 0:
        state._save_memory()

    # 백그라운드 프로파일 분석
    asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(
        None, analyze_and_save_profile, user_cmd,
        tool_results_summary[0] if tool_results_summary else ""
    ))
