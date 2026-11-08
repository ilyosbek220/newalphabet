import unittest

from uzconv import TUTUQ, convert, detect_script


def c(text, **kw):
    return convert(text, **kw).text


class LatinTests(unittest.TestCase):
    def test_four_letters(self):
        self.assertEqual(c("O'zbekiston"), "Özbekiston")
        self.assertEqual(c("G'alaba"), "Ğalaba")
        self.assertEqual(c("Shahar"), "Şahar")
        self.assertEqual(c("choy"), "çoy")

    def test_apostrophe_variants(self):
        for apos in ["'", "\u02bb", "\u2018", "`", "\u00b4"]:
            self.assertEqual(c(f"O{apos}zbek"), "Özbek", apos)
            self.assertEqual(c(f"tog{apos}"), "toğ", apos)

    def test_case(self):
        self.assertEqual(c("O'ZBEKISTON"), "ÖZBEKISTON")
        self.assertEqual(c("SHAHAR"), "ŞAHAR")
        self.assertEqual(c("CHIROYLI"), "ÇIROYLI")
        self.assertEqual(c("Chiroyli"), "Çiroyli")

    def test_word_final_modifier(self):
        self.assertEqual(c("tog' va bog'."), "toğ va boğ.")
        self.assertEqual(c("tong' ming"), "tong' ming")   # ng + ' -> tegilmaydi
        self.assertEqual(c("'ming' so'm"), "'ming' söm")

    def test_double_modifier(self):
        self.assertEqual(c("to'g'ri"), "töğri")
        self.assertEqual(c("yo'q"), "yöq")

    def test_tutuq(self):
        self.assertEqual(c("ma'no"), "ma" + TUTUQ + "no")
        self.assertEqual(c("ma\u2019no"), "ma" + TUTUQ + "no")
        self.assertEqual(c("she'r"), "şe" + TUTUQ + "r")

    def test_o_plus_vowel_is_flagged_tutuq(self):
        r = convert("mo'ayyan")
        self.assertEqual(r.text, "mo" + TUTUQ + "ayyan")
        self.assertTrue(r.flags)

    def test_ng_and_sh_h(self):
        self.assertEqual(c("tong"), "tong")
        self.assertEqual(c("mashhur"), "maşhur")

    def test_quotes_are_not_touched(self):
        self.assertEqual(c("'Salom' dedi"), "'Salom' dedi")
        self.assertEqual(c('"Shahar" va 123!'), '"Şahar" va 123!')

    def test_sentence(self):
        self.assertEqual(
            c("O'zbekiston shahrida g'alaba bayrami bo'ldi."),
            "Özbekiston şahrida ğalaba bayrami böldi.",
        )

    def test_exceptions(self):
        # Mexanizm testi (so'zlar sun'iy, lingvistik da'vo emas)
        ex = {"mashhad": "mashhad"}
        self.assertEqual(c("Mashhad", exceptions=ex), "Mashhad")
        self.assertEqual(c("MASHHAD", exceptions=ex), "MASHHAD")
        self.assertEqual(c("mashhur", exceptions=ex), "maşhur")

    def test_urls_emails_handles_untouched(self):
        s = "Sayt: https://shahar.uz/o'zbek va xat info@choy.uz, @shahar_bot"
        self.assertEqual(c(s), "Sayt: https://shahar.uz/o'zbek va xat info@choy.uz, @shahar_bot")
        self.assertEqual(c("shahar https://x.uz/ch shahar"), "şahar https://x.uz/ch şahar")

    def test_preserves_layout(self):
        text = "Birinci\tqator\n\nIkkinci  qator 12.05.2026 — OK"
        self.assertEqual(c(text), text)


class CyrillicTests(unittest.TestCase):
    def test_four_letters(self):
        self.assertEqual(c("Ўзбекистон"), "Özbekiston")
        self.assertEqual(c("Ғалаба"), "Ğalaba")
        self.assertEqual(c("шаҳар"), "şahar")
        self.assertEqual(c("чой"), "çoy")

    def test_case(self):
        self.assertEqual(c("ШАҲАР"), "ŞAHAR")
        self.assertEqual(c("ёш"), "yoş")
        self.assertEqual(c("Ёш"), "Yoş")
        self.assertEqual(c("ЁШ"), "YOŞ")

    def test_ye_rule(self):
        self.assertEqual(c("ер"), "yer")
        self.assertEqual(c("Ер"), "Yer")
        self.assertEqual(c("ЕР"), "YER")
        self.assertEqual(c("бер"), "ber")

    def test_misc(self):
        self.assertEqual(c("маъно"), "ma" + TUTUQ + "no")
        self.assertEqual(c("қалам"), "qalam")
        self.assertEqual(c("яхши"), "yaxşi")
        self.assertEqual(c("юрак"), "yurak")
        self.assertEqual(c("Конституция"), "Konstitutsiya")

    def test_ts_is_flagged(self):
        self.assertTrue(convert("Конституция").flags)

    def test_mixed_script_sentence(self):
        self.assertEqual(c("Ўзбекистон — O'zbekiston"), "Özbekiston — Özbekiston")

    def test_source_filter(self):
        self.assertEqual(c("Ўзбек O'zbek", source="latin"), "Ўзбек Özbek")
        self.assertEqual(c("Ўзбек O'zbek", source="cyrillic"), "Özbek O'zbek")


class GeneralTests(unittest.TestCase):
    def test_idempotent(self):
        samples = [
            "O'zbekiston shahrida g'alaba, ma'no va to'g'ri choy.",
            "Ўзбекистон шаҳрида ғалаба, маъно ва тўғри чой.",
            "mo'ayyan SHAHAR CHOY",
        ]
        for s in samples:
            once = c(s)
            self.assertEqual(c(once), once, s)

    def test_stats(self):
        r = convert("O'zbek va tong")
        self.assertEqual(r.stats, {"words": 3, "changed": 1})

    def test_detect(self):
        self.assertEqual(detect_script("Ўзбекистон"), "cyrillic")
        self.assertEqual(detect_script("O'zbekiston"), "latin_old")
        self.assertEqual(detect_script("Özbekiston"), "new")
        self.assertEqual(detect_script("12345"), "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
