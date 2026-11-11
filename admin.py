"""Admin paneli. FAQAT ADMIN_IDS dagi foydalanuvchilar uchun ishlaydi va ko'rinadi:
router darajasidagi filtr boshqalarning xabar va tugma bosishlarini umuman qabul qilmaydi."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import (ADMINS, EXCEPTIONS, add_exception, card_info, db, delete_exception, fmt_date, fmt_dt,
                    free_chars, free_files, get_fast_plan, get_fast_plan_raw, get_plans, money,
                    ref_count, ref_days, set_fast_plan, set_plans, set_ref_settings)
from payments import STATUS, decide_kb, notify_user, pay_caption, send_proof, who
from ui import B, NOT_CMD, back_home_kb, edit_any, kb, show

log = logging.getLogger("alifbo-bot")
router = Router(name="admin")
router.message.filter(F.from_user.id.in_(ADMINS))
router.callback_query.filter(F.from_user.id.in_(ADMINS))

_tasks: set = set()   # fon vazifalari (broadcast) o'chib ketmasligi uchun


class A(StatesGroup):
    user = State()
    broadcast = State()
    word_add = State()
    word_del = State()
    card = State()
    plan = State()
    fast_plan = State()
    ref_cfg = State()
    ff = State()
    fc = State()


CANCEL = kb([B(text="❌ Bekor qilish", callback_data="adm:cancel")])
BACK = kb([B(text="🔙 Admin panel", callback_data="adm:home")])


# ------------------------------------------------------------------- bosh panel
async def open_home(ev, state: FSMContext) -> None:
    await state.clear()
    s = db.stats()
    text = (f"🛠 Admin panel\n\n👥 Foydalanuvchilar: {s['users']}   💎 Premium: {s['premium']}\n"
            f"💳 Kutilayotgan to'lovlar: {s['pending']}")
    markup = kb(
        [B(text="📊 Statistika", callback_data="adm:stats"),
         B(text=f"💳 To'lovlar ({s['pending']})", callback_data="adm:pay")],
        [B(text="👥 Foydalanuvchi", callback_data="adm:users"), B(text="📢 Xabar tarqatish", callback_data="adm:bc")],
        [B(text="📝 Istisno so'zlar", callback_data="adm:words"), B(text="⚙️ Sozlamalar", callback_data="adm:set")],
        [B(text="🚫 Bloklanganlar", callback_data="adm:banned")],
        [B(text="🏠 Foydalanuvchi menyusi", callback_data="u:home")],
    )
    await show(ev, text, markup)


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await open_home(m, state)


@router.callback_query(F.data == "adm:home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await open_home(cb, state)


@router.callback_query(F.data == "adm:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await open_home(cb, state)


# -------------------------------------------------------------------- statistika
def stats_text() -> str:
    s = db.stats()
    baza = "PostgreSQL (doimiy)" if db.is_postgres else "SQLite (lokal)"
    return ("📊 Statistika\n\n"
            f"👥 Foydalanuvchilar: {s['users']} (🤝 referal: {s['referred']})\n💎 Faol premium: {s['premium']}\n🚫 Bloklangan: {s['banned']}\n"
            f"📁 Bugun: {s['files_today']} fayl, {s['chars_today']} belgi\n"
            f"💳 Kutilayotgan to'lovlar: {s['pending']}\n"
            f"💰 Daromad: 30 kunda {money(s['income_30d'])} so'm, jami {money(s['income_total'])} so'm\n"
            f"🗄 Baza: {baza}")


@router.message(Command("stats"))
async def cmd_stats(m: Message):
    await m.answer(stats_text(), reply_markup=BACK)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(cb: CallbackQuery):
    await show(cb, stats_text(), BACK)


# ---------------------------------------------------------------------- to'lovlar
@router.callback_query(F.data == "adm:pay")
async def cb_pay(cb: CallbackQuery):
    pend = db.pending_payments(20)
    if not pend:
        text = "💳 To'lovlar\n\n✅ Kutilayotgan to'lov yo'q."
    else:
        text = f"💳 Kutilayotgan to'lovlar: {len(pend)}\n\nKo'rish uchun tanlang 👇"
    rows = [[B(text=f"#{p['id']} · {p['full_name'] or p['user_id']} · {p['days']} kun · {money(p['amount'])}",
               callback_data=f"adm:pay:{p['id']}")] for p in pend]
    rows.append([B(text="📜 Tarix", callback_data="adm:payhist")])
    rows.append([B(text="🔙 Admin panel", callback_data="adm:home")])
    await show(cb, text, kb(*rows))


@router.callback_query(F.data == "adm:payhist")
async def cb_payhist(cb: CallbackQuery):
    rows = db.recent_payments(15)
    lines = ["📜 So'nggi to'lovlar\n"]
    for p in rows:
        icon = STATUS.get(p["status"], "?").split()[0]
        lines.append(f"{icon} #{p['id']} · {who(p)} · {p['days']} kun · {money(p['amount'])} · {fmt_dt(p['created'])}")
    if not rows:
        lines.append("Hali to'lovlar yo'q.")
    await show(cb, "\n".join(lines), kb([B(text="🔙 To'lovlar", callback_data="adm:pay")]))


@router.callback_query(F.data.regexp(r"^adm:pay:\d+$"))
async def cb_pay_detail(cb: CallbackQuery, bot: Bot):
    p = db.get_payment(int(cb.data.split(":")[2]))
    if not p:
        await cb.answer("To'lov topilmadi", show_alert=True)
        return
    markup = decide_kb(p["id"]) if p["status"] == "pending" else kb([B(text="🔙 To'lovlar", callback_data="adm:pay")])
    await send_proof(bot, cb.from_user.id, p, pay_caption(p), markup)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:(ok|no):\d+$"))
async def cb_decide(cb: CallbackQuery, bot: Bot):
    _, action, pid_s = cb.data.split(":")
    pid = int(pid_s)
    approve = action == "ok"
    if not db.decide_payment(pid, "approved" if approve else "rejected", cb.from_user.id):
        await cb.answer("Bu to'lov allaqachon qayta ishlangan", show_alert=True)
        return
    p = db.get_payment(pid)
    if approve:
        until = db.grant(p["user_id"], p["days"])
        await notify_user(bot, p["user_id"],
                          f"🎉 To'lovingiz tasdiqlandi!\n💎 Premium {fmt_date(until)} gacha faol.", back_home_kb())
    else:
        await notify_user(bot, p["user_id"],
                          f"❌ #{pid} to'lovingiz tasdiqlanmadi.\nMuammo bo'lsa, admin bilan bog'laning.", back_home_kb())
    await edit_any(cb, pay_caption(p), kb([B(text="🔙 To'lovlar", callback_data="adm:pay")]))


# ------------------------------------------------------------------ foydalanuvchi
def user_card(u: dict) -> str:
    files, chars = db.usage_today(u["id"])
    prem = f"{fmt_date(u['premium_until'])} gacha" if u["premium_until"] > time.time() else "yo'q"
    uname = f" @{u['username']}" if u["username"] else ""
    banned = "ha" if u["banned"] else "yo'q"
    return (f"👤 {u['full_name'] or '—'}{uname}\n🆔 {u['id']}\n📅 Qo'shilgan: {fmt_date(u['joined'])}\n"
            f"💎 Premium: {prem}\n📊 Bugun: {files} fayl, {chars} belgi\n"
            f"🤝 Taklif qilganlar: {u.get('ref_total', 0)} ta\n🚫 Bloklangan: {banned}"
            + ("\n📴 Botni to'xtatgan" if u["blocked"] else ""))


def user_kb(u: dict):
    uid = u["id"]
    ban_btn = (B(text="✅ Blokdan chiqarish", callback_data=f"adm:u:unban:{uid}") if u["banned"]
               else B(text="🚫 Bloklash", callback_data=f"adm:u:ban:{uid}"))
    return kb(
        [B(text="➕ 30 kun", callback_data=f"adm:u:grant:{uid}:30"), B(text="➕ 90 kun", callback_data=f"adm:u:grant:{uid}:90")],
        [B(text="➖ Premiumni bekor qilish", callback_data=f"adm:u:revoke:{uid}")],
        [ban_btn],
        [B(text="🔙 Admin panel", callback_data="adm:home")],
    )


async def show_user(ev, u: dict) -> None:
    await show(ev, user_card(u), user_kb(u))


@router.callback_query(F.data == "adm:users")
async def cb_users(cb: CallbackQuery, state: FSMContext):
    await state.set_state(A.user)
    await show(cb, "👥 Foydalanuvchi ID raqami yoki @username ni yuboring:", CANCEL)


@router.message(Command("user"))
async def cmd_user(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    if not command.args:
        await state.set_state(A.user)
        await m.answer("👥 Foydalanuvchi ID raqami yoki @username ni yuboring:", reply_markup=CANCEL)
        return
    await _lookup(m, command.args)


@router.message(A.user, NOT_CMD)
async def got_user(m: Message, state: FSMContext):
    await state.clear()
    await _lookup(m, m.text or "")


async def _lookup(m: Message, query: str) -> None:
    u = db.find_user(query)
    if not u:
        await m.answer("Foydalanuvchi topilmadi (u botni hali ishga tushirmagan bo'lishi mumkin).", reply_markup=BACK)
        return
    await show_user(m, u)


@router.callback_query(F.data.startswith("adm:u:"))
async def cb_user_action(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")          # adm:u:<action>:<uid>[:<days>]
    try:
        action, uid = parts[2], int(parts[3])
    except (IndexError, ValueError):
        await cb.answer()
        return
    if action == "grant":
        days = int(parts[4])
        until = db.grant(uid, days)
        await notify_user(bot, uid, f"🎁 Sizga {days} kunlik premium berildi.\n💎 {fmt_date(until)} gacha faol.", back_home_kb())
    elif action == "revoke":
        db.set_premium(uid, 0)
    elif action == "ban":
        db.set_banned(uid, True)
    elif action == "unban":
        db.set_banned(uid, False)
    u = db.get_user(uid)
    if u:
        await show_user(cb, u)
    else:
        await cb.answer()


def _uid_days(command: CommandObject):
    try:
        a, b = (command.args or "").split()
        return int(a), int(b)
    except ValueError:
        return None


@router.message(Command("grant"))
async def cmd_grant(m: Message, command: CommandObject, bot: Bot):
    parsed = _uid_days(command)
    if not parsed:
        await m.answer("Foydalanish: /grant <user_id> <kun>")
        return
    uid, days = parsed
    until = db.grant(uid, days)
    await m.answer(f"✅ {uid} uchun premium {fmt_date(until)} gacha.")
    if days > 0:
        await notify_user(bot, uid, f"🎁 Sizga {days} kunlik premium berildi.\n💎 {fmt_date(until)} gacha faol.", back_home_kb())


@router.message(Command("ban"))
async def cmd_ban(m: Message, command: CommandObject):
    if (command.args or "").strip().lstrip("-").isdigit():
        db.set_banned(int(command.args.strip()), True)
        await m.answer("🚫 Bloklandi.")
    else:
        await m.answer("Foydalanish: /ban <user_id>")


@router.message(Command("unban"))
async def cmd_unban(m: Message, command: CommandObject):
    if (command.args or "").strip().lstrip("-").isdigit():
        db.set_banned(int(command.args.strip()), False)
        await m.answer("✅ Blokdan chiqarildi.")
    else:
        await m.answer("Foydalanish: /unban <user_id>")


@router.callback_query(F.data == "adm:banned")
async def cb_banned(cb: CallbackQuery):
    rows = db.banned_list(30)
    text = f"🚫 Bloklanganlar: {len(rows)}" if rows else "🚫 Bloklangan foydalanuvchi yo'q."
    btns = [[B(text=f"✅ {u['full_name'] or u['id']} ({u['id']})", callback_data=f"adm:u:unban:{u['id']}")] for u in rows]
    btns.append([B(text="🔙 Admin panel", callback_data="adm:home")])
    await show(cb, text + ("\n\nBlokdan chiqarish uchun bosing 👇" if rows else ""), kb(*btns))


# ------------------------------------------------------------------ xabar tarqatish
@router.callback_query(F.data == "adm:bc")
async def cb_bc(cb: CallbackQuery, state: FSMContext):
    await state.set_state(A.broadcast)
    await show(cb, "📢 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring (matn, rasm, fayl, hammasi bo'ladi):", CANCEL)


@router.message(Command("broadcast"))
async def cmd_bc(m: Message, state: FSMContext):
    await state.set_state(A.broadcast)
    await m.answer("📢 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring (matn, rasm, fayl, hammasi bo'ladi):",
                   reply_markup=CANCEL)


@router.message(A.broadcast, NOT_CMD)
async def got_bc(m: Message, state: FSMContext, bot: Bot):
    await state.update_data(chat_id=m.chat.id, message_id=m.message_id)
    n = len(db.broadcast_ids())
    await m.answer("👆 Xabar shunday ko'rinadi.", reply_to_message_id=m.message_id)
    await m.answer(f"📢 Xabar {n} ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?",
                   reply_markup=kb([B(text="✅ Yuborish", callback_data="adm:bc:go"),
                                    B(text="❌ Bekor qilish", callback_data="adm:cancel")]))


@router.callback_query(F.data == "adm:bc:go")
async def cb_bc_go(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    if "message_id" not in data:
        await cb.answer("Xabar topilmadi", show_alert=True)
        return
    ids = db.broadcast_ids()
    task = asyncio.create_task(_broadcast(bot, cb.from_user.id, data["chat_id"], data["message_id"], ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    await show(cb, f"📤 Yuborish boshlandi: {len(ids)} ta foydalanuvchi.\nTugagach hisobot keladi.", BACK)


async def _broadcast(bot: Bot, admin_id: int, chat_id: int, message_id: int, ids: list[int]) -> None:
    ok = failed = blocked = 0
    for uid in ids:
        for attempt in (1, 2):
            try:
                await bot.copy_message(uid, chat_id, message_id)
                ok += 1
            except TelegramRetryAfter as e:
                if attempt == 1:
                    await asyncio.sleep(e.retry_after)
                    continue
                failed += 1
            except TelegramForbiddenError:
                db.mark_blocked(uid)
                blocked += 1
            except Exception:  # noqa: BLE001
                failed += 1
            break
        await asyncio.sleep(0.05)   # Telegram limiti: sekundiga ~20-30 xabar
    try:
        await bot.send_message(admin_id, f"📢 Tarqatish tugadi\n\n✅ Yuborildi: {ok}\n📴 Botni to'xtatgan: {blocked}\n⚠️ Xato: {failed}")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- istisno so'zlar
WORDS_HELP = ("Format: eski_lotin yangi\nMasalan: so'zlik sözlik\n\n"
              "Eski shakl hozirgi lotin imlosida (apostrof bilan), yangi shakl yangi alifboda yoziladi.")


def _words_kb():
    return kb(
        [B(text="➕ Qo'shish", callback_data="adm:w:add"), B(text="🗑 O'chirish", callback_data="adm:w:del")],
        [B(text="📄 Ro'yxat", callback_data="adm:w:list")],
        [B(text="🔙 Admin panel", callback_data="adm:home")],
    )


@router.callback_query(F.data == "adm:words")
async def cb_words(cb: CallbackQuery):
    await show(cb, f"📝 Istisno so'zlar: {len(EXCEPTIONS)} ta\n\nBot bu so'zlarni qoidadan tashqari, aynan siz belgilagandek o'zgartiradi.", _words_kb())


@router.callback_query(F.data == "adm:w:list")
async def cb_wlist(cb: CallbackQuery):
    items = list(EXCEPTIONS.items())[:60]
    body = "\n".join(f"{k} → {v}" for k, v in items) or "Hozircha bo'sh."
    more = f"\n… va yana {len(EXCEPTIONS) - 60} ta" if len(EXCEPTIONS) > 60 else ""
    await show(cb, f"📄 Istisno so'zlar ({len(EXCEPTIONS)})\n\n{body}{more}", _words_kb())


@router.callback_query(F.data == "adm:w:add")
async def cb_wadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(A.word_add)
    await show(cb, f"➕ Yangi istisno so'z\n\n{WORDS_HELP}", CANCEL)


@router.callback_query(F.data == "adm:w:del")
async def cb_wdel(cb: CallbackQuery, state: FSMContext):
    await state.set_state(A.word_del)
    await show(cb, "🗑 O'chiriladigan so'zning eski shaklini yuboring:", CANCEL)


def _parse_pair(text: str):
    parts = (text or "").split()
    return (parts[0], parts[1]) if len(parts) == 2 else None


@router.message(Command("addword"))
async def cmd_addword(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    pair = _parse_pair(command.args or "")
    if not pair:
        await m.answer(f"Foydalanish: /addword eski yangi\n\n{WORDS_HELP}")
        return
    add_exception(*pair)
    await m.answer(f"✅ Qo'shildi: {pair[0].lower()} → {pair[1].lower()}\nJami: {len(EXCEPTIONS)} ta", reply_markup=_words_kb())


@router.message(Command("delword"))
async def cmd_delword(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    word = (command.args or "").strip()
    if not word:
        await m.answer("Foydalanish: /delword eski")
        return
    done = delete_exception(word)
    await m.answer("🗑 O'chirildi." if done else "Bunday so'z ro'yxatda yo'q.", reply_markup=_words_kb())


@router.message(A.word_add, NOT_CMD)
async def got_word_add(m: Message, state: FSMContext):
    pair = _parse_pair(m.text)
    if not pair:
        await m.answer(f"❗ Ikki so'z yuboring.\n\n{WORDS_HELP}", reply_markup=CANCEL)
        return
    await state.clear()
    add_exception(*pair)
    await m.answer(f"✅ Qo'shildi: {pair[0].lower()} → {pair[1].lower()}\nJami: {len(EXCEPTIONS)} ta", reply_markup=_words_kb())


@router.message(A.word_del, NOT_CMD)
async def got_word_del(m: Message, state: FSMContext):
    await state.clear()
    done = delete_exception((m.text or "").strip())
    await m.answer("🗑 O'chirildi." if done else "Bunday so'z ro'yxatda yo'q.", reply_markup=_words_kb())


# ------------------------------------------------------------------- sozlamalar
def settings_text() -> str:
    fast = get_fast_plan()
    fast_line = f"⚡ Tezkor tarif: {fast['days']} kun — {money(fast['price'])} so'm" if fast else "⚡ Tezkor tarif: o'chirilgan"
    rc, rd = ref_count(), ref_days()
    ref_line = f"🎁 Referal: {rc} ta do'st → {rd} kun Premium" if (rc > 0 and rd > 0) else "🎁 Referal: o'chirilgan"
    plans = "\n".join(f"   ⭐ {p['days']} kun — {money(p['price'])} so'm" for p in get_plans()) or "   (yo'q)"
    card = card_info()
    return ("⚙️ Sozlamalar\n\n"
            f"📁 Bepul fayl limiti: {free_files()} ta/kun\n🔤 Bepul matn limiti: {free_chars()} belgi/kun\n"
            f"{fast_line}\n{ref_line}\n\n"
            f"💰 Tariflar:\n{plans}\n\n💳 To'lov matni:\n{card[:300]}")


def settings_kb():
    return kb(
        [B(text="💳 To'lov matni", callback_data="adm:set:card"), B(text="💰 Tariflar", callback_data="adm:set:plan")],
        [B(text="⚡ Tezkor tarif", callback_data="adm:set:fast"), B(text="🎁 Referal", callback_data="adm:set:ref")],
        [B(text="📁 Fayl limiti", callback_data="adm:set:ff"), B(text="🔤 Matn limiti", callback_data="adm:set:fc")],
        [B(text="🔙 Admin panel", callback_data="adm:home")],
    )


@router.callback_query(F.data == "adm:set")
async def cb_set(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, settings_text(), settings_kb())


PROMPTS = {
    "card": (A.card, "💳 Mijozlarga ko'rsatiladigan to'lov matnini yuboring (karta raqami, ism, izoh):"),
    "plan": (A.plan, "💰 Tarifni \"kun narx\" ko'rinishida yuboring.\nMasalan: 30 29000\n"
                     "Mavjud tarif yangilanadi, yangisi qo'shiladi. O'chirish uchun narxni 0 yozing (30 0)."),
    "ff": (A.ff, "📁 Kunlik bepul fayl limitini (son) yuboring:"),
    "fc": (A.fc, "🔤 Kunlik bepul belgi limitini (son) yuboring:"),
}


def _dynamic_prompt(key: str):
    if key == "fast":
        fp = get_fast_plan_raw()
        status = f"{fp['days']} kun — {money(fp['price'])} so'm" if fp["price"] > 0 else f"{fp['days']} kun — o'chirilgan"
        return (A.fast_plan,
                f"⚡ Tezkor tarif sozlamasi\nHozirgi: {status}\n\n"
                "O'zingiz xohlagan narxni yuboring (masalan: 7000)\n"
                "yoki \"kun narx\" ko'rinishida yozing (masalan: 3 5000).\n"
                "O'chirish uchun 0 yuboring.")
    if key == "ref":
        rc, rd = ref_count(), ref_days()
        status = f"{rc} ta do'st → {rd} kun Premium" if (rc > 0 and rd > 0) else "o'chirilgan"
        return (A.ref_cfg,
                f"🎁 Referal sozlamasi\nHozirgi: {status}\n\n"
                "Format: \"do'stlar_soni kun\" ko'rinishida yuboring.\n"
                "Masalan: 3 5  (3 ta do'st taklif qilsa — 5 kun Premium beriladi)\n\n"
                "Referalni o'chirish uchun: 0 0 yuboring.")
    return PROMPTS[key]


@router.callback_query(F.data.regexp(r"^adm:set:(card|plan|fast|ref|ff|fc)$"))
async def cb_set_prompt(cb: CallbackQuery, state: FSMContext):
    st, prompt = _dynamic_prompt(cb.data.split(":")[2])
    await state.set_state(st)
    await show(cb, prompt, CANCEL)


async def _saved(m: Message, state: FSMContext, note: str) -> None:
    await state.clear()
    await m.answer(f"✅ {note}\n\n{settings_text()}", reply_markup=settings_kb())


@router.message(A.card, NOT_CMD)
async def got_card(m: Message, state: FSMContext):
    text = (m.text or "").strip()
    if not text or len(text) > 1500:
        await m.answer("❗ Matn bo'sh yoki juda uzun (1500 belgigacha).", reply_markup=CANCEL)
        return
    db.set_setting("card_info", text)
    await _saved(m, state, "To'lov matni saqlandi")


@router.message(A.plan, NOT_CMD)
async def got_plan(m: Message, state: FSMContext):
    parts = (m.text or "").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or not (1 <= int(parts[0]) <= 3650) or int(parts[1]) > 100_000_000:
        await m.answer("❗ Format: kun narx. Masalan: 30 29000", reply_markup=CANCEL)
        return
    days, price = int(parts[0]), int(parts[1])
    plans = [p for p in get_plans() if p["days"] != days]
    if price > 0:
        plans.append({"days": days, "price": price})
    set_plans(plans)
    await _saved(m, state, "Tarif o'chirildi" if price == 0 else "Tarif saqlandi")


@router.message(A.fast_plan, NOT_CMD)
async def got_fast_plan(m: Message, state: FSMContext):
    parts = (m.text or "").split()
    cur = get_fast_plan_raw()
    if len(parts) == 1 and parts[0].isdigit() and int(parts[0]) <= 100_000_000:
        days, price = cur["days"], int(parts[0])
    elif (len(parts) == 2 and all(p.isdigit() for p in parts)
          and 1 <= int(parts[0]) <= 3650 and int(parts[1]) <= 100_000_000):
        days, price = int(parts[0]), int(parts[1])
    else:
        await m.answer("❗ Narx (masalan: 7000) yoki \"kun narx\" (masalan: 3 5000) yuboring.", reply_markup=CANCEL)
        return
    set_fast_plan(days, price)
    await _saved(m, state, "Tezkor tarif o'chirildi" if price == 0 else f"Tezkor tarif saqlandi: {days} kun — {money(price)} so'm")


@router.message(A.ref_cfg, NOT_CMD)
async def got_ref_cfg(m: Message, state: FSMContext):
    parts = (m.text or "").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await m.answer("❗ Format: do'stlar_soni kun. Masalan: 3 5 (o'chirish uchun: 0 0)", reply_markup=CANCEL)
        return
    cnt, days = int(parts[0]), int(parts[1])
    if (cnt == 0 and days == 0) or (1 <= cnt <= 1000 and 1 <= days <= 3650):
        set_ref_settings(cnt, days)
        note = "Referal tizimi o'chirildi" if cnt == 0 else f"Referal saqlandi: {cnt} ta do'st → {days} kun"
        await _saved(m, state, note)
        return
    await m.answer("❗ Limit 1–1000, kun 1–3650 oralig'ida bo'lsin (yoki 0 0).", reply_markup=CANCEL)


@router.message(A.ff, NOT_CMD)
async def got_ff(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit() or int(m.text) > 1000:
        await m.answer("❗ 0 dan 1000 gacha son yuboring.", reply_markup=CANCEL)
        return
    db.set_setting("free_files", str(int(m.text)))
    await _saved(m, state, "Fayl limiti saqlandi")


@router.message(A.fc, NOT_CMD)
async def got_fc(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit() or int(m.text) > 10_000_000:
        await m.answer("❗ Butun son yuboring.", reply_markup=CANCEL)
        return
    db.set_setting("free_chars", str(int(m.text)))
    await _saved(m, state, "Matn limiti saqlandi")
