"""Yangi o'zbek alifbosi konvertor Telegram boti. Ishga tushirish: python bot.py"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (BotCommand, BotCommandScopeChat, CallbackQuery, FSInputFile, Message)

import admin
import payments
from config import (EXCEPTIONS, MAX_FILE_MB, TOKEN, ADMINS, db, fmt_date, free_chars, free_files, is_admin, touch)
from officeconv import SUPPORTED, UnsupportedFile, convert_file
from uzconv import convert, detect_script
from ui import (B, RULES_TEXT, START_TEXT, SUPPORTED_TEXT, back_home_kb, home_kb, kb, show)

log = logging.getLogger("alifbo-bot")
router = Router(name="user")
SEM = asyncio.Semaphore(3)  # bir vaqtda ko'pi bilan 3 ta fayl ishlanadi

PLANS_BTN = [B(text="💎 Obunalar", callback_data="u:plans")]


# ---------------------------------------------------------------------- yordamchi
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
        return (f"📊 Limitim\n\n💎 Premium faol: {fmt_date(db.premium_until(uid))} gacha\n"
                f"♾ Cheklov yo'q\n\nBugun: 📁 {files} fayl, 🔤 {chars} belgi")
    return (f"📊 Limitim\n\n💎 Premium: yo'q\n\nBugungi bepul limit:\n"
            f"📁 Fayllar: {files}/{free_files()}\n🔤 Matn: {chars}/{free_chars()} belgi\n\n"
            "Cheksiz foydalanish uchun obuna oling 👇")


def me_kb(uid: int):
    # Premium bo'lmaganlarga "Obunalar" tugmasi, hammaga "Bosh menyu"
    return back_home_kb(None if db.is_premium(uid) else PLANS_BTN)


# ------------------------------------------------------------------------ menyu
@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    touch(m.from_user)
    await m.answer(START_TEXT, reply_markup=home_kb(is_admin(m.from_user.id)))


@router.message(Command("help"))
async def cmd_help(m: Message, state: FSMContext):
    await cmd_start(m, state)


@router.callback_query(F.data == "u:home")
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    touch(cb.from_user)
    await show(cb, START_TEXT, home_kb(is_admin(cb.from_user.id)))


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
    if not db.is_premium(uid) and db.usage_today(uid)[1] + len(text) > free_chars():
        await m.answer(f"⛔ Bugungi bepul matn limiti tugadi ({free_chars()} belgi).", reply_markup=kb(PLANS_BTN))
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
        await m.answer(f"⛔ Bugungi bepul fayl limiti tugadi ({free_files()} ta).", reply_markup=kb(PLANS_BTN))
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
    await bot.set_my_commands(PUBLIC_COMMANDS)
    for admin_id in ADMINS:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:  # noqa: BLE001 - admin botni hali boshlamagan bo'lishi mumkin
            log.warning("admin %s uchun buyruqlar o'rnatilmadi (avval botga /start yozing)", admin_id)
    log.info("Bot ishga tushdi")
    runner = await _start_health_server()
    try:
        await build_dispatcher().start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
