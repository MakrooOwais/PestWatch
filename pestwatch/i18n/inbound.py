"""Understand farmer replies (SMS, app, IVR keypad) in any supported language.

Deterministic keyword grammar from the catalogs, so it runs offline on the
server or gateway with no ML. Handles accents, punctuation, casing, simple
typos, negation ("sijaona kutu" = *haven't* seen rust), and detects
which language the farmer wrote in so the reply matches it.
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import catalogs, resolve, t

# Earlier wins when several intents match: negations beat positives.
PRIORITY = ["STOP", "START", "LANGUAGE", "HELP", "REFERRAL", "NO_PEST", "PEST_FOUND"]


@dataclass
class Parsed:
    intent: str                 # one of PRIORITY or "UNKNOWN"
    lang: str                   # language to reply in
    detected_lang: Optional[str]  # language the text was written in (None if ambiguous, e.g. "1")
    new_language: Optional[str]   # for LANGUAGE intent
    reply: str
    matched: List[str]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9?]+", " ", text).strip()


def _keyword_index() -> Dict[str, List[Tuple[str, str]]]:
    """normalized keyword -> [(lang, intent)]"""
    idx: Dict[str, List[Tuple[str, str]]] = {}
    for code, c in catalogs().items():
        for intent, words in c["intents"].items():
            for w in words:
                idx.setdefault(normalize(w), []).append((code, intent))
    return idx


def _aliases() -> Dict[str, str]:
    return {normalize(a): code for code, c in catalogs().items() for a in c["meta"]["aliases"]}


def _match(text: str, kw: str, tokens: List[str]) -> bool:
    if " " in kw or len(kw) < 5:
        return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text) is not None
    # Single longer words tolerate small typos ("nimeonaa", "rouile").
    return any(tok == kw or difflib.SequenceMatcher(None, tok, kw).ratio() >= 0.85 for tok in tokens)


def parse(text: str, profile_lang: str = "en") -> Parsed:
    profile_lang = resolve(profile_lang)
    norm = normalize(text)
    tokens = norm.split()
    aliases = _aliases()

    # "SW", "Kiswahili", "language french", "lugha kiswahili"
    lang_words = {normalize(w) for c in catalogs().values() for w in c["intents"]["LANGUAGE"]}
    rest = [tok for tok in tokens if tok not in lang_words]
    if len(rest) == 1 and rest[0] in aliases:
        new = aliases[rest[0]]
        return Parsed("LANGUAGE", new, new, new, t("reply.LANGUAGE", new), [rest[0]])

    hits: Dict[str, List[Tuple[str, str]]] = {}
    for kw, owners in _keyword_index().items():
        if kw and _match(norm, kw, tokens):
            for lang, intent in owners:
                hits.setdefault(intent, []).append((lang, kw))

    # Language evidence: keywords unique to one language, weighted by length.
    score: Dict[str, int] = {}
    idx = _keyword_index()
    for intent, pairs in hits.items():
        for lang, kw in pairs:
            if len({lg for lg, _ in idx[kw]}) == 1 and not kw.isdigit():
                score[lang] = score.get(lang, 0) + len(kw)
    detected = max(score, key=score.get) if score else None
    reply_lang = detected or profile_lang

    intent = next((i for i in PRIORITY if i in hits and i != "LANGUAGE"), "UNKNOWN")
    matched = sorted({kw for pairs in hits.values() for _, kw in pairs})
    return Parsed(intent, reply_lang, detected, None, t(f"reply.{intent}", reply_lang), matched)
