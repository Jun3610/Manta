import re
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

def resolve_date_string(range_str: str) -> tuple[date, date]:
    """
    상대 날짜 표현을 datetime.now() 기준 절대 날짜 범위로 환산합니다.
    """
    today = datetime.now().date()
    range_str = range_str.replace(" ", "")
    
    if "오늘" in range_str:
        return today, today
        
    if "내일" in range_str:
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow
        
    if "모레" in range_str:
        day_after = today + timedelta(days=2)
        return day_after, day_after

    if "이번달" in range_str or "이번 달" in range_str:
        start = today.replace(day=1)
        if today.month == 12:
            end = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(today.year, today.month + 1, 1) - timedelta(days=1)
        return start, end

    if "다음달" in range_str or "다음 달" in range_str:
        if today.month == 12:
            start = date(today.year + 1, 1, 1)
            end = date(today.year + 1, 2, 1) - timedelta(days=1)
        else:
            start = date(today.year, today.month + 1, 1)
            if today.month + 1 == 12:
                end = date(today.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(today.year, today.month + 2, 1) - timedelta(days=1)
        return start, end

    if "이번주" in range_str or "이번 주" in range_str:
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end

    if "다음주" in range_str or "다음 주" in range_str:
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        end = start + timedelta(days=6)
        return start, end

    # "X월 Y일" 매칭
    m_md = re.search(r"(\d{1,2})월\s*(\d{1,2})일", range_str)
    if m_md:
        month, day = int(m_md.group(1)), int(m_md.group(2))
        try:
            target_date = date(today.year, month, day)
            return target_date, target_date
        except ValueError:
            pass

    # "N월" 또는 "N월 지금까지" 등 특정 월 표현
    month_match = re.search(r"(\d{1,2})월", range_str)
    if month_match:
        month = int(month_match.group(1))
        year = today.year
        if 1 <= month <= 12:
            start = date(year, month, 1)
            if month == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            # "지금까지" 포함 시 오늘까지만
            if "지금까지" in range_str or "현재까지" in range_str:
                end = min(end, today)
            return start, end

    # YYYY-MM-DD~YYYY-MM-DD 형식 시도
    match = re.search(r"(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})", range_str)
    if match:
        try:
            return (
                date.fromisoformat(match.group(1)),
                date.fromisoformat(match.group(2)),
            )
        except ValueError:
            pass
            
    # YYYY-MM-DD 형식 시도
    m_full = re.search(r"(\d{4})-(\d{2})-(\d{2})", range_str)
    if m_full:
        try:
            d = date.fromisoformat(m_full.group(1) + "-" + m_full.group(2) + "-" + m_full.group(3))
            return d, d
        except ValueError:
            pass

    # YYYY-MM 형식 시도
    m_ym = re.search(r"(\d{4})-(\d{2})", range_str)
    if m_ym:
        year, month = int(m_ym.group(1)), int(m_ym.group(2))
        try:
            start = date(year, month, 1)
            if month == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            return start, end
        except ValueError:
            pass

    # 파싱 불가 시 빈 값 반환
    return None, None
