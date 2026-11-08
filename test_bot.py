"""Botni Telegram'siz sinash: soxta sessiya orqali handlerlarning to'liq oqimi."""
import glob
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

_TMPDIR = tempfile.mkdtemp()
_EXC = Path(_TMPDIR) / "exceptions.json"
_EXC.write_text(json.dumps({"words": {}}), encoding="utf-8")
os.environ.update(
    DB_PATH=str(Path(_TMPDIR) / "t.db"), EXCEPTIONS_PATH=str(_EXC), ADMIN_IDS="999",
    FREE_FILES_PER_DAY="1", FREE_CHARS_PER_DAY="50",
)

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import (CallbackQuery, Chat, Document, File, Message, MessageId, PhotoSize,  # noqa: E402
                           Update, User)
from docx import Document as DocxDocument  # noqa: E402

import bot as botmod  # noqa: E402
import config  # noqa: E402

DP = botmod.build_dispatcher()   # router faqat bir marta ulanadi
ADMIN = 999


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []   # {"name", "chat", "text", "markup", "data"}
        self.download_bytes = b""
        self._n = 0

    async def close(self):
        pass

    def buttons(self, i=None):
        """Tugmalari bor oxirgi xabar (yoki i-yozuv) tugmalari."""
        if i is None:
            i = max((k for k, s in enumerate(self.sent) if s["markup"] is not None), default=-1)
        m = self.sent[i]["markup"] if i >= 0 else None
        return [b for row in (m.inline_keyboard if m else []) for b in row]

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self._n += 1
        chat_id = getattr(method, "chat_id", 0)
        text = getattr(method, "text", None) or getattr(method, "caption", None)
        rec = {"name": name, "chat": chat_id, "text": text, "markup": getattr(method, "reply_markup", None), "data": None}
        self.sent.append(rec)
        base = dict(message_id=self._n, date=datetime.now(), chat=Chat(id=chat_id or 1, type="private"))
        if name == "SendMessage":
            return Message(**base, text=method.text)
        if name == "SendDocument":
            f = method.document
            if not isinstance(f, str):
                rec["data"] = b"".join([c async for c in f.read(bot)])
                rec["text"] = rec["text"]
                rec["filename"] = f.filename
            return Message(**base, caption=method.caption)
        if name == "SendPhoto":
            return Message(**base, caption=method.caption)
        if name == "CopyMessage":
            return MessageId(message_id=1)
        if name in ("EditMessageText", "EditMessageCaption"):
            return Message(**base, text=text)
        if name == "GetFile":
            return File(file_id=method.file_id, file_unique_id="u", file_path="docs/f")
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.download_bytes


def _docx_bytes(text):
    d = DocxDocument()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class Base(unittest.IsolatedAsyncioTestCase):
    _uid = 1000

    async def asyncSetUp(self):
        self.session = FakeSession()
        self.bot = Bot("123456:TEST", session=self.session)
        Base._uid += 1
        self.uid = Base._uid
        self._i = 0

    def _user(self, uid):
        return User(id=uid, is_bot=False, first_name=f"U{uid}", username=f"user{uid}")

    def _msg(self, uid, **kw):
        self._i += 1
        return Message(message_id=500 + self._i, date=datetime.now(), chat=Chat(id=uid, type="private"),
                       from_user=self._user(uid), **kw)

    async def say(self, text, uid=None):
        uid = uid or self.uid
        await DP.feed_update(self.bot, Update(update_id=self._i + 1, message=self._msg(uid, text=text)))

    async def press(self, data, uid=None):
        uid = uid or self.uid
        cb = CallbackQuery(id=str(self._i + 1), from_user=self._user(uid), chat_instance="c",
                           message=self._msg(uid, text="menu"), data=data)
        self._i += 1
        await DP.feed_update(self.bot, Update(update_id=self._i, callback_query=cb))

    async def send_file(self, name, data, uid=None):
        uid = uid or self.uid
        self.session.download_bytes = data
        doc = Document(file_id="f1", file_unique_id="u1", file_name=name, file_size=len(data))
        await DP.feed_update(self.bot, Update(update_id=self._i + 1, message=self._msg(uid, document=doc)))

    async def send_photo(self, uid=None):
        uid = uid or self.uid
        ph = [PhotoSize(file_id="ph1", file_unique_id="pu", width=10, height=10)]
        await DP.feed_update(self.bot, Update(update_id=self._i + 1, message=self._msg(uid, photo=ph)))

    def last_text(self):
        return [s["text"] for s in self.session.sent if s["name"] in ("SendMessage", "EditMessageText") and s["text"]][-1]

    def names(self, name, chat=None):
        return [s for s in self.session.sent if s["name"] == name and (chat is None or s["chat"] == chat)]


