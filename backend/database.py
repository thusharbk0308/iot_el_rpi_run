"""
SQLite database layer for FaceGuard Security System.
Auto-creates security.db on first run.
All queries use parameterized statements. Thread-safe via internal Lock.
"""
import os
import csv
import sqlite3
import threading
from datetime import datetime, timedelta


class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    # ──────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read/write
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT UNIQUE NOT NULL,
                    image_count INTEGER DEFAULT 0,
                    created_at  TEXT,
                    enabled     INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS access_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    person_name TEXT NOT NULL,
                    confidence  REAL NOT NULL,
                    result      TEXT NOT NULL,
                    lock_status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS unknown_faces (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp  TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    image_path TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_al_ts  ON access_logs(timestamp);
                CREATE INDEX IF NOT EXISTS idx_uf_ts  ON unknown_faces(timestamp);
            """)
            conn.commit()
            conn.close()

    # ──────────────────────────────────────────────────────────
    # Logging
    # ──────────────────────────────────────────────────────────
    def log_access(self, name, confidence, result, lock_status):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT INTO access_logs (timestamp,person_name,confidence,result,lock_status) VALUES (?,?,?,?,?)",
                (ts, name, confidence, result, lock_status)
            )
            conn.commit()
            conn.close()

    def log_unknown(self, image_path, confidence):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._conn()
            conn.execute(
                "INSERT INTO unknown_faces (timestamp,confidence,image_path) VALUES (?,?,?)",
                (ts, confidence, image_path)
            )
            conn.commit()
            conn.close()

    # ──────────────────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────────────────
    def get_stats_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            conn = self._conn()
            unlocks = conn.execute(
                "SELECT COUNT(*) FROM access_logs WHERE result='GRANTED' AND date(timestamp)=?", (today,)
            ).fetchone()[0]
            recognitions = conn.execute(
                "SELECT COUNT(*) FROM access_logs WHERE result='GRANTED' AND date(timestamp)=?", (today,)
            ).fetchone()[0]
            unknowns = conn.execute(
                "SELECT COUNT(*) FROM unknown_faces WHERE date(timestamp)=?", (today,)
            ).fetchone()[0]
            conn.close()
        return {"unlock_count": unlocks, "recognition_count": recognitions, "unknown_count": unknowns}

    def get_stats_weekly(self):
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        with self._lock:
            conn = self._conn()
            unlocks  = conn.execute("SELECT COUNT(*) FROM access_logs  WHERE result='GRANTED' AND date(timestamp)>=?", (week_ago,)).fetchone()[0]
            unknowns = conn.execute("SELECT COUNT(*) FROM unknown_faces WHERE date(timestamp)>=?", (week_ago,)).fetchone()[0]
            conn.close()
        return {"unlock_count": unlocks, "unknown_count": unknowns}

    def get_stats_monthly(self):
        month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        with self._lock:
            conn = self._conn()
            unlocks  = conn.execute("SELECT COUNT(*) FROM access_logs  WHERE result='GRANTED' AND date(timestamp)>=?", (month_ago,)).fetchone()[0]
            unknowns = conn.execute("SELECT COUNT(*) FROM unknown_faces WHERE date(timestamp)>=?", (month_ago,)).fetchone()[0]
            conn.close()
        return {"unlock_count": unlocks, "unknown_count": unknowns}

    def get_recent_events(self, limit=20):
        with self._lock:
            conn = self._conn()
            access = conn.execute(
                "SELECT timestamp,person_name,confidence,result FROM access_logs ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            unknowns = conn.execute(
                "SELECT timestamp,confidence,image_path FROM unknown_faces ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            conn.close()

        events = []
        for r in access:
            events.append({
                "timestamp": r["timestamp"],
                "type": "granted" if r["result"] == "GRANTED" else "denied",
                "person": r["person_name"],
                "confidence": round(r["confidence"], 2),
                "message": "Door Opened" if r["result"] == "GRANTED" else "Access Denied",
            })
        for r in unknowns:
            events.append({
                "timestamp": r["timestamp"],
                "type": "unknown",
                "person": "Unknown",
                "confidence": round(r["confidence"], 2),
                "message": "Snapshot Saved",
            })
        events.sort(key=lambda x: x["timestamp"], reverse=True)
        return events[:limit]

    # ──────────────────────────────────────────────────────────
    # Access Logs
    # ──────────────────────────────────────────────────────────
    def get_access_logs(self, page=1, per_page=20, search="", date_filter=""):
        offset = (page - 1) * per_page
        clauses, params = [], []
        if search:
            clauses.append("(person_name LIKE ? OR result LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if date_filter:
            clauses.append("date(timestamp)=?")
            params.append(date_filter)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            conn = self._conn()
            total = conn.execute(f"SELECT COUNT(*) FROM access_logs {where}", params).fetchone()[0]
            rows  = conn.execute(
                f"SELECT * FROM access_logs {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                params + [per_page, offset]
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows], total

    # ──────────────────────────────────────────────────────────
    # Unknown Faces
    # ──────────────────────────────────────────────────────────
    def get_unknown_faces(self, date_filter=""):
        where  = "WHERE date(timestamp)=?" if date_filter else ""
        params = [date_filter] if date_filter else []
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                f"SELECT * FROM unknown_faces {where} ORDER BY timestamp DESC", params
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]

    def get_unknown_dates(self):
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT DISTINCT date(timestamp) as d FROM unknown_faces ORDER BY d DESC"
            ).fetchall()
            conn.close()
        return [r["d"] for r in rows]

    def delete_unknown(self, unknown_id):
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT image_path FROM unknown_faces WHERE id=?", (unknown_id,)).fetchone()
            if row:
                conn.execute("DELETE FROM unknown_faces WHERE id=?", (unknown_id,))
                conn.commit()
                conn.close()
                return row["image_path"]
            conn.close()
        return None

    # ──────────────────────────────────────────────────────────
    # Users
    # ──────────────────────────────────────────────────────────
    def get_users(self):
        with self._lock:
            conn = self._conn()
            rows = conn.execute("SELECT * FROM users ORDER BY name ASC").fetchall()
            conn.close()
        return [dict(r) for r in rows]

    def sync_users_from_dataset(self, dataset_dir):
        """Scan dataset/ and upsert into users table."""
        if not os.path.exists(dataset_dir):
            return
        with self._lock:
            conn = self._conn()
            for folder in os.listdir(dataset_dir):
                path = os.path.join(dataset_dir, folder)
                if not os.path.isdir(path):
                    continue
                images = [f for f in os.listdir(path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
                created_at = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("""
                    INSERT INTO users (name, image_count, created_at, enabled)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(name) DO UPDATE SET image_count=excluded.image_count
                """, (folder, len(images), created_at))
            conn.commit()
            conn.close()

    def set_user_enabled(self, name, enabled):
        with self._lock:
            conn = self._conn()
            conn.execute("UPDATE users SET enabled=? WHERE name=?", (1 if enabled else 0, name))
            conn.commit()
            conn.close()

    def delete_user(self, name):
        with self._lock:
            conn = self._conn()
            conn.execute("DELETE FROM users WHERE name=?", (name,))
            conn.commit()
            conn.close()

    # ──────────────────────────────────────────────────────────
    # Settings
    # ──────────────────────────────────────────────────────────
    def get_setting(self, key, default=""):
        with self._lock:
            conn = self._conn()
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            conn.close()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self._lock:
            conn = self._conn()
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))
            conn.commit()
            conn.close()

    def get_all_settings(self):
        with self._lock:
            conn = self._conn()
            rows = conn.execute("SELECT key,value FROM settings").fetchall()
            conn.close()
        return {r["key"]: r["value"] for r in rows}

    # ──────────────────────────────────────────────────────────
    # Charts
    # ──────────────────────────────────────────────────────────
    def get_hourly_chart(self):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            conn = self._conn()
            a_rows = conn.execute("""
                SELECT strftime('%H',timestamp) as h,
                       SUM(CASE WHEN result='GRANTED' THEN 1 ELSE 0 END) as unlocks
                FROM access_logs WHERE date(timestamp)=? GROUP BY h
            """, (today,)).fetchall()
            u_rows = conn.execute("""
                SELECT strftime('%H',timestamp) as h, COUNT(*) as cnt
                FROM unknown_faces WHERE date(timestamp)=? GROUP BY h
            """, (today,)).fetchall()
            conn.close()
        labels  = [f"{h:02d}:00" for h in range(24)]
        unlocks = [0] * 24
        unknowns= [0] * 24
        for r in a_rows:  unlocks[int(r["h"])]  = r["unlocks"]
        for r in u_rows:  unknowns[int(r["h"])] = r["cnt"]
        return {"labels": labels, "unlocks": unlocks, "unknowns": unknowns}

    def get_weekly_chart(self):
        with self._lock:
            conn = self._conn()
            a_rows = conn.execute("""
                SELECT date(timestamp) as d,
                       SUM(CASE WHEN result='GRANTED' THEN 1 ELSE 0 END) as unlocks
                FROM access_logs
                WHERE timestamp >= date('now','-6 days','localtime') GROUP BY d ORDER BY d
            """).fetchall()
            u_rows = conn.execute("""
                SELECT date(timestamp) as d, COUNT(*) as cnt
                FROM unknown_faces
                WHERE timestamp >= date('now','-6 days','localtime') GROUP BY d ORDER BY d
            """).fetchall()
            conn.close()
        days    = [(datetime.now()-timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6,-1,-1)]
        labels  = [(datetime.now()-timedelta(days=i)).strftime("%a")       for i in range(6,-1,-1)]
        u_map   = {r["d"]: r["unlocks"] for r in a_rows}
        unk_map = {r["d"]: r["cnt"]     for r in u_rows}
        return {
            "labels":  labels,
            "unlocks": [u_map.get(d,0)   for d in days],
            "unknowns":[unk_map.get(d,0) for d in days],
        }

    # ──────────────────────────────────────────────────────────
    # CSV Export
    # ──────────────────────────────────────────────────────────
    def export_csv(self, output_path):
        with self._lock:
            conn = self._conn()
            rows = conn.execute(
                "SELECT timestamp,person_name,confidence,result,lock_status FROM access_logs ORDER BY timestamp DESC"
            ).fetchall()
            conn.close()
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        with open(output_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Timestamp","Name","Confidence","Result","Lock Status"])
            for r in rows:
                w.writerow([r["timestamp"], r["person_name"], f"{r['confidence']:.2f}%", r["result"], r["lock_status"]])
