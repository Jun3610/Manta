"""
integrations/gmail.py — Gmail OAuth 초기화, 폴링, 알림
"""
import asyncio
import re

import discord

import manta_daemon.state as state
from manta_daemon.config import (
    GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH,
    MANTA_CHANNEL_ID, MY_DISCORD_UID, _PROJECT_ROOT,
)

# ── Gmail 스코프 / 인증 키워드 ──────────────────────────────────────────────

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_AUTH_KEYWORDS = [
    "인증", "verification", "verify", "code", "otp",
    "confirm", "확인", "일회용", "one-time", "passcode",
    "pin", "비밀번호", "password reset",
]
_AUTH_CODE_RE = re.compile(r'\b(\d{4,8})\b')


# ── 초기화 ──────────────────────────────────────────────────────────────────

def _init_gmail():
    import os
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_path = os.path.join(_PROJECT_ROOT, GMAIL_TOKEN_PATH)
        creds_path = os.path.join(_PROJECT_ROOT, GMAIL_CREDENTIALS_PATH)

        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, _GMAIL_SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(creds_path):
                    print("[Gmail] credentials.json 없음 — Gmail 비활성화")
                    return
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, _GMAIL_SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as _f:
                _f.write(creds.to_json())

        state._gmail_service = build("gmail", "v1", credentials=creds)
        print("[Gmail] 초기화 완료")
    except ImportError:
        print("[Gmail] google-api-python-client 미설치 — Gmail 비활성화")
    except Exception as e:
        print(f"[Gmail] 초기화 실패: {e}")


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _gmail_is_english(text: str) -> bool:
    if not text:
        return False
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    total_alpha = sum(1 for c in text if c.isalpha())
    return total_alpha > 0 and ascii_alpha / total_alpha > 0.65


def _gmail_extract_auth_code(text: str) -> str | None:
    lower = text.lower()
    if not any(k in lower for k in _AUTH_KEYWORDS):
        return None
    m = _AUTH_CODE_RE.search(text)
    return m.group(1) if m else None


def _gmail_translate(text: str) -> str:
    try:
        r = state.ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "영어 텍스트를 자연스러운 한국어로 번역해줘. 번역문만 출력."},
                {"role": "user", "content": text[:3000]},
            ],
            max_tokens=800, temperature=0.2,
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return text


def _gmail_summarize(sender: str, subject: str, body: str) -> str:
    try:
        r = state.ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "이메일을 한국어로 3~5줄 요약해줘. 핵심 내용 위주로, 번역이 필요하면 번역도 해줘."},
                {"role": "user", "content": f"보낸 사람: {sender}\n제목: {subject}\n\n{body[:3000]}"},
            ],
            max_tokens=600, temperature=0.3,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"요약 실패: {e}"


def _gmail_extract_body(msg: dict) -> str:
    import base64

    def _parts(part):
        mt = part.get("mimeType", "")
        if mt == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mt == "text/html":
            return ""  # plain 우선
        for sub in part.get("parts", []):
            result = _parts(sub)
            if result:
                return result
        return ""

    body = _parts(msg["payload"])
    return body.strip() or msg.get("snippet", "")


def _gmail_sender_name(raw: str) -> str:
    m = re.match(r'^"?([^"<\n]+)"?\s*<?', raw)
    return (m.group(1).strip() if m else raw).strip('"')


def _gmail_fetch_inbox_sync(n: int = 10) -> list:
    if not state._gmail_service:
        return []
    result = state._gmail_service.users().messages().list(
        userId="me", labelIds=["INBOX"], maxResults=n
    ).execute()
    items = []
    for ref in result.get("messages", []):
        try:
            msg = state._gmail_service.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            hdrs = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            items.append({
                "id": ref["id"],
                "from": hdrs.get("From", "?"),
                "subject": hdrs.get("Subject", "(제목 없음)"),
                "date": hdrs.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "unread": "UNREAD" in msg.get("labelIds", []),
            })
        except Exception:
            pass
    return items


# ── Discord View ─────────────────────────────────────────────────────────────

