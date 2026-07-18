"""
tools/web_ops.py — URL 위험 분석, 웹 컨펌 UI, LMS 스크래핑
"""
import asyncio
import re
import urllib.parse

import discord
from bs4 import BeautifulSoup

from manta_daemon.config import (
    MY_DISCORD_UID, ALLOWED_DOMAINS, SITE_NAME_MAP, LMS_BASE,
    _SUSPICIOUS_TLDS, _SUSPICIOUS_KEYWORDS, _IP_URL_PATTERN,
)
import manta_daemon.state as state


def analyze_url_risk(url: str) -> dict:
    """
    URL 위험성 다각도 분석.
    반환: {"level": "안전"|"주의"|"위험"|"차단", "score": int, "reasons": [str], "domain": str}
    """
    reasons = []
    score = 0

    try:
        parsed = urllib.parse.urlparse(url if url.startswith("http") else f"https://{url}")
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        full = url.lower()
    except Exception:
        return {"level": "위험", "score": 100, "reasons": ["URL 파싱 불가"], "domain": url}

    in_whitelist = any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS)
    if in_whitelist:
        reasons.append(f"✅ 화이트리스트 도메인 ({domain})")
    else:
        reasons.append(f"⚠️ 화이트리스트 외 도메인 ({domain})")
        score += 30

    if url.startswith("http://"):
        reasons.append("⚠️ HTTP (암호화 없음) — 중간자 공격 가능")
        score += 20
    else:
        reasons.append("✅ HTTPS 암호화 사용")

    if _IP_URL_PATTERN.match(url):
        reasons.append("🚨 IP 주소 직접 접근 — 도메인 없이 서버 직접 연결, 피싱 의심")
        score += 50

    tld = "." + domain.split(".")[-1] if "." in domain else ""
    if tld in _SUSPICIOUS_TLDS:
        reasons.append(f"🚨 위험 TLD ({tld}) — 무료/악용 빈번한 도메인")
        score += 40

    if len(url) > 200:
        reasons.append(f"⚠️ URL 매우 김 ({len(url)}자) — 파라미터 숨기기 의심")
        score += 15

    if url.count("http") > 1 or "redirect" in full or "url=" in full:
        reasons.append("⚠️ 리다이렉트 파라미터 감지 — 최종 목적지 불명")
        score += 25

    matched_kw = [kw for kw in _SUSPICIOUS_KEYWORDS if kw in full]
    if matched_kw:
        reasons.append(f"🚨 위험 키워드 포함: {', '.join(matched_kw)}")
        score += 40

    danger_exts = [".exe", ".bat", ".sh", ".dmg", ".pkg", ".msi", ".apk", ".jar"]
    matched_ext = [e for e in danger_exts if path.endswith(e)]
    if matched_ext:
        reasons.append(f"🚨 실행 파일 다운로드 감지: {matched_ext[0]}")
        score += 60

    if score == 0 and in_whitelist:
        level = "안전"
    elif score < 40:
        level = "주의"
    elif score < 70:
        level = "위험"
    else:
        level = "차단"

    return {"level": level, "score": score, "reasons": reasons, "domain": domain}


def _risk_emoji(level: str) -> str:
    return {"안전": "✅", "주의": "⚠️", "위험": "🔴", "차단": "🚫"}.get(level, "❓")


def _build_risk_report(action: str, url: str, analysis: dict) -> str:
    emoji = _risk_emoji(analysis["level"])
    lines = [
        f"{emoji} **웹 활동 보안 분석 보고서**",
        f"",
        f"**요청 작업:** {action}",
        f"**대상 URL:** `{url}`",
        f"**위험 레벨:** {emoji} **{analysis['level']}** (점수: {analysis['score']}/100)",
        f"",
        f"**분석 항목:**",
    ]
    for r in analysis["reasons"]:
        lines.append(f"  {r}")
    if analysis["level"] in ("위험", "차단"):
        lines.append("")
        lines.append("🚨 **경고:** 이 URL은 높은 위험 신호를 포함하고 있습니다. 신중히 판단 후 결정하세요.")
    return "\n".join(lines)


