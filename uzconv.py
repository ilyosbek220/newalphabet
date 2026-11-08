"""
uzconv - o'zbek yozuvini yangi alifboga o'tkazuvchi motor.

Yangi alifbo (2026-yil 21-oktabrdagi qonun, kuchga kirishi 2027-yil 22-aprel):
    Oʻ -> Ö,  Gʻ -> Ğ,  Sh -> Ş,  Ch -> Ç,  "ng" alohida harf emas.

Qo'llab-quvvatlanadi:
    * hozirgi lotin  -> yangi alifbo
    * kirill         -> yangi alifbo

Asosiy g'oya: hammasi QOIDALAR bilan (deterministik), AI ishtirok etmaydi.
Noaniq joylar `Result.flags` ro'yxatida qaytariladi, istisno so'zlar
`exceptions.json` orqali boshqariladi.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Tutuq belgisi qaysi belgi bilan yozilishi hali rasman aniq emas -> sozlanadi.
TUTUQ = "\u02bc"  # ʼ

# Apostrofning turlari
_MODIFIER = "\u02bb\u2018"      # ʻ ‘  -> oʻ / gʻ belgisi
_TUTUQ_CHARS = "\u02bc\u2019"   # ʼ ’  -> tutuq belgisi
_AMBIGUOUS = "'`\u00b4"         # ' ` ´ -> klaviaturadagi oddiy apostrof (ikki xil ma'no)
_APOS = _MODIFIER + _TUTUQ_CHARS + _AMBIGUOUS
_NEW_LETTERS = "ÖöĞğŞşÇç"
_LAT_VOWELS = "aeiouAEIOU"

_TOKEN = re.compile("([\u0400-\u04ff]+)|([A-Za-z" + _NEW_LETTERS + _APOS + "]+)")
# URL, email va @username matnlari o'zgartirilmaydi (havola buzilmasligi uchun)
_PROTECT = re.compile(r"(https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|@\w+)")
_SH = re.compile(r"[Ss][Hh]")
_CH = re.compile(r"[Cc][Hh]")

_MOD_MAP = {"o": "ö", "O": "Ö", "g": "ğ", "G": "Ğ"}

# ---------------------------------------------------------------- kirill ----
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "x", "ч": "ç", "ш": "ş", "щ": "şç", "ъ": TUTUQ, "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya", "ў": "ö", "қ": "q", "ғ": "ğ", "ҳ": "h",
}
_CYR_VOWELS = set("аеёиоуўэюяы")


@dataclass
class Flag:
    word: str
    reason: str


@dataclass
class Result:
    text: str
    flags: list[Flag] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class Exceptions(dict):
    """Kalitlari normallashtirilgan istisno lug'ati (convert uni qayta hisoblamaydi)."""


def _norm_key(word: str) -> str:
    w = word.lower()
    for ch in _APOS:
        w = w.replace(ch, "'")
    return w


norm_key = _norm_key


