import os
import discord
from discord.ext import commands
from openai import OpenAI
from datetime import datetime
import logging
from dotenv import load_dotenv
import re
import urllib.parse
import json
import subprocess
import time
import io
import pyautogui
import pyperclip
from notion_client import Client
import requests
from bs4 import BeautifulSoup

logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.gateway').setLevel(logging.WARNING)

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, ".env")
load_dotenv(dotenv_path=env_path)

# ==================== [ 설정 ] ====================
MY_DISCORD_UID = int(os.getenv("MY_DISCORD_UID"))
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")

if NOTION_TOKEN and NOTION_PAGE_ID:
    notion = Client(auth=NOTION_TOKEN)
else:
    notion = None

ALLOWED_MAC_APPS = {
    "notion": "Notion", "노션": "Notion",
    "safari": "Safari", "사파리": "Safari",
    "preview": "Preview", "미리보기": "Preview",
    "visual studio code": "Visual Studio Code",
    "vscode": "Visual Studio Code", "비주얼": "Visual Studio Code",
    "intellij": "IntelliJ IDEA", "인텔리": "IntelliJ IDEA",
    "mail": "Mail", "메일": "Mail"
}
ALLOWED_DOMAINS = ["lms.pknu.ac.kr"]
HOME = os.path.expanduser("~")
WORK_STATION_ROOT = os.path.join(HOME, "work-station")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

ai_client = OpenAI(api_key=OPENAI_API_KEY)
conversation_history = []

# ── 현재 열람 컨텍스트 (파일 OR 노션 페이지 공용) ──
current_context = {}
# {
#   "type": "file" | "notion",
#   "name": str,           # 파일명 or 노션 페이지 제목
#   "content": str,        # 원본 내용
#   "numbered_content": str  # 줄번호 붙인 버전 (파일일 때만)
# }


def log_activity(action_type, details):
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{current_time}] ⚡ [{action_type}] {details}")


# ==================== [ Discord UI ] ====================

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
        delete_notion_page(self.page_id)
        self.clear_items()
        await interaction.response.edit_message(content=f"🗑️ `{self.page_title}` 삭제 완료!", view=self)
        self.origin_view.disable_page_button(self.page_id)
        try:
            await self.origin_view.origin_message.edit(view=self.origin_view)
        except Exception:
            pass

    @discord.ui.button(label="취소 ↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="↩️ 취소했어요.", view=self)


class NotionDeleteView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=120)
        self.pages = pages
        self.origin_message = None
        for page in pages:
            label = page["title"][:75]
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=f"notion_del_{page['id']}")
            btn.callback = self._make_callback(page)
            self.add_item(btn)
        all_btn = discord.ui.Button(label="전체 삭제 🗑️", style=discord.ButtonStyle.danger, custom_id="notion_del_all")
        all_btn.callback = self._delete_all_callback
        self.add_item(all_btn)
        close_btn = discord.ui.Button(label="닫기 ✖", style=discord.ButtonStyle.secondary, custom_id="notion_del_close")
        close_btn.callback = self._close_callback
        self.add_item(close_btn)

    def _make_callback(self, page):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != MY_DISCORD_UID:
                await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
                return
            confirm_view = NotionDeleteConfirmView(page["id"], page["title"], self)
            await interaction.response.send_message(content=f"⚠️ **`{page['title']}`** 삭제할까요?", view=confirm_view)
        return callback

    async def _delete_all_callback(self, interaction: discord.Interaction):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        confirm_view = DeleteAllConfirmView(self.pages, self)
        await interaction.response.send_message(content=f"⚠️ **{len(self.pages)}개** 전부 삭제할까요?", view=confirm_view)

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
        await interaction.response.edit_message(content="🗑️ **전체 삭제 완료**\n" + "\n".join(results), view=self)

    @discord.ui.button(label="취소 ↩️", style=discord.ButtonStyle.secondary)
    async def cancel_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(content="↩️ 취소했어요.", view=self)


# ==================== [ 앱 제어 ] ====================

