import sqlite3
import os
from datetime import datetime
import sys

def main():
    db_path = os.path.join(os.path.dirname(__file__), "data", "calendar_store.db")
    if not os.path.exists(db_path):
        print(f"❌ DB 파일을 찾을 수 없습니다: {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # 전체 일정 개수
    c.execute("SELECT count(*) FROM events")
    total = c.fetchone()[0]
    print(f"✅ 현재 SQLite DB에 저장된 총 일정 개수: {total}개")
    
    # 7월 일정 검색
    c.execute("SELECT date, title, calendar_name FROM events WHERE date >= '2026-07-01' AND date <= '2026-07-31'")
    july_events = c.fetchall()
    
    print(f"\n✅ 7월 일정 조회 결과 ({len(july_events)}개 발견):")
    if len(july_events) == 0:
        print("  -> 📭 진짜로 DB 안에 7월 일정이 단 하나도 없습니다!! (Apple Calendar에 7월 일정이 없거나, 동기화되지 않음)")
    else:
        for ev in july_events:
            print(f"  - [{ev[2]}] {ev[0]} : {ev[1]}")

    conn.close()

if __name__ == "__main__":
    main()
