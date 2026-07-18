"""
tasks/system_status.py — #system 채널 시스템 상태 이미지 주기적 갱신
"""
import asyncio
import io
import os
from datetime import datetime

import discord
import psutil

import manta_daemon.state as state


def _build_system_embed() -> discord.Embed:
    """#system 채널용 시스템 상태 임베드"""
    import calendar as _cal
    now = datetime.now()

    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    ram_used = mem.used / (1024**3)
    ram_total = mem.total / (1024**3)
    ram_pct = mem.percent

    _data_vol = "/System/Volumes/Data"
    disk = psutil.disk_usage(_data_vol if os.path.isdir(_data_vol) else "/")
    disk_used = disk.used / (1024**3)
    disk_total = disk.total / (1024**3)

    battery = psutil.sensors_battery()
    if battery:
        pct = battery.percent
        if battery.power_plugged and pct >= 99:
            bat_str = f"⚡ 완충 `{pct:.0f}%`"
        elif battery.power_plugged:
            bat_str = f"🔌 충전중 `{pct:.0f}%`"
        else:
            bat_str = f"{'🪫' if pct < 20 else '🔋'} `{pct:.0f}%`"
    else:
        bat_str = "정보 없음"

    DAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
    date_str = f"{now.month}월 {now.day}일 ({DAY_KR[now.weekday()]}) {now.strftime('%H:%M')}"

    embed = discord.Embed(title="🖥️ 시스템 상태", color=0x2b2d31, timestamp=now)
    embed.add_field(name="📅 날짜", value=date_str, inline=False)
    embed.add_field(name="CPU", value=f"`{cpu:.0f}%`", inline=True)
    embed.add_field(name="RAM", value=f"`{ram_used:.1f}/{ram_total:.0f}GB` ({ram_pct:.0f}%)", inline=True)
    embed.add_field(name="배터리", value=bat_str, inline=True)
    embed.add_field(name="디스크", value=f"`{disk_used:.0f}/{disk_total:.0f}GB`", inline=True)
    embed.set_footer(text="마지막 업데이트")
    return embed


_net_prev: dict = {"bytes_recv": 0, "bytes_sent": 0, "time": 0.0}


