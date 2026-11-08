"""
officeconv - Word / Excel / PowerPoint / matn fayllarini yangi alifboga o'tkazish.

Usul: fayl ZIP ichidagi XML to'g'ridan-to'g'ri tahrirlanadi, faqat matn elementlari
(w:t, a:t, shared strings) almashtiriladi. Shrift, rang, jadval, rasm, formula,
raqam va boshqa hamma narsa BAYT-BAYT o'zgarmay qoladi.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from uzconv import Flag, convert

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# (zip ichidagi fayl yo'li regex, namespace, "paragraf" tegi, matn tegi, rPh ni chetlab o'tish)
SPECS: dict[str, list[tuple[str, str, str, str, bool]]] = {
    ".docx": [(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml", NS_W, "p", "t", False)],
    ".pptx": [(r"ppt/(slides/slide\d+|notesSlides/notesSlide\d+)\.xml", NS_A, "p", "t", False)],
    ".xlsx": [
        (r"xl/sharedStrings\.xml", NS_S, "si", "t", True),
        (r"xl/worksheets/sheet\d+\.xml", NS_S, "is", "t", True),
        (r"xl/comments\d*\.xml", NS_S, "text", "t", True),
    ],
}
LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}
TEXT_EXT = {".txt", ".md", ".csv", ".srt"}
SUPPORTED = set(SPECS) | set(LEGACY) | TEXT_EXT

MAX_UNCOMPRESSED = 300 * 1024 * 1024  # zip-bomb himoyasi

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


class UnsupportedFile(Exception):
    pass


@dataclass
class Report:
    words: int = 0
    changed: int = 0
    flags: list[Flag] = field(default_factory=list)

    def add_flags(self, flags: list[Flag]) -> None:
        seen = {(f.word, f.reason) for f in self.flags}
        for f in flags:
            if (f.word, f.reason) not in seen:
                seen.add((f.word, f.reason))
                self.flags.append(f)


def _process_xml(data: bytes, ns: str, para_tag: str, text_tag: str, skip_rph: bool,
                 exceptions: dict, report: Report) -> bytes:
    root = etree.fromstring(data, _PARSER)
    q_para, q_text, q_rph = f"{{{ns}}}{para_tag}", f"{{{ns}}}{text_tag}", f"{{{ns}}}rPh"

    # Har bir matn elementi eng yaqin "paragraf"iga biriktiriladi (ichma-ich matn
    # qutilarida ikki marta ishlanmasligi uchun).
    groups: dict = {}
    for t in root.iter(q_text):
        if skip_rph and any(True for _ in t.iterancestors(q_rph)):
            continue
        p = next(t.iterancestors(q_para), None)
        if p is not None:
            groups.setdefault(p, []).append(t)

    modified = False
    for ts in groups.values():
        olds = [t.text or "" for t in ts]
        joined = "".join(olds)
        if not joined.strip():
            continue
        whole = convert(joined, exceptions)
        report.words += whole.stats["words"]
        report.changed += whole.stats["changed"]
        report.add_flags(whole.flags)
        if whole.text == joined:
            continue
        per_run = [convert(x, exceptions).text for x in olds]
        # So'z run'lar orasida bo'linmagan bo'lsa, har bir run o'z formatini saqlaydi.
        news = per_run if "".join(per_run) == whole.text else [whole.text] + [""] * (len(ts) - 1)
        for t, old, new in zip(ts, olds, news):
            if new != old:
                t.text = new
                if new != new.strip():
                    t.set(XML_SPACE, "preserve")
                modified = True

    if not modified:
        return data
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _convert_ooxml(src: Path, dst: Path, ext: str, exceptions: dict) -> Report:
    report = Report()
    specs = [(re.compile(rx), ns, p, t, rph) for rx, ns, p, t, rph in SPECS[ext]]
    try:
        zin = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        raise UnsupportedFile("Fayl buzilgan yoki parol bilan himoyalangan") from e
    with zin, zipfile.ZipFile(dst, "w") as zout:
        if sum(i.file_size for i in zin.infolist()) > MAX_UNCOMPRESSED:
            raise UnsupportedFile("Fayl juda katta")
        for item in zin.infolist():
            data = zin.read(item.filename)
            for rx, ns, p, t, rph in specs:
                if rx.fullmatch(item.filename):
                    data = _process_xml(data, ns, p, t, rph, exceptions, report)
                    break
            zout.writestr(item, data, compress_type=item.compress_type)
    return report


def _convert_text(src: Path, dst: Path, exceptions: dict) -> Report:
    raw = src.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnsupportedFile("Matn kodirovkasi aniqlanmadi")
    res = convert(text, exceptions)
    dst.write_text(res.text, encoding="utf-8")
    return Report(res.stats["words"], res.stats["changed"], res.flags)


def _libreoffice_convert(src: Path, target_ext: str, workdir: Path) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise UnsupportedFile("Eski format (.doc/.xls/.ppt) serverda qo'llab-quvvatlanmaydi, "
                              "faylni .docx/.xlsx/.pptx qilib saqlab yuboring")
    profile = Path(tempfile.gettempdir()) / f"lo_{uuid.uuid4().hex}"
    try:
        subprocess.run(
            [soffice, f"-env:UserInstallation=file://{profile}", "--headless",
             "--convert-to", target_ext.lstrip("."), "--outdir", str(workdir), str(src)],
            check=True, timeout=120, capture_output=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise UnsupportedFile("Eski formatdagi faylni o'qib bo'lmadi") from e
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    out = workdir / (src.stem + target_ext)
    if not out.exists():
        raise UnsupportedFile("Eski formatdagi faylni o'qib bo'lmadi")
    return out


def convert_file(src: Path, out_dir: Path, exceptions: dict | None = None) -> tuple[Path, Report]:
    """Faylni yangi alifboga o'tkazadi. (natija_fayl_yo'li, hisobot) qaytaradi."""
    exceptions = exceptions or {}
    src, out_dir = Path(src), Path(out_dir)
    ext = src.suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFile(f"{ext or 'bu'} formati qo'llab-quvvatlanmaydi")

    if ext in LEGACY:
        src = _libreoffice_convert(src, LEGACY[ext], out_dir)
        ext = LEGACY[ext]

    dst = out_dir / f"{src.stem}_yangi-alifbo{ext}"
    if ext in SPECS:
        report = _convert_ooxml(src, dst, ext, exceptions)
    else:
        report = _convert_text(src, dst, exceptions)
    return dst, report