class GmailReadView(discord.ui.View):
    def __init__(self, msg_id: str, sender: str, subject: str, is_english: bool):
        super().__init__(timeout=600)
        self.msg_id     = msg_id
        self.sender     = sender
        self.subject    = subject
        self.is_english = is_english

    @discord.ui.button(label="📖 읽어줘", style=discord.ButtonStyle.primary)
    async def read_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        await interaction.response.defer()

        loop = asyncio.get_running_loop()

        def _fetch():
            return state._gmail_service.users().messages().get(
                userId="me", id=self.msg_id, format="full"
            ).execute()

        try:
            msg  = await loop.run_in_executor(None, _fetch)
            body = _gmail_extract_body(msg)
            if self.is_english:
                body = await loop.run_in_executor(None, lambda: _gmail_translate(body[:2500]))
            summary = await loop.run_in_executor(
                None, lambda: _gmail_summarize(self.sender, self.subject, body)
            )
        except Exception as e:
            summary = f"오류: {e}"

        self.clear_items()
        await interaction.message.edit(view=None)
        await interaction.followup.send(f"📧 **메일 요약 — {self.sender}**\n{summary}")

    @discord.ui.button(label="✖ 나중에", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        self.clear_items()
        await interaction.response.edit_message(
            content=interaction.message.content.replace("\n\n읽어줄까?", " ✔"), view=None
        )


# ── 새 메일 처리 / 폴링 ─────────────────────────────────────────────────────

async def _handle_new_gmail(channel, msg: dict):
    hdrs    = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    sender  = hdrs.get("From", "알 수 없음")
    subject = hdrs.get("Subject", "(제목 없음)")
    snippet = msg.get("snippet", "")
    msg_id  = msg["id"]

    sender_name = _gmail_sender_name(sender)
    combined    = subject + " " + snippet

    # 인증번호 감지 → 즉시 알림
    auth_code = _gmail_extract_auth_code(combined)
    if auth_code:
        await channel.send(
            f"📧 **{sender_name}**한테서 인증 메일 왔어!\n"
            f"> {subject}\n\n"
            f"🔑 **인증번호: `{auth_code}`**"
        )
        return

    # 영어 여부 판단
    is_english = _gmail_is_english(combined)
    loop       = asyncio.get_running_loop()

    # 제목 번역 (영어일 때)
    display_subject = subject
    if is_english:
        translated_sub  = await loop.run_in_executor(None, lambda: _gmail_translate(subject))
        display_subject = f"{translated_sub}  *(원문: {subject})*"

    # 스니펫 번역 (영어일 때)
    brief = snippet[:120] + ("…" if len(snippet) > 120 else "")
    if is_english and brief:
        brief = await loop.run_in_executor(None, lambda: _gmail_translate(brief))

    view = GmailReadView(msg_id=msg_id, sender=sender_name, subject=subject, is_english=is_english)
    await channel.send(
        f"📧 **{sender_name}**한테서 메일 왔어!\n"
        f"제목: {display_subject}\n"
        f"> {brief}\n\n"
        f"읽어줄까?",
        view=view,
    )


async def _gmail_check_new(channel):
    if not state._gmail_service:
        return
    loop = asyncio.get_running_loop()

    def _fetch():
        return state._gmail_service.users().messages().list(
            userId="me", q="is:unread newer_than:3m", maxResults=5
        ).execute().get("messages", [])

    try:
        refs = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        print(f"[Gmail Poll] fetch 오류: {e}")
        return

    for ref in refs:
        mid = ref["id"]
        if mid in state._gmail_notified_ids:
            continue
        state._gmail_notified_ids.add(mid)

        def _get(m=mid):
            return state._gmail_service.users().messages().get(
                userId="me", id=m, format="full"
            ).execute()

        try:
            msg = await loop.run_in_executor(None, _get)
            await _handle_new_gmail(channel, msg)
        except Exception as e:
            print(f"[Gmail] 메시지 처리 오류: {e}")


async def _gmail_poll_loop():
    """60초마다 새 메일 확인 → MANTA 채널에 알림"""
    await asyncio.sleep(10)  # 봇 준비 대기
    manta_ch = state.bot.get_channel(MANTA_CHANNEL_ID)
    if not manta_ch:
        print("[Gmail Poll] MANTA_CHANNEL_ID 채널 없음")
        return
    while True:
        try:
            await _gmail_check_new(manta_ch)
        except Exception as e:
            print(f"[Gmail Poll] 루프 오류: {e}")
        await asyncio.sleep(60)
