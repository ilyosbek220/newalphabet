"""UI yordamchilari: tugmalar, matnlar va xabarni tahrirlash."""
from __future__ import annotations

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

SUPPORTED_TEXT = ".docx .xlsx .pptx .doc .xls .ppt .txt .csv .md"

START_TEXT = (
    "👋 Assalomu alaykum!\n\n"
    "Men matn va hujjatlarni yangi o'zbek alifbosiga o'tkazaman.\n"
    "Oʻ → Ö   Gʻ → Ğ   Sh → Ş   Ch → Ç\n\n"
    "📝 Matn yuboring, o'zgartirib qaytaraman\n"
    f"📎 Yoki fayl yuboring ({SUPPORTED_TEXT}), formati buzilmasdan qaytaraman\n\n"
    "🔤 Lotin ham, kirill ham qo'llab-quvvatlanadi.\n"
    "🔒 Fayllaringiz saqlanmaydi, ishlangach darhol o'chiriladi."
)

RULES_TEXT = (
    "📖 Yangi alifbo qoidalari\n\n"
    "Qonun 2026-yil 21-oktabrda imzolangan, 2027-yil 22-aprelda kuchga kiradi.\n\n"
    "Oʻ → Ö     O'zbek → Özbek\n"
    "Gʻ → Ğ     G'alaba → Ğalaba\n"
    "Sh → Ş     shahar → şahar\n"
    "Ch → Ç     choy → çoy\n\n"
    "🔗 Havolalar, email va @username'lar o'zgartirilmaydi.\n"
    "🔎 Noaniq so'zlar tekshirish uchun alohida ko'rsatiladi."
)


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def home_kb(is_admin: bool = False, has_ref: bool = True) -> InlineKeyboardMarkup:
    second = [B(text="💎 Obunalar", callback_data="u:plans")]
    if has_ref:
        second.append(B(text="🎁 Bepul Premium", callback_data="u:ref"))
    rows = [
        [B(text="📖 Qoidalar", callback_data="u:rules"), B(text="📊 Limitim", callback_data="u:me")],
        second,
    ]
    if is_admin:   # faqat adminga ko'rinadi
        rows.append([B(text="🛠 Admin panel", callback_data="adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_home_kb(extra=None) -> InlineKeyboardMarkup:
    rows = [extra] if extra else []
    rows.append([B(text="🏠 Bosh menyu", callback_data="u:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Buyruqdan keyin handler "command message" qabul qilmasligi uchun: "/" bilan boshlanmagan xabarlar
NOT_CMD = F.func(lambda m: not (getattr(m, "text", None) or "").startswith("/"))


async def show(ev, text: str, markup=None) -> None:
    """Callback bo'lsa xabarni tahrirlaydi, aks holda yangi xabar yuboradi."""
    if isinstance(ev, CallbackQuery):
        try:
            await ev.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                await ev.message.answer(text, reply_markup=markup)
        await ev.answer()
    else:
        await ev.answer(text, reply_markup=markup)


async def edit_any(cb: CallbackQuery, text: str, markup=None) -> None:
    """Matnli yoki rasm/fayl (caption) xabarni tahrirlaydi."""
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                await cb.message.answer(text, reply_markup=markup)
    await cb.answer()
