"""
integrations/calendar_ops.py — Apple 캘린더 CRUD, 로컬 DB 싱크, 유저 프로파일 DB
"""
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta

from manta_daemon.config import _CAL_DB_PATH, _USER_DB_PATH, _SYNC_CALENDARS
import manta_daemon.state as state


# ==================== [ 로컬 캘린더 DB ] ====================

def _cal_db_conn():
    conn = sqlite3.connect(_CAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cal_db_init():
    """DB 테이블 초기화 (없으면 생성)"""
    with _cal_db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                uid          TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                date         TEXT NOT NULL,
                time         TEXT NOT NULL,
                duration_min INTEGER DEFAULT 60,
                calendar_name TEXT DEFAULT '캘린더',
                notes        TEXT DEFAULT '',
                important    INTEGER DEFAULT 0,
                synced_at    TEXT
            )
        """)
        conn.commit()


def _cal_db_upsert(uid: str, title: str, date: str, time: str,
                   duration_min: int = 60, calendar_name: str = "캘린더",
                   notes: str = "", important: int = 0):
    now = datetime.now().isoformat(timespec="seconds")
    with _cal_db_conn() as conn:
        conn.execute("""
            INSERT INTO events (uid, title, date, time, duration_min, calendar_name, notes, important, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                title=excluded.title, date=excluded.date, time=excluded.time,
                duration_min=excluded.duration_min, calendar_name=excluded.calendar_name,
                notes=excluded.notes, important=excluded.important, synced_at=excluded.synced_at
        """, (uid, title, date, time, duration_min, calendar_name, notes, important, now))
        conn.commit()


def _cal_db_delete_by_uid(uid: str):
    with _cal_db_conn() as conn:
        conn.execute("DELETE FROM events WHERE uid = ?", (uid,))
        conn.commit()


def _cal_db_find_by_keyword(keyword: str, date: str = "") -> list:
    """공백 무시 LIKE 매칭으로 로컬 DB에서 이벤트 검색"""
    kw_nospace = keyword.replace(" ", "")
    with _cal_db_conn() as conn:
        if date:
            rows = conn.execute(
                "SELECT * FROM events WHERE REPLACE(title, ' ', '') LIKE ? AND date = ?",
                (f"%{kw_nospace}%", date)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE REPLACE(title, ' ', '') LIKE ?",
                (f"%{kw_nospace}%",)
            ).fetchall()
    return [dict(r) for r in rows]


def _cal_db_delete_by_uids(uids: list) -> int:
    if not uids:
        return 0
    with _cal_db_conn() as conn:
        conn.executemany("DELETE FROM events WHERE uid = ?", [(u,) for u in uids])
        conn.commit()
    return len(uids)


def _cal_db_delete_by_keyword(keyword: str, date: str = "") -> int:
    rows = _cal_db_find_by_keyword(keyword, date)
    uids = [r["uid"] for r in rows]
    return _cal_db_delete_by_uids(uids)


def cal_db_query(start_date: str = "", end_date: str = "", keyword: str = "") -> list:
    """로컬 DB 조회. 결과: list of dict"""
    with _cal_db_conn() as conn:
        parts = []
        params = []
        if start_date:
            parts.append("date >= ?")
            params.append(start_date)
        if end_date:
            parts.append("date <= ?")
            params.append(end_date)
        if keyword:
            parts.append("(title LIKE ? OR notes LIKE ?)")
            params += [f"%{keyword}%", f"%{keyword}%"]
        where = ("WHERE " + " AND ".join(parts)) if parts else ""
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY date, time", params
        ).fetchall()
    return [dict(r) for r in rows]


def cal_db_full_sync():
    """iCloud/구글 실제 캘린더만 로컬 DB에 싱크 (과거 60일 ~ 미래 270일)."""
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    s = today - _td(days=60)
    e = today + _td(days=270)
    sy, smo, sd = s.year, s.month, s.day
    ey, emo, ed = e.year, e.month, e.day

    cal_list_literal = "{" + ", ".join(f'"{c}"' for c in _SYNC_CALENDARS) + "}"

    script = f'''
set output to ""
set startDate to current date
set year of startDate to {sy}
set month of startDate to {smo}
set day of startDate to {sd}
set hours of startDate to 0
set minutes of startDate to 0
set seconds of startDate to 0
set endDate to current date
set year of endDate to {ey}
set month of endDate to {emo}
set day of endDate to {ed}
set hours of endDate to 23
set minutes of endDate to 59
set seconds of endDate to 59
tell application "Calendar"
    repeat with calName in {cal_list_literal}
        try
            set cal to first calendar whose name is calName
            set evts to (every event of cal whose start date >= startDate and start date <= endDate)
            repeat with evt in evts
                try
                    set evtUID to uid of evt
                    set evtTitle to summary of evt
                    set evtStart to start date of evt
                    set evtEnd to end date of evt
                    set yr to year of evtStart as integer
                    set mo to month of evtStart as integer
                    set dy to day of evtStart as integer
                    set hr to hours of evtStart as integer
                    set mi to minutes of evtStart as integer
                    set durSec to (evtEnd - evtStart) as integer
                    set durMin to durSec div 60
                    set dateStr to (yr as string) & "-" & text -2 thru -1 of ("0" & (mo as string)) & "-" & text -2 thru -1 of ("0" & (dy as string))
                    set timeStr to text -2 thru -1 of ("0" & (hr as string)) & ":" & text -2 thru -1 of ("0" & (mi as string))
                    set output to output & evtUID & "|||" & calName & "|||" & evtTitle & "|||" & dateStr & "|||" & timeStr & "|||" & (durMin as string) & "|||" & "\\n"
                end try
            end repeat
        end try
    end repeat
end tell
return output
'''
    try:
        raw = subprocess.check_output(["osascript", "-e", script], timeout=150).decode("utf-8").strip()
    except Exception as e:
        print(f"[CalDB] 풀싱크 osascript 오류: {e}")
        return 0

    cal_db_init()
    count = 0
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) < 6:
            continue
        uid, cal_name, title, date, time_, dur = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        notes = parts[6] if len(parts) > 6 else ""
        try:
            dur_min = int(dur)
        except Exception:
            dur_min = 60
        imp = 1 if title.startswith("⭐") else 0
        _cal_db_upsert(uid, title, date, time_, dur_min, cal_name, notes, imp)
        count += 1

    print(f"[CalDB] 풀싱크 완료: {count}개 이벤트")
    return count


# ==================== [ 유저 프로파일 DB ] ====================

def _user_db_conn():
    conn = sqlite3.connect(_USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def user_db_init():
    with _user_db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                topic      TEXT PRIMARY KEY,
                count      INTEGER DEFAULT 1,
                last_seen  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()


def _user_db_add_topic(topic: str):
    now = datetime.now().isoformat(timespec="seconds")
    with _user_db_conn() as conn:
        conn.execute("""
            INSERT INTO topics (topic, count, last_seen) VALUES (?, 1, ?)
            ON CONFLICT(topic) DO UPDATE SET count=count+1, last_seen=excluded.last_seen
        """, (topic, now))
        conn.commit()


def _user_db_set_pref(key: str, value: str):
    now = datetime.now().isoformat(timespec="seconds")
    with _user_db_conn() as conn:
        conn.execute("""
            INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value, now))
        conn.commit()


