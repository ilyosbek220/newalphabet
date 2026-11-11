"""Yangi o'zbek alifbosi konvertor Telegram boti. Ishga tushirish: python bot.py"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (BotCommand, BotCommandScopeChat, CallbackQuery, FSInputFile, Message)

import admin
import config
import payments
from config import (ADMINS, EXCEPTIONS, MAX_FILE_MB, TOKEN, db, fmt_date, free_chars, free_files,
                    get_fast_plan, is_admin, money, ref_count, ref_days, touch)
from officeconv import SUPPORTED, UnsupportedFile, convert_file
from uzconv import convert, detect_script
from ui import (B, RULES_TEXT, START_TEXT, SUPPORTED_TEXT, back_home_kb, home_kb, kb, show)

log = logging.getLogger("alifbo-bot")
router = Router(name="user")
SEM = asyncio.Semaphore(3)  # bir vaqtda ko'pi bilan 3 ta fayl ishlanadi
PREVIEW_MAX_BYTES = 5 * 1024 * 1024

PLANS_BTN = [B(text="💎 Obunalar", callback_data="u:plans")]


# ---------------------------------------------------------------------- yordamchi
def _ref_enabled() -> bool:
    return ref_count() > 0 and ref_days() > 0


def _home_kb(uid: int):
    return home_kb(is_admin(uid), has_ref=_ref_enabled())


def _limit_kb():
    rows = []
    fast = get_fast_plan()
    if fast:
        rows.append([B(text=f"⚡ Tezkor tarif: {fast['days']} kun — {money(fast['price'])} so'm", callback_data="u:buy:fast")])
    rows.append(PLANS_BTN)
    if _ref_enabled():
        rows.append([B(text="🎁 Do'stlarni taklif qilish (Bepul)", callback_data="u:ref")])
    return kb(*rows)


def _limit_preview_text(
    header: str, analysis_title: str, subject_noun: str, preview: tuple[int, int] | None, cta: str
) -> str:
    lines = [header]
    if preview is not None:
        changed, flagged = preview
        lines.extend(["", f"📊 {analysis_title}:"])
        lines.append(
            f"• {changed} ta so'z to'liq o'girishga tayyor" if changed > 0
            else f"• {subject_noun} tekshiruvga tayyor"
        )
        if flagged > 0:
            lines.append(f"• {flagged} ta bahsli/imloviy so'z aniqlandi")
    lines.extend(["", f"🔓 {cta} 👇"])
    return "\n".join(lines)


def _chunks(text: str, size: int = 4000):
    while text:
        if len(text) <= size:
            yield text
            return
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip("\n")


def _flags_line(flags, limit: int = 8) -> str:
    words = list(dict.fromkeys(f.word for f in flags))
    if not words:
        return ""
    more = f" va yana {len(words) - limit} ta" if len(words) > limit else ""
    return "🔎 Tekshirib ko'ring: " + ", ".join(words[:limit]) + more


def me_text(uid: int) -> str:
    files, chars = db.usage_today(uid)
    if db.is_premium(uid):
        text = (f"📊 Limitim\n\n💎 Premium faol: {fmt_date(db.premium_until(uid))} gacha\n"
                f"♾ Cheklov yo'q\n\nBugun: 📁 {files} fayl, 🔤 {chars} belgi")
    else:
        text = (f"📊 Limitim\n\n💎 Premium: yo'q\n\nBugungi bepul limit:\n"
                f"📁 Fayllar: {files}/{free_files()}\n🔤 Matn: {chars}/{free_chars()} belgi")
    if _ref_enabled():
        u = db.get_user(uid)
        prog = int(u["ref_progress"] or 0) if u else 0
        total = int(u["ref_total"] or 0) if u else 0
        text += f"\n\n🎁 Takliflar: {prog}/{ref_count()} (Jami: {total} ta) — /ref"
    if not db.is_premium(uid):
        text += "\n\nCheksiz foydalanish uchun obuna oling 👇"
    return text


def me_kb(uid: int):
    # Premium bo'lmaganlarga "Obunalar" tugmasi, hammaga "Bosh menyu"
    return back_home_kb(None if db.is_premium(uid) else PLANS_BTN)


async def _bot_username(bot: Bot) -> str:
    if config.BOT_USERNAME:
        return config.BOT_USERNAME
    try:
        me = await bot.get_me()
        username = getattr(me, "username", None) or ""
        if username:
            config.BOT_USERNAME = username
        return username
    except Exception:  # noqa: BLE001
        return ""


async def _ref_screen(uid: int, bot: Bot):
    if not _ref_enabled():
        return (
            "🎁 Referal dasturi\n\nHozircha referal dasturi vaqtincha o'chirilgan.",
            back_home_kb(None),
        )
    rc, rd = ref_count(), ref_days()
    u = db.get_user(uid)
    prog = int(u["ref_progress"] or 0) if u else 0
    total = int(u["ref_total"] or 0) if u else 0
    left = max(1, rc - prog)
    username = await _bot_username(bot)
    link = f"https://t.me/{username}?start=ref_{uid}" if username else f"/start ref_{uid}"
    text = (
        "🎁 Bepul Premium — Do'stlarni taklif qiling!\n\n"
        f"Har {rc} ta yangi do'stingiz botga kirsa, sizga avtomatik "
        f"{rd} kunlik BEPUL Premium beriladi!\n\n"
        "📊 Natijangiz:\n"
        f"• Joriy bosqich: {prog}/{rc} ta do'st (yana {left} ta qoldi)\n"
        f"• Jami taklif qilganlar: {total} ta\n\n"
        f"🔗 Shaxsiy havolangiz:\n{link}\n\n"
        "💡 Faqat botga birinchi marta kirgan foydalanuvchilar hisoblanadi."
    )
    rows = []
    if username:
        share_msg = quote("Lotin <-> Yangi o'zbek alifbosi konvertori va Word/Excel/PDF bot! Kiring:")
        share_url = f"https://t.me/share/url?url={quote(link)}&text={share_msg}"
        rows.append([B(text="📤 Do'stlarga ulashish", url=share_url)])
    rows.append(PLANS_BTN)
    rows.append([B(text="🏠 Bosh menyu", callback_data="u:home")])
    return text, kb(*rows)


# ------------------------------------------------------------------------ menyu
@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    ref_award = None
    arg = (command.args or "").strip() if command else ""
    if arg.startswith("ref_") and _ref_enabled():
        raw_ref = arg[4:]
        if raw_ref.isdigit():
            ref_award = db.register_referral(m.from_user.id, int(raw_ref), ref_count(), ref_days())
    touch(m.from_user)
    await m.answer(START_TEXT, reply_markup=_home_kb(m.from_user.id))
    if ref_award:
        ref_uid = ref_award["ref_uid"]
        prog, need = ref_award["progress"], ref_award["need"]
        if ref_award["awarded_days"] > 0:
            note = (
                f"🎉 Tabriklaymiz! Yangi do'stingiz sizning havolangiz orqali qo'shildi "
                f"({need}/{need}).\n\n"
                f"🎁 Sizga {ref_award['awarded_days']} kunlik BEPUL Premium berildi!\n"
                f"📅 Amal qilish muddati: {fmt_date(ref_award['premium_until'])} gacha."
            )
        else:
            note = (
                f"🤝 Yangi do'stingiz sizning havolangiz orqali qo'shildi!\n"
                f"📊 Jarayon: {prog}/{need} (yana {max(1, need - prog)} ta qoldi)."
            )
        await payments.notify_user(bot, ref_uid, note)


@router.message(Command("help"))
async def cmd_help(m: Message, state: FSMContext):
    await state.clear()
    touch(m.from_user)
    await m.answer(START_TEXT, reply_markup=_home_kb(m.from_user.id))


@router.callback_query(F.data == "u:home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    touch(cb.from_user)
    await show(cb, START_TEXT, _home_kb(cb.from_user.id))


@router.message(Command("ref"))
async def cmd_ref(m: Message, bot: Bot):
    touch(m.from_user)
    text, markup = await _ref_screen(m.from_user.id, bot)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data == "u:ref")
async def cb_ref(cb: CallbackQuery, bot: Bot):
    touch(cb.from_user)
    text, markup = await _ref_screen(cb.from_user.id, bot)
    await show(cb, text, markup)


@router.callback_query(F.data == "u:rules")
async def cb_rules(cb: CallbackQuery):
    await show(cb, RULES_TEXT, back_home_kb())


@router.message(Command("rules"))
async def cmd_rules(m: Message):
    await show(m, RULES_TEXT, back_home_kb())


@router.callback_query(F.data == "u:me")
async def cb_me(cb: CallbackQuery):
    touch(cb.from_user)
    await show(cb, me_text(cb.from_user.id), me_kb(cb.from_user.id))


@router.message(Command("me"))
async def cmd_me(m: Message):
    touch(m.from_user)
    await show(m, me_text(m.from_user.id), me_kb(m.from_user.id))


@router.callback_query(F.data.startswith("adm:"))
async def cb_not_admin(cb: CallbackQuery):
    await cb.answer()   # admin bo'lmaganlar uchun hech narsa ko'rsatilmaydi


# ------------------------------------------------------------------------ matn
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    uid, text = m.from_user.id, m.text
    touch(m.from_user)
    used = db.usage_today(uid)[1]
    limit = free_chars()
    if not db.is_premium(uid) and used + len(text) > limit:
        res = convert(text, EXCEPTIONS)
        flagged = len(dict.fromkeys(f.word for f in res.flags))
        msg = _limit_preview_text(
            f"⛔ Bugungi bepul matn limiti tugadi ({used}/{limit} belgi).",
            f"Yuborilgan matn tahlili ({len(text)} belgi)",
            "Matn",
            (res.stats["changed"], flagged),
            "Hozirgi matnni to'liq olish va cheksiz foydalanish uchun quyidagilardan birini tanlang",
        )
        await m.answer(msg, reply_markup=_limit_kb())
        return
    res = convert(text, EXCEPTIONS)
    if res.stats["changed"] == 0:
        hint = ("✅ Matn allaqachon yangi alifboda ko'rinadi." if detect_script(text) == "new"
                else "🤷 O'zgartiriladigan so'z topilmadi.")
        await m.answer(hint)
        return
    db.add_usage(uid, chars=len(text))
    for part in _chunks(res.text):
        await m.answer(part)
    line = _flags_line(res.flags)
    if line:
        await m.answer(line)


# ------------------------------------------------------------------------ fayl
async def _preview_doc(bot: Bot, doc) -> tuple[int, int] | None:
    if doc.file_size and doc.file_size > PREVIEW_MAX_BYTES:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="alifbo_"))
    try:
        out_dir = tmp / "out"
        out_dir.mkdir()
        src = tmp / Path(doc.file_name or "fayl").name
        await bot.download(doc, destination=src)
        async with SEM:
            _dst, rep = await asyncio.wait_for(
                asyncio.to_thread(convert_file, src, out_dir, EXCEPTIONS), timeout=30
            )
        flagged = len(dict.fromkeys(f.word for f in rep.flags))
        return rep.changed, flagged
    except Exception:  # noqa: BLE001
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.message(F.document)
async def on_document(m: Message, bot: Bot):
    uid, doc = m.from_user.id, m.document
    touch(m.from_user)
    name = Path(doc.file_name or "fayl").name
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        await m.answer(f"⚠️ Bu format qo'llab-quvvatlanmaydi.\nMumkin: {SUPPORTED_TEXT}")
        return
    if doc.file_size and doc.file_size > MAX_FILE_MB * 1024 * 1024:
        await m.answer(f"⚠️ Fayl juda katta (ko'pi bilan {MAX_FILE_MB} MB).")
        return
    if not db.is_premium(uid) and db.usage_today(uid)[0] >= free_files():
        preview = await _preview_doc(bot, doc)
        msg = _limit_preview_text(
            f"⛔ Bugungi bepul fayl limiti tugadi ({free_files()} ta).",
            f"«{name}» fayli tahlili",
            "Fayl",
            preview,
            "Tayyor faylni olish va cheksiz konvertatsiya uchun quyidagilardan birini tanlang",
        )
        await m.answer(msg, reply_markup=_limit_kb())
        return

    status = await m.answer("⏳ Fayl ishlanmoqda...")
    tmp = Path(tempfile.mkdtemp(prefix="alifbo_"))
    try:
        out_dir = tmp / "out"
        out_dir.mkdir()
        src = tmp / name
        await bot.download(doc, destination=src)
        async with SEM:
            dst, rep = await asyncio.wait_for(
                asyncio.to_thread(convert_file, src, out_dir, EXCEPTIONS), timeout=180
            )
        if rep.changed == 0:
            await m.answer("🤷 Faylda o'zgartiriladigan so'z topilmadi (allaqachon yangi alifboda bo'lishi mumkin).")
            return
        caption = f"✅ Tayyor: {rep.changed} ta so'z o'zgartirildi."
        line = _flags_line(rep.flags)
        if line:
            caption += "\n" + line
        await m.answer_document(FSInputFile(dst, filename=dst.name), caption=caption[:1000])
        db.add_usage(uid, files=1)
    except UnsupportedFile as e:
        await m.answer(f"⚠️ {e}")
    except asyncio.TimeoutError:
        await m.answer("⌛ Fayl juda murakkab, vaqt tugadi. Kichikroq qismlarga bo'lib yuboring.")
    except Exception as e:  # noqa: BLE001 - matn loglarga tushmasligi uchun faqat tur yoziladi
        log.error("fayl xatosi: %s", type(e).__name__)
        await m.answer("⚠️ Faylni ishlashda xatolik chiqdi. Boshqa fayl bilan urinib ko'ring.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            await status.delete()
        except Exception:  # noqa: BLE001
            pass


@router.message()
async def fallback(m: Message):
    await m.answer("📝 Matn yoki 📎 fayl yuboring. Menyu: /start")


# ------------------------------------------------------------------------ main
class BanMiddleware(BaseMiddleware):
    """Bloklangan foydalanuvchilarning hamma so'rovlarini to'xtatadi (adminlardan tashqari)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and not is_admin(user.id) and db.is_banned(user.id):
            if isinstance(event, Message):
                await event.answer("⛔ Siz botdan foydalana olmaysiz.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔", show_alert=True)
            return None
        return await handler(event, data)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.outer_middleware(BanMiddleware())
    dp.callback_query.outer_middleware(BanMiddleware())
    # Tartib muhim: admin -> to'lovlar -> oddiy foydalanuvchi
    dp.include_routers(admin.router, payments.router, router)
    return dp


