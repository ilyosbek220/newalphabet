"""SQLite ombori: foydalanuvchilar, limitlar, premium, to'lovlar, sozlamalar.
Foydalanuvchi matni/fayllari HECH QACHON saqlanmaydi, faqat hisoblagichlar va to'lov cheki ID si."""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, joined INTEGER NOT NULL, premium_until INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
    username TEXT NOT NULL DEFAULT '', full_name TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS usage(
    user_id INTEGER NOT NULL, day TEXT NOT NULL,
    files INTEGER NOT NULL DEFAULT 0, chars INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, day));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, days INTEGER NOT NULL,
    amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created INTEGER NOT NULL,
    decided_by INTEGER, decided_at INTEGER,
    proof_type TEXT NOT NULL DEFAULT 'photo', proof_file_id TEXT NOT NULL DEFAULT '');
"""

# Eski bazalar (bot.db) uchun: yetishmayotgan ustunlar avtomatik qo'shiladi
_USER_COLS = {
    "banned": "INTEGER NOT NULL DEFAULT 0",
    "blocked": "INTEGER NOT NULL DEFAULT 0",
    "username": "TEXT NOT NULL DEFAULT ''",
    "full_name": "TEXT NOT NULL DEFAULT ''",
}


class DB:
    def __init__(self, path: str = "bot.db"):
        self._lock = threading.RLock()
        self._c = sqlite3.connect(path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        with self._lock, self._c:
            self._c.executescript(SCHEMA)
            have = {r["name"] for r in self._c.execute("PRAGMA table_info(users)")}
            for col, ddl in _USER_COLS.items():
                if col not in have:
                    self._c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    # ------------------------------------------------------------ foydalanuvchi
    def touch_user(self, uid: int, username: str = "", full_name: str = "") -> None:
        with self._lock, self._c:
            self._c.execute("INSERT OR IGNORE INTO users(id, joined) VALUES(?, ?)", (uid, int(time.time())))
            self._c.execute(
                "UPDATE users SET blocked=0, username=?, full_name=? WHERE id=?",
                (username or "", full_name or "", uid),
            )

    def get_user(self, uid: int) -> dict | None:
        with self._lock:
            row = self._c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    def find_user(self, query: str) -> dict | None:
        q = query.strip()
        if q.lstrip("-").isdigit():
            return self.get_user(int(q))
        with self._lock:
            row = self._c.execute("SELECT * FROM users WHERE lower(username)=?", (q.lstrip("@").lower(),)).fetchone()
        return dict(row) if row else None

    def premium_until(self, uid: int) -> int:
        u = self.get_user(uid)
        return u["premium_until"] if u else 0

    def is_premium(self, uid: int) -> bool:
        return self.premium_until(uid) > time.time()

    def grant(self, uid: int, days: int) -> int:
        """Premiumni `days` kunga uzaytiradi (manfiy bo'lsa qisqartiradi). Yangi muddatni qaytaradi."""
        self.touch_user_if_missing(uid)
        with self._lock, self._c:
            cur = self._c.execute("SELECT premium_until FROM users WHERE id=?", (uid,)).fetchone()[0]
            new = max(0, max(cur, int(time.time())) + days * 86400)
            self._c.execute("UPDATE users SET premium_until=? WHERE id=?", (new, uid))
        return new

    def set_premium(self, uid: int, ts: int) -> None:
        self.touch_user_if_missing(uid)
        with self._lock, self._c:
            self._c.execute("UPDATE users SET premium_until=? WHERE id=?", (ts, uid))

    def touch_user_if_missing(self, uid: int) -> None:
        with self._lock, self._c:
            self._c.execute("INSERT OR IGNORE INTO users(id, joined) VALUES(?, ?)", (uid, int(time.time())))

    def set_banned(self, uid: int, flag: bool) -> None:
        self.touch_user_if_missing(uid)
        with self._lock, self._c:
            self._c.execute("UPDATE users SET banned=? WHERE id=?", (1 if flag else 0, uid))

    def is_banned(self, uid: int) -> bool:
        u = self.get_user(uid)
        return bool(u and u["banned"])

    def banned_list(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._c.execute("SELECT * FROM users WHERE banned=1 ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def mark_blocked(self, uid: int) -> None:
        with self._lock, self._c:
            self._c.execute("UPDATE users SET blocked=1 WHERE id=?", (uid,))

    def broadcast_ids(self) -> list[int]:
        with self._lock:
            rows = self._c.execute("SELECT id FROM users WHERE banned=0 AND blocked=0 ORDER BY id").fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ limit
    def usage_today(self, uid: int) -> tuple[int, int]:
        with self._lock:
            row = self._c.execute(
                "SELECT files, chars FROM usage WHERE user_id=? AND day=?", (uid, self._today())
            ).fetchone()
        return (row["files"], row["chars"]) if row else (0, 0)

    def add_usage(self, uid: int, files: int = 0, chars: int = 0) -> None:
        with self._lock, self._c:
            self._c.execute(
                """INSERT INTO usage(user_id, day, files, chars) VALUES(?,?,?,?)
                   ON CONFLICT(user_id, day) DO UPDATE SET files=files+excluded.files, chars=chars+excluded.chars""",
                (uid, self._today(), files, chars),
            )

    # --------------------------------------------------------------- sozlamalar
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._c:
            self._c.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---------------------------------------------------------------- to'lovlar
    _PAY_SQL = ("SELECT p.*, u.full_name, u.username FROM payments p "
                "LEFT JOIN users u ON u.id=p.user_id ")

    def create_payment(self, uid: int, days: int, amount: int, proof_type: str, file_id: str) -> int:
        with self._lock, self._c:
            cur = self._c.execute(
                "INSERT INTO payments(user_id, days, amount, created, proof_type, proof_file_id) VALUES(?,?,?,?,?,?)",
                (uid, days, amount, int(time.time()), proof_type, file_id),
            )
            return cur.lastrowid

    def get_payment(self, pid: int) -> dict | None:
        with self._lock:
            row = self._c.execute(self._PAY_SQL + "WHERE p.id=?", (pid,)).fetchone()
        return dict(row) if row else None

    def pending_payments(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._c.execute(
                self._PAY_SQL + "WHERE p.status='pending' ORDER BY p.id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def recent_payments(self, limit: int = 15) -> list[dict]:
        with self._lock:
            rows = self._c.execute(self._PAY_SQL + "ORDER BY p.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def user_pending_count(self, uid: int) -> int:
        with self._lock:
            return self._c.execute(
                "SELECT COUNT(*) FROM payments WHERE user_id=? AND status='pending'", (uid,)).fetchone()[0]

    def decide_payment(self, pid: int, status: str, admin_id: int) -> bool:
        """Faqat 'pending' holatdagi to'lovni hal qiladi. Ikki marta bosilsa False qaytaradi."""
        with self._lock, self._c:
            cur = self._c.execute(
                "UPDATE payments SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
                (status, admin_id, int(time.time()), pid),
            )
            return cur.rowcount == 1

    # --------------------------------------------------------------- statistika
    def stats(self) -> dict:
        now = int(time.time())
        with self._lock:
            q = lambda sql, *a: self._c.execute(sql, a).fetchone()[0]  # noqa: E731
            files, chars = self._c.execute(
                "SELECT COALESCE(SUM(files),0), COALESCE(SUM(chars),0) FROM usage WHERE day=?", (self._today(),)
            ).fetchone()
            return {
                "users": q("SELECT COUNT(*) FROM users"),
                "premium": q("SELECT COUNT(*) FROM users WHERE premium_until>?", now),
                "banned": q("SELECT COUNT(*) FROM users WHERE banned=1"),
                "files_today": files,
                "chars_today": chars,
                "pending": q("SELECT COUNT(*) FROM payments WHERE status='pending'"),
                "income_total": q("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'"),
                "income_30d": q("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved' AND decided_at>?",
                                now - 30 * 86400),
            }
