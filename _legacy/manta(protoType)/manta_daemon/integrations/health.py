"""
integrations/health.py — Health 채널 데이터 기록·조회·리포트
"""
import json
import sqlite3
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import discord

from manta_daemon.config import _PROJECT_ROOT
import manta_daemon.state as state

_DB_PATH = str(Path(_PROJECT_ROOT) / "health_store.db")


def _init_db():
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS health_records (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at  TEXT NOT NULL,
                date         TEXT NOT NULL,
                entry_type   TEXT NOT NULL,
                data         TEXT NOT NULL,
                raw_text     TEXT
            )
        """)
        conn.commit()


_init_db()


def _store_records(records: list, raw_text: str = ""):
    now = datetime.now()
    with sqlite3.connect(_DB_PATH) as conn:
        for rec in records:
            conn.execute(
                "INSERT INTO health_records (recorded_at, date, entry_type, data, raw_text) VALUES (?, ?, ?, ?, ?)",
                (now.isoformat(), now.strftime("%Y-%m-%d"), rec["type"],
                 json.dumps(rec["data"], ensure_ascii=False), raw_text),
            )
        conn.commit()


def _fetch_records(start_date: str, end_date: str, entry_type: Optional[str] = None) -> list:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if entry_type:
            rows = conn.execute(
                "SELECT * FROM health_records WHERE date BETWEEN ? AND ? AND entry_type = ? ORDER BY recorded_at",
                (start_date, end_date, entry_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM health_records WHERE date BETWEEN ? AND ? ORDER BY recorded_at",
                (start_date, end_date),
            ).fetchall()
    return [dict(r) for r in rows]


async def handle_health_message(message: discord.Message) -> None:
    user_text = message.content.strip()
    image_urls = [
        a.url for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]

    # 리포트 요청
    if any(kw in user_text for kw in ["리포트", "report", "요약", "통계", "분석"]):
        period = "week"
        if "오늘" in user_text:
            period = "today"
        elif any(kw in user_text for kw in ["이번달", "이달"]):
            period = "month"
        async with message.channel.typing():
            embed, img_buf = _build_health_report(period)
        if img_buf:
            img_buf.seek(0)
            await message.channel.send(
                embed=embed,
                file=discord.File(img_buf, filename="health_report.png"),
            )
        else:
            await message.channel.send(embed=embed)
        return

    async with message.channel.typing():
        result = await _classify_and_extract(user_text, image_urls)

    intent = result.get("intent")

    if intent == "record":
        records = result.get("records", [])
        if not records:
            await message.channel.send("❓ 기록할 내용을 찾지 못했어요. 좀 더 구체적으로 입력해줘요!")
            return
        _store_records(records, raw_text=user_text)
        await message.channel.send(embed=_build_confirm_embed(records))

    elif intent == "query":
        async with message.channel.typing():
            answer = await _answer_query(user_text)
        await message.channel.send(answer)

    else:
        await message.channel.send(
            "❓ 이해하지 못했어요.\n"
            "• 기록: `점심 닭가슴살 현미밥 450kcal` / `공복 72.5kg` / 나이키런 캡처 이미지\n"
            "• 조회: `이번주 운동 몇번?` / `오늘 칼로리는?`\n"
            "• 리포트: `리포트` / `이번주 리포트`"
        )


async def _classify_and_extract(text: str, image_urls: list) -> dict:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    system = textwrap.dedent(f"""
        너는 헬스 데이터 추출 AI야. 현재 시간: {now_str}

        아래 JSON 형식으로만 응답해:
        - intent: "record" (기록) | "query" (조회) | "unknown"
        - records: intent가 record일 때만. 여러 항목이면 배열로.

        record 타입별 data 구조:
        - diet: {{"meal": "아침|점심|저녁|간식", "items": ["음식명"], "calories": 숫자(없으면 null)}}
        - weight: {{"kg": 숫자, "note": "공복|운동후|null"}}
        - exercise: {{"activity": "러닝|헬스|수영 등", "distance_km": 숫자(없으면 null), "duration_min": 숫자(없으면 null), "calories_burned": 숫자(없으면 null), "pace": "문자열(없으면 null)"}}

        나이키런/Nike Run 스크린샷이면 exercise로 추출. 거리·시간·페이스 읽기.
        기록과 조회가 섞이면 record 우선.
        응답은 JSON만.
    """).strip()

    content: list = [{"type": "text", "text": text or "(이미지 첨부됨)"}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})

    resp = state.ai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        max_tokens=800,
    )
    try:
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return {"intent": "unknown", "records": []}


async def _answer_query(question: str) -> str:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    month_start = now.strftime("%Y-%m-01")

    rows = _fetch_records((now - timedelta(days=30)).strftime("%Y-%m-%d"), today)
    summary = _rows_to_summary(rows)

    system = (
        f"오늘: {today}  이번주 시작: {week_start}  이번달 시작: {month_start}\n"
        "아래 헬스 기록을 보고 질문에 간결하게 답해줘. 데이터 없으면 '기록이 없어요'."
    )

    resp = state.ai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"[헬스 기록]\n{summary}\n\n[질문]\n{question}"},
        ],
        max_tokens=400,
    )
    return resp.choices[0].message.content or "답변을 생성하지 못했어요."


def _rows_to_summary(rows: list) -> str:
    lines = []
    for r in rows:
        data = json.loads(r["data"])
        t = r["entry_type"]
        date = r["date"]
        time_ = r["recorded_at"][11:16]
        if t == "diet":
            items = ", ".join(data.get("items", []))
            cal = data.get("calories")
            lines.append(
                f"{date} {time_} [식단/{data.get('meal','')}] {items}"
                + (f" {cal}kcal" if cal else "")
            )
        elif t == "weight":
            lines.append(f"{date} {time_} [몸무게] {data.get('kg')}kg {data.get('note','')}")
        elif t == "exercise":
            parts = [data.get("activity", "운동")]
            if data.get("distance_km"):
                parts.append(f"{data['distance_km']}km")
            if data.get("duration_min"):
                parts.append(f"{data['duration_min']}분")
            if data.get("calories_burned"):
                parts.append(f"{data['calories_burned']}kcal")
            lines.append(f"{date} {time_} [운동] {' '.join(parts)}")
    return "\n".join(lines) if lines else "(기록 없음)"


def _build_confirm_embed(records: list) -> discord.Embed:
    now = datetime.now()
    embed = discord.Embed(title="✅ 기록 완료", color=0x57F287, timestamp=now)
    for rec in records:
        t = rec["type"]
        data = rec["data"]
        if t == "diet":
            items = ", ".join(data.get("items", []))
            cal = data.get("calories")
            val = f"🍽️ **{data.get('meal', '')}** — {items}"
            if cal:
                val += f"\n`{cal} kcal`"
            embed.add_field(name="식단", value=val, inline=False)
        elif t == "weight":
            note = data.get("note") or ""
            embed.add_field(
                name="체중",
                value=f"⚖️ **{data.get('kg')} kg**" + (f"  `{note}`" if note else ""),
                inline=False,
            )
        elif t == "exercise":
            act = data.get("activity", "운동")
            parts = []
            if data.get("distance_km"):
                parts.append(f"📏 {data['distance_km']} km")
            if data.get("duration_min"):
                parts.append(f"⏱ {data['duration_min']} 분")
            if data.get("calories_burned"):
                parts.append(f"🔥 {data['calories_burned']} kcal")
            if data.get("pace"):
                parts.append(f"🏃 {data['pace']}")
            embed.add_field(
                name=f"운동 — {act}",
                value="\n".join(parts) or "기록됨",
                inline=False,
            )
    embed.set_footer(text=now.strftime("%H:%M 기록됨"))
    return embed


def _build_health_report(period: str = "week") -> tuple:
    """헬스 리포트 embed + 그래프 PNG BytesIO 반환. 데이터 없으면 img=None."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    if period == "today":
        start = today
        title = f"📊 오늘 헬스 리포트 ({now.strftime('%m/%d')})"
    elif period == "month":
        start = now.strftime("%Y-%m-01")
        title = f"📊 이번달 헬스 리포트 ({now.strftime('%Y.%m')})"
    else:
        start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        title = f"📊 이번주 헬스 리포트 ({start[5:]} ~ {today[5:]})"

    rows = _fetch_records(start, today)
    ex_rows = [r for r in rows if r["entry_type"] == "exercise"]
    w_rows  = [r for r in rows if r["entry_type"] == "weight"]
    d_rows  = [r for r in rows if r["entry_type"] == "diet"]

    # ── embed (텍스트 요약) ─────────────────────────────────────────────────────
    embed = discord.Embed(title=title, color=0x5865F2, timestamp=now)

    if ex_rows:
        total_dist = sum(json.loads(r["data"]).get("distance_km") or 0 for r in ex_rows)
        total_cal_burned = sum(json.loads(r["data"]).get("calories_burned") or 0 for r in ex_rows)
        ex_lines = []
        for r in ex_rows[-7:]:
            d = json.loads(r["data"])
            parts = [f"**{d.get('activity','운동')}**"]
            if d.get("distance_km"): parts.append(f"{d['distance_km']}km")
            if d.get("duration_min"): parts.append(f"{d['duration_min']}분")
            if d.get("calories_burned"): parts.append(f"{d['calories_burned']}kcal")
            ex_lines.append(f"`{r['date'][5:]}` " + " · ".join(parts))
        sp = [f"총 **{len(ex_rows)}회**"]
        if total_dist: sp.append(f"**{total_dist:.1f}km**")
        if total_cal_burned: sp.append(f"**{total_cal_burned:,}kcal** 소모")
        embed.add_field(name=f"🏃 운동 — {' · '.join(sp)}", value="\n".join(ex_lines), inline=False)
    else:
        embed.add_field(name="🏃 운동", value="기록 없음", inline=False)

    if w_rows:
        w_lines, weights = [], []
        for r in w_rows[-5:]:
            d = json.loads(r["data"])
            kg = d.get("kg")
            if kg: weights.append(kg)
            w_lines.append(f"`{r['date'][5:]}` **{kg}kg** {d.get('note','') or ''}".strip())
        w_val = "\n".join(w_lines)
        if len(weights) >= 2:
            diff = weights[-1] - weights[0]
            if diff != 0:
                w_val += f"\n{'▲' if diff > 0 else '▼'} {abs(diff):.1f}kg {'증가' if diff > 0 else '감소'}"
        embed.add_field(name="⚖️ 체중", value=w_val, inline=True)
    else:
        embed.add_field(name="⚖️ 체중", value="기록 없음", inline=True)

    if d_rows:
        by_day: dict = {}
        for r in d_rows:
            cal = json.loads(r["data"]).get("calories") or 0
            by_day[r["date"]] = by_day.get(r["date"], 0) + cal
        cal_lines = [f"`{k[5:]}` {v:,}kcal" for k, v in sorted(by_day.items())[-5:]]
        avg = sum(by_day.values()) / len(by_day) if by_day else 0
        embed.add_field(name=f"🍽️ 식단  평균 {avg:,.0f}kcal/일", value="\n".join(cal_lines), inline=True)
    else:
        embed.add_field(name="🍽️ 식단", value="기록 없음", inline=True)

    if not rows:
        embed.description = "이 기간에 기록된 데이터가 없어요."
    embed.set_footer(text="Manta Health  •  image: graph")

    # ── 그래프 PNG ─────────────────────────────────────────────────────────────
    has_data = ex_rows or w_rows or d_rows
    if not has_data:
        return embed, None

    # 서브플롯 수 결정
    n_plots = sum([bool(ex_rows), bool(w_rows), bool(d_rows)])
    fig, axes = plt.subplots(n_plots, 1, figsize=(8, 3.2 * n_plots), facecolor="#1e1f22")
    if n_plots == 1:
        axes = [axes]
    fig.subplots_adjust(hspace=0.55)

    ax_idx = 0

    # ─ 운동 막대그래프 (거리 or 시간) ─
    if ex_rows:
        ax = axes[ax_idx]; ax_idx += 1
        dates = [r["date"][5:] for r in ex_rows]
        vals = [json.loads(r["data"]).get("distance_km") or json.loads(r["data"]).get("duration_min") or 1
                for r in ex_rows]
        labels = ["km" if json.loads(r["data"]).get("distance_km") else "분" for r in ex_rows]
        bars = ax.bar(dates, vals, color="#57f287", width=0.5)
        ax.set_facecolor("#2b2d31")
        ax.tick_params(colors="#b5bac1", labelsize=8)
        ax.spines[:].set_color("#3f4147")
        ax.set_title("🏃 운동", color="#ffffff", fontsize=10, pad=6)
        ax.set_ylabel(labels[0] if labels else "", color="#b5bac1", fontsize=8)
        for bar, v, lbl in zip(bars, vals, labels):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02 * max(vals),
                    f"{v}{lbl}", ha="center", va="bottom", color="#ffffff", fontsize=7)

    # ─ 체중 선그래프 ─
    if w_rows:
        ax = axes[ax_idx]; ax_idx += 1
        dates_w = [r["date"][5:] for r in w_rows]
        kgs = [json.loads(r["data"]).get("kg") or 0 for r in w_rows]
        ax.plot(dates_w, kgs, color="#eb459e", marker="o", linewidth=2, markersize=5)
        ax.fill_between(dates_w, kgs, min(kgs) - 0.5, alpha=0.15, color="#eb459e")
        ax.set_facecolor("#2b2d31")
        ax.tick_params(colors="#b5bac1", labelsize=8)
        ax.spines[:].set_color("#3f4147")
        ax.set_title("⚖️ 체중 (kg)", color="#ffffff", fontsize=10, pad=6)
        for x, y in zip(dates_w, kgs):
            ax.annotate(f"{y}kg", (x, y), textcoords="offset points", xytext=(0, 6),
                        ha="center", color="#ffffff", fontsize=7)

    # ─ 칼로리 막대그래프 ─
    if d_rows:
        ax = axes[ax_idx]; ax_idx += 1
        by_day_cal: dict = {}
        for r in d_rows:
            cal = json.loads(r["data"]).get("calories") or 0
            by_day_cal[r["date"][5:]] = by_day_cal.get(r["date"][5:], 0) + cal
        days_c = list(sorted(by_day_cal.keys()))
        cals = [by_day_cal[d] for d in days_c]
        avg_c = sum(cals) / len(cals) if cals else 0
        bars = ax.bar(days_c, cals, color="#fee75c", width=0.5)
        ax.axhline(avg_c, color="#ff6b6b", linestyle="--", linewidth=1, label=f"평균 {avg_c:,.0f}")
        ax.set_facecolor("#2b2d31")
        ax.tick_params(colors="#b5bac1", labelsize=8)
        ax.spines[:].set_color("#3f4147")
        ax.set_title("🍽️ 칼로리 (kcal)", color="#ffffff", fontsize=10, pad=6)
        ax.legend(fontsize=7, labelcolor="#b5bac1", facecolor="#2b2d31", edgecolor="#3f4147")
        for bar, v in zip(bars, cals):
            if v:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                        f"{v:,}", ha="center", va="bottom", color="#ffffff", fontsize=7)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#1e1f22")
    plt.close(fig)
    return embed, buf