PUBLIC_COMMANDS = [
    BotCommand(command="start", description="🏠 Boshlash"),
    BotCommand(command="rules", description="📖 Qoidalar"),
    BotCommand(command="me", description="📊 Limitim"),
    BotCommand(command="premium", description="💎 Obunalar"),
    BotCommand(command="ref", description="🎁 Bepul Premium (Taklif)"),
]
# Faqat adminning o'z chatida ko'rinadi
ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand(command="admin", description="🛠 Admin panel"),
    BotCommand(command="stats", description="📊 Statistika"),
    BotCommand(command="broadcast", description="📢 Xabar tarqatish"),
    BotCommand(command="user", description="👥 Foydalanuvchini topish"),
]


async def _start_health_server():
    port_str = os.getenv("PORT")
    if not port_str:
        return None
    try:
        from aiohttp import web
    except ImportError:
        return None

    app = web.Application()

    async def index_handler(req):
        return web.Response(text="Alifbo Bot 24/7 ishlab turibdi!", content_type="text/plain; charset=utf-8")

    app.router.add_get("/", index_handler)
    app.router.add_get("/health", index_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(port_str))
    await site.start()
    log.info("Render health-check veb server %s-portda ishga tushdi", port_str)
    return runner


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not TOKEN:
        from config import BASE
        env = BASE / ".env"
        raise SystemExit(
            "BOT_TOKEN topilmadi.\n"
            f"Qidirilgan fayl: {env}  ({'bor' if env.exists() else 'YOQ'})\n"
            "Fayl nomi aynan  .env  bo'lishi kerak (.env.txt yoki .env.example emas) va ichida:\n"
            "BOT_TOKEN=123456:ABC...   (tirnoqsiz, probelsiz)"
        )
    bot = Bot(TOKEN)
    await _bot_username(bot)
    await bot.set_my_commands(PUBLIC_COMMANDS)
    for admin_id in ADMINS:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:  # noqa: BLE001 - admin botni hali boshlamagan bo'lishi mumkin
            log.warning("admin %s uchun buyruqlar o'rnatilmadi (avval botga /start yozing)", admin_id)
    log.info("Bot ishga tushdi (baza: %s)", db.backend)
    runner = await _start_health_server()
    try:
        await build_dispatcher().start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
