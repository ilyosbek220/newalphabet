# Yangi alifbo Telegram boti

Matn va Word/Excel/PowerPoint fayllarini yangi o'zbek alifbosiga (Ö Ğ Ş Ç) o'tkazadi.
Lotin va kirill ikkalasi ham qo'llab-quvvatlanadi.

## Ishga tushirish
1. @BotFather → `/newbot` → **token** oling.
2. `.env.example` nusxasini `.env` deb nomlang (aynan `.env`, `.env.txt` emas), ichiga yozing:
   `BOT_TOKEN=...` va `ADMIN_IDS=sizning_telegram_id` (ID ni @userinfobot dan bilasiz).
3. `pip install -r requirements.txt` → `python bot.py`
4. Serverda doimiy ishlashi: `alifbo-bot.service` (systemd) faylidan foydalaning.
   Eski .doc/.xls/.ppt uchun serverga `libreoffice-writer libreoffice-calc libreoffice-impress` o'rnating.

## Foydalanuvchi menyusi
`/start` → tugmalar: 📖 Qoidalar · 📊 Limitim · 💎 Obunalar.

## Admin panel (faqat ADMIN_IDS dagilarga)
`/admin` yoki `/start` dagi "🛠 Admin panel" tugmasi. Boshqa foydalanuvchilarga bu tugma ham,
buyruqlar ham ko'rinmaydi va ishlamaydi.

| Bo'lim | Nima qiladi |
|---|---|
| 📊 Statistika | foydalanuvchilar, premium, bugungi yuk, daromad |
| 💳 To'lovlar | kutilayotgan cheklar → ✅ Tasdiqlash / ❌ Rad etish, tarix |
| 👥 Foydalanuvchi | ID yoki @username bo'yicha: +30/+90 kun, premiumni olish, bloklash |
| 📢 Xabar tarqatish | istalgan xabarni (matn/rasm/fayl) hammaga yuborish, oldindan ko'rish bilan |
| 📝 Istisno so'zlar | qo'shish/o'chirish/ro'yxat, qayta ishga tushirmasdan darhol ishlaydi |
| ⚙️ Sozlamalar | to'lov matni (karta), tarif narxlari, bepul limitlar |
| 🚫 Bloklanganlar | ro'yxat va blokdan chiqarish |

Qisqa buyruqlar (faqat admin): `/stats`, `/user <id>`, `/grant <id> <kun>`, `/ban <id>`, `/unban <id>`,
`/broadcast`, `/addword eski yangi`, `/delword eski`.

## To'lov jarayoni
1. Mijoz 💎 Obunalar → tarif → sizning karta matningiz ko'rsatiladi → chek rasmini yuboradi.
2. Sizga chek rasmi va ✅/❌ tugmalari bilan xabar keladi.
3. ✅ bosilsa obuna darhol faollashadi, mijozga xabar ketadi. Ikki marta bosish xavfsiz (qayta qo'shilmaydi).
4. Karta matni va narxlarni Sozlamalar bo'limida o'zgartirasiz (`.env` ga tegmasdan).

## Testlar
```bash
pip install -r requirements-dev.txt
python test_uzconv.py && python test_office.py && python test_bot.py
```

## Qoidalarni yangilash
- Istisno so'zlar: botdagi 📝 bo'lim (yoki `exceptions.json`).
- Tutuq belgisi: `uzconv.py` dagi `TUTUQ`.

## Cheklovlar (mijozga ochiq ayting)
Rasm ichidagi matn, PDF, diagramma/grafik matni, Excel varaq nomlari o'zgartirilmaydi.
Fayllar ishlangandan keyin serverdan darhol o'chiriladi.
