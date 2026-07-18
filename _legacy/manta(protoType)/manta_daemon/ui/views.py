"""
ui/views.py — 모든 discord.ui.View 클래스들 + delegate_write
"""
import os
import asyncio
import subprocess

import discord

from manta_daemon.config import (
    MY_DISCORD_UID, WORK_STATION_ROOT,
    MAX_BUTTONS_PER_PAGE, MAX_WORKSPACE_BUTTONS, MAX_COURSE_BUTTONS,
    ENTERTAINMENT_SERVICES,
    _CLAUDE_CLI_AVAILABLE, _DESKTOP_LLM_APPS, _DESKTOP_APP_EMOJI,
)
import manta_daemon.state as state
from manta_daemon.utils.helpers import send_long, _bring_discord_to_front, _open_entertainment_service


# ==================== [ 작업공간 헬퍼 ] ====================

def get_workspace_folders():
    """work-station 하위 폴더 목록 반환 (숨김/제외 폴더 제외)"""
    EXCLUDE = {'.git', 'node_modules', 'venv', '__pycache__', '.idea', 'build', 'dist', '.next', '.claude'}
    folders = []
    try:
        for entry in sorted(os.scandir(WORK_STATION_ROOT), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith('.') and entry.name not in EXCLUDE:
                folders.append({"name": entry.name, "path": entry.path})
    except Exception:
        pass
    return folders


# ==================== [ LLM 위임 헬퍼 ] ====================

def _desktop_emoji(app_name: str) -> str:
    return _DESKTOP_APP_EMOJI.get(app_name.lower(), "💻")


async def _run_desktop_llm(app_name: str, prompt: str, channel) -> None:
    """데스크탑 앱 직원: 클립보드에 프롬프트 복사 + 앱 열기 + 유저에게 안내"""
    import pyperclip
    try:
        pyperclip.copy(prompt)
        subprocess.run(["open", "-a", app_name], check=True)
    except Exception as e:
        await channel.send(f"❌ `{app_name}` 앱 열기 실패: {e}")
        return

    emoji = _desktop_emoji(app_name)
    await channel.send(
        f"{emoji} **{app_name}** 켰어요!\n\n"
        f"📋 프롬프트가 **클립보드에 복사**됐어요 — 앱에 붙여넣기(⌘V)하고 결과 받으면\n"
        f"여기에 결과를 붙여넣어줘요. 그러면 이어서 처리할게요!"
    )


def _build_worker_prompt(task_description: str) -> str:
    """팀장→직원 브리핑 포함 프롬프트 생성"""
    ctx_lines = []
    if state.current_workspace:
        ctx_lines.append(f"현재 작업 폴더: {state.current_workspace['path']}")
    if state.current_context.get("type") == "file":
        ctx_lines.append(f"현재 열린 파일: {state.current_context.get('name', '')}")
    ctx = "\n".join(ctx_lines)
    return (
        f"[팀장 지시 — 반드시 한국어로 답변]\n{task_description}\n"
        + (f"\n[작업 컨텍스트]\n{ctx}" if ctx else "")
        + "\n\n위 지시에 따라 최선을 다해 작업하고, 결과를 한국어로 반환하세요."
    )


# ==================== [ ConfirmView ] ====================

class ConfirmView(discord.ui.View):
    """범용 예/아니오 컨펌 UI"""
    def __init__(self, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.confirmed = False

    @discord.ui.button(label="✅ 실행", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except discord.InteractionResponded:
            pass
        self.stop()

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.confirmed = False
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except discord.InteractionResponded:
            pass
        self.stop()


# ==================== [ EntertainmentView ] ====================

class EntertainmentView(discord.ui.View):
    """오래 걸리는 작업 중 엔터테인먼트 제안 UI"""

    def __init__(self):
        super().__init__(timeout=120)
        for key, svc in ENTERTAINMENT_SERVICES.items():
            btn = discord.ui.Button(
                label=svc["label"],
                style=discord.ButtonStyle.secondary,
                custom_id=f"ent_{key}"
            )
            btn.callback = self._make_callback(key, svc["label"])
            self.add_item(btn)

        close_btn = discord.ui.Button(label="✖ 괜찮아", style=discord.ButtonStyle.danger)
        close_btn.callback = self._close_callback
        self.add_item(close_btn)

    def _make_callback(self, key: str, label: str):
        async def callback(interaction: discord.Interaction):
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=f"🎉 {label} 켜줄게요, 작업 끝나면 디코 알려드릴게요!", view=self
            )
            _open_entertainment_service(key)
        return callback

    async def _close_callback(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="👌 알겠어요! 작업 끝나면 알려드릴게요.", view=self
        )


# ==================== [ RepoSelectView ] ====================

class RepoSelectView(discord.ui.View):
    """git 레포 선택 UI"""

    def __init__(self, repos: list):
        super().__init__(timeout=60)
        self.selected_path = None
        self._done = asyncio.Event()

        options = []
        for path in repos[:25]:
            name = os.path.basename(path)
            try:
                branch = subprocess.check_output(
                    ["git", "branch", "--show-current"], cwd=path, timeout=3
                ).decode().strip()
            except Exception:
                branch = "?"
            options.append(discord.SelectOption(label=name, value=path, description=f"브랜치: {branch}"))

        select = discord.ui.Select(placeholder="레포 선택...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.selected_path = interaction.data["values"][0]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"📁 `{os.path.basename(self.selected_path)}` 선택됨 — 조회 중...", view=self
        )
        self._done.set()
        self.stop()


# ==================== [ LLMSelectView / delegate_write ] ====================

def _get_llm_callers():
    """integrations.gpt에서 _LLM_CALLERS 로컬 임포트 (순환 임포트 방지)"""
    from manta_daemon.integrations.gpt import _LLM_CALLERS
    return _LLM_CALLERS


class LLMSelectView(discord.ui.View):
    """작성 작업을 어떤 LLM에게 시킬지 선택하는 UI"""

    def __init__(self, task_description: str, channel):
        super().__init__(timeout=120)
        self.task_description = task_description
        self.channel = channel
        _LLM_CALLERS = _get_llm_callers()

        # ── API/CLI 직원 ──
        api_options = []
        if _CLAUDE_CLI_AVAILABLE:
            api_options.append(("claude_code", discord.ButtonStyle.blurple))
        api_options += [
            ("claude", discord.ButtonStyle.primary),
            ("gemini", discord.ButtonStyle.success),
            ("gpt",    discord.ButtonStyle.secondary),
        ]
        for key, style in api_options:
            label, _ = _LLM_CALLERS[key]
            btn = discord.ui.Button(label=label, style=style, custom_id=f"llm_{key}")
            btn.callback = self._make_api_callback(key)
            self.add_item(btn)

        # ── 데스크탑 앱 직원 ──
        for app_name in _DESKTOP_LLM_APPS:
            emoji = _desktop_emoji(app_name)
            btn = discord.ui.Button(
                label=f"{emoji} {app_name} (앱)",
                style=discord.ButtonStyle.secondary,
                custom_id=f"desktop_{app_name}"
            )
            btn.callback = self._make_desktop_callback(app_name)
            self.add_item(btn)

    def _make_api_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            _LLM_CALLERS = _get_llm_callers()
            for child in self.children:
                child.disabled = True
            label, caller = _LLM_CALLERS[key]
            await interaction.response.edit_message(
                content=f"📋 {label}에게 지시 전달 중...", view=self
            )
            worker_prompt = _build_worker_prompt(self.task_description)
            result = await asyncio.get_running_loop().run_in_executor(
                None, caller, worker_prompt
            )
            _bring_discord_to_front()
            await send_long(self.channel, f"📬 **{label} 보고**\n\n{result}")
        return callback

    def _make_desktop_callback(self, app_name: str):
        async def callback(interaction: discord.Interaction):
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=f"{_desktop_emoji(app_name)} **{app_name}** 앱 켜는 중...", view=self
            )
            worker_prompt = _build_worker_prompt(self.task_description)
            await _run_desktop_llm(app_name, worker_prompt, self.channel)
        return callback


async def delegate_write(channel, task_description: str):
    """사장→팀장→직원 위임 흐름. 직원 LLM 선택 UI 표시."""
    _LLM_CALLERS = _get_llm_callers()
    view = LLMSelectView(task_description, channel)
    _api_keys = (["claude_code"] if _CLAUDE_CLI_AVAILABLE else []) + ["claude", "gemini", "gpt"]
    worker_names = [_LLM_CALLERS[k][0] for k in _api_keys]
    _api_name_set = {n.lower().split()[0] for n in worker_names}
    worker_names += [f"{_desktop_emoji(a)} {a} (앱)" for a in _DESKTOP_LLM_APPS
                     if a.lower().split()[0] not in _api_name_set]
    await channel.send(
        f"👔 **팀장(만타) → 직원 배정**\n"
        f"배정된 직원: [{', '.join(worker_names)}]\n"
        f"```\n{task_description[:300]}\n```\n어떤 직원한테 맡길까요?",
        view=view
    )


# ==================== [ WorkspaceSelectView ] ====================

class WorkspaceSelectView(discord.ui.View):
    def __init__(self, folders, page_index=0):
        super().__init__(timeout=120)
        self.folders = folders
        self.page_index = page_index
        self.origin_message = None
        self._build_buttons()

    def _current_folders(self):
        start = self.page_index * MAX_WORKSPACE_BUTTONS
        return self.folders[start:start + MAX_WORKSPACE_BUTTONS]

    def _build_buttons(self):
        self.clear_items()
        for folder in self._current_folders():
            btn = discord.ui.Button(
                label=f"📁 {folder['name']}"[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"ws_{folder['name']}"
            )
            btn.callback = self._make_callback(folder)
            self.add_item(btn)

        total_pages = max(1, (len(self.folders) - 1) // MAX_WORKSPACE_BUTTONS + 1)
        if total_pages > 1:
            if self.page_index > 0:
                prev = discord.ui.Button(label="◀ 이전", style=discord.ButtonStyle.secondary, custom_id="ws_prev")
                prev.callback = self._prev_callback
                self.add_item(prev)
            if self.page_index < total_pages - 1:
                nxt = discord.ui.Button(label="다음 ▶", style=discord.ButtonStyle.secondary, custom_id="ws_next")
                nxt.callback = self._next_callback
                self.add_item(nxt)

        close = discord.ui.Button(label="✖ 닫기", style=discord.ButtonStyle.secondary, custom_id="ws_close")
        close.callback = self._close_callback
        self.add_item(close)

    def _make_callback(self, folder):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != MY_DISCORD_UID:
                await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
                return
            state.current_workspace = {"name": folder["name"], "path": folder["path"]}
            state.current_context = {}
            state.conversation_history = []
            self.stop()
            self.clear_items()
            await interaction.response.edit_message(
                content=f"🎯 **`{folder['name']}`** 작업공간 진입!\n"
                        f"경로: `{folder['path']}`\n"
                        f"이제 이 폴더에 집중해서 작업할게요. 나갈 땐 **나가자** 또는 **작업 종료** 라고 해줘요!",
                view=None
            )
            from manta_daemon.utils.helpers import log_activity
            log_activity("작업공간", f"진입: {folder['path']}")
        return callback

    async def _prev_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.page_index -= 1
        self._build_buttons()
        total_pages = max(1, (len(self.folders) - 1) // MAX_WORKSPACE_BUTTONS + 1)
        await interaction.response.edit_message(
            content=f"📂 **작업공간 선택** — {self.page_index+1}/{total_pages}페이지",
            view=self
        )

    async def _next_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.page_index += 1
        self._build_buttons()
        total_pages = max(1, (len(self.folders) - 1) // MAX_WORKSPACE_BUTTONS + 1)
        await interaction.response.edit_message(
            content=f"📂 **작업공간 선택** — {self.page_index+1}/{total_pages}페이지",
            view=self
        )

    async def _close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="✖ 닫았어요.", view=None)


# ==================== [ NotionDeleteConfirmView ] ====================

class NotionDeleteConfirmView(discord.ui.View):
    def __init__(self, page_id, page_title, origin_view):
        super().__init__(timeout=60)
        self.page_id = page_id
        self.page_title = page_title
        self.origin_view = origin_view

    @discord.ui.button(label="삭제 확인 ✅", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        from manta_daemon.integrations.notion import delete_notion_page
        result = delete_notion_page(self.page_id)
        self.clear_items()
        if result == "success":
            await interaction.response.edit_message(content=f"🗑️ `{self.page_title}` 삭제 완료!", view=self)
            self.origin_view.disable_page_button(self.page_id)
            try:
                await self.origin_view.origin_message.edit(view=self.origin_view)
            except Exception:
                pass
        else:
            await interaction.response.edit_message(content=f"❌ 삭제 실패: {result}", view=self)

    @discord.ui.button(label="취소 ↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="↩️ 취소했어요.", view=self)


# ==================== [ NotionDeleteView ] ====================

class NotionDeleteView(discord.ui.View):
    def __init__(self, pages, page_index=0):
        super().__init__(timeout=180)
        self.all_pages = pages
        self.page_index = page_index
        self.origin_message = None
        self._build_buttons()

    def _current_pages(self):
        start = self.page_index * MAX_BUTTONS_PER_PAGE
        return self.all_pages[start:start + MAX_BUTTONS_PER_PAGE]

    def _build_buttons(self):
        self.clear_items()
        for page in self._current_pages():
            label = page["title"][:75]
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                                    custom_id=f"notion_del_{page['id']}")
            btn.callback = self._make_callback(page)
            self.add_item(btn)

        total_pages = (len(self.all_pages) - 1) // MAX_BUTTONS_PER_PAGE + 1
        if total_pages > 1:
            if self.page_index > 0:
                prev_btn = discord.ui.Button(label="◀ 이전", style=discord.ButtonStyle.primary,
                                             custom_id="notion_prev")
                prev_btn.callback = self._prev_callback
                self.add_item(prev_btn)
            if self.page_index < total_pages - 1:
                next_btn = discord.ui.Button(label="다음 ▶", style=discord.ButtonStyle.primary,
                                             custom_id="notion_next")
                next_btn.callback = self._next_callback
                self.add_item(next_btn)

        all_btn = discord.ui.Button(label="전체 삭제 🗑️", style=discord.ButtonStyle.danger,
                                    custom_id="notion_del_all")
        all_btn.callback = self._delete_all_callback
        self.add_item(all_btn)

        close_btn = discord.ui.Button(label="닫기 ✖", style=discord.ButtonStyle.secondary,
                                      custom_id="notion_del_close")
        close_btn.callback = self._close_callback
        self.add_item(close_btn)

    def _make_callback(self, page):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != MY_DISCORD_UID:
                await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
                return
            confirm_view = NotionDeleteConfirmView(page["id"], page["title"], self)
            await interaction.response.send_message(
                content=f"⚠️ **`{page['title']}`** 삭제할까요?", view=confirm_view)
        return callback

    async def _prev_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.page_index -= 1
        self._build_buttons()
        total_pages = (len(self.all_pages) - 1) // MAX_BUTTONS_PER_PAGE + 1
        await interaction.response.edit_message(
            content=f"📋 **노션 하위 페이지 목록** ({len(self.all_pages)}개) — {self.page_index+1}/{total_pages}페이지",
            view=self)

    async def _next_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.page_index += 1
        self._build_buttons()
        total_pages = (len(self.all_pages) - 1) // MAX_BUTTONS_PER_PAGE + 1
        await interaction.response.edit_message(
            content=f"📋 **노션 하위 페이지 목록** ({len(self.all_pages)}개) — {self.page_index+1}/{total_pages}페이지",
            view=self)

    async def _delete_all_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        confirm_view = DeleteAllConfirmView(self.all_pages, self)
        await interaction.response.send_message(
            content=f"⚠️ **{len(self.all_pages)}개** 전부 삭제할까요?", view=confirm_view)

    async def _close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="✖ 닫았어요.", view=self)

    def disable_page_button(self, page_id):
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == f"notion_del_{page_id}":
                item.disabled = True
                item.label = item.label + " (삭제됨)"
                break


# ==================== [ DeleteAllConfirmView ] ====================

class DeleteAllConfirmView(discord.ui.View):
    def __init__(self, pages, origin_view):
        super().__init__(timeout=30)
        self.pages = pages
        self.origin_view = origin_view

    @discord.ui.button(label="전체 삭제 확인 ✅", style=discord.ButtonStyle.danger)
    async def confirm_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        from manta_daemon.integrations.notion import delete_notion_page
        results = []
        for page in self.pages:
            r = delete_notion_page(page["id"])
            results.append(f"✅ `{page['title']}`" if r == "success" else f"❌ `{page['title']}` 실패")
        for item in self.origin_view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            await self.origin_view.origin_message.edit(view=self.origin_view)
        except Exception:
            pass
        self.clear_items()
        result_text = "🗑️ **전체 삭제 완료**\n" + "\n".join(results)
        await interaction.response.edit_message(content=result_text[:1900], view=self)

    @discord.ui.button(label="취소 ↩️", style=discord.ButtonStyle.secondary)
    async def cancel_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="↩️ 취소했어요.", view=self)


# ==================== [ LMSCourseMenuView ] ====================

class LMSCourseMenuView(discord.ui.View):
    """강의 선택 후 뭘 볼지 선택하는 메뉴"""
    def __init__(self, course):
        super().__init__(timeout=180)
        self.course = course
        self._last_action = ""

    def _set_loading(self, label: str):
        for item in self.children:
            item.disabled = True
            if isinstance(item, discord.ui.Button) and item.label and label in item.label:
                item.label = f"🔄 {label.strip()} 로딩중..."
                item.style = discord.ButtonStyle.secondary

    def _reset_buttons(self, done_label: str):
        labels = {
            "📢 공지사항": discord.ButtonStyle.primary,
            "📝 과제": discord.ButtonStyle.primary,
            "📁 강의자료": discord.ButtonStyle.primary,
            "🏠 강의홈": discord.ButtonStyle.secondary,
            "✖ 닫기": discord.ButtonStyle.secondary,
        }
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = False
                for orig, style in labels.items():
                    if done_label in (item.label or ""):
                        item.label = f"✅ {done_label.strip()}"
                        item.style = discord.ButtonStyle.success
                    elif item.label and item.label in labels:
                        item.style = labels[item.label]

    async def _run(self, interaction, button, label, fetch_fn, msg_prefix):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self._set_loading(label)
        await interaction.response.edit_message(
            content=f"📚 **`{self.course['name']}`** — 🔄 {label} 불러오는 중...",
            view=self
        )
        from manta_daemon.config import LMS_BASE
        from manta_daemon.tools.web_ops import analyze_url_risk, _build_risk_report
        lms_url = f"{LMS_BASE}/ilos/st/course/course_home_form.acl?KJKEY={self.course['kjkey']}"
        analysis = analyze_url_risk(lms_url)
        report = _build_risk_report(label, lms_url, analysis)
        await send_long(interaction.channel, report)

        result = fetch_fn()
        self._reset_buttons(label)
        await interaction.edit_original_response(
            content=f"📚 **`{self.course['name']}`** — ✅ 마지막 조회: **{label}**",
            view=self
        )
        import io as _io
        if len(result) > 1800:
            buf = _io.BytesIO(result.encode("utf-8"))
            f = discord.File(fp=buf, filename=f"{label.strip()}.txt")
            await interaction.channel.send(content=f"{msg_prefix}\n\n*(내용이 길어 파일로 드려요)*", file=f)
        else:
            await send_long(interaction.channel, f"{msg_prefix}\n\n{result}")

    @discord.ui.button(label="📢 공지사항", style=discord.ButtonStyle.primary)
    async def notices(self, interaction: discord.Interaction, button: discord.ui.Button):
        from manta_daemon.integrations.lms import lms_get_notices
        await self._run(
            interaction, button, "📢 공지사항",
            lambda: lms_get_notices(self.course["kjkey"]),
            f"📢 **`{self.course['name']}` 공지사항**"
        )

    @discord.ui.button(label="📝 과제", style=discord.ButtonStyle.primary)
    async def homework(self, interaction: discord.Interaction, button: discord.ui.Button):
        from manta_daemon.integrations.lms import lms_get_homework
        await self._run(
            interaction, button, "📝 과제",
            lambda: lms_get_homework(self.course["kjkey"]),
            f"📝 **`{self.course['name']}` 과제 목록**"
        )

    @discord.ui.button(label="📁 강의자료", style=discord.ButtonStyle.primary)
    async def materials(self, interaction: discord.Interaction, button: discord.ui.Button):
        from manta_daemon.integrations.lms import lms_get_materials
        await self._run(
            interaction, button, "📁 강의자료",
            lambda: lms_get_materials(self.course["kjkey"]),
            f"📁 **`{self.course['name']}` 강의자료**"
        )

    @discord.ui.button(label="🏠 강의홈", style=discord.ButtonStyle.secondary)
    async def course_home(self, interaction: discord.Interaction, button: discord.ui.Button):
        from manta_daemon.integrations.lms import lms_get_course_home
        await self._run(
            interaction, button, "🏠 강의홈",
            lambda: lms_get_course_home(self.course["kjkey"]),
            f"🏠 **`{self.course['name']}` 강의홈**"
        )

    @discord.ui.button(label="✖ 닫기", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="✖ 닫았어요.", view=None)


# ==================== [ LMSCourseSelectView ] ====================

class LMSCourseSelectView(discord.ui.View):
    def __init__(self, courses, page_index=0):
        super().__init__(timeout=120)
        self.courses = courses
        self.page_index = page_index
        self._build_buttons()

    def _current_courses(self):
        start = self.page_index * MAX_COURSE_BUTTONS
        return self.courses[start:start + MAX_COURSE_BUTTONS]

    def _build_buttons(self):
        self.clear_items()
        for course in self._current_courses():
            label = (course["name"] or "(이름없음)")[:78]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=f"lms_{course['kjkey']}"
            )
            btn.callback = self._make_callback(course)
            self.add_item(btn)

        total_pages = max(1, (len(self.courses) - 1) // MAX_COURSE_BUTTONS + 1)
        if total_pages > 1:
            if self.page_index > 0:
                prev = discord.ui.Button(label="◀ 이전", style=discord.ButtonStyle.secondary, custom_id="lms_prev")
                prev.callback = self._prev_callback
                self.add_item(prev)
            if self.page_index < total_pages - 1:
                nxt = discord.ui.Button(label="다음 ▶", style=discord.ButtonStyle.secondary, custom_id="lms_next")
                nxt.callback = self._next_callback
                self.add_item(nxt)

        close = discord.ui.Button(label="✖ 닫기", style=discord.ButtonStyle.secondary, custom_id="lms_close")
        close.callback = self._close_callback
        self.add_item(close)

    def _make_callback(self, course):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != MY_DISCORD_UID:
                await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
                return
            from manta_daemon.utils.helpers import log_activity
            state.lms_current_course = course
            log_activity("LMS", f"강의 선택: {course['name']}")
            menu_view = LMSCourseMenuView(course)
            await interaction.response.edit_message(
                content=f"📚 **`{course['name']}`** 선택됨!\n뭘 볼까요?",
                view=menu_view
            )
        return callback

    async def _prev_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.page_index -= 1
        self._build_buttons()
        total_pages = max(1, (len(self.courses) - 1) // MAX_COURSE_BUTTONS + 1)
        await interaction.response.edit_message(
            content=f"📚 **LMS 강의 선택** ({len(self.courses)}개) — {self.page_index+1}/{total_pages}페이지",
            view=self
        )

    async def _next_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.page_index += 1
        self._build_buttons()
        total_pages = max(1, (len(self.courses) - 1) // MAX_COURSE_BUTTONS + 1)
        await interaction.response.edit_message(
            content=f"📚 **LMS 강의 선택** ({len(self.courses)}개) — {self.page_index+1}/{total_pages}페이지",
            view=self
        )

    async def _close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="✖ 닫았어요.", view=None)
