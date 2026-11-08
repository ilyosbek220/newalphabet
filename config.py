"""Umumiy sozlamalar va yagona obyektlar (db, istisno so'zlar). Boshqa modullar shu yerdan oladi."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from db import DB
from uzconv import Exceptions, load_exceptions, norm_key

BASE = Path(__file__).parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    # utf-8-sig: Windows Notepad qo'shadigan BOM belgisini o'tkazib yuboradi
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v and not os.environ.get(k):   # bo'sh o'zgaruvchi .env ni bosib ketmasin
                os.environ[k] = v


_load_dotenv(BASE / ".env")

TOKEN = os.getenv("BOT_TOKEN", "")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "15"))

db = DB(os.getenv("DB_PATH", str(BASE / "bot.db")))


def is_admin(uid: int) -> bool:
    return uid in ADMINS


def touch(user) -> None:
    db.touch_user(user.id, user.username or "", user.full_name or "")


# ------------------------------------------------------------------ istisno so'zlar
EXC_PATH = Path(os.getenv("EXCEPTIONS_PATH", str(BASE / "exceptions.json")))
EXCEPTIONS: Exceptions = load_exceptions(EXC_PATH) if EXC_PATH.exists() else Exceptions()


def _raw_exceptions() -> dict:
    if EXC_PATH.exists():
        return json.loads(EXC_PATH.read_text(encoding="utf-8-sig"))
    return {"_comment": "Istisno so'zlar: eski lotin shakli -> yangi alifbodagi shakli.", "words": {}}


def _write_exceptions(data: dict) -> None:
    tmp = EXC_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, EXC_PATH)
    EXCEPTIONS.clear()
    EXCEPTIONS.update(load_exceptions(EXC_PATH))   # shu obyekt o'zgaradi, qayta ishga tushirish shart emas


def add_exception(old: str, new: str) -> None:
    data = _raw_exceptions()
    data.setdefault("words", {})[norm_key(old)] = new.lower()
    _write_exceptions(data)


def delete_exception(old: str) -> bool:
    data = _raw_exceptions()
    words = data.setdefault("words", {})
    key = norm_key(old)
    if key not in words:
        return False
    del words[key]
    _write_exceptions(data)
    return True


# ---------------------------------------------------------------------- tariflar
DEFAULT_PLANS = [{"days": 30, "price": 29000}, {"days": 90, "price": 69000}, {"days": 365, "price": 199000}]


def get_plans() -> list[dict]:
    raw = db.get_setting("plans")
    try:
        plans = json.loads(raw) if raw else DEFAULT_PLANS
        return sorted(({"days": int(p["days"]), "price": int(p["price"])} for p in plans), key=lambda p: p["days"])
    except (ValueError, KeyError, TypeError):
        return list(DEFAULT_PLANS)


def set_plans(plans: list[dict]) -> None:
    db.set_setting("plans", json.dumps(sorted(plans, key=lambda p: p["days"])))


def free_files() -> int:
    return int(db.get_setting("free_files", os.getenv("FREE_FILES_PER_DAY", "3")))


def free_chars() -> int:
    return int(db.get_setting("free_chars", os.getenv("FREE_CHARS_PER_DAY", "10000")))


def card_info() -> str:
    default = os.getenv("PREMIUM_INFO", "To'lov rekvizitlari hali kiritilmagan. Admin bilan bog'laning.").replace("\\n", "\n")
    return db.get_setting("card_info", default)


# ---------------------------------------------------------------------- formatlar
def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def fmt_date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def fmt_dt(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