def open_mac_app(app_name):
    clean = app_name.strip().lower()
    matched = next((v for k, v in ALLOWED_MAC_APPS.items() if k in clean), None)
    if not matched:
        return f"❌ '{app_name}'은 허용된 앱 목록에 없어요!"
    subprocess.run(["open", "-a", matched])
    return f"✨ '{matched}' 켰어요!"


def quit_mac_app(app_name):
    clean = app_name.strip().lower()
    matched = next((v for k, v in ALLOWED_MAC_APPS.items() if k in clean), None)
    if not matched:
        return f"❌ '{app_name}'은 허용된 앱 목록에 없어요!"
    try:
        subprocess.run(['osascript', '-e', f'tell application "{matched}" to quit'])
        return f"🔴 '{matched}' 종료했어요!"
    except Exception as e:
        return f"😥 종료 오류: {e}"


def get_notion_app_context(target_app_name="Notion"):
    matched = next((v for k, v in ALLOWED_MAC_APPS.items() if k in target_app_name.lower()), None)
    if not matched:
        return f"❌ '{target_app_name}'은 허용 앱 목록에 없어요."
    try:
        subprocess.run(['osascript', '-e', f'tell application "{matched}" to activate'])
        time.sleep(0.7)
        original = pyperclip.paste()
        pyautogui.hotkey('command', 'a')
        time.sleep(0.2)
        pyautogui.hotkey('command', 'c')
        time.sleep(0.2)
        text = pyperclip.paste()
        pyperclip.copy(original)
        if not text.strip():
            return f"'{matched}' 창이 비어 있어요."
        return f"'{matched}' 앱 내용이에요! 👇\n{text[:3000]}"
    except Exception as e:
        return f"😥 {matched} 읽기 오류: {e}"


def read_mac_mail(count=5):
    try:
        script = f'''
        tell application "Mail"
            set output to ""
            set msgs to messages of inbox
            set n to count of msgs
            if n > {count} then set n to {count}
            repeat with i from 1 to n
                set m to item i of msgs
                set output to output & "---\\n제목: " & (subject of m) & "\\n보낸이: " & (sender of m) & "\\n날짜: " & ((date received of m) as string) & "\\n내용: " & (content of m) & "\\n"
            end repeat
            return output
        end tell
        '''
        result = subprocess.check_output(['osascript', '-e', script], timeout=15).decode('utf-8').strip()
        if not result:
            return "📭 받은 편지함에 메일이 없어요."
        return f"📬 **최근 메일 {count}개**\n\n{result[:3000]}"
    except subprocess.TimeoutExpired:
        return "⏱️ Mail 앱 응답 시간 초과."
    except Exception as e:
        return f"😥 메일 읽기 오류: {e}"


# ==================== [ 파일 탐색 ] ====================

def _add_line_numbers(content):
    lines = content.splitlines()
    return "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))