def load_exceptions(path: str | Path) -> Exceptions:
    """exceptions.json -> {eski_lotin_so'z: yangi_shakli}. "_" bilan boshlangan kalitlar izoh."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return Exceptions(
        {_norm_key(k): str(v).lower() for k, v in data.get("words", {}).items() if not k.startswith("_")}
    )


def _shape(new_lower: str, original: str) -> str:
    """Asl so'zning registrini (kichik / Bosh / KATTA) yangi so'zga ko'chiradi."""
    letters = [c for c in original if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return new_lower.upper()
    if letters and letters[0].isupper():
        return new_lower[:1].upper() + new_lower[1:]
    return new_lower


# ----------------------------------------------------------------- lotin ----
def _latin_word(tok: str, exceptions: dict[str, str], flags: list[Flag]) -> str:
    core = tok.strip(_APOS)
    if not core:
        return tok
    start = tok.index(core)
    lead, trail = tok[:start], tok[start + len(core):]

    # So'z oxiridagi apostrof: "tog'", "bog'" -> gʻ. Lekin "ng'" (tong') va
    # tutuq/yopuvchi qo'shtirnoq (') bilan adashmaslik uchun cheklovlar bor.
    if (trail and trail[0] in (_MODIFIER + _AMBIGUOUS) and core[-1] in "oOgG"
            and not (core[-1] in "gG" and len(core) > 1 and core[-2] in "nN")):
        core += trail[0]
        trail = trail[1:]
        if core[-2] in "oO":
            flags.append(Flag(core, "so'z oxiridagi o + apostrof: oʻ deb olindi, tekshiring"))

    key = _norm_key(core)
    if key in exceptions:
        return lead + _shape(exceptions[key], core) + trail

    out: list[str] = []
    n = len(core)
    for i, c in enumerate(core):
        if c not in _APOS:
            out.append(c)
            continue
        prev = core[i - 1] if i > 0 else ""
        nxt = core[i + 1] if i + 1 < n else ""
        at_end = i == n - 1
        if not (prev.isalpha() and (nxt.isalpha() or (at_end and prev.lower() in "og"
                                                        and c not in _TUTUQ_CHARS))):
            out.append(c)
            flags.append(Flag(core, "tushunarsiz apostrof joylashuvi, o'zgartirilmadi"))
            continue

        pl = prev.lower()
        if pl in "og":
            if c in _MODIFIER:
                kind = "mod"
            elif c in _TUTUQ_CHARS:
                kind = "tut"
            elif at_end:
                kind = "mod"
            elif pl == "o" and nxt in _LAT_VOWELS:
                # "mo'ayyan" kabi: o dan keyin unli kelsa, bu odatda tutuq
                kind = "tut"
                flags.append(Flag(core, "o + apostrof + unli: tutuq deb olindi, tekshiring"))
            else:
                kind = "mod"
        else:
            kind = "tut"

        if kind == "mod":
            out[-1] = _MOD_MAP.get(out[-1], out[-1])
        else:
            out.append(TUTUQ)

    res = "".join(out)
    res = _SH.sub(lambda m: "Ş" if m.group()[0] == "S" else "ş", res)
    res = _CH.sub(lambda m: "Ç" if m.group()[0] == "C" else "ç", res)
    return lead + res + trail


# ---------------------------------------------------------------- kirill ----
def _cyr_word(tok: str, flags: list[Flag]) -> str:
    out: list[str] = []
    for i, ch in enumerate(tok):
        lo = ch.lower()
        prev = tok[i - 1].lower() if i > 0 else ""
        if lo == "е":
            r = "ye" if (i == 0 or prev in _CYR_VOWELS or prev == "ъ") else "e"
        elif lo == "ц":
            r = "s" if (i > 0 and prev not in _CYR_VOWELS and prev != "ъ") else "ts"
            flags.append(Flag(tok, "ц: ts/s qoidasi so'zga bog'liq, tekshiring"))
        else:
            r = _CYR.get(lo)
        if r is None:
            out.append(ch)
            continue
        if ch.isupper() and r:
            nxt = tok[i + 1] if i + 1 < len(tok) else ""
            prv = tok[i - 1] if i > 0 else ""
            if len(r) > 1 and (nxt.isupper() or prv.isupper()):
                r = r.upper()
            else:
                r = r[:1].upper() + r[1:]
        out.append(r)
    return "".join(out)


# ------------------------------------------------------------------ API -----
def convert(text: str, exceptions: dict[str, str] | None = None, source: str = "auto") -> Result:
    """
    text:       istalgan matn (lotin, kirill yoki aralash)
    exceptions: {eski_lotin_so'z: yangi_shakli}
    source:     "auto" | "latin" | "cyrillic"
    """
    ex = exceptions if isinstance(exceptions, Exceptions) else Exceptions(
        {_norm_key(k): v for k, v in (exceptions or {}).items()})
    flags: list[Flag] = []
    stats = {"words": 0, "changed": 0}

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if m.group(1):
            if source == "latin":
                return tok
            new = _cyr_word(tok, flags)
        else:
            if source == "cyrillic":
                return tok
            new = _latin_word(tok, ex, flags)
        stats["words"] += 1
        if new != tok:
            stats["changed"] += 1
        return new

    parts = _PROTECT.split(text)  # toq indekslar = himoyalangan bo'laklar
    converted = "".join(p if i % 2 else _TOKEN.sub(repl, p) for i, p in enumerate(parts))
    seen, unique = set(), []
    for f in flags:
        if (f.word, f.reason) not in seen:
            seen.add((f.word, f.reason))
            unique.append(f)
    return Result(converted, unique, stats)


def detect_script(text: str) -> str:
    """'cyrillic' | 'latin_old' | 'new' | 'latin' | 'none' (taxminiy aniqlash)."""
    cyr = len(re.findall("[\u0400-\u04ff]", text))
    lat = len(re.findall("[A-Za-z]", text))
    new = len(re.findall("[ÖöĞğŞşÇç]", text))
    if cyr > lat:
        return "cyrillic"
    if lat + new == 0:
        return "none"
    if re.search("[oOgG][" + _APOS + "]|[sScC][hH]", text):
        return "latin_old"
    return "new" if new else "latin"


if __name__ == "__main__":
    data = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    r = convert(data)
    print(r.text)
    for f in r.flags:
        print(f"[tekshiring] {f.word}: {f.reason}", file=sys.stderr)