# ------------------------------------------------------------------ menyu tugmalari
class MenuTests(Base):
    async def test_start_has_icon_buttons_without_admin(self):
        await self.say("/start")
        labels = [b.text for b in self.session.buttons()]
        self.assertEqual(labels, ["📖 Qoidalar", "📊 Limitim", "💎 Obunalar"])
        self.assertFalse(any(b.callback_data.startswith("adm:") for b in self.session.buttons()))

    async def test_admin_sees_admin_button_only_for_admin(self):
        await self.say("/start", uid=ADMIN)
        self.assertIn("🛠 Admin panel", [b.text for b in self.session.buttons()])

    async def test_rules_me_plans_buttons(self):
        await self.press("u:rules")
        self.assertIn("Ö", self.last_text())
        await self.press("u:me")
        self.assertIn("Limitim", self.last_text())
        self.assertIn("0/1", self.last_text())
        await self.press("u:plans")
        self.assertIn("30 kun", self.last_text())
        self.assertTrue(any(b.callback_data == "u:buy:30" for b in self.session.buttons()))

    async def test_back_to_home(self):
        await self.press("u:home")
        self.assertIn("Assalomu alaykum", self.last_text())


# ------------------------------------------------------------- matn va fayl
class ConvertTests(Base):
    async def test_text_conversion(self):
        await self.say("O'zbekiston shahri")
        self.assertEqual(self.last_text(), "Özbekiston şahri")

    async def test_already_new(self):
        await self.say("Özbekiston şahri")
        self.assertIn("allaqachon", self.last_text())

    async def test_text_limit(self):
        await self.say("O'zbekiston shahri g'alaba choy " * 3)
        self.assertIn("limiti tugadi", self.last_text())
        self.assertIn("💎 Obunalar", [b.text for b in self.session.buttons()])

    async def test_docx_flow_limit_and_cleanup(self):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "alifbo_*")))
        await self.send_file("hujjat.docx", _docx_bytes("O'zbekiston shahri"))
        docs = self.names("SendDocument")
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["filename"], "hujjat_yangi-alifbo.docx")
        self.assertEqual(DocxDocument(io.BytesIO(docs[0]["data"])).paragraphs[0].text, "Özbekiston şahri")
        await self.send_file("yana.docx", _docx_bytes("choy"))
        self.assertIn("limiti tugadi", self.last_text())
        self.assertEqual(len(self.names("SendDocument")), 1)
        self.assertEqual(set(glob.glob(os.path.join(tempfile.gettempdir(), "alifbo_*"))) - before, set())

    async def test_unsupported_and_corrupt_and_unchanged(self):
        await self.send_file("x.pdf", b"%PDF")
        self.assertIn("qo'llab-quvvatlanmaydi", self.last_text())
        await self.send_file("bad.docx", b"not a zip")
        self.assertIn("buzilgan", self.last_text())
        await self.send_file("toza.docx", _docx_bytes("salom dunyo"))
        self.assertIn("topilmadi", self.last_text())


