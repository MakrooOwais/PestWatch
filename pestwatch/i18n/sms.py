"""SMS encoding: GSM 03.38 (7-bit) vs UCS-2, and segment counting.

One accented character outside GSM-7 (e.g. 'ç', 'ê') switches the whole SMS to
UCS-2, cutting a segment from 160 to 70 characters and multiplying cost. We
keep correct spelling when it fits in one segment and otherwise transliterate
only the offending characters.
"""
import math
import unicodedata

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = set("^{}\\[~]|€\f")  # cost 2 septets each


def gsm7_length(text: str):
    """Septets needed, or None if the text is not GSM-7 encodable."""
    n = 0
    for ch in text:
        if ch in GSM7_BASIC:
            n += 1
        elif ch in GSM7_EXT:
            n += 2
        else:
            return None
    return n


def segments(text: str):
    """(encoding, segment count) for a message."""
    g = gsm7_length(text)
    if g is not None:
        return "GSM-7", 1 if g <= 160 else math.ceil(g / 153)
    n = len(text.encode("utf-16-le")) // 2
    return "UCS-2", 1 if n <= 70 else math.ceil(n / 67)


def transliterate(text: str) -> str:
    """Replace only non-GSM-7 characters with their unaccented form."""
    out = []
    for ch in text:
        if ch in GSM7_BASIC or ch in GSM7_EXT:
            out.append(ch)
        else:
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base or "?")
    return "".join(out)


def fit(text: str):
    """Best single-message form of ``text``: (text, encoding, segments)."""
    enc, seg = segments(text)
    if enc == "UCS-2":
        alt = transliterate(text)
        enc2, seg2 = segments(alt)
        if seg2 < seg or seg2 == 1:
            return alt, enc2, seg2
    return text, enc, seg
