import re
import json
from datetime import datetime, date, timedelta
from langchain_core.tools import tool
from services.calendar_service import CalendarService

calendar_service = CalendarService()

def _format_events_summary(events: list) -> str:
    """리스트 조회용으로 최소한의 데이터만 포맷팅하여 반환합니다."""
    if not events:
        return "📭 조회된 일정이 없습니다."
    
    summary_list = []
    for ev in events:
        summary_list.append({
            "uid": ev["uid"],
            "title": ev["title"],
            "date": ev["date"],
            "time": ev["time"],
            "duration_min": ev["duration_min"]
        })
    return json.dumps(summary_list, ensure_ascii=False, indent=2)

@tool
def get_today_events() -> str:
    """오늘의 일정을 조회합니다. 반환 데이터는 요약본입니다."""
    try:
        today_str = date.today().strftime("%Y-%m-%d")
        events = calendar_service.search_events(start_date=today_str, end_date=today_str)
        return _format_events_summary(events)
    except Exception as e:
        return f"😥 오늘 일정 조회 중 오류: {e}"

@tool
def get_tomorrow_events() -> str:
    """내일의 일정을 조회합니다. 반환 데이터는 요약본입니다."""
    try:
        tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        events = calendar_service.search_events(start_date=tomorrow_str, end_date=tomorrow_str)
        return _format_events_summary(events)
    except Exception as e:
        return f"😥 내일 일정 조회 중 오류: {e}"

@tool
def get_week_events() -> str:
    """오늘부터 향후 7일간의 일정을 조회합니다. 반환 데이터는 요약본입니다."""
    try:
        today_str = date.today().strftime("%Y-%m-%d")
        next_week_str = (date.today() + timedelta(days=6)).strftime("%Y-%m-%d")
        events = calendar_service.search_events(start_date=today_str, end_date=next_week_str)
        return _format_events_summary(events)
    except Exception as e:
        return f"😥 주간 일정 조회 중 오류: {e}"

@tool
def get_events_by_range(start_date: str, end_date: str) -> str:
    """
    특정 기간의 일정을 조회합니다. 반환 데이터는 요약본입니다.
    - start_date (str): 시작일 (YYYY-MM-DD 형식) (필수)
    - end_date (str): 종료일 (YYYY-MM-DD 형식) (필수)
    """
    try:
        if not start_date or not end_date:
            return "❌ 시작일과 종료일을 모두 입력해주세요."
        events = calendar_service.search_events(start_date=start_date, end_date=end_date)
        return _format_events_summary(events)
    except Exception as e:
        return f"😥 기간별 일정 조회 중 오류: {e}"

@tool
def search_events(keyword: str) -> str:
    """
    제목이나 메모에 특정 키워드가 포함된 일정을 검색합니다. 반환 데이터는 요약본입니다.
    - keyword (str): 검색할 키워드 (필수)
    """
    try:
        if not keyword:
            return "❌ 검색할 키워드를 입력해주세요."
        events = calendar_service.search_events(keyword=keyword)
        return _format_events_summary(events)
    except Exception as e:
        return f"😥 일정 검색 중 오류: {e}"

@tool
def get_event_detail(event_uid: str) -> str:
    """
    이벤트의 고유 식별자(UID)를 이용해 전체 상세 정보(메모 포함)를 조회합니다.
    - event_uid (str): 일정의 고유 식별자 (필수)
    """
    try:
        ev = calendar_service.get_event_by_uid(event_uid)
        if not ev:
            return f"❌ UID가 '{event_uid}'인 일정을 찾을 수 없습니다."
        return json.dumps(ev, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"😥 일정 상세 조회 중 오류: {e}"


@tool
def add_apple_calendar_event(title: str, date_str: str, time_str: str = "09:00", duration_min: int = 60, notes: str = "", important: bool = False, end_date: str = "") -> str:
    """
    새로운 일정을 Mac 캘린더에 추가합니다.
    - title (str): 일정 제목 (필수)
    - date_str (str): 일정 시작일 (YYYY-MM-DD 형식) (필수)
    - time_str (str): 일정 시작 시간 (HH:MM 형식, 기본값 09:00)
    - duration_min (int): 일정 소요 시간 (분 단위, 기본값 60)
    - notes (str): 일정 관련 메모
    - important (bool): 중요 일정 여부 (기본값 False)
    - end_date (str): 연속된 날짜에 동일 일정을 추가할 경우 종료일 (YYYY-MM-DD 형식)
    """
    try:
        def parse_date(d_str):
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d_str.strip())
            if m:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return None

        start_d = parse_date(date_str)
        if not start_d:
            return f"❌ 날짜 형식이 잘못되었습니다: {date_str} (YYYY-MM-DD 형식을 사용해주세요)"
            
        end_d = parse_date(end_date) if end_date else start_d
        if end_d < start_d:
            return "❌ 종료일이 시작일보다 빠를 수 없습니다."

        tm = re.match(r"(\d{1,2}):(\d{2})", time_str.strip())
        if not tm:
            return f"❌ 시간 형식이 잘못되었습니다: {time_str} (HH:MM 형식을 사용해주세요)"
        
        hour, minute = int(tm.group(1)), int(tm.group(2))
        
        current_d = start_d
        added_count = 0
        added_uids = []

        while current_d <= end_d:
            start_dt = datetime(current_d.year, current_d.month, current_d.day, hour, minute)
            end_dt = start_dt + timedelta(minutes=duration_min)
            
            ev_data = calendar_service.create_event(
                title=title, 
                start_dt=start_dt, 
                end_dt=end_dt, 
                notes=notes, 
                important=important
            )
            added_uids.append(ev_data['uid'])
            added_count += 1
            current_d += timedelta(days=1)
            
        if added_count > 1:
            return f"✅ '{title}' 일정이 {start_d}부터 {end_d}까지 매일 추가되었습니다. (총 {added_count}일)"
        return f"✅ '{title}' 일정이 {start_d} {time_str}에 추가되었습니다. (UID: {added_uids[0]})"
    except Exception as e:
        return f"😥 일정 추가 중 오류가 발생했습니다: {e}"