# ------------------------------------------------------------ to'lov oqimi
class PaymentTests(Base):
    async def _request(self, days=30):
        await self.press(f"u:buy:{days}")
        await self.send_photo()
        notes = self.names("SendPhoto", ADMIN)
        self.assertTrue(notes, "admin bildirishnoma olmadi")
        ok_btn = next(b for b in self.session.buttons(self.session.sent.index(notes[-1])) if b.callback_data.startswith("adm:ok:"))
        return int(ok_btn.callback_data.split(":")[2])

    async def test_instruction_screen(self):
        await self.press("u:buy:30")
        self.assertIn("29 000", self.last_text())
        self.assertIn("chek", self.last_text().lower())

    async def test_approve_flow(self):
        pid = await self._request(30)
        self.assertFalse(config.db.is_premium(self.uid))
        await self.press(f"adm:ok:{pid}", uid=ADMIN)
        self.assertTrue(config.db.is_premium(self.uid))
        user_msgs = [s["text"] for s in self.names("SendMessage", self.uid)]
        self.assertTrue(any("tasdiqlandi" in t for t in user_msgs))
        # ikkinchi bosish: qayta ishlanmaydi, premium ikki marta qo'shilmaydi
        until = config.db.premium_until(self.uid)
        await self.press(f"adm:ok:{pid}", uid=ADMIN)
        self.assertEqual(config.db.premium_until(self.uid), until)
        self.assertEqual(config.db.get_payment(pid)["status"], "approved")

    async def test_reject_flow(self):
        pid = await self._request(90)
        await self.press(f"adm:no:{pid}", uid=ADMIN)
        self.assertFalse(config.db.is_premium(self.uid))
        self.assertEqual(config.db.get_payment(pid)["status"], "rejected")
        self.assertTrue(any("tasdiqlanmadi" in s["text"] for s in self.names("SendMessage", self.uid)))

    async def test_non_admin_cannot_decide(self):
        pid = await self._request(30)
        await self.press(f"adm:ok:{pid}")          # oddiy foydalanuvchi o'zi tasdiqlamoqchi
        self.assertEqual(config.db.get_payment(pid)["status"], "pending")
        self.assertFalse(config.db.is_premium(self.uid))

    async def test_text_instead_of_proof(self):
        await self.press("u:buy:30")
        await self.say("mana pul")
        self.assertIn("chekini", self.last_text())
        self.assertEqual(config.db.user_pending_count(self.uid), 0)

    async def test_pending_limit(self):
        for _ in range(3):
            await self._request(30)
        shown = len([s for s in self.names("EditMessageText") if "To'lov:" in (s["text"] or "")])
        await self.press("u:buy:30")
        self.assertEqual(config.db.user_pending_count(self.uid), 3)
        self.assertEqual(len([s for s in self.names("EditMessageText") if "To'lov:" in (s["text"] or "")]), shown)

    async def test_admin_payment_menu_lists_pending(self):
        pid = await self._request(30)
        await self.press("adm:pay", uid=ADMIN)
        self.assertTrue(any(b.callback_data == f"adm:pay:{pid}" for b in self.session.buttons()))
        await self.press(f"adm:pay:{pid}", uid=ADMIN)
        self.assertTrue(self.names("SendPhoto", ADMIN))


