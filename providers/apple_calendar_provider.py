import subprocess
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class AppleCalendarProvider:
    """
    Apple Calendar.app과 통신하는 Provider (osascript 기반)
    오직 Mac 캘린더와의 통신(CRUD)만 담당합니다.
    """
    def __init__(self, target_calendars: List[str] = ["캘린더", "Home", "Work"]):
        self.target_calendars = target_calendars
        self.cal_list_literal = "{" + ", ".join(f'"{c}"' for c in target_calendars) + "}"

    def create_event(self, title: str, start_dt: datetime, end_dt: datetime,
                     calendar_name: str = "캘린더", notes: str = "", important: bool = False) -> Dict:
        """이벤트를 생성하고 생성된 이벤트의 UID와 상세 정보를 반환합니다."""
        event_title = f"⭐ {title}" if important else title
        cal_clause = f'set targetCal to first calendar whose name is "{calendar_name}"'
        notes_line = f'set description of newEvent to "{notes}"' if notes else ""
        
        alarm_lines = ""
        if important:
            alarm_lines = (
                "make new display alarm at end of newEvent with properties {trigger interval: -10080}\n"
                "make new display alarm at end of newEvent with properties {trigger interval: -1440}"
            )

        script = f'''
        tell application "Calendar"
            {cal_clause}
            set startDate to current date
            set year of startDate to {start_dt.year}
            set month of startDate to {start_dt.month}
            set day of startDate to {start_dt.day}
            set hours of startDate to {start_dt.hour}
            set minutes of startDate to {start_dt.minute}
            set seconds of startDate to 0
            
            set endDate to current date
            set year of endDate to {end_dt.year}
            set month of endDate to {end_dt.month}
            set day of endDate to {end_dt.day}
            set hours of endDate to {end_dt.hour}
            set minutes of endDate to {end_dt.minute}
            set seconds of endDate to 0
            
            set newEvent to make new event at end of events of targetCal with properties {{summary:"{event_title}", start date:startDate, end date:endDate}}
            {notes_line}
            {alarm_lines}
            
            return uid of newEvent
        end tell
        '''
        try:
            uid = subprocess.check_output(["osascript", "-e", script], timeout=15).decode().strip()
            return {
                "uid": uid,
                "title": event_title,
                "date": start_dt.strftime("%Y-%m-%d"),
                "time": start_dt.strftime("%H:%M"),
                "duration_min": int((end_dt - start_dt).total_seconds() / 60),
                "calendar_name": calendar_name,
                "notes": notes,
                "important": 1 if important else 0
            }
        except subprocess.CalledProcessError as e:
            logger.error(f"Apple Calendar Event Creation Failed: {e}")
            raise Exception("Mac 캘린더 일정 생성에 실패했습니다.")
        except subprocess.TimeoutExpired:
            raise Exception("Mac 캘린더 응답 시간이 초과되었습니다.")

    def delete_event(self, uid: str) -> bool:
        """UID를 기반으로 이벤트를 삭제합니다."""
        script = f'''
        set deletedCount to 0
        tell application "Calendar"
            repeat with cal in calendars
                set evts to (every event of cal whose uid is "{uid}")
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
            return result.isdigit() and int(result) > 0
        except Exception as e:
            logger.error(f"Apple Calendar Event Deletion Failed (UID: {uid}): {e}")
            raise Exception(f"일정 삭제 실패: {e}")

    def modify_event(self, uid: str, new_title: Optional[str] = None, 
                     new_start_dt: Optional[datetime] = None, new_end_dt: Optional[datetime] = None) -> bool:
        """UID를 기반으로 이벤트를 수정합니다."""
        mod_lines = []
        if new_title:
            mod_lines.append(f'set summary of evt to "{new_title}"')
            
        if new_start_dt or new_end_dt:
            # -10025 에러(시작일이 종료일보다 늦어지는 순간 발생) 우회를 위해 
            # 임시로 종료일을 아주 먼 미래(10년 뒤)로 밀어둠
            mod_lines.append('''
                set safeEd to (start date of evt) + (3650 * days)
                set end date of evt to safeEd
            ''')
            
        if new_start_dt:
            mod_lines.append(f'''
                set sd to start date of evt
                set year of sd to {new_start_dt.year}
                set month of sd to {new_start_dt.month}
                set day of sd to {new_start_dt.day}
                set hours of sd to {new_start_dt.hour}
                set minutes of sd to {new_start_dt.minute}
                set seconds of sd to 0
                set start date of evt to sd''')
                
        if new_end_dt:
            mod_lines.append(f'''
                set ed to end date of evt
                set year of ed to {new_end_dt.year}
                set month of ed to {new_end_dt.month}
                set day of ed to {new_end_dt.day}
                set hours of ed to {new_end_dt.hour}
                set minutes of ed to {new_end_dt.minute}
                set seconds of ed to 0
                set end date of evt to ed''')
                
        if not mod_lines:
            return False

        mod_block = "\n                ".join(mod_lines)
        script = f'''
        set modCount to 0
        tell application "Calendar"
            repeat with cal in calendars
                set evts to (every event of cal whose uid is "{uid}")
                repeat with evt in evts
                    {mod_block}
                    set modCount to modCount + 1
                end repeat
            end repeat
        end tell
        return modCount as string
        '''
        try:
            result = subprocess.check_output(["osascript", "-e", script], timeout=15).decode().strip()
            return result.isdigit() and int(result) > 0
        except Exception as e:
            logger.error(f"Apple Calendar Event Modification Failed (UID: {uid}): {e}")
            raise Exception(f"일정 수정 실패: {e}")

    def fetch_all_events(self, start_dt: datetime, end_dt: datetime) -> List[Dict]:
        """특정 기간의 모든 이벤트를 가져옵니다."""
        script = f'''
        set output to ""
        set startDate to current date
        set year of startDate to {start_dt.year}
        set month of startDate to {start_dt.month}
        set day of startDate to {start_dt.day}
        set hours of startDate to 0
        set minutes of startDate to 0
        set seconds of startDate to 0
        
        set endDate to current date
        set year of endDate to {end_dt.year}
        set month of endDate to {end_dt.month}
        set day of endDate to {end_dt.day}
        set hours of endDate to 23
        set minutes of endDate to 59
        set seconds of endDate to 59
        
        tell application "Calendar"
            repeat with calName in {self.cal_list_literal}
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
            events = []
            for line in raw.splitlines():
                parts = line.split("|||")
                if len(parts) < 6:
                    continue
                uid, cal_name, title, date_str, time_str, dur_str = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                notes = parts[6] if len(parts) > 6 else ""
                try:
                    dur_min = int(dur_str)
                except:
                    dur_min = 60
                
                events.append({
                    "uid": uid,
                    "calendar_name": cal_name,
                    "title": title,
                    "date": date_str,
                    "time": time_str,
                    "duration_min": dur_min,
                    "notes": notes,
                    "important": 1 if title.startswith("⭐") else 0
                })
            return events
        except Exception as e:
            logger.error(f"Apple Calendar Fetch Failed: {e}")
            return []
