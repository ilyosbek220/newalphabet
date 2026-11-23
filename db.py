"""Ma'lumotlar ombori: SQLite (lokal) va PostgreSQL / Neon (bulutli, DATABASE_URL orqali).
Foydalanuvchi matni/fayllari HECH QACHON saqlanmaydi, faqat hisoblagichlar va to'lov cheki ID si."""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date
from urllib.parse import parse_qsl, urlsplit

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, joined INTEGER NOT NULL, premium_until INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
    username TEXT NOT NULL DEFAULT '', full_name TEXT NOT NULL DEFAULT '',
    referred_by INTEGER NOT NULL DEFAULT 0, ref_progress INTEGER NOT NULL DEFAULT 0,
    ref_total INTEGER NOT NULL DEFAULT 0);
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

PG_TABLES = (
    """CREATE TABLE IF NOT EXISTS users(
        id BIGINT PRIMARY KEY, joined BIGINT NOT NULL, premium_until BIGINT NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
        username TEXT NOT NULL DEFAULT '', full_name TEXT NOT NULL DEFAULT '',
        referred_by BIGINT NOT NULL DEFAULT 0, ref_progress INTEGER NOT NULL DEFAULT 0,
        ref_total INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS usage(
        user_id BIGINT NOT NULL, day TEXT NOT NULL,
        files INTEGER NOT NULL DEFAULT 0, chars BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id, day))""",
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS payments(
        id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, days INTEGER NOT NULL,
        amount BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created BIGINT NOT NULL,
        decided_by BIGINT, decided_at BIGINT,
        proof_type TEXT NOT NULL DEFAULT 'photo', proof_file_id TEXT NOT NULL DEFAULT '')""",
)

# Mavjud jadvallarga yetishmayotgan ustunlarni avtomatik qo'shish (SQLite va PostgreSQL)
_USER_COLS = {
    "banned": ("INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
    "blocked": ("INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
    "username": ("TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''"),
    "full_name": ("TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''"),
    "referred_by": ("INTEGER NOT NULL DEFAULT 0", "BIGINT NOT NULL DEFAULT 0"),
    "ref_progress": ("INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
    "ref_total": ("INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
}


def _is_pg_url(target: str) -> bool:
    return (target or "").strip().lower().startswith(("postgres://", "postgresql://"))


def _normalize_pg_url(url: str) -> str:
    u = url.strip()
    if u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://"):]
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    if "sslmode" not in q and host not in ("", "localhost", "127.0.0.1"):
        u += ("&" if p.query else "?") + "sslmode=require"
    return u


class DB:
    def __init__(self, target: str = "bot.db"):
        self.is_postgres = _is_pg_url(target)
        self.backend = "postgresql" if self.is_postgres else "sqlite"
        self._lock = threading.RLock()
        self._pg_c = None
        self._c = None

        if self.is_postgres:
            self._target = _normalize_pg_url(target)
            self._pg_mod, self._dict_row = self._import_pg()
            self._retry_excs = (self._pg_mod.OperationalError, self._pg_mod.InterfaceError, OSError)
            self._run(self._init_pg)
        else:
            self._target = target or "bot.db"
            self._pg_mod = self._dict_row = None
            self._retry_excs = ()
            self._c = sqlite3.connect(self._target, check_same_thread=False)
            self._c.row_factory = sqlite3.Row
            with self._lock, self._c:
                self._c.executescript(SQLITE_SCHEMA)
                have = {r["name"] for r in self._c.execute("PRAGMA table_info(users)")}
                for col, (sqlite_ddl, _) in _USER_COLS.items():
                    if col not in have:
                        self._c.execute(f"ALTER TABLE users ADD COLUMN {col} {sqlite_ddl}")

    @staticmethod
    def _import_pg():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise RuntimeError(
                "PostgreSQL (Neon) uchun 'psycopg[binary]' o'rnatilishi kerak: pip install -r requirements.txt"
            ) from e
        return psycopg, dict_row

    def _ensure_pg(self):
        if self._pg_c is None or getattr(self._pg_c, "closed", False):
            self._pg_c = self._pg_mod.connect(
                self._target,
                autocommit=True,
                connect_timeout=15,
                row_factory=self._dict_row,
            )
        return self._pg_c

    def _reset_pg(self) -> None:
        if self._pg_c is not None:
            try:
                self._pg_c.close()
            except Exception:  # noqa: BLE001
                pass
            self._pg_c = None

    def _init_pg(self, c) -> None:
        for stmt in PG_TABLES:
            c.execute(stmt)
        rows = c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='users'"
        ).fetchall()
        have = {r["column_name"] for r in rows}
        for col, (_, pg_ddl) in _USER_COLS.items():
            if col not in have:
                c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {pg_ddl}")

    def _exec(self, c, sql: str, params=()):
        q = sql.replace("?", "%s") if self.is_postgres else sql
        return c.execute(q, params)

    def _run(self, fn):
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self.is_postgres:
                        c = self._ensure_pg()
                        with c.transaction():
                            return fn(c)
                    with self._c:
                        return fn(self._c)
                except Exception as e:
                    # Neon serveri uxlab qolganda uzilgan ulanishni bir marta yangilab qayta urinamiz
                    if self.is_postgres and attempt == 1 and isinstance(e, self._retry_excs):
                        self._reset_pg()
                        continue
                    raise

    def close(self) -> None:
        with self._lock:
            self._reset_pg()
            if self._c is not None:
                self._c.close()
                self._c = None

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    # ------------------------------------------------------------ foydalanuvchi
    def touch_user(self, uid: int, username: str = "", full_name: str = "") -> None:
        now = int(time.time())

        def op(c):
            self._exec(c, "INSERT INTO users(id, joined) VALUES(?, ?) ON CONFLICT(id) DO NOTHING", (uid, now))
            self._exec(
                c,
                "UPDATE users SET blocked=0, username=?, full_name=? WHERE id=?",
                (username or "", full_name or "", uid),
            )

        self._run(op)

    def get_user(self, uid: int) -> dict | None:
        def op(c):
            row = self._exec(c, "SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            return dict(row) if row else None

        return self._run(op)

    def find_user(self, query: str) -> dict | None:
        q = query.strip()
        if q.lstrip("-").isdigit():
            return self.get_user(int(q))

        def op(c):
            row = self._exec(c, "SELECT * FROM users WHERE lower(username)=?", (q.lstrip("@").lower(),)).fetchone()
            return dict(row) if row else None

        return self._run(op)

    def premium_until(self, uid: int) -> int:
        u = self.get_user(uid)
        return int(u["premium_until"]) if u else 0

    def is_premium(self, uid: int) -> bool:
        return self.premium_until(uid) > time.time()

    def grant(self, uid: int, days: int) -> int:
        """Premiumni `days` kunga uzaytiradi (manfiy bo'lsa qisqartiradi). Yangi muddatni qaytaradi."""
        now = int(time.time())

        def op(c):
            self._exec(c, "INSERT INTO users(id, joined) VALUES(?, ?) ON CONFLICT(id) DO NOTHING", (uid, now))
            cur = int(self._exec(c, "SELECT premium_until FROM users WHERE id=?", (uid,)).fetchone()["premium_until"])
            new = max(0, max(cur, now) + days * 86400)
            self._exec(c, "UPDATE users SET premium_until=? WHERE id=?", (new, uid))
            return new

        return self._run(op)

    def set_premium(self, uid: int, ts: int) -> None:
        now = int(time.time())

        def op(c):
            self._exec(c, "INSERT INTO users(id, joined) VALUES(?, ?) ON CONFLICT(id) DO NOTHING", (uid, now))
            self._exec(c, "UPDATE users SET premium_until=? WHERE id=?", (ts, uid))

        self._run(op)

    def touch_user_if_missing(self, uid: int) -> None:
        now = int(time.time())
        self._run(lambda c: self._exec(
            c, "INSERT INTO users(id, joined) VALUES(?, ?) ON CONFLICT(id) DO NOTHING", (uid, now)
        ))

    def register_referral(self, new_uid: int, ref_uid: int, ref_need: int, ref_days: int) -> dict | None:
        """Yangi foydalanuvchini referal orqali ro'yxatdan o'tkazadi va limit to'lsa mukofot beradi."""
        if ref_need <= 0 or ref_days <= 0 or not ref_uid or new_uid == ref_uid:
            return None
        now = int(time.time())

        def op(c):
            if self._exec(c, "SELECT 1 AS ok FROM users WHERE id=?", (new_uid,)).fetchone():
                return None
            ref = self._exec(
                c, "SELECT banned, ref_progress, ref_total, premium_until FROM users WHERE id=?", (ref_uid,)
            ).fetchone()
            if not ref or ref["banned"]:
                return None
            cur = self._exec(
                c,
                "INSERT INTO users(id, joined, referred_by) VALUES(?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (new_uid, now, ref_uid),
            )
            if cur.rowcount == 0:
                return None
            prog = int(ref["ref_progress"]) + 1
            total = int(ref["ref_total"]) + 1
            awarded_days = 0
            until = int(ref["premium_until"])
            if prog >= ref_need:
                cycles = prog // ref_need
                prog %= ref_need
                awarded_days = cycles * ref_days
                until = max(until, now) + awarded_days * 86400
            self._exec(
                c,
                "UPDATE users SET ref_progress=?, ref_total=?, premium_until=? WHERE id=?",
                (prog, total, until, ref_uid),
            )
            return {
                "ref_uid": ref_uid,
                "progress": prog,
                "need": ref_need,
                "total": total,
                "awarded_days": awarded_days,
                "premium_until": until,
            }

        return self._run(op)

    def set_banned(self, uid: int, flag: bool) -> None:
        now = int(time.time())

        def op(c):
            self._exec(c, "INSERT INTO users(id, joined) VALUES(?, ?) ON CONFLICT(id) DO NOTHING", (uid, now))
            self._exec(c, "UPDATE users SET banned=? WHERE id=?", (1 if flag else 0, uid))

        self._run(op)

    def is_banned(self, uid: int) -> bool:
        u = self.get_user(uid)
        return bool(u and u["banned"])

    def banned_list(self, limit: int = 30) -> list[dict]:
        return self._run(
            lambda c: [dict(r) for r in self._exec(c, "SELECT * FROM users WHERE banned=1 ORDER BY id LIMIT ?", (limit,)).fetchall()]
        )

    def mark_blocked(self, uid: int) -> None:
        self._run(lambda c: self._exec(c, "UPDATE users SET blocked=1 WHERE id=?", (uid,)))

    def broadcast_ids(self) -> list[int]:
        return self._run(
            lambda c: [int(r["id"]) for r in self._exec(c, "SELECT id FROM users WHERE banned=0 AND blocked=0 ORDER BY id").fetchall()]
        )

    # ------------------------------------------------------------------ limit
    def usage_today(self, uid: int) -> tuple[int, int]:
        today = self._today()

        def op(c):
            row = self._exec(c, "SELECT files, chars FROM usage WHERE user_id=? AND day=?", (uid, today)).fetchone()
            return (int(row["files"]), int(row["chars"])) if row else (0, 0)

        return self._run(op)

    def usage_total(self, uid: int) -> tuple[int, int]:
        def op(c):
            row = self._exec(
                c, "SELECT COALESCE(SUM(files),0) AS f, COALESCE(SUM(chars),0) AS c FROM usage WHERE user_id=?", (uid,)
            ).fetchone()
            return (int(row["f"]), int(row["c"])) if row else (0, 0)

        return self._run(op)

    def add_usage(self, uid: int, files: int = 0, chars: int = 0) -> None:
        today = self._today()
        self._run(
            lambda c: self._exec(
                c,
                """INSERT INTO usage(user_id, day, files, chars) VALUES(?,?,?,?)
                   ON CONFLICT(user_id, day) DO UPDATE
                   SET files=usage.files+excluded.files, chars=usage.chars+excluded.chars""",
                (uid, today, files, chars),
            )
        )

    # --------------------------------------------------------------- sozlamalar
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        def op(c):
            row = self._exec(c, "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

        return self._run(op)

    def set_setting(self, key: str, value: str) -> None:
        self._run(
            lambda c: self._exec(
                c,
                "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        )

    # ---------------------------------------------------------------- to'lovlar
    _PAY_SQL = "SELECT p.*, u.full_name, u.username FROM payments p LEFT JOIN users u ON u.id=p.user_id "

    def create_payment(self, uid: int, days: int, amount: int, proof_type: str, file_id: str) -> int:
        params = (uid, days, amount, int(time.time()), proof_type, file_id)

        def op(c):
            if self.is_postgres:
                row = self._exec(
                    c,
                    "INSERT INTO payments(user_id, days, amount, created, proof_type, proof_file_id) "
                    "VALUES(?,?,?,?,?,?) RETURNING id",
                    params,
                ).fetchone()
                return int(row["id"])
            cur = self._exec(
                c,
                "INSERT INTO payments(user_id, days, amount, created, proof_type, proof_file_id) VALUES(?,?,?,?,?,?)",
                params,
            )
            return int(cur.lastrowid)

        return self._run(op)

    def get_payment(self, pid: int) -> dict | None:
        def op(c):
            row = self._exec(c, self._PAY_SQL + "WHERE p.id=?", (pid,)).fetchone()
            return dict(row) if row else None

        return self._run(op)

    def pending_payments(self, limit: int = 20) -> list[dict]:
        return self._run(
            lambda c: [dict(r) for r in self._exec(
                c, self._PAY_SQL + "WHERE p.status='pending' ORDER BY p.id LIMIT ?", (limit,)
            ).fetchall()]
        )

    def recent_payments(self, limit: int = 15) -> list[dict]:
        return self._run(
            lambda c: [dict(r) for r in self._exec(
                c, self._PAY_SQL + "ORDER BY p.id DESC LIMIT ?", (limit,)
            ).fetchall()]
        )

    def user_pending_count(self, uid: int) -> int:
        return self._run(
            lambda c: int(
                self._exec(c, "SELECT COUNT(*) AS c FROM payments WHERE user_id=? AND status='pending'", (uid,))
                .fetchone()["c"]
            )
        )

    def decide_payment(self, pid: int, status: str, admin_id: int) -> bool:
        """Faqat 'pending' holatdagi to'lovni hal qiladi. Ikki marta bosilsa False qaytaradi."""
        now = int(time.time())

        def op(c):
            cur = self._exec(
                c,
                "UPDATE payments SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
                (status, admin_id, now, pid),
            )
            return cur.rowcount == 1

        return self._run(op)

    # --------------------------------------------------------------- statistika
    def stats(self) -> dict:
        now, today = int(time.time()), self._today()

        def op(c):
            q = lambda sql, *a: int(self._exec(c, sql, a).fetchone()["v"])  # noqa: E731
            u_row = self._exec(
                c,
                "SELECT COALESCE(SUM(files),0) AS f, COALESCE(SUM(chars),0) AS c FROM usage WHERE day=?",
                (today,),
            ).fetchone()
            return {
                "users": q("SELECT COUNT(*) AS v FROM users"),
                "premium": q("SELECT COUNT(*) AS v FROM users WHERE premium_until>?", now),
                "banned": q("SELECT COUNT(*) AS v FROM users WHERE banned=1"),
                "referred": q("SELECT COUNT(*) AS v FROM users WHERE referred_by>0"),
                "files_today": int(u_row["f"]),
                "chars_today": int(u_row["c"]),
                "pending": q("SELECT COUNT(*) AS v FROM payments WHERE status='pending'"),
                "income_total": q("SELECT COALESCE(SUM(amount),0) AS v FROM payments WHERE status='approved'"),
                "income_30d": q(
                    "SELECT COALESCE(SUM(amount),0) AS v FROM payments WHERE status='approved' AND decided_at>?",
                    now - 30 * 86400,
                ),
            }

        return self._run(op)