# ------------------------------------------------------------- admin huquqlari
class AdminTests(Base):
    async def test_non_admin_gets_no_admin_features(self):
        for cmd in ["/admin", "/stats", f"/grant {self.uid} 30", "/broadcast", "/user 1", "/ban 5"]:
            self.session.sent.clear()
            await self.say(cmd)
            for s in self.session.sent:
                self.assertNotIn("Admin panel", s["text"] or "")
                self.assertNotIn("Statistika", s["text"] or "")
            self.assertFalse(config.db.is_premium(self.uid))
        await self.press("adm:home")
        await self.press("adm:stats")
        await self.press("adm:bc:go")
        self.assertFalse([s for s in self.session.sent if s["name"] == "CopyMessage"])

    async def test_admin_panel_and_stats(self):
        await self.say("/admin", uid=ADMIN)
        labels = [b.text for b in self.session.buttons()]
        for expected in ["📊 Statistika", "👥 Foydalanuvchi", "📢 Xabar tarqatish", "📝 Istisno so'zlar", "⚙️ Sozlamalar"]:
            self.assertIn(expected, labels)
        await self.press("adm:stats", uid=ADMIN)
        self.assertIn("Foydalanuvchilar", self.last_text())

    async def test_grant_user_lookup_and_ban(self):
        config.db.touch_user(self.uid, f"user{self.uid}", "Test User")
        await self.say(f"/grant {self.uid} 30", uid=ADMIN)
        self.assertTrue(config.db.is_premium(self.uid))
        await self.say(f"/user {self.uid}", uid=ADMIN)
        self.assertIn(str(self.uid), self.last_text())
        # ban tugmasi
        await self.press(f"adm:u:ban:{self.uid}", uid=ADMIN)
        self.assertTrue(config.db.is_banned(self.uid))
        await self.say("salom")
        self.assertIn("foydalana olmaysiz", self.last_text())
        await self.press(f"adm:u:unban:{self.uid}", uid=ADMIN)
        await self.say("O'zbek")
        self.assertEqual(self.last_text(), "Özbek")
        await self.press(f"adm:u:revoke:{self.uid}", uid=ADMIN)
        self.assertFalse(config.db.is_premium(self.uid))

    async def test_user_lookup_via_menu_state(self):
        config.db.touch_user(self.uid, f"user{self.uid}", "Test User")
        await self.press("adm:users", uid=ADMIN)
        await self.say(f"@user{self.uid}", uid=ADMIN)
        self.assertIn(str(self.uid), self.last_text())

    async def test_add_and_delete_exception_word(self):
        await self.say("/addword mashhur maşhur2", uid=ADMIN)
        self.assertIn("mashhur", config.EXCEPTIONS)
        await self.say("mashhur odam")
        self.assertEqual(self.last_text(), "maşhur2 odam")
        # fayl ham yangilandi (qayta ishga tushirishda saqlanadi)
        self.assertIn("mashhur", json.loads(_EXC.read_text(encoding="utf-8"))["words"])
        await self.press("adm:w:del", uid=ADMIN)
        await self.say("mashhur", uid=ADMIN)
        self.assertNotIn("mashhur", config.EXCEPTIONS)

    async def test_add_word_via_menu(self):
        await self.press("adm:w:add", uid=ADMIN)
        await self.say("juda_noto'g'ri", uid=ADMIN)       # bitta so'z -> xato
        self.assertIn("Ikki so'z", self.last_text())
        await self.say("ishhona ishhona2", uid=ADMIN)
        self.assertIn("ishhona", config.EXCEPTIONS)
        config.delete_exception("ishhona")

    async def test_settings_plans_and_limits(self):
        await self.press("adm:set:plan", uid=ADMIN)
        await self.say("60 49000", uid=ADMIN)
        self.assertIn({"days": 60, "price": 49000}, config.get_plans())
        await self.press("adm:set:plan", uid=ADMIN)
        await self.say("60 0", uid=ADMIN)
        self.assertNotIn(60, [p["days"] for p in config.get_plans()])
        await self.press("adm:set:card", uid=ADMIN)
        await self.say("Karta: 8600 1111 2222 3333", uid=ADMIN)
        await self.press("u:buy:30")
        self.assertIn("8600 1111 2222 3333", self.last_text())
        await self.press("adm:set:ff", uid=ADMIN)
        await self.say("5", uid=ADMIN)
        self.assertEqual(config.free_files(), 5)
        await self.press("adm:set:ff", uid=ADMIN)
        await self.say("1", uid=ADMIN)

    async def test_broadcast(self):
        for u in (self.uid, self.uid + 5000):
            config.db.touch_user(u, "", "x")
        config.db.set_banned(self.uid + 5000, True)
        await self.say("/broadcast", uid=ADMIN)
        await self.say("Yangi aksiya!", uid=ADMIN)
        self.assertIn("Tasdiqlaysizmi", self.last_text())
        expected = len(config.db.broadcast_ids())
        await self.press("adm:bc:go", uid=ADMIN)
        import asyncio
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any("Tarqatish tugadi" in (s["text"] or "") for s in self.session.sent):
                break
        copies = self.names("CopyMessage")
        self.assertEqual(len(copies), expected)
        self.assertNotIn(self.uid + 5000, [c["chat"] for c in copies])   # bloklangan olmaydi
        self.assertTrue(any(f"Yuborildi: {expected}" in (s["text"] or "") for s in self.session.sent))

    async def test_state_cleared_by_start(self):
        await self.press("adm:w:add", uid=ADMIN)
        await self.say("/start", uid=ADMIN)
        await self.say("O'zbek", uid=ADMIN)          # holat tozalangan: oddiy konversiya
        self.assertEqual(self.last_text(), "Özbek")


if __name__ == "__main__":
    unittest.main(verbosity=2)