class WebConfirmView(discord.ui.View):
    """웹 활동 컨펌 UI — asyncio.Event로 결과를 web_confirm_gate에 반환"""

    def __init__(self, action: str, url: str, analysis: dict, execute_fn):
        super().__init__(timeout=120)
        self.action = action
        self.url = url
        self.analysis = analysis
        self.execute_fn = execute_fn
        self.result = None
        self._done = asyncio.Event()
        level = analysis["level"]
        if level == "차단":
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.custom_id == "web_confirm":
                    item.disabled = True
                    item.label = "🚫 차단됨 (실행 불가)"
                    item.style = discord.ButtonStyle.danger

    @discord.ui.button(label="✅ 실행", style=discord.ButtonStyle.success, custom_id="web_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(content="🔄 실행 중...", view=self)
        except discord.InteractionResponded:
            pass
        try:
            self.result = self.execute_fn()
            await interaction.edit_original_response(content=f"✅ **완료** `{self.url}`", view=None)
        except Exception as e:
            self.result = f"😥 실행 오류: {e}"
            await interaction.edit_original_response(content=self.result, view=None)
        self._done.set()
        self.stop()

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.secondary, custom_id="web_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MY_DISCORD_UID:
            await interaction.response.send_message("❌ 권한 없음", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(content="↩️ 웹 활동을 취소했어요.", view=None)
        except discord.InteractionResponded:
            pass
        self.result = "__CANCELLED__"
        self._done.set()
        self.stop()


async def web_confirm_gate(channel, action: str, url: str, execute_fn) -> str:
    """
    웹 활동 전 분석 + 컨펌 게이트.
    화이트리스트 도메인(안전)은 컨펌 없이 자동 실행, 나머지는 반드시 컨펌.
    """
    analysis = analyze_url_risk(url)

    if analysis["level"] == "안전":
        # 화이트리스트 도메인 → 즉시 실행
        return execute_fn()

    report = _build_risk_report(action, url, analysis)
    view = WebConfirmView(action, url, analysis, execute_fn)
    await channel.send(content=report, view=view)
    await view._done.wait()
    result = view.result or "__CANCELLED__"
    if result == "__CANCELLED__":
        return "__CONFIRM_PENDING__"
    return result


def _sanitize_web_content(text: str) -> str:
    """프롬프트 인젝션 방지: URL 제거 + 지시어처럼 보이는 패턴 무력화."""
    text = re.sub(r'https?://\S+', '[URL 제거됨]', text)
    injection_patterns = [
        r'(?i)(ignore|forget|disregard)\s+(previous|prior|above|all)\s+instruction',
        r'(?i)you\s+are\s+now\s+',
        r'(?i)new\s+instruction',
        r'(?i)system\s*:\s*',
        r'(?i)assistant\s*:\s*',
    ]
    for pat in injection_patterns:
        text = re.sub(pat, '[필터됨]', text)
    return text


def scrap_lms_website(url=None):
    """화이트리스트 도메인만 허용하는 안전한 웹 스크래핑"""
    if not url:
        url = f"{LMS_BASE}/ilos/main/main_form.acl"
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        if not domain:
            domain = urllib.parse.urlparse(f"https://{url}").netloc
        if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS):
            return f"❌ '{domain}'은 허용되지 않은 도메인이에요. (허용: {', '.join(ALLOWED_DOMAINS)})"
        resp = state.lms_session.get(url, timeout=10)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "a"]):
            tag.decompose()
        lines = [l for l in soup.get_text(separator="\n", strip=True).splitlines() if l.strip()]
        raw = "\n".join(lines[:120])
        if not raw.strip():
            return "📭 텍스트 추출 실패. 로그인이 필요할 수 있어요."
        safe = _sanitize_web_content(raw)
        return f"✅ **LMS** 스크래핑 결과!\n\n{safe[:2000]}"
    except Exception as e:
        return f"😥 스크래핑 오류: {e}"