@tool
def modify_apple_calendar_event(event_uid: str, new_title: str = "", new_date: str = "", new_time: str = "", new_duration_min: int = 0) -> str:
    """
    고유 식별자(UID)를 사용하여 기존 일정을 수정합니다.
    (반드시 조회 도구들을 사용해 대상 이벤트의 UID를 확인하세요.)
    - event_uid (str): 수정할 일정의 고유 식별자 (필수)
    - new_title (str): 변경할 새 제목
    - new_date (str): 변경할 새 날짜 (YYYY-MM-DD 형식)
    - new_time (str): 변경할 새 시간 (HH:MM 형식)
    - new_duration_min (int): 변경할 소요 시간 (분 단위)
    """
    try:
        new_start_dt = None
        new_end_dt = None
        
        target_ev = calendar_service.get_event_by_uid(event_uid)
        if not target_ev:
            return f"❌ UID가 '{event_uid}'인 일정을 찾을 수 없습니다."

        current_date = target_ev['date']
        current_time = target_ev['time']
        
        if new_date or new_time:
            d_str = new_date if new_date else current_date
            t_str = new_time if new_time else current_time
            
            dm = re.match(r"(\d{4})-(\d{2})-(\d{2})", d_str.strip())
            tm = re.match(r"(\d{1,2}):(\d{2})", t_str.strip())
            
            if dm and tm:
                new_start_dt = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)),
                                        int(tm.group(1)), int(tm.group(2)))
                dur = new_duration_min if new_duration_min > 0 else target_ev['duration_min']
                new_end_dt = new_start_dt + timedelta(minutes=dur)

        success = calendar_service.modify_event(
            uid=event_uid,
            new_title=new_title if new_title else None,
            new_start_dt=new_start_dt,
            new_end_dt=new_end_dt
        )
        if success:
            return f"✅ 일정이 성공적으로 수정되었습니다. (UID: {event_uid})"
        else:
            return f"❌ 일정 수정에 실패했습니다."
    except Exception as e:
        return f"😥 일정 수정 중 오류가 발생했습니다: {e}"

@tool
def delete_apple_calendar_event(event_uid: str) -> str:
    """
    고유 식별자(UID)를 사용하여 단일 일정을 정확히 삭제합니다.
    (반드시 조회 도구들을 사용해 대상 이벤트의 UID를 확인하세요.)
    - event_uid (str): 삭제할 일정의 고유 식별자 (필수)
    """
    try:
        success = calendar_service.delete_event(event_uid)
        if success:
            return f"🗑️ 일정(UID: {event_uid})이 성공적으로 삭제되었습니다."
        else:
            return f"❌ 해당 일정을 찾을 수 없거나 삭제에 실패했습니다."
    except Exception as e:
        return f"😥 일정 삭제 중 오류가 발생했습니다: {e}"

@tool
def delete_all_calendar_events_on_date(date_str: str) -> str:
    """
    특정 날짜의 모든 일정을 삭제합니다.
    - date_str (str): 일정을 삭제할 날짜 (YYYY-MM-DD 형식) (필수)
    """
    try:
        events = calendar_service.search_events(start_date=date_str, end_date=date_str)
        if not events:
            return f"📭 {date_str}에 등록된 일정이 없습니다."
            
        deleted_count = 0
        for ev in events:
            if calendar_service.delete_event(ev['uid']):
                deleted_count += 1
                
        return f"🗑️ {date_str}의 일정 {deleted_count}개가 모두 삭제되었습니다."
    except Exception as e:
        return f"😥 날짜별 일정 삭제 중 오류가 발생했습니다: {e}"