def load_user_profile_summary() -> str:
    """시스템 프롬프트에 삽입할 유저 프로파일 요약"""
    try:
        with _user_db_conn() as conn:
            topics = conn.execute(
                "SELECT topic, count FROM topics ORDER BY count DESC LIMIT 12"
            ).fetchall()
            prefs = conn.execute(
                "SELECT key, value FROM preferences ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
        if not topics and not prefs:
            return ""
        lines = ["\n\n[👤 유저 프로파일 — 이걸 참고해서 맞춤 응답해줘]"]
        if topics:
            top_str = ", ".join(f"{r['topic']}({r['count']})" for r in topics)
            lines.append(f"자주 묻는 주제: {top_str}")
        if prefs:
            for r in prefs:
                lines.append(f"• {r['key']}: {r['value']}")
        return "\n".join(lines)
    except Exception:
        return ""


# ==================== [ 캘린더 DB 기반 조회 포맷터 ] ====================

def _format_cal_rows(rows: list, date_label: str, is_single_day: bool) -> str:
    from datetime import date as _date
    _DAY_SHORT = {0:"(월)",1:"(화)",2:"(수)",3:"(목)",4:"(금)",5:"(토)",6:"(일)"}

    if not rows:
        return f"📭 {date_label} 일정이 없어요!"

    if is_single_day:
        time_groups: dict = {}
        for r in rows:
            t_ = r["time"][:5]
            time_groups.setdefault(t_, []).append(r["title"])
        items = [f"• `{t}` {', '.join(titles)}" for t, titles in time_groups.items()]
        return f"📅 **{date_label} 일정**\n\n" + "\n".join(items)
    else:
        merged: dict = {}
        order = []
        for r in rows:
            try:
                y, mo, d = int(r["date"][:4]), int(r["date"][5:7]), int(r["date"][8:10])
                dow = _DAY_SHORT[_date(y, mo, d).weekday()]
                dl = f"{mo}/{d}{dow}"
            except Exception:
                dl = r["date"]
            t_ = r["time"][:5]
            key = (dl, t_)
            if key not in merged:
                merged[key] = []
                order.append(key)
            merged[key].append(r["title"])
        table = "| 날짜 | 시간 | 일정 |\n|------|------|------|\n"
        for (dl, t_) in order:
            titles = ", ".join(merged[(dl, t_)])
            table += f"| {dl} | {t_} | {titles} |\n"
        return f"📅 **{date_label} 일정**\n\n{table}"


def get_apple_calendar(days: int = 1, start_date: str = "", end_date: str = "",
                        keyword: str = "") -> str:
    """로컬 캘린더 DB에서 일정 조회 (iCloud 싱크 완료 후 빠름)."""
    try:
        from datetime import date as _date
        today = _date.today()

        if start_date and end_date:
            is_single_day = (start_date == end_date)
            date_label = "오늘" if is_single_day and start_date == today.strftime("%Y-%m-%d") else (
                start_date if is_single_day else f"{start_date} ~ {end_date}"
            )
        else:
            is_single_day = (days == 1)
            start_date = today.strftime("%Y-%m-%d")
            end_date = (today + timedelta(days=days - 1)).strftime("%Y-%m-%d")
            date_label = "오늘" if days == 1 else f"{days}일간"

        if keyword:
            date_label = f"{date_label} ('{keyword}' 필터)"

        rows = cal_db_query(start_date=start_date, end_date=end_date, keyword=keyword)
        return _format_cal_rows(rows, date_label, is_single_day)
    except Exception as e:
        return f"😥 캘린더 조회 오류: {e}"


def delete_apple_calendar_event(title_keyword: str, date_str: str = "") -> str:
    """제목 키워드(+ 날짜)로 캘린더 일정 삭제."""
    try:
        import re as _re

        matched = _cal_db_find_by_keyword(title_keyword, date_str)
        if not matched:
            return f"📭 '{title_keyword}' 일정을 찾지 못했어요."

        total_deleted = 0
        deleted_uids = []
        for row in matched:
            exact_title = row["title"]
            ev_date = row["date"]
            m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", ev_date)
            if not m:
                continue
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            escaped_title = exact_title.replace('"', '\\"')
            script = f'''
        set deletedCount to 0
        set startDate to current date
        set year of startDate to {y}
        set month of startDate to {mo}
        set day of startDate to {d}
        set hours of startDate to 0
        set minutes of startDate to 0
        set seconds of startDate to 0
        set endDate to startDate + 1 * days
        tell application "Calendar"
            repeat with cal in calendars
                set evts to (every event of cal whose summary is "{escaped_title}" and start date >= startDate and start date < endDate)
                repeat with evt in evts
                    delete evt
                    set deletedCount to deletedCount + 1
                end repeat
            end repeat
        end tell
        return deletedCount as string
        '''
            try:
                result = subprocess.check_output(["osascript", "-e", script], timeout=15).decode().strip()
                count = int(result) if result.isdigit() else 0
                if count > 0:
                    total_deleted += count
                    deleted_uids.append(row["uid"])
            except Exception as e:
                print(f"[캘린더 삭제] '{exact_title}' osascript 오류: {e}")

        if total_deleted == 0:
            return f"📭 '{title_keyword}' 일정을 찾지 못했어요."

        _cal_db_delete_by_uids(deleted_uids)
        titles_str = ", ".join(r["title"] for r in matched if r["uid"] in deleted_uids)
        return f"🗑️ {total_deleted}개 삭제 완료! ({titles_str})"
    except subprocess.TimeoutExpired:
        return "⏱️ 캘린더 응답 시간 초과."
    except Exception as e:
        return f"😥 일정 삭제 오류: {e}"


def _parse_date_str(date_str: str):
    """'YYYY-MM-DD' → (year, month, day). 실패 시 None."""
    import re as _re
    from datetime import date as _date
    date_clean = date_str.strip()
    m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", date_clean)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    today = _date.today()
    m2 = _re.search(r"(\d{1,2})월\s*(\d{1,2})일", date_clean)
    if m2:
        mo, d = int(m2.group(1)), int(m2.group(2))
        yr = today.year
        if mo < today.month or (mo == today.month and d < today.day):
            yr += 1
        return yr, mo, d
    return None


def _add_single_calendar_event(title, year, month, day, hour, minute,
                                duration_min, calendar_name, notes, important):
    import re as _re
    event_title = f"⭐ {title}" if important else title
    cal_name = calendar_name if calendar_name else "캘린더"
    cal_clause = f'set targetCal to first calendar whose name is "{cal_name}"'
    notes_line = f'set description of newEvent to "{notes}"' if notes else ""
    alarm_lines = ""
    if important:
        alarm_lines = (
            "make new display alarm at end of newEvent "
            "with properties {trigger interval: -10080}\n"
            "            make new display alarm at end of newEvent "
            "with properties {trigger interval: -1440}"
        )
    script = f'''
    tell application "Calendar"
        {cal_clause}
        set startDate to current date
        set year of startDate to {year}
        set month of startDate to {month}
        set day of startDate to {day}
        set hours of startDate to {hour}
        set minutes of startDate to {minute}
        set seconds of startDate to 0
        set newEvent to make new event at end of events of targetCal with properties {{summary:"{event_title}", start date:startDate, end date:startDate + ({duration_min} * minutes)}}
        {notes_line}
        {alarm_lines}
    end tell
    return "done"
    '''
    subprocess.check_output(["osascript", "-e", script], timeout=15)
    date_s = f"{year:04d}-{month:02d}-{day:02d}"
    time_s = f"{hour:02d}:{minute:02d}"
    uid_tmp = f"local_{event_title}_{date_s}_{time_s}"
    _cal_db_upsert(uid_tmp, event_title, date_s, time_s, duration_min,
                   cal_name, notes, 1 if important else 0)


def delete_all_calendar_events_on_date(date_str: str) -> str:
    """특정 날짜의 모든 캘린더 일정 삭제."""
    try:
        import re as _re
        from datetime import date as _date
        if not date_str:
            date_str = _date.today().strftime("%Y-%m-%d")
        m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str.strip())
        if not m:
            return "❌ 날짜 형식이 올바르지 않아요. YYYY-MM-DD로 넣어주세요."
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))

        rows = cal_db_query(start_date=date_str, end_date=date_str)
        if not rows:
            return f"📭 {mo}월 {d}일에 삭제할 일정이 없어요."

        script = f'''
set deletedCount to 0
set startDate to current date
set year of startDate to {y}
set month of startDate to {mo}
set day of startDate to {d}
set hours of startDate to 0
set minutes of startDate to 0
set seconds of startDate to 0
set endDate to startDate + 1 * days
tell application "Calendar"
    repeat with cal in calendars
        set evts to (every event of cal whose start date >= startDate and start date < endDate)
        repeat with evt in evts
            delete evt
            set deletedCount to deletedCount + 1
        end repeat
    end repeat
end tell
return deletedCount as string
'''
        result = subprocess.check_output(["osascript", "-e", script], timeout=20).decode().strip()
        count = int(result) if result.isdigit() else 0

        uids = [r["uid"] for r in rows]
        _cal_db_delete_by_uids(uids)

        titles = ", ".join(r["title"] for r in rows)
        return f"🗑️ {mo}월 {d}일 일정 {count}개 삭제 완료! ({titles})"
    except subprocess.TimeoutExpired:
        return "⏱️ 캘린더 응답 시간 초과."
    except Exception as e:
        return f"😥 전체 삭제 오류: {e}"


