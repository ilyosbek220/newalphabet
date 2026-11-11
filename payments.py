"""Obuna sotib olish: tariflar, to'lov cheki yuborish, adminga bildirishnoma."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import (ADMINS, card_info, db, fmt_date, fmt_dt, get_fast_plan, get_plans,
                    money, ref_count, ref_days, touch)
from ui import B, back_home_kb, kb, show

log = logging.getLogger("alifbo-bot")
router = Router(name="payments")

MAX_PENDING_PER_USER = 3
STATUS = {"pending": "⏳ Kutilmoqda", "approved": "✅ Tasdiqlangan", "rejected": "❌ Rad etilgan"}


class PayStates(StatesGroup):
    waiting_proof = State()


# ------------------------------------------------------------------ yordamchilar
def who(p: dict) -> str:
    name = p.get("full_name") or "—"
    return f"{name} @{p['username']}" if p.get("username") else name


def pay_caption(p: dict) -> str:
    return (
        f"💳 To'lov #{p['id']}\n"
        f"👤 {who(p)}\n🆔 {p['user_id']}\n"
        f"💎 {p['days']} kun — {money(p['amount'])} so'm\n"
        f"🕒 {fmt_dt(p['created'])}\n"
        f"Holat: {STATUS.get(p['status'], p['status'])}"
    )


def decide_kb(pid: int):
    return kb([B(text="✅ Tasdiqlash", callback_data=f"adm:ok:{pid}"),
               B(text="❌ Rad etish", callback_data=f"adm:no:{pid}")])


async def send_proof(bot: Bot, chat_id: int, p: dict, caption: str, markup=None) -> None:
    if p["proof_type"] == "photo":
        await bot.send_photo(chat_id, p["proof_file_id"], caption=caption, reply_markup=markup)
    else:
        await bot.send_document(chat_id, p["proof_file_id"], caption=caption, reply_markup=markup)


async def notify_admins(bot: Bot, pid: int) -> int:
    p = db.get_payment(pid)
    sent = 0
    for admin_id in ADMINS:
        try:
            await send_proof(bot, admin_id, p, pay_caption(p), decide_kb(pid))
            sent += 1
        except Exception as e:  # noqa: BLE001 - admin botni hali boshlamagan bo'lishi mumkin
            log.warning("admin %s ga bildirishnoma yuborilmadi: %s", admin_id, type(e).__name__)
    return sent


def plans_text(uid: int) -> str:
    fast = get_fast_plan()
    plans = get_plans()
    lines = ["💎 Obunalar", "", "Premium bilan fayl va matn limiti yo'q, cheksiz foydalanasiz.", ""]
    if db.is_premium(uid):
        lines += [f"✅ Premium faol: {fmt_date(db.premium_until(uid))} gacha", "Yangi to'lov mavjud muddatga qo'shiladi.", ""]
    if fast:
        lines.append(f"⚡ Tezkor tarif: {fast['days']} kun — {money(fast['price'])} so'm")
    if plans:
        lines += [f"⭐ {p['days']} kun — {money(p['price'])} so'm" for p in plans]
    if fast or plans:
        lines += ["", "Tarifni tanlang 👇"]
    else:
        lines.append("Hozircha tariflar kiritilmagan.")
    return "\n".join(lines)


def plans_kb():
    rows = []
    fast = get_fast_plan()
    if fast:
        rows.append([B(text=f"⚡ Tezkor: {fast['days']} kun — {money(fast['price'])} so'm", callback_data="u:buy:fast")])
    rows += [[B(text=f"⭐ {p['days']} kun — {money(p['price'])} so'm", callback_data=f"u:buy:{p['days']}")]
             for p in get_plans()]
    if ref_count() > 0 and ref_days() > 0:
        rows.append([B(text="🎁 Do'stlarni taklif qilish (Bepul)", callback_data="u:ref")])
    rows.append([B(text="🏠 Bosh menyu", callback_data="u:home")])
    return kb(*rows)


# --------------------------------------------------------------------- handlerlar
@router.callback_query(F.data == "u:plans")
async def cb_plans(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    touch(cb.from_user)
    await show(cb, plans_text(cb.from_user.id), plans_kb())


@router.message(Command("premium"))
async def cmd_premium(m: Message, state: FSMContext):
    await state.clear()
    touch(m.from_user)
    await show(m, plans_text(m.from_user.id), plans_kb())


@router.callback_query(F.data.regexp(r"^u:buy:(fast|\d+)$"))
async def cb_buy(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key == "fast":
        plan = get_fast_plan()
        tag = " (Tezkor tarif)"
    else:
        days = int(key)
        plan = next((p for p in get_plans() if p["days"] == days), None)
        tag = ""
    if not plan:
        await cb.answer("Bu tarif endi mavjud emas", show_alert=True)
        return
    if db.user_pending_count(cb.from_user.id) >= MAX_PENDING_PER_USER:
        await cb.answer("Sizda tekshirilayotgan to'lovlar bor, admin javobini kuting.", show_alert=True)
        return
    await state.set_state(PayStates.waiting_proof)
    await state.update_data(days=plan["days"], price=plan["price"])
    text = (
        f"💳 To'lov{tag}: {plan['days']} kun — {money(plan['price'])} so'm\n\n"
        f"{card_info()}\n\n"
        "📸 To'lovni amalga oshirgach, chekni (skrinshot) shu yerga yuboring.\n"
        "Admin tekshirib, obunani faollashtiradi."
    )
    await show(cb, text, kb([B(text="❌ Bekor qilish", callback_data="u:plans")]))


@router.message(PayStates.waiting_proof, F.photo | F.document)
async def got_proof(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if "days" not in data:
        await state.clear()
        await m.answer("Sessiya tugadi. Qaytadan tarifni tanlang.", reply_markup=kb([B(text="💎 Obunalar", callback_data="u:plans")]))
        return
    if db.user_pending_count(m.from_user.id) >= MAX_PENDING_PER_USER:
        await state.clear()
        await m.answer("Sizda tekshirilayotgan to'lovlar bor, admin javobini kuting.")
        return
    touch(m.from_user)
    ptype, file_id = ("photo", m.photo[-1].file_id) if m.photo else ("document", m.document.file_id)
    pid = db.create_payment(m.from_user.id, data["days"], data["price"], ptype, file_id)
    await state.clear()
    await notify_admins(bot, pid)
    await m.answer(
        f"✅ Chekingiz qabul qilindi (#{pid}).\n"
        "Admin tekshirgach, obuna avtomatik faollashadi va sizga xabar keladi.",
        reply_markup=back_home_kb(),
    )


@router.message(PayStates.waiting_proof, F.func(lambda m: not (m.text or "").startswith("/")))
async def wrong_proof(m: Message):
    await m.answer("📸 Iltimos, to'lov chekini rasm yoki fayl ko'rinishida yuboring.",
                   reply_markup=kb([B(text="❌ Bekor qilish", callback_data="u:plans")]))


async def notify_user(bot: Bot, uid: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(uid, text, reply_markup=markup)
    except TelegramForbiddenError:
        db.mark_blocked(uid)
    except Exception as e:  # noqa: BLE001
        log.warning("foydalanuvchiga xabar yuborilmadi: %s", type(e).__name__)