def _read_file_at_path(path):
    name = os.path.basename(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        try:
            with open(path, "r", encoding="cp949") as f:
                content = f.read()
        except Exception as e:
            return None, f"🚨 파일 읽기 오류: {e}"
    return {"name": name, "content": content, "numbered_content": _add_line_numbers(content)}, None


def find_file(hint):
    """
    hint를 받아 work-station 전체를 재귀 탐색해서 가장 적합한 파일 경로 반환.
    - 폴더명/파일명 힌트 모두 처리
    - venv, .git, node_modules, __pycache__ 제외
    """
    hint_lower = hint.strip().lower()

    # 힌트에서 폴더 키워드 / 파일 키워드 분리
    # 예: "만타 폴더에서 파이썬 파일" → folder_hint="만타", file_hint=".py"
    folder_hint = None
    file_hint = hint_lower

    folder_keywords = ["폴더", "디렉토리", "folder", "directory", "프로젝트", "project"]
    for kw in folder_keywords:
        if kw in hint_lower:
            parts = hint_lower.split(kw)
            folder_hint = parts[0].strip()
            file_hint = parts[1].strip() if len(parts) > 1 else ""
            break

    EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '__pycache__', '.idea', 'build', 'dist', '.next'}

    candidates = []
    for root, dirs, files in os.walk(WORK_STATION_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('.')]

        # 폴더 힌트가 있으면 해당 폴더 하위만 탐색
        if folder_hint:
            rel = os.path.relpath(root, WORK_STATION_ROOT).lower()
            if not any(folder_hint in part for part in rel.split(os.sep)):
                continue

        for fname in files:
            if fname.startswith('.'):
                continue
            fname_lower = fname.lower()
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, WORK_STATION_ROOT).lower()

            score = 0
            # 파일명 직접 매칭
            if file_hint and file_hint in fname_lower:
                score += 10
            # 확장자 매칭
            if any(x in file_hint for x in ["파이썬", "python", ".py"]) and fname_lower.endswith(".py"):
                score += 5
            if any(x in file_hint for x in ["자바", "java", ".java"]) and fname_lower.endswith(".java"):
                score += 5
            # 경로에 힌트 포함
            if file_hint and file_hint in rel_path:
                score += 3
            # 폴더 힌트 매칭된 경우 보너스
            if folder_hint:
                score += 2

            if score > 0:
                candidates.append((score, full_path))

    if not candidates:
        return None, f"❌ '{hint}' 관련 파일을 work-station에서 찾지 못했어요."

    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1], None


def read_local_file(target_hint):
    path, err = find_file(target_hint)
    if err:
        return err
    result, err = _read_file_at_path(path)
    if err:
        return err
    return result


def analyze_and_suggest_code(target_hint, question):
    global current_context

    # 이미 같은 파일이 로드돼 있으면 재사용
    if (current_context.get("type") == "file" and
            current_context.get("name", "").lower().replace(".py", "") in target_hint.lower()):
        content = current_context["content"]
        numbered = current_context["numbered_content"]
        name = current_context["name"]
        log_activity("코드 분석", f"캐시 사용: {name}")
    else:
        path, err = find_file(target_hint)
        if err:
            return err
        result, err = _read_file_at_path(path)
        if err:
            return err
        content = result["content"]
        numbered = result["numbered_content"]
        name = result["name"]
        current_context = {"type": "file", "name": name, "content": content, "numbered_content": numbered}
        log_activity("코드 분석", f"새 파일 로드: {name}")

    prompt = (
        f"파일명: '{name}'\n"
        f"줄번호 포함 전체 코드:\n```\n{numbered[:10000]}\n```\n\n"
        f"요청: {question}\n\n"
        "줄번호가 언급되면 반드시 해당 줄을 정확히 찾아 답해줘. "
        "코드 수정 제안 시 반드시 코드블록으로 감싸줘."
    )
    resp = ai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "너는 시니어 개발자야. 줄번호 기반으로 정확히 코드를 분석해줘."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=2000
    )
    analysis = resp.choices[0].message.content

    if len(analysis) > 1900:
        buf = io.BytesIO(analysis.encode('utf-8'))
        f = discord.File(fp=buf, filename=f"{name}_analysis.md")
        return {"type": "file", "message": f"📋 `{name}` 분석 결과", "file_object": f,
                "content": content, "numbered_content": numbered, "name": name, "analysis": analysis}
    return {"type": "inline", "message": f"📋 **`{name}` 분석**\n\n{analysis}",
            "content": content, "numbered_content": numbered, "name": name, "analysis": analysis}


# ==================== [ LMS ] ====================

def scrap_lms_website(url=None):
    if not url:
        url = "https://lms.pknu.ac.kr"
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc or urllib.parse.urlparse(f"https://{url}").netloc
        if domain not in ALLOWED_DOMAINS:
            return f"❌ '{domain}'은 허용되지 않은 도메인이에요."
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        lines = [l for l in soup.get_text(separator="\n", strip=True).splitlines() if l.strip()]
        result = "\n".join(lines[:100])
        if not result.strip():
            return "📭 텍스트 추출 실패. 로그인이 필요할 수 있어요."
        return f"✅ **LMS** 스크래핑 결과!\n\n{result[:2000]}"
    except requests.exceptions.Timeout:
        return "⏱️ LMS 응답 시간 초과."
    except Exception as e:
        return f"😥 스크래핑 오류: {e}"