def add_apple_calendar_event(title: str, date_str: str, time_str: str = "09:00",
                              duration_min: int = 60, calendar_name: str = "",
                              notes: str = "", important: bool = False,
                              end_date: str = "") -> str:
    """Apple 캘린더에 일정 추가."""
    try:
        import re as _re
        from datetime import date as _date, timedelta as _td
        parsed = _parse_date_str(date_str)
        if not parsed:
            return f"❌ 날짜 파싱 실패: '{date_str}'. YYYY-MM-DD 형식으로 다시 알려줘요."
        start_y, start_m, start_d = parsed

        tm = _re.match(r"(\d{1,2}):(\d{2})", time_str.strip())
        hour = int(tm.group(1)) if tm else 9
        minute = int(tm.group(2)) if tm else 0
        end_h = hour + duration_min // 60
        end_min = minute + duration_min % 60
        if end_min >= 60:
            end_h += 1; end_min -= 60

        event_title = f"⭐ {title}" if important else title
        imp_str = "\n⭐ **중요 일정** — 7일 전 / 1일 전 알림 설정됨" if important else ""

        if not end_date:
            _add_single_calendar_event(title, start_y, start_m, start_d,
                                       hour, minute, duration_min, calendar_name, notes, important)
            return (f"✅ **캘린더 일정 추가 완료!**\n"
                    f"📌 {event_title}\n"
                    f"📅 {start_y}/{start_m}/{start_d} {hour:02d}:{minute:02d} ~ {end_h:02d}:{end_min:02d}"
                    + (f"\n📝 {notes}" if notes else "") + imp_str)

        parsed_end = _parse_date_str(end_date)
        if not parsed_end:
            return f"❌ 종료 날짜 파싱 실패: '{end_date}'"
        end_y, end_m, end_d = parsed_end
        cur = _date(start_y, start_m, start_d)
        last = _date(end_y, end_m, end_d)
        if cur > last:
            return "❌ 시작 날짜가 종료 날짜보다 늦어요."
        added = []
        while cur <= last:
            _add_single_calendar_event(title, cur.year, cur.month, cur.day,
                                       hour, minute, duration_min, calendar_name, notes, important)
            added.append(f"{cur.month}/{cur.day}")
            cur += _td(days=1)
        dates_str = ", ".join(added)
        return (f"✅ **캘린더 일정 추가 완료! ({len(added)}일)**\n"
                f"📌 {event_title}\n"
                f"📅 {dates_str} {hour:02d}:{minute:02d} ~ {end_h:02d}:{end_min:02d}"
                + (f"\n📝 {notes}" if notes else "") + imp_str)
    except subprocess.TimeoutExpired:
        return "⏱️ 캘린더 응답 시간 초과."
    except Exception as e:
        return f"😥 일정 추가 오류: {e}"


