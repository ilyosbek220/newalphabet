import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from pptx import Presentation
from pptx.util import Inches

from officeconv import UnsupportedFile, convert_file


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "out"
        self.out.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------------------- docx
    def test_docx(self):
        d = Document()
        p = d.add_paragraph()
        p.add_run("O'z")                      # so'z run'lar orasida bo'lingan, lekin
        p.add_run("bekiston ")                # har biri alohida ishlanadi
        r = p.add_run("shahri")
        r.bold = True
        p2 = d.add_paragraph()
        p2.add_run("o")                       # "o" va "'zbek" run chegarasida bo'lingan
        p2.add_run("'zbek tili")
        d.add_paragraph("Sayt: https://shahar.uz/choy")
        t = d.add_table(rows=1, cols=2)
        t.cell(0, 0).text = "G'alaba"
        t.cell(0, 1).text = "Choy"
        d.sections[0].header.paragraphs[0].text = "Toshkent shahri"
        src = self.tmp / "hujjat.docx"
        d.save(src)

        dst, rep = convert_file(src, self.out)
        self.assertEqual(dst.name, "hujjat_yangi-alifbo.docx")
        r2 = Document(dst)
        runs = r2.paragraphs[0].runs
        self.assertEqual("".join(x.text for x in runs), "Özbekiston şahri")
        self.assertEqual(len(runs), 3)
        self.assertTrue(runs[2].bold)          # format saqlandi
        self.assertIn("özbek tili", r2.paragraphs[1].text)
        self.assertIn("https://shahar.uz/choy", r2.paragraphs[2].text)  # havola tegilmadi
        self.assertEqual(r2.tables[0].cell(0, 0).text, "Ğalaba")
        self.assertEqual(r2.tables[0].cell(0, 1).text, "Çoy")
        self.assertEqual(r2.sections[0].header.paragraphs[0].text, "Toşkent şahri")
        self.assertGreater(rep.changed, 5)

    # ---------------------------------------------------------------- xlsx
    def test_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "Varaq"
        ws["A1"] = "O'zbekiston"
        ws["A1"].font = Font(bold=True)
        ws["A2"] = "shahar"
        ws["B1"] = 12345
        ws["B2"] = "=B1*2"
        ws["C1"] = "https://choy.uz"
        src = self.tmp / "jadval.xlsx"
        wb.save(src)

        dst, rep = convert_file(src, self.out)
        wb2 = load_workbook(dst)
        ws2 = wb2["Varaq"]
        self.assertEqual(ws2["A1"].value, "Özbekiston")
        self.assertTrue(ws2["A1"].font.bold)
        self.assertEqual(ws2["A2"].value, "şahar")
        self.assertEqual(ws2["B1"].value, 12345)
        self.assertEqual(ws2["B2"].value, "=B1*2")            # formula buzilmadi
        self.assertEqual(ws2["C1"].value, "https://choy.uz")

    # ---------------------------------------------------------------- pptx
    def test_pptx(self):
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = "O'zbekiston shahri"
        tb = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
        tb.text_frame.text = "G'alaba choy"
        src = self.tmp / "taqdimot.pptx"
        prs.save(src)

        dst, rep = convert_file(src, self.out)
        prs2 = Presentation(dst)
        texts = [s.text_frame.text for s in prs2.slides[0].shapes if s.has_text_frame]
        self.assertIn("Özbekiston şahri", texts)
        self.assertIn("Ğalaba çoy", texts)

    # ------------------------------------------------------- boshqa fayllar
    def test_untouched_parts_are_byte_identical(self):
        d = Document()
        d.add_paragraph("shahar")
        src = self.tmp / "a.docx"
        d.save(src)
        dst, _ = convert_file(src, self.out)
        with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for name in a.namelist():
                if name != "word/document.xml":
                    self.assertEqual(a.read(name), b.read(name), name)

    def test_txt_utf8_and_cp1251(self):
        a = self.tmp / "a.txt"
        a.write_text("O'zbek shahar", encoding="utf-8")
        dst, _ = convert_file(a, self.out)
        self.assertEqual(dst.read_text(encoding="utf-8"), "Özbek şahar")
        b = self.tmp / "b.txt"
        b.write_bytes("Ўзбек шаҳар".encode("cp1251", errors="replace"))
        # cp1251 da ў, ҳ yo'q; kamida xatosiz ishlashi kerak
        convert_file(b, self.out)

    def test_unsupported_and_corrupt(self):
        pdf = self.tmp / "x.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with self.assertRaises(UnsupportedFile):
            convert_file(pdf, self.out)
        bad = self.tmp / "bad.docx"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(UnsupportedFile):
            convert_file(bad, self.out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