# ==================== [ 노션 ] ====================

def _make_notion_blocks(content):
    """
    내용을 파싱해서 노션 블록 리스트 생성.
    ```lang ... ``` 코드블록은 code 타입으로, 나머지는 paragraph로.
    """
    blocks = []
    # 코드블록 파싱
    pattern = re.compile(r'```(\w*)\n?(.*?)```', re.DOTALL)
    last_end = 0
    for m in pattern.finditer(content):
        # 코드블록 앞 텍스트
        before = content[last_end:m.start()].strip()
        if before:
            for para in before.split('\n\n'):
                para = para.strip()
                if para:
                    blocks.append({
                        "object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[:2000]}}]}
                    })
        # 코드블록
        lang = m.group(1).strip() or "plain text"
        code = m.group(2)
        blocks.append({
            "object": "block", "type": "code",
            "code": {
                "rich_text": [{"type": "text", "text": {"content": code[:2000]}}],
                "language": lang if lang in [
                    "python", "javascript", "typescript", "java", "c", "cpp",
                    "go", "rust", "shell", "sql", "html", "css", "json",
                    "markdown", "plain text"
                ] else "plain text"
            }
        })
        last_end = m.end()

    # 남은 텍스트
    remaining = content[last_end:].strip()
    if remaining:
        for para in remaining.split('\n\n'):
            para = para.strip()
            if para:
                blocks.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[:2000]}}]}
                })

    return blocks if blocks else [{
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}
    }]


def create_notion_page(title, content):
    if not notion:
        return "❌ NOTION 설정 누락."
    try:
        blocks = _make_notion_blocks(content)
        notion.pages.create(
            parent={"page_id": NOTION_PAGE_ID},
            properties={"title": {"title": [{"text": {"content": title}}]}},
            children=blocks
        )
        return f"📝 노션에 '{title}' 페이지 작성 완료!"
    except Exception as e:
        return f"😥 노션 페이지 생성 오류: {e}"


def read_notion_page(page_id):
    """노션 페이지 본문을 읽어서 current_context에 저장"""
    global current_context
    if not notion:
        return "❌ NOTION 설정 누락."
    try:
        page_info = notion.pages.retrieve(page_id=page_id)
        title_prop = page_info.get("properties", {}).get("title", {}).get("title", [])
        title = title_prop[0]["text"]["content"] if title_prop else "(제목 없음)"

        blocks = notion.blocks.children.list(block_id=page_id)
        lines = [f"📄 **{title}**\n"]
        for block in blocks.get("results", []):
            btype = block.get("type", "")
            if btype == "code":
                lang = block["code"].get("language", "")
                code_text = "".join(rt.get("plain_text", "") for rt in block["code"].get("rich_text", []))
                lines.append(f"```{lang}\n{code_text}\n```")
            else:
                rich_text = block.get(btype, {}).get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_text)
                if text:
                    lines.append(text)

        full_content = "\n".join(lines)

        # ✅ 노션 컨텍스트 저장 → 후속 질문에 바로 답 가능
        current_context = {
            "type": "notion",
            "name": title,
            "content": full_content,
            "numbered_content": ""
        }
        log_activity("노션 읽기", f"'{title}' 컨텍스트 저장 완료")

        if len(full_content) > 1800:
            buf = io.BytesIO(full_content.encode('utf-8'))
            f = discord.File(fp=buf, filename=f"{title}.txt")
            return {"type": "file", "message": f"📄 `{title}` 내용 (파일로 드려요)", "file_object": f}
        return full_content
    except Exception as e:
        return f"😥 노션 페이지 읽기 오류: {e}"