def modify_apple_calendar_event(title_keyword: str, new_title: str = "",
                                new_date: str = "", new_time: str = "",
                                new_duration_min: int = 0,
                                search_date: str = "") -> str:
    """기존 캘린더 일정 수정 (제목/날짜/시간 변경)."""
    try:
        import re as _re

        matched = _cal_db_find_by_keyword(title_keyword, search_date)
        if not matched:
            return f"❌ '{title_keyword}' 키워드로 일정을 찾지 못했어요."
        exact_title = matched[0]["title"]
        escaped_keyword = exact_title.replace('"', '\\"')

        if search_date:
            m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", search_date)
            if m:
                sy, smo, sd = int(m.group(1)), int(m.group(2)), int(m.group(3))
                date_filter = f'''
        set filterStart to current date
        set year of filterStart to {sy}
        set month of filterStart to {smo}
        set day of filterStart to {sd}
        set hours of filterStart to 0
        set minutes of filterStart to 0
        set seconds of filterStart to 0
        set filterEnd to filterStart + 1 * days
        set matchEvts to (every event of cal whose summary is "{escaped_keyword}" and start date >= filterStart and start date < filterEnd)'''
            else:
                date_filter = f'set matchEvts to (every event of cal whose summary is "{escaped_keyword}")'
        else:
            date_filter = f'set matchEvts to (every event of cal whose summary is "{escaped_keyword}")'

        mod_lines = []
        if new_title:
            mod_lines.append(f'set summary of evt to "{new_title}"')
        if new_date:
            pm = _re.match(r"(\d{4})-(\d{2})-(\d{2})", new_date)
            if pm:
                dy, dmo, dd = int(pm.group(1)), int(pm.group(2)), int(pm.group(3))
                mod_lines.append(f'''
                set sd to start date of evt
                set year of sd to {dy}
                set month of sd to {dmo}
                set day of sd to {dd}
                set start date of evt to sd''')
        if new_time:
            tm = _re.match(r"(\d{1,2}):(\d{2})", new_time)
            if tm:
                nh, nm = int(tm.group(1)), int(tm.group(2))
                mod_lines.append(f'''
                set sd2 to start date of evt
                set hours of sd2 to {nh}
                set minutes of sd2 to {nm}
                set seconds of sd2 to 0
                set start date of evt to sd2''')
        if new_duration_min > 0:
            mod_lines.append(f'set end date of evt to (start date of evt) + ({new_duration_min} * minutes)')
        if not mod_lines:
            return "❌ 수정할 내용이 없어요. new_title / new_date / new_time 중 하나는 넣어줘요."

        mod_block = "\n                ".join(mod_lines)
        script = f'''
        set modCount to 0
        tell application "Calendar"
            repeat with cal in calendars
                {date_filter}
                repeat with evt in matchEvts
                    {mod_block}
                    set modCount to modCount + 1
                end repeat
            end repeat
        end tell
        return modCount as string
        '''
        result = subprocess.check_output(["osascript", "-e", script], timeout=15).decode().strip()
        count = int(result) if result.isdigit() else 0
        if count == 0:
            return f"❌ '{title_keyword}' 키워드로 일정을 찾지 못했어요."

        for row in matched:
            _cal_db_upsert(
                row["uid"],
                new_title if new_title else row["title"],
                new_date if new_date else row["date"],
                new_time if new_time else row["time"],
                new_duration_min if new_duration_min > 0 else row["duration_min"],
                row["calendar_name"], row["notes"], row["important"],
            )

        changes = []
        if new_title: changes.append(f"제목 → {new_title}")
        if new_date:  changes.append(f"날짜 → {new_date}")
        if new_time:  changes.append(f"시간 → {new_time}")
        if new_duration_min: changes.append(f"길이 → {new_duration_min}분")
        return f"✅ **'{exact_title}' 수정 완료!** ({count}개)\n" + "\n".join(f"  • {c}" for c in changes)
    except subprocess.TimeoutExpired:
        return "⏱️ 캘린더 응답 시간 초과."
    except Exception as e:
        return f"😥 일정 수정 오류: {e}"
