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
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "15"))

DATABASE_URL = "" if os.getenv("ALIFBO_TESTING") else os.getenv("DATABASE_URL", "").strip()
db = DB(DATABASE_URL or os.getenv("DB_PATH", str(BASE / "bot.db")))


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
DEFAULT_FAST_PLAN = {"days": 7, "price": 9000}
DEFAULT_REF_COUNT = 3
DEFAULT_REF_DAYS = 3
DEFAULT_LIMIT_MODE = "trial"
DEFAULT_TRIAL_CHARS = 500
DEFAULT_TRIAL_FILES = 1


def get_plans() -> list[dict]:
    raw = db.get_setting("plans")
    try:
        plans = json.loads(raw) if raw else DEFAULT_PLANS
        return sorted(({"days": int(p["days"]), "price": int(p["price"])} for p in plans), key=lambda p: p["days"])
    except (ValueError, KeyError, TypeError):
        return list(DEFAULT_PLANS)


def set_plans(plans: list[dict]) -> None:
    db.set_setting("plans", json.dumps(sorted(plans, key=lambda p: p["days"])))


def get_fast_plan_raw() -> dict:
    raw = db.get_setting("fast_plan")
    try:
        p = json.loads(raw) if raw else DEFAULT_FAST_PLAN
        return {"days": max(1, int(p["days"])), "price": max(0, int(p["price"]))}
    except (ValueError, KeyError, TypeError):
        return dict(DEFAULT_FAST_PLAN)


def get_fast_plan() -> dict | None:
    p = get_fast_plan_raw()
    return p if p["price"] > 0 else None


def set_fast_plan(days: int, price: int) -> None:
    db.set_setting("fast_plan", json.dumps({"days": max(1, int(days)), "price": max(0, int(price))}))


def ref_count() -> int:
    return int(db.get_setting("ref_count", os.getenv("REF_COUNT", str(DEFAULT_REF_COUNT))))


def ref_days() -> int:
    return int(db.get_setting("ref_days", os.getenv("REF_DAYS", str(DEFAULT_REF_DAYS))))


def set_ref_settings(count: int, days: int) -> None:
    db.set_setting("ref_count", str(max(0, int(count))))
    db.set_setting("ref_days", str(max(0, int(days))))


def free_files() -> int:
    return int(db.get_setting("free_files", os.getenv("FREE_FILES_PER_DAY", "3")))


def free_chars() -> int:
    return int(db.get_setting("free_chars", os.getenv("FREE_CHARS_PER_DAY", "10000")))


def limit_mode() -> str:
    # "trial" (bir martalik sinov) yoki "daily" (kunlik)
    mode = db.get_setting("limit_mode", os.getenv("LIMIT_MODE", DEFAULT_LIMIT_MODE))
    return "daily" if mode == "daily" else "trial"


def set_limit_mode(mode: str) -> None:
    db.set_setting("limit_mode", "daily" if mode == "daily" else "trial")


def is_trial_mode() -> bool:
    return limit_mode() == "trial"


def trial_chars() -> int:
    return int(db.get_setting("trial_chars", os.getenv("TRIAL_CHARS", str(DEFAULT_TRIAL_CHARS))))


def set_trial_chars(chars: int) -> None:
    db.set_setting("trial_chars", str(max(0, int(chars))))


def trial_files() -> int:
    return int(db.get_setting("trial_files", os.getenv("TRIAL_FILES", str(DEFAULT_TRIAL_FILES))))


def set_trial_files(files: int) -> None:
    db.set_setting("trial_files", str(max(0, int(files))))


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