def update_notion_page(page_id, new_title=None, new_content=None):
    if not notion:
        return "❌ NOTION 설정 누락."
    try:
        if new_title:
            notion.pages.update(page_id=page_id,
                                properties={"title": {"title": [{"text": {"content": new_title}}]}})
        if new_content:
            existing = notion.blocks.children.list(block_id=page_id)
            for block in existing.get("results", []):
                try:
                    notion.blocks.delete(block_id=block["id"])
                except Exception:
                    pass
            blocks = _make_notion_blocks(new_content)
            # 2000자 초과 시 청크로 나눠서 append
            chunk_size = 90  # notion API 한번에 최대 100블록
            for i in range(0, len(blocks), chunk_size):
                notion.blocks.children.append(block_id=page_id, children=blocks[i:i+chunk_size])
        return "✏️ 노션 페이지 수정 완료!"
    except Exception as e:
        return f"😥 노션 수정 오류: {e}"


def list_notion_subpages():
    if not notion:
        return None, "❌ NOTION 설정 누락."
    try:
        result = notion.blocks.children.list(block_id=NOTION_PAGE_ID)
        pages = [
            {"id": b["id"], "title": b["child_page"].get("title", "(제목 없음)")}
            for b in result.get("results", []) if b.get("type") == "child_page"
        ]
        if not pages:
            return [], "📭 하위 페이지가 없어요."
        return pages, None
    except Exception as e:
        return None, f"😥 노션 목록 조회 오류: {e}"


def delete_notion_page(page_id):
    if not notion:
        return "error:NOTION 설정 누락"
    try:
        notion.pages.update(page_id=page_id, archived=True)
        return "success"
    except Exception as e:
        return f"error:{e}"


# ==================== [ Tools 선언 ] ====================

