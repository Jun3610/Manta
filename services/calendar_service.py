import sqlite3
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta, date as _date

from infrastructure.database import CALENDAR_DB_PATH
from providers.apple_calendar_provider import AppleCalendarProvider

logger = logging.getLogger(__name__)

class CalendarService:
    """
    비즈니스 로직을 담당하는 Service.
    Provider를 통해 Mac 캘린더를 CRUD하고, 성공 시 SQLite 미러(Cache) DB를 업데이트합니다.
    조회는 SQLite에서 빠르게 수행합니다.
    """
    def __init__(self):
        self.provider = AppleCalendarProvider()

    def _db_conn(self):
        conn = sqlite3.connect(CALENDAR_DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _upsert_to_db(self, uid: str, title: str, date: str, time: str,
                      duration_min: int, calendar_name: str, notes: str, important: int):
        now = datetime.now().isoformat(timespec="seconds")
        with self._db_conn() as conn:
            conn.execute("""
                INSERT INTO events (uid, title, date, time, duration_min, calendar_name, notes, important, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    title=excluded.title, date=excluded.date, time=excluded.time,
                    duration_min=excluded.duration_min, calendar_name=excluded.calendar_name,
                    notes=excluded.notes, important=excluded.important, synced_at=excluded.synced_at
            """, (uid, title, date, time, duration_min, calendar_name, notes, important, now))
            conn.commit()

    def _delete_from_db(self, uid: str):
        with self._db_conn() as conn:
            conn.execute("DELETE FROM events WHERE uid = ?", (uid,))
            conn.commit()

    def search_events(self, start_date: str = "", end_date: str = "", keyword: str = "", mode: str = "cache") -> List[Dict]:
        """로컬 SQLite 미러 DB에서 일정을 빠르게 조회합니다. mode='live'일 경우 Mac 캘린더 전체 동기화 후 반환합니다."""
        if mode == "live":
            self.sync_all_from_provider()

        with self._db_conn() as conn:
            parts = []
            params = []
            if start_date:
                parts.append("date >= ?")
                params.append(start_date)
            if end_date:
                parts.append("date <= ?")
                params.append(end_date)
            if keyword:
                kw_nospace = keyword.replace(" ", "")
                parts.append("(REPLACE(title, ' ', '') LIKE ? OR notes LIKE ?)")
                params += [f"%{kw_nospace}%", f"%{keyword}%"]
                
            where = ("WHERE " + " AND ".join(parts)) if parts else ""
            rows = conn.execute(
                f"SELECT * FROM events {where} ORDER BY date, time LIMIT 50", params
            ).fetchall()
        return [dict(r) for r in rows]

    def get_event_by_uid(self, uid: str) -> Optional[Dict]:
        """UID로 단일 일정의 모든 정보(메모 등 포함)를 상세 조회합니다."""
        with self._db_conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE uid = ?", (uid,)).fetchone()
            if row:
                return dict(row)
            return None

    def create_event(self, title: str, start_dt: datetime, end_dt: datetime,
                     calendar_name: str = "캘린더", notes: str = "", important: bool = False) -> Dict:
        """Mac 캘린더에 일정을 생성하고 로컬 DB를 업데이트합니다."""
        # 1. Provider를 통해 Mac 캘린더에 생성
        event_data = self.provider.create_event(title, start_dt, end_dt, calendar_name, notes, important)
        
        # 2. 로컬 SQLite DB 업데이트
        self._upsert_to_db(
            uid=event_data["uid"],
            title=event_data["title"],
            date=event_data["date"],
            time=event_data["time"],
            duration_min=event_data["duration_min"],
            calendar_name=event_data["calendar_name"],
            notes=event_data["notes"],
            important=event_data["important"]
        )
        return event_data

    def delete_event(self, uid: str) -> bool:
        """UID를 통해 일정을 삭제합니다."""
        # 1. Mac 캘린더에서 삭제
        success = self.provider.delete_event(uid)
        
        # 2. 로컬 DB에서 삭제
        if success:
            self._delete_from_db(uid)
            
        return success

    def modify_event(self, uid: str, new_title: Optional[str] = None, 
                     new_start_dt: Optional[datetime] = None, new_end_dt: Optional[datetime] = None) -> bool:
        """UID를 통해 일정을 수정합니다."""
        # 1. Mac 캘린더에서 수정
        success = self.provider.modify_event(uid, new_title, new_start_dt, new_end_dt)
        
        # 2. 로컬 DB에서 해당 일정 조회 후 업데이트
        if success:
            with self._db_conn() as conn:
                row = conn.execute("SELECT * FROM events WHERE uid = ?", (uid,)).fetchone()
                if row:
                    updated_title = new_title if new_title else row["title"]
                    updated_date = new_start_dt.strftime("%Y-%m-%d") if new_start_dt else row["date"]
                    updated_time = new_start_dt.strftime("%H:%M") if new_start_dt else row["time"]
                    if new_start_dt and new_end_dt:
                        updated_dur = int((new_end_dt - new_start_dt).total_seconds() / 60)
                    else:
                        updated_dur = row["duration_min"]
                    
                    self._upsert_to_db(
                        uid=uid, title=updated_title, date=updated_date, time=updated_time,
                        duration_min=updated_dur, calendar_name=row["calendar_name"],
                        notes=row["notes"], important=row["important"]
                    )
        return success

    def sync_all_from_provider(self):
        """Provider로부터 일정들을 가져와 로컬 DB를 Truncate + Rebuild (전면 동기화) 합니다."""
        today = _date.today()
        start_dt = datetime(today.year, today.month, today.day) - timedelta(days=60)
        end_dt = datetime(today.year, today.month, today.day) + timedelta(days=270)
        
        events = self.provider.fetch_all_events(start_dt, end_dt)
        
        with self._db_conn() as conn:
            # 1. 기존 데이터 모두 삭제 (Truncate 방식)
            conn.execute("DELETE FROM events")
            
            # 2. 새로 가져온 데이터로 모두 재삽입 (Rebuild)
            now = datetime.now().isoformat(timespec="seconds")
            insert_data = []
            for ev in events:
                insert_data.append((
                    ev["uid"], ev["title"], ev["date"], ev["time"],
                    ev["duration_min"], ev["calendar_name"], ev["notes"], ev["important"], now
                ))
            
            if insert_data:
                conn.executemany("""
                    INSERT INTO events (uid, title, date, time, duration_min, calendar_name, notes, important, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, insert_data)
            conn.commit()
            
        logger.info(f"[CalendarService] Full Sync 완료: {len(events)}개 일정 Rebuild 됨.")
        return len(events)

    # -------------------------------------------------------------------------
    # LangGraph 파이프라인 전용 메서드 (SPEC 2.2절, bulk_update 그래프 사용)
    # -------------------------------------------------------------------------

    async def get_events_in_range(
        self, start_date: _date, end_date: _date
    ) -> List[Dict]:
        """
        날짜 범위(date 객체)로 SQLite 캐시에서 이벤트를 조회한다.

        LangGraph Filter 노드에서 호출. Python datetime.now() 기준으로
        환산된 절대 날짜 범위를 받는다 (SPEC 2.4절 — LLM 날짜 추론 금지).

        Args:
            start_date: 조회 시작 날짜 (포함).
            end_date:   조회 종료 날짜 (포함).

        Returns:
            이벤트 딕셔너리 목록. 각 항목에 uid, title, date, time,
            calendar_name(tag), duration_min 포함.
        """
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()
        events = self.search_events(start_date=start_str, end_date=end_str)

        # calendar_name 을 tag 필드로도 노출 (필터 노드 편의)
        for ev in events:
            ev.setdefault("tag", ev.get("calendar_name", ""))

        logger.debug(
            "[CalendarService.get_events_in_range] %s~%s → %d건",
            start_str, end_str, len(events),
        )
        return events

    async def update_event_time(
        self,
        uid: str,
        start_time_str: str,
        end_time_str: str,
    ) -> bool:
        """
        event_uid 기준으로 일정 시간을 수정한다 (SPEC 5.2.1절 동기화 순서 준수).

        수정 순서:
          1. Apple Calendar 반영
          2. 성공 시 SQLite 캐시 반영

        Args:
            uid:            수정할 이벤트의 event_uid.
            start_time_str: 새 시작 시간 "HH:MM" (예: "15:00").
            end_time_str:   새 종료 시간 "HH:MM" (예: "24:00").

        Returns:
            True 시 성공, False 시 실패.
        """
        ev = self.get_event_by_uid(uid)
        if not ev:
            logger.warning("[CalendarService.update_event_time] uid '%s' 조회 실패.", uid)
            return False

        # 기존 날짜 + 새 시간으로 datetime 조합
        event_date_str = ev.get("date", "")
        try:
            new_start_dt = datetime.fromisoformat(f"{event_date_str}T{start_time_str}:00")
            # "24:00" → 다음 날 00:00 처리
            if end_time_str == "24:00":
                from datetime import date as _d
                end_date = _d.fromisoformat(event_date_str) + timedelta(days=1)
                new_end_dt = datetime.combine(end_date, datetime.min.time())
            else:
                new_end_dt = datetime.fromisoformat(f"{event_date_str}T{end_time_str}:00")
        except ValueError as e:
            logger.error(
                "[CalendarService.update_event_time] 시간 파싱 실패 "
                "(uid=%s, start=%s, end=%s): %s",
                uid, start_time_str, end_time_str, e,
            )
            return False

        return self.modify_event(uid, new_start_dt=new_start_dt, new_end_dt=new_end_dt)

