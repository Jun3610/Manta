import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "data", "calendar_store.db")
print("DB path:", db_path)
if not os.path.exists(db_path):
    print("DB does not exist!")
else:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT count(*) FROM events")
    total = c.fetchone()[0]
    print(f"Total events: {total}")
    
    c.execute("SELECT count(*) FROM events WHERE date >= '2026-07-01' AND date <= '2026-07-31'")
    july = c.fetchone()[0]
    print(f"July events: {july}")
    
    c.execute("SELECT date, title FROM events ORDER BY date LIMIT 10")
    print("First 10 events:")
    for r in c.fetchall():
        print(f"  {r[0]} - {r[1]}")
    conn.close()