tools = [
    {
        "type": "function",
        "function": {
            "name": "open_mac_app",
            "description": "맥 앱 켜기. '켜줘', '실행해줘' 요청에만 사용.",
            "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quit_mac_app",
            "description": "맥 앱 종료. '꺼줘', '종료해줘' 요청에 사용.",
            "parameters": {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notion_app_context",
            "description": "노션 등 특정 앱을 직접 활성화해서 화면 내용 긁어오기. '노션에서 ~해줘' 요청에 사용.",
            "parameters": {"type": "object", "properties": {"target_app_name": {"type": "string"}}, "required": ["target_app_name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_mac_mail",
            "description": "맥 Mail 앱 최근 메일 읽기. '메일 확인', '받은 메일', '인증 메일' 요청에 사용.",
            "parameters": {"type": "object", "properties": {"count": {"type": "integer"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_local_file",
            "description": "로컬 파일 내용만 보여줄 때. 분석/줄번호 질문엔 analyze_and_suggest_code 사용.",
            "parameters": {"type": "object", "properties": {"target_hint": {"type": "string", "description": "파일명, 폴더명, 확장자 등 자연어 힌트"}}, "required": ["target_hint"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_and_suggest_code",
            "description": "파일 읽고 코드 분석, 줄번호 설명, 버그/수정 제안. 줄번호 언급 시 반드시 사용. 다른 파일 요청 시 컨텍스트 자동 전환.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_hint": {"type": "string"},
                    "question": {"type": "string"}
                },
                "required": ["target_hint", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrap_lms_website",
            "description": "부경대 LMS 스크래핑. URL 없어도 바로 실행.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_notion_page",
            "description": "노션 새 페이지 생성. 코드가 포함된 내용은 자동으로 코드블록으로 감싸짐.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notion_page",
            "description": "노션 특정 페이지 본문 읽기. 읽은 내용은 컨텍스트로 저장되어 후속 질문(설명, 번역, 분석 등)에 바로 답할 수 있음. 반드시 list_notion_subpages로 page_id 먼저 확인.",
            "parameters": {"type": "object", "properties": {"page_id": {"type": "string"}}, "required": ["page_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_notion_page",
            "description": "노션 페이지 제목/본문 수정. 코드는 자동 코드블록 처리. 반드시 list_notion_subpages로 page_id 먼저 확인.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "new_title": {"type": "string"},
                    "new_content": {"type": "string"}
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notion_subpages",
            "description": "노션 하위 페이지 목록 조회 + 삭제 UI 표시. 삭제/수정/읽기 요청 시 반드시 먼저 호출.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ==================== [ 메인 이벤트 루프 ] ====================

@bot.event
async def on_ready():
    print("==========================================")
    print("Manta 가드 시스템 연동 완료.")
    print(f"작업 루트: {WORK_STATION_ROOT}")
    print("==========================================")


@bot.event
async def on_message(message):
    global conversation_history, current_context
    if message.author == bot.user or message.author.id != MY_DISCORD_UID:
        return

    user_cmd = message.content.strip()
    log_activity("유저 활동", f"메시지 수신: '{user_cmd}'")

    if user_cmd in ["클리어", "clear", "청소"]:
        conversation_history = []
        current_context = {}
        await message.channel.purge(limit=100)
        await message.channel.send("🧹 버퍼 청소 완료!", delete_after=3)
        return

    conversation_history.append({"role": "user", "content": user_cmd})

    # ── 컨텍스트 요약 (토큰 절약: 전체 코드 대신 요약만 시스템 프롬프트에) ──
    ctx_summary = ""
    if current_context:
        ctype = current_context.get("type", "")
        cname = current_context.get("name", "")
        ccontent = current_context.get("content", "")
        if ctype == "file":
            # 줄번호 붙인 전체 코드는 analyze_and_suggest_code 내부에서만 사용
            # 시스템 프롬프트엔 요약 + 앞부분만 넣어서 토큰 절약
            ctx_summary = (
                f"\n\n[현재 열람 파일: `{cname}` | 총 {len(ccontent.splitlines())}줄]\n"
                f"줄번호/메서드 질문은 analyze_and_suggest_code를 호출해서 정확히 답해줘.\n"
                f"다른 파일 요청이 오면 컨텍스트를 전환해줘."
            )
        elif ctype == "notion":
            # 노션 내용은 시스템 프롬프트에 직접 포함 (파일보다 짧은 경우 많음)
            ctx_summary = (
                f"\n\n[현재 열람 노션 페이지: `{cname}`]\n"
                f"내용:\n{ccontent[:3000]}\n"
                f"이 내용을 바탕으로 주인의 질문(설명, 번역, 분석 등)에 바로 답해줘. "
                f"추가 tool 호출 없이 위 내용만으로 답할 수 있으면 바로 답해줘."
            )

    messages_input = [
        {
            "role": "system",
            "content": (
                "너는 주인의 시스템 비서 '만타(Manta)'야. 사근사근하고 친근한 대화체를 써줘.\n"
                "규칙:\n"
                "- 노션 작성: create_notion_page만. open_mac_app 자동 호출 금지.\n"
                "- 노션 삭제/수정/읽기: 반드시 list_notion_subpages로 page_id 먼저 확인.\n"
                "- 노션 페이지 읽기 후 후속 질문(설명/번역/분석): 이미 컨텍스트에 내용 있으니 read_notion_page 재호출 금지, 바로 답해줘.\n"
                "- LMS 과제 확인: URL 없어도 scrap_lms_website 바로 실행.\n"
                "- 코드 분석/줄번호 질문: analyze_and_suggest_code 사용.\n"
                "- 노션에 코드 작성 시: 반드시 ```lang ... ``` 형식으로 감싸서 전달.\n"
                "- 수정 요청 후 완료 보고 시: 실제로 수정이 완료된 경우에만 완료라고 말해줘."
                + ctx_summary
            )
        }
    ] + conversation_history[-10:]

    async with message.channel.typing():
        try:
            response = ai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages_input,
                tools=tools,
                tool_choice="auto"
            )

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if tool_calls:
                messages_input.append(response_message)
                last_func_name = ""
                last_tool_result = ""

                for tool_call in tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)
                    tool_result = ""
                    last_func_name = func_name

                    if func_name == "open_mac_app":
                        tool_result = open_mac_app(func_args.get("app_name"))
                        await message.channel.send(tool_result)

                    elif func_name == "quit_mac_app":
                        tool_result = quit_mac_app(func_args.get("app_name"))
                        await message.channel.send(tool_result)

                    elif func_name == "get_notion_app_context":
                        tool_result = get_notion_app_context(func_args.get("target_app_name", "Notion"))
                        await message.channel.send(tool_result)

                    elif func_name == "read_mac_mail":
                        tool_result = read_mac_mail(func_args.get("count", 5))
                        await message.channel.send(tool_result)

                    elif func_name == "read_local_file":
                        res = read_local_file(func_args.get("target_hint"))
                        if isinstance(res, dict):
                            current_context = {"type": "file", "name": res["name"],
                                               "content": res["content"], "numbered_content": res["numbered_content"]}
                            msg = f"📂 `{res['name']}` 로드 완료! ({len(res['content'].splitlines())}줄)"
                            await message.channel.send(msg)
                            tool_result = f"파일 로드: {res['name']} ({len(res['content'])}자)"
                        else:
                            await message.channel.send(res)
                            tool_result = res

                    elif func_name == "analyze_and_suggest_code":
                        res = analyze_and_suggest_code(func_args.get("target_hint"), func_args.get("question"))
                        if isinstance(res, dict):
                            if res.get("type") == "file":
                                await message.channel.send(content=res["message"], file=res["file_object"])
                            else:
                                await message.channel.send(res["message"])
                            tool_result = f"분석완료: {res.get('name')} | {res.get('analysis','')[:150]}"
                        else:
                            await message.channel.send(res)
                            tool_result = res

                    elif func_name == "scrap_lms_website":
                        tool_result = scrap_lms_website(func_args.get("url"))
                        await message.channel.send(tool_result)

                    elif func_name == "create_notion_page":
                        tool_result = create_notion_page(func_args.get("title"), func_args.get("content"))
                        await message.channel.send(tool_result)

                    elif func_name == "read_notion_page":
                        res = read_notion_page(func_args.get("page_id"))
                        if isinstance(res, dict) and res.get("type") == "file":
                            await message.channel.send(content=res["message"], file=res["file_object"])
                            tool_result = f"노션 페이지 파일 전송 완료: {current_context.get('name','')}"
                        else:
                            await message.channel.send(str(res))
                            tool_result = str(res)[:300]

                    elif func_name == "update_notion_page":
                        tool_result = update_notion_page(
                            func_args.get("page_id"),
                            func_args.get("new_title"),
                            func_args.get("new_content")
                        )
                        await message.channel.send(tool_result)

                    elif func_name == "list_notion_subpages":
                        pages, error = list_notion_subpages()
                        if error:
                            await message.channel.send(error)
                            tool_result = error
                        elif not pages:
                            await message.channel.send("📭 하위 페이지가 없어요.")
                            tool_result = "페이지 없음"
                        else:
                            view = NotionDeleteView(pages)
                            sent_msg = await message.channel.send(
                                content=f"📋 **노션 하위 페이지 목록** ({len(pages)}개)",
                                view=view
                            )
                            view.origin_message = sent_msg
                            pages_summary = "\n".join([f"- {p['title']} (id: {p['id']})" for p in pages])
                            tool_result = f"목록 조회 완료 ({len(pages)}개):\n{pages_summary}"

                    else:
                        tool_result = "❌ 정의되지 않은 도구."
                        await message.channel.send(tool_result)

                    last_tool_result = tool_result
                    messages_input.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": func_name,
                        "content": str(tool_result),
                    })

                conversation_history.append({
                    "role": "assistant",
                    "content": f"[{last_func_name}] {str(last_tool_result)[:200]}"
                })

            else:
                ai_reply = response_message.content
                conversation_history.append({"role": "assistant", "content": ai_reply})
                await message.channel.send(ai_reply)

        except Exception as e:
            await message.channel.send(f"🚨 런타임 오류: {e}")


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)