def _render_system_card() -> io.BytesIO:
    """psutil 데이터 → PIL로 Discord 스타일 시스템 카드 렌더링 → PNG BytesIO"""
    import time as _time
    from PIL import Image, ImageDraw, ImageFont

    global _net_prev

    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    _dv = "/System/Volumes/Data"
    disk = psutil.disk_usage(_dv if os.path.isdir(_dv) else "/")
    bat = psutil.sensors_battery()

    net_now = psutil.net_io_counters()
    t_now = _time.time()
    dt = t_now - _net_prev["time"] if _net_prev["time"] else 60.0
    dl_bps = max(0.0, (net_now.bytes_recv - _net_prev["bytes_recv"]) / dt) if _net_prev["time"] else 0.0
    ul_bps = max(0.0, (net_now.bytes_sent - _net_prev["bytes_sent"]) / dt) if _net_prev["time"] else 0.0
    _net_prev = {"bytes_recv": net_now.bytes_recv, "bytes_sent": net_now.bytes_sent, "time": t_now}

    def _fmt_speed(bps: float) -> str:
        if bps < 1024: return f"{bps:.0f} B/s"
        if bps < 1024 ** 2: return f"{bps / 1024:.1f} KB/s"
        return f"{bps / 1024 ** 2:.1f} MB/s"

    def _fmt_mem(b: int) -> str:
        if b < 1024 ** 2: return f"{b // 1024} KB"
        if b < 1024 ** 3: return f"{b // 1024 ** 2} MB"
        return f"{b / 1024 ** 3:.1f} GB"

    top_procs: list = []
    for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
        try:
            rss = p.info["memory_info"].rss
            if rss > 0:
                top_procs.append((p.info["name"] or "?", rss, p.info["cpu_percent"] or 0.0))
        except Exception:
            pass
    top_procs.sort(key=lambda x: x[1], reverse=True)
    top_procs = top_procs[:3]

    load1, load5, load15 = os.getloadavg()

    C_BG       = (30, 31, 34)
    C_CARD     = (43, 45, 49)
    C_HEADER   = (35, 39, 42)
    C_DIV      = (42, 43, 48)
    C_BAR_BG   = (52, 54, 60)
    C_TEXT     = (219, 222, 225)
    C_MUTED    = (148, 155, 164)
    C_BLURPLE  = (88, 101, 242)
    C_GREEN    = (35, 165, 90)
    C_YELLOW   = (240, 178, 50)
    C_PINK     = (235, 69, 158)

    _FP = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    try:
        fn_val   = ImageFont.truetype(_FP, 24)
        fn_label = ImageFont.truetype(_FP, 11)
        fn_sub   = ImageFont.truetype(_FP, 12)
        fn_hdr   = ImageFont.truetype(_FP, 13)
        fn_proc  = ImageFont.truetype(_FP, 13)
    except Exception:
        fn_val = fn_label = fn_sub = fn_hdr = fn_proc = ImageFont.load_default()

    W   = 380
    PAD = 16
    COL_GAP = 14
    COL_W = (W - PAD * 2 - COL_GAP) // 2

    HEADER_H  = 42
    STAT_ROW_H = 76
    NET_H     = 52
    PROC_ROW_H = 28

    H = (HEADER_H + 1
         + PAD + STAT_ROW_H + 10 + STAT_ROW_H + PAD
         + 1 + NET_H
         + 1 + 10 + 16 + PROC_ROW_H * len(top_procs) + PAD)

    img = Image.new("RGB", (W, H), C_CARD)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, HEADER_H], fill=C_HEADER)
    draw.ellipse([PAD, HEADER_H // 2 - 5, PAD + 10, HEADER_H // 2 + 5], fill=C_GREEN)
    draw.text((PAD + 16, HEADER_H // 2 - 8), "M4 Pro 시스템", font=fn_hdr, fill=C_TEXT)
    ts = datetime.now().strftime("%H:%M:%S")
    ts_w = draw.textlength(ts, font=fn_label)
    draw.text((W - PAD - ts_w, HEADER_H // 2 - 6), ts, font=fn_label, fill=C_MUTED)
    draw.rectangle([0, HEADER_H, W, HEADER_H + 1], fill=C_DIV)

    def _draw_stat(x: int, y: int, label: str, val_str: str, pct: float, color: tuple, sub: str):
        draw.text((x, y), label, font=fn_label, fill=C_MUTED)
        draw.text((x, y + 14), val_str, font=fn_val, fill=color)
        by = y + 46
        bw = COL_W - 4
        draw.rounded_rectangle([x, by, x + bw, by + 5], radius=2, fill=C_BAR_BG)
        fw = max(4, int(bw * min(pct, 100) / 100))
        draw.rounded_rectangle([x, by, x + fw, by + 5], radius=2, fill=color)
        draw.text((x, by + 9), sub, font=fn_sub, fill=C_MUTED)

    if bat:
        bp = bat.percent
        if bat.power_plugged and bp >= 99:
            bat_str, bat_col, bat_sub = "100%", C_YELLOW, "⚡ 완충 AC"
        elif bat.power_plugged:
            bat_str, bat_col, bat_sub = f"{bp:.0f}%", C_YELLOW, "🔌 충전중"
        else:
            bat_str, bat_col = f"{bp:.0f}%", (C_PINK if bp < 20 else C_YELLOW)
            bat_sub = "방전중"
    else:
        bp, bat_str, bat_col, bat_sub = 0, "—", C_MUTED, ""

    disk_pct = disk.percent
    disk_sub = f"{disk.used / 1024**3:.0f} / {disk.total / 1024**3:.0f} GB"
    mem_sub  = f"{mem.used / 1024**3:.1f} / {mem.total / 1024**3:.0f} GiB"

    y0 = HEADER_H + 1 + PAD
    _draw_stat(PAD,             y0, "CPU",  f"{cpu_pct:.0f}%",   cpu_pct,   C_BLURPLE, f"로드 {load1:.2f}")
    _draw_stat(PAD + COL_W + COL_GAP, y0, "RAM",  f"{mem.percent:.0f}%", mem.percent, C_GREEN,   mem_sub)

    y1 = y0 + STAT_ROW_H + 10
    _draw_stat(PAD,             y1, "배터리", bat_str,             bp,        bat_col,   bat_sub)
    _draw_stat(PAD + COL_W + COL_GAP, y1, "디스크", f"{disk_pct:.0f}%", disk_pct, C_BLURPLE, disk_sub)

    ny = y1 + STAT_ROW_H + PAD
    draw.rectangle([PAD, ny, W - PAD, ny + 1], fill=C_DIV)
    ny += 12
    net_items = [("↓ 다운", _fmt_speed(dl_bps)), ("↑ 업", _fmt_speed(ul_bps)),
                 ("로드 15m", f"{load15:.2f}")]
    nc_w = (W - PAD * 2) // 3
    for i, (lbl, val) in enumerate(net_items):
        nx = PAD + i * nc_w + nc_w // 2
        lw = draw.textlength(lbl, font=fn_label)
        vw = draw.textlength(val, font=fn_sub)
        draw.text((nx - lw / 2, ny), lbl, font=fn_label, fill=C_MUTED)
        draw.text((nx - vw / 2, ny + 14), val, font=fn_sub, fill=C_TEXT)

    py = ny + NET_H - 8
    draw.rectangle([PAD, py, W - PAD, py + 1], fill=C_DIV)
    py += 10
    draw.text((PAD, py), "상위 프로세스", font=fn_label, fill=C_MUTED)
    py += 16

    for pname, pmem, pcpu in top_procs:
        short = pname[:24] if len(pname) > 24 else pname
        draw.text((PAD, py + 6), short, font=fn_proc, fill=C_TEXT)

        mem_s = _fmt_mem(pmem)
        mw = draw.textlength(mem_s, font=fn_sub)
        draw.text((W - PAD - 58 - mw - 6, py + 7), mem_s, font=fn_sub, fill=C_MUTED)

        cpu_s = f"{pcpu:.1f}%"
        badge_col = C_BLURPLE if pcpu < 10 else (C_YELLOW if pcpu < 50 else C_PINK)
        badge_bg  = tuple(max(0, c // 4) for c in badge_col)
        draw.rounded_rectangle([W - PAD - 56, py + 3, W - PAD, py + PROC_ROW_H - 4],
                                radius=4, fill=badge_bg)
        cw = draw.textlength(cpu_s, font=fn_label)
        draw.text((W - PAD - 28 - cw / 2, py + 7), cpu_s, font=fn_label, fill=badge_col)

        py += PROC_ROW_H

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


async def _system_embed_task(channel):
    """#system 채널에 시스템 카드를 60초마다 교체"""
    try:
        async for msg in channel.history(limit=20):
            if msg.author == state.bot.user:
                await msg.delete()
    except Exception:
        pass
    state._system_embed_msg = None

    while True:
        try:
            buf = await asyncio.get_running_loop().run_in_executor(None, _render_system_card)
            if state._system_embed_msg:
                try:
                    await state._system_embed_msg.delete()
                except Exception:
                    pass
            state._system_embed_msg = await channel.send(
                file=discord.File(buf, filename="system_status.png")
            )
        except Exception as e:
            print(f"[system 채널] 시스템 카드 실패: {e}")
            try:
                embed = await asyncio.get_running_loop().run_in_executor(None, _build_system_embed)
                if state._system_embed_msg:
                    try:
                        await state._system_embed_msg.delete()
                    except Exception:
                        pass
                state._system_embed_msg = await channel.send(embed=embed)
            except Exception:
                pass
        await asyncio.sleep(60)